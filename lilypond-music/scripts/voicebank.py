"""A DiffSinger voicebank on disk, and the built-in preview voice beside it.

    voice = Voice("~/voices/tiger")            # config, tables, ONNX sessions
    voice = PreviewVoice()                     # no files, no download

A bank is a directory holding `dsconfig.yaml`, an acoustic `.onnx`, a phoneme
table, a `dsdict*.yaml` grapheme-to-phoneme dictionary, and a vocoder package
with its own config and ONNX -- usually `dsvocoder/` inside the bank, sometimes
an nsf_hifigan package outside it. Banks are made for OpenUtau; this reads the
same files directly, so nothing here needs a GUI.

Little of that is uniform, and this module is mostly the catalogue of what
varies. The phoneme table is `phonemes.txt` with the line number as the token
id, or `*.phonemes.json` with the ids stated. The dictionary holds ten thousand
words or two hundred, with the rest coming from a phonemizer plugin that has to
be the one for the language being sung. The vocoder package ships one yaml with
mel parameters and sometimes a second with none, and picking the wrong one gives
a compatibility check that compares None against None and passes.
`references/singing-synthesis.md` section 8 is the user-facing catalogue and
`scripts/dev/bank_check.py` answers it for a specific bank.

`PreviewVoice` presents the same surface -- `is_vowel`, `consonant_len`,
`silence`, `guess` -- over three formants and no files, which is what lets
`sing.py --preview` run the whole phoneme and pitch path with no voicebank and
no onnxruntime installed.

Almost every English bank is licensed for non-commercial use, and several
forbid redistribution or synthesis of real people. Read the bank's terms;
nothing here bundles or downloads one.
"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from errors import die, onnx_errors                        # noqa: E402
from phonemes import CONSONANT_S, DEFAULT_CONSONANT_S      # noqa: E402

SIL = ["SP", "sil", "pau", "sp"]
BREATH = ["AP", "br", "breath"]


def load_yaml(path, tolerant=False):
    """Read a bank's yaml. `tolerant` returns None rather than raising.

    Banks ship hand-edited dictionaries and not all of them parse: LIEE's
    `dsdict-zh-yue.yaml` has one list item outdented by a space, which is a
    parse error rather than a warning. That must not take down a scan of every
    dictionary in the bank -- it is one language out of eighteen and the run is
    almost certainly not singing in it. Configs are still read strictly.
    """
    try:
        import yaml
    except ImportError:
        die("pyyaml is missing -- pip install pyyaml (or rerun scripts/setup.sh)")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError:
        if not tolerant:
            raise
        return None


class Voice:
    """A DiffSinger bank: config, phoneme table, dictionary, ONNX sessions."""

    def __init__(self, path, vocoder=None, plugin=None, voice_mode=None,
                 use_predictors=("duration", "pitch", "variance")):
        try:
            import onnxruntime
        except ImportError:
            die("onnxruntime is missing -- pip install onnxruntime pyyaml")
        self.dir = Path(path).expanduser().resolve()
        cfg_path = self.dir / "dsconfig.yaml"
        if not cfg_path.exists():
            die(f"{self.dir} has no dsconfig.yaml -- is that a DiffSinger bank?")
        self.cfg = load_yaml(cfg_path)

        for key in ("acoustic", "phonemes"):
            if not self.cfg.get(key):
                die(f"dsconfig.yaml has no '{key}' key")

        self.phonemes = self._load_phonemes(self.dir / self.cfg["phonemes"])
        self.symbols, self.entries = self._load_dict()
        self.from_plugin, self.from_g2p = set(), set()
        self._phonemizer = self._load_phonemizer(plugin)
        self.spelled_out = set()
        # Multi-speaker banks -- "voice modes", "vocal colours" -- carry one
        # embedding file per mode and expect the chosen one on every frame.
        self.speakers = [str(s) for s in (self.cfg.get("speakers") or [])]
        self.hop = int(self.cfg.get("hop_size", 512))
        self.sample_rate = int(self.cfg.get("sample_rate", 44100))
        self.frame_s = self.hop / self.sample_rate
        self.mel_base = str(self.cfg.get("mel_base", "10"))

        opts = onnxruntime.SessionOptions()
        opts.log_severity_level = 3
        self.acoustic = onnxruntime.InferenceSession(
            str(self.dir / self.cfg["acoustic"]), opts,
            providers=["CPUExecutionProvider"])

        vdir = Path(vocoder).expanduser().resolve() if vocoder else self._find_vocoder()
        self.vocoder_cfg, self.vocoder = self._load_vocoder(vdir, onnxruntime, opts)

        # The optional models: whichever of dsdur/dspitch/dsvariance this bank
        # ships. A bank with none of them is the common case and is not an
        # error, but a bank that has one and fails to load it is worth saying
        # out loud -- a run that quietly sounds worse is the thing to avoid.
        from predictors import load_predictors
        self.predictors = load_predictors(self.dir, onnxruntime, opts,
                                          voice_mode or (self.speakers or [None])[0],
                                          use_predictors)

        # A vocoder trained on a different mel definition does not merely sound
        # worse, it produces noise; the numbers must match exactly.
        for key in ("sample_rate", "hop_size", "num_mel_bins", "mel_fmin", "mel_fmax"):
            a, b = self.cfg.get(key), self.vocoder_cfg.get(key)
            if a is not None and b is not None and abs(float(a) - float(b)) > 1e-5:
                die(f"acoustic model and vocoder disagree on {key} ({a} != {b}). "
                    "This bank needs its matching vocoder package.")

    def _load_phonemes(self, path):
        """The bank's phoneme table, in either format banks use.

        `phonemes.txt` is one phoneme per line and the line number is the token
        id. LIEE and the other multi-language banks ship `*.phonemes.json`
        instead, an object mapping phoneme to id -- and there the ids are not
        the position in the file, so they have to be read rather than counted.
        """
        if not path.exists():
            die(f"phoneme list {path} not found")
        if path.suffix.lower() == ".json":
            table = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(table, dict):
                return {str(k): int(v) for k, v in table.items()}
            return {str(p): i for i, p in enumerate(table)}
        lines = path.read_text(encoding="utf-8").splitlines()
        return {p.strip(): i for i, p in enumerate(lines) if p.strip()}

    def _load_dict(self):
        """Read dsdict*.yaml: phoneme types plus the word -> phonemes table."""
        import phonemizer as ph_mod
        symbols, entries, path, unreadable = ph_mod.bank_dictionary(self.dir)
        self.dict_path, self.unreadable_dicts = path, unreadable
        if not entries:
            die("no usable dsdict*.yaml in the bank. English banks ship one; "
                "without it there is no way to turn words into phonemes."
                + (f" ({', '.join(unreadable)} did not parse)" if unreadable else ""))
        return symbols, entries

    def _load_phonemizer(self, plugin):
        """The bank's phonemizer plugin, and how far it agrees with the bank.

        The agreement is kept rather than merely used to choose, because it is
        the number that says whether a bank has a usable pronunciation source
        at all: a bank whose plugin is missing falls back to a few hundred
        dsdict words plus letter-to-sound rules, and that is worth knowing
        before listening to eleven seconds of it.
        """
        import phonemizer as ph_mod
        self.plugin_path, self.plugin_agreement = None, None
        path = plugin or ph_mod.find_plugin(self.dir, self.entries)
        if not path:
            return None
        try:
            found = ph_mod.Phonemizer.from_plugin(path)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            # A plugin that cannot be read costs pronunciation, not the render,
            # so it is reported and skipped. Narrowly: these are what an
            # unreadable file, a .dll with no archive in it and a truncated zip
            # actually raise, and anything else here would be a bug in
            # phonemizer.py rather than a problem with the bank.
            print(f"  ! could not read the phonemizer plugin {Path(path).name}: "
                  f"{str(e)[:120]}")
            return None
        self.plugin_path = Path(path)
        self.plugin_agreement = ph_mod.agreement(found, self.entries)
        return found

    def _find_vocoder(self):
        named = self.cfg.get("vocoder")
        if named and (self.dir / str(named)).is_dir():
            return self.dir / str(named)
        for name in ("vocoder", "nsf_hifigan", "dsvocoder"):
            if (self.dir / name).is_dir():
                return self.dir / name
        die("no vocoder found. Pass --vocoder /path/to/nsf_hifigan, or put the "
            "vocoder package in a 'vocoder' folder inside the bank.")

    def _load_vocoder(self, vdir, onnxruntime, opts):
        """Find the vocoder's own config and ONNX inside its package.

        A vocoder package can hold more than one yaml -- openvpi's ships an
        `oudep.yaml` describing the OpenUtau package alongside the real
        `vocoder.yaml` -- so the config is the one that actually declares mel
        parameters. Taking whichever file globbed last would silently produce a
        config with no sample rate, and the compatibility check below would then
        compare None against None and pass.
        """
        cfg = {}
        for c in sorted(list(vdir.glob("*.yaml")) + list(vdir.glob("*.json"))):
            found = load_yaml(c) if c.suffix == ".yaml" else json.loads(c.read_text())
            if isinstance(found, dict) and (found.get("model") or found.get("sample_rate")):
                cfg = found
                break
        # openvpi's vocoder packages spell the mel band edges `mel_fmin` and
        # `mel_fmax`; a bank shipping its own vocoder tends to spell them
        # `fmin`/`fmax` (TIGER does). Without normalising, the compatibility
        # check below compares a number against None and passes -- and that is
        # the one check whose failure is noise rather than a worse voice.
        for short, long in (("fmin", "mel_fmin"), ("fmax", "mel_fmax")):
            if long not in cfg and short in cfg:
                cfg[long] = cfg[short]
        model = cfg.get("model")
        path = (vdir / model) if model else None
        if path is None or not path.exists():
            onnxs = list(vdir.glob("*.onnx"))
            if not onnxs:
                die(f"no .onnx in the vocoder directory {vdir}")
            path = onnxs[0]
        return cfg, onnxruntime.InferenceSession(
            str(path), opts, providers=["CPUExecutionProvider"])

    def guess(self, word):
        """Words the bank's own dictionary does not cover.

        A miss in the bank's dsdict is normal, not a broken bank: TIGER ships
        about ten thousand words there and expects its OpenUtau phonemizer
        plugin to cover the rest. So ask the plugin -- it carries a 133,000
        word dictionary in this bank's own phone set, plus a neural G2P for
        whatever is not in that. See scripts/phonemizer.py.

        Do not substitute a general English dictionary here. This phone set
        has `dr` and `tr` as single affricates, so CMUdict's `d r` for "drift"
        would be both the wrong symbols and the wrong phoneme count.
        """
        if self._phonemizer is None:
            return self._spell_out(word)
        out = self._phonemizer(word)
        if out and all(p in self.phonemes for p in out):
            (self.from_g2p if word.lower() in self._phonemizer.predicted
             else self.from_plugin).add(word)
            return out
        return self._spell_out(word)

    def _spell_out(self, word):
        """Last resort: letter-to-sound rules, which are frequently wrong."""
        import preview_voice
        guessed = [p for p in preview_voice.letters_to_phonemes(word)
                   if p in self.phonemes]
        if guessed:
            self.spelled_out.add(word)
            return guessed
        return None

    def speaker_embedding(self, name=None):
        """Load a voice mode's `.emb`: a raw little-endian float32 vector."""
        if not self.speakers:
            return None, None
        chosen = name or self.speakers[0]
        if chosen not in self.speakers:
            match = [s for s in self.speakers if Path(s).name == chosen]
            chosen = match[0] if match else chosen
        if chosen not in self.speakers:
            die(f"voice mode {chosen!r} not in this bank. Available: "
                f"{', '.join(Path(s).name for s in self.speakers)}")
        path = self.dir / f"{chosen}.emb"
        if not path.exists():
            die(f"the bank lists voice mode {chosen!r} but has no {path.name}")
        emb = np.fromfile(path, dtype="<f4")
        if not 32 <= emb.size <= 4096:
            die(f"{path.name} does not look like an embedding vector "
                f"({emb.size} floats)")
        return chosen, emb

    def is_vowel(self, ph):
        """Only true vowels count as the syllable's nucleus.

        OpenUtau's dictionary format calls `w` and `y` semivowels and treats
        them as medials, but in English they are onset consonants: letting a
        semivowel be the nucleus turns "world" into [w][er l d] and the note
        lands on the w.
        """
        return self.symbols.get(ph, "") == "vowel"

    def consonant_len(self, ph):
        return CONSONANT_S.get(self.symbols.get(ph, ""), DEFAULT_CONSONANT_S)

    def token(self, ph):
        if ph in self.phonemes:
            return self.phonemes[ph]
        for alt in (ph.upper(), ph.lower()):
            if alt in self.phonemes:
                return self.phonemes[alt]
        return None

    def silence(self):
        for s in SIL:
            if s in self.phonemes:
                return s
        die("the bank's phoneme list has no silence phoneme (SP)")

    def breath(self):
        for s in BREATH:
            if s in self.phonemes:
                return s
        return self.silence()


class PreviewVoice:
    """The built-in formant voice: same interface, no files, no download."""

    def __init__(self):
        import preview_voice
        self.pv = preview_voice
        self.dir = Path("(built-in preview voice)")
        self.symbols = preview_voice.SYMBOL_TYPES
        self.entries = {}
        self.phonemes = {p: i for i, p in enumerate(self.symbols)}
        self.sample_rate = preview_voice.SR
        self.hop = 256
        self.frame_s = self.hop / self.sample_rate

    def guess(self, word):
        return self.pv.letters_to_phonemes(word)

    is_vowel = Voice.is_vowel
    consonant_len = Voice.consonant_len

    def silence(self):
        return "SP"

    def breath(self):
        return "AP"


def inspect_bank(path, vocoder=None):
    """Print a voicebank's layout and every model's ONNX interface.

    This exists because the DiffSinger ONNX contract is only partly documented
    in prose. The acoustic and vocoder interfaces are pinned down (see
    references/singing-synthesis.md); the duration, pitch and variance models
    are not, and guessing at a tensor's meaning produces plausible garbage
    rather than an error. The models themselves declare names, dtypes and
    shapes, so read them from the file rather than from a blog post.
    """
    try:
        import onnxruntime
    except ImportError:
        die("onnxruntime is missing -- bash scripts/setup-singing.sh")
    bank = Path(path).expanduser().resolve()
    if not bank.is_dir():
        die(f"{bank} is not a directory")

    print(f"bank: {bank}")
    cfg_path = bank / "dsconfig.yaml"
    if cfg_path.exists():
        cfg = load_yaml(cfg_path) or {}
        print("\ndsconfig.yaml")
        for k in sorted(cfg):
            v = cfg[k]
            print(f"  {k}: {v if not isinstance(v, dict) else '{...}'}")
    else:
        print("\n  ! no dsconfig.yaml at the top level -- is this the bank root?")

    # Dictionaries live wherever the bank felt like putting them: TIGER keeps
    # none at the root and one per predictor folder. Summarise them all, and
    # summarise rather than print -- these files run to 690 KB.
    for d in sorted(bank.rglob("dsdict*.yaml")):
        data = load_yaml(d, tolerant=True)
        if not isinstance(data, dict):
            print(f"\n{d.relative_to(bank)}: ! not readable as yaml, skipped")
            continue
        types = {}
        for s in (data.get("symbols") or []):
            types[str(s.get("type", "?"))] = types.get(str(s.get("type", "?")), 0) + 1
        print(f"\n{d.relative_to(bank)}: {len(data.get('entries') or [])} entries, "
              f"{len(data.get('symbols') or [])} symbols "
              f"({', '.join(f'{k} {v}' for k, v in sorted(types.items()))})")
        if data.get("replacements"):
            print(f"  replacements: {len(data['replacements'])}")

    ph = bank / str((load_yaml(cfg_path) or {}).get("phonemes", "phonemes.txt")) \
        if cfg_path.exists() else None
    if ph and ph.exists():
        if ph.suffix.lower() == ".json":
            table = json.loads(ph.read_text(encoding="utf-8"))
            names = list(table) if isinstance(table, dict) else [str(p) for p in table]
        else:
            names = [n.strip()
                     for n in ph.read_text(encoding="utf-8").splitlines()
                     if n.strip()]
        print(f"\n{ph.name}: {len(names)} phonemes, first 12: {' '.join(names[:12])}")

    embeddings = sorted(bank.rglob("*.emb"))
    for emb in embeddings:
        print(f"  {emb.relative_to(bank)}: "
              f"{np.fromfile(emb, dtype='<f4').size} floats")

    opts = onnxruntime.SessionOptions()
    opts.log_severity_level = 3
    opts.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL

    # Every dsconfig in the tree, not just the top level: the predictors keep
    # theirs one level down and their models one level below that (TIGER's live
    # in dsdur/files/), so a single-level walk misses exactly the models whose
    # interface is the reason to run --inspect at all.
    for sub in sorted(bank.rglob("dsconfig.yaml")):
        if sub.parent == bank:
            continue
        data = load_yaml(sub) or {}
        print(f"\n{sub.relative_to(bank)}")
        for k in sorted(data):
            print(f"  {k}: {data[k]}")

    onnx_files = sorted(bank.rglob("*.onnx"))
    if vocoder:
        vdir = Path(vocoder).expanduser().resolve()
        for sub in sorted(vdir.glob("*.yaml")):
            data = load_yaml(sub) or {}
            print(f"\n{vdir.name}/{sub.name}")
            for k in sorted(data):
                print(f"  {k}: {data[k]}")
        onnx_files += sorted(vdir.glob("*.onnx"))
    for onnx_file in onnx_files:
        rel = (onnx_file.relative_to(bank) if bank in onnx_file.parents
               else onnx_file.name)
        mb = onnx_file.stat().st_size / 1e6
        print(f"\n=== {rel}  ({mb:.1f} MB)")
        try:
            s = onnxruntime.InferenceSession(str(onnx_file), opts,
                                             providers=["CPUExecutionProvider"])
        except onnx_errors() as e:
            # --inspect exists to describe a bank that is not working, so one
            # model it cannot open must not stop it describing the rest.
            print(f"  ! could not load: {str(e)[:200]}")
            continue
        for i in s.get_inputs():
            print(f"  IN   {i.name:<16} {i.type:<16} {i.shape}")
        for o in s.get_outputs():
            print(f"  OUT  {o.name:<16} {o.type:<16} {o.shape}")

    print("\nThis output is the specification: where it and the documentation "
          "disagree, believe it.\nWhat the predictors' tensors mean, and how "
          "that was established, is in scripts/predictors.py.")
