#!/usr/bin/env python3
"""Sing the vocal line of a score with a DiffSinger voicebank (English).

    python3 scripts/sing.py score.ly --voice ~/voices/TigerDS -o out/

Takes either a .ly score (it runs vocal_score.py for you) or the
`*-vocals.json` that script produces, and writes `<stem>-vocal.wav`: the sung
line, at the score's tempo, padded so that sample 0 is beat 0 and it lines up
with the instrumental render without further alignment.

    render.py score.ly --vocal out/score-vocal.wav

WHAT A VOICEBANK IS
-------------------
A DiffSinger bank is a directory holding `dsconfig.yaml`, an acoustic `.onnx`,
a phoneme table, a `dsdict*.yaml` grapheme-to-phoneme dictionary, and a vocoder
package with its own config and ONNX -- usually `dsvocoder/` inside the bank,
sometimes an nsf_hifigan package outside it. Banks are made for OpenUtau; this
script drives the same files directly so nothing here needs a GUI. Point
`--voice` at the bank directory and, if the bank carries no vocoder,
`--vocoder` at the vocoder directory.

Little of that is uniform. The phoneme table is `phonemes.txt`, one per line
with the line number as the token id, or `*.phonemes.json` with the ids stated;
the acoustic model asks for `speedup` or for `steps`, and for `depth` as a step
count or as a fraction; the dictionary holds ten thousand words or two hundred,
with the rest coming from a phonemizer plugin that has to be the one for the
language being sung. `references/singing-synthesis.md` section 8 is the
catalogue of what varies, and `scripts/dev/bank_check.py` answers it for a
specific bank.

Almost every English bank is licensed for non-commercial use, and several
forbid redistribution or synthesis of real people. Read the bank's terms; this
script deliberately does not bundle or download one.

WHAT THIS DOES AND DOES NOT MODEL
---------------------------------
Every model the bank ships is used. The acoustic model and the vocoder are the
two every bank has; beside them a bank may carry up to three predictors, and
each one replaces something this script would otherwise have to invent:

    dsdur       how a syllable's time divides between its consonants and its
                vowel -- something a score says nothing about. Without it, a
                table of constants (CONSONANT_S below).
    dspitch     that singer's expressive deviation *around* the written notes:
                the scoop into a phrase, the drift on a held note. Without it,
                synthetic portamento and vibrato in f0_curve().
    dsvariance  energy, breathiness, voicing and tension curves, where the
                acoustic model asks for them. Without it, flat inputs.

The score keeps the decisions the score should keep. `dsdur` is told how long
each syllable lasts and only divides that time up; `dspitch` is told the notes
and only deviates around them. What is being borrowed is the singer's habits,
not their opinion about the tune.

Each is optional. A bank with none of them still renders -- most banks ship one
or two -- and the run prints which models it actually used, because a render
that quietly sounds worse because a folder was missing is the failure worth
guarding against. `--literal-timing` and `--literal-pitch` force the built-in
fallbacks; `--literal-pitch` in particular is what a score-following video
wants, since a model that scoops hard into a note visibly disagrees with a
playhead drawn on exact onsets.

See scripts/predictors.py for what the predictors' tensors mean and how that
was established.
"""

import argparse
import json
import math
import random
import re
import subprocess
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from errors import SkillError, cli, onnx_errors           # noqa: E402

HERE = Path(__file__).resolve().parent

# Consonants are short and, crucially, land *before* the beat: a singer starts
# the "l" of "lantern" early so the vowel arrives on time.  These are seconds,
# by phoneme class, and they are borrowed from the end of the preceding note.
CONSONANT_S = {"stop": 0.055, "affricate": 0.090, "fricative": 0.090,
               "aspirate": 0.070, "nasal": 0.060, "liquid": 0.050,
               "semivowel": 0.050}
DEFAULT_CONSONANT_S = 0.065
# Below about a frame and a half the acoustic model has nothing to work with and
# the phoneme is heard as a click rather than a consonant.
MIN_PHONEME_S = 0.02

SIL = ["SP", "sil", "pau", "sp"]
BREATH = ["AP", "br", "breath"]


def die(msg):
    """Stop, with a sentence the caller can act on.

    A raise rather than `sys.exit`, because every one of these sits in a
    function something else imports: `selftest.py` and `bank_check.py` both
    build a `Voice` in-process, and a bank without a dsconfig.yaml should be an
    exception they can see rather than the end of their process.
    """
    raise SkillError(msg)


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


# --------------------------------------------------------------------------
# the voicebank
# --------------------------------------------------------------------------

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
            names = [l.strip() for l in ph.read_text(encoding="utf-8").splitlines()
                     if l.strip()]
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


# --------------------------------------------------------------------------
# words to phonemes
# --------------------------------------------------------------------------

WORD_CLEAN = re.compile(r"[^A-Za-z'’-]+")


def lookup(voice, word):
    """The bank's dictionary, or letter-to-sound when previewing."""
    w = WORD_CLEAN.sub("", word).lower().replace("’", "'")
    if not w:
        return None
    for key in (w, w.replace("'", ""), w.rstrip("-")):
        if key in voice.entries:
            return list(voice.entries[key])
    return voice.guess(w) if hasattr(voice, "guess") else None


def syllabify(voice, phonemes, count):
    """Split a word's phonemes into `count` syllables, one vowel each.

    Maximal onset: consonants between two vowels belong to the syllable that
    follows them, which is what singers do -- "lan-tern" is sung [l a][n t er n]
    only because the notation says so; left alone, an English singer sings
    [l a n][t er n] and lands the t with the second note.
    """
    if count <= 1:
        return [list(phonemes)]
    vowels = [i for i, p in enumerate(phonemes) if voice.is_vowel(p)]
    if len(vowels) != count:
        # The dictionary and the hyphenation disagree.  Spreading the vowels we
        # have over the notes we have is wrong but audible-as-intended; failing
        # here would kill the whole render over one word.
        groups = [[] for _ in range(count)]
        for i, p in enumerate(phonemes):
            groups[min(i * count // max(len(phonemes), 1), count - 1)].append(p)
        return [g or [phonemes[-1]] for g in groups]

    groups, start = [], 0
    for n, v in enumerate(vowels):
        if n + 1 < len(vowels):
            nxt = vowels[n + 1]
            # leave one consonant as this syllable's coda only if there are two
            # or more between the vowels
            split = v + 1 if nxt - v <= 2 else nxt - 1
        else:
            split = len(phonemes)
        groups.append(list(phonemes[start:split]))
        start = split
    return groups


# --------------------------------------------------------------------------
# score to phoneme timeline
# --------------------------------------------------------------------------

class Clock:
    """Score moments (in whole notes) to seconds, through a tempo map.

    A single tempo cannot describe a score that changes speed, and getting this
    wrong is invisible until it is glaring: with one number taken from the
    piece's *last* `\\tempo`, every note before the change is placed at the
    wrong time and the sung line drifts steadily away from the instruments.
    The map is the same one LilyPond writes into its MIDI, so the two agree by
    construction.

    Callable, so it can stand where a bare tempo used to: `clock(moment)`.
    """

    def __init__(self, tempo_map=None, tempo=None):
        pairs = [(float(w), float(q)) for w, q in (tempo_map or [])
                 if float(q) > 0]
        if not pairs:
            pairs = [(0.0, float(tempo or 60.0))]
        pairs.sort()
        if pairs[0][0] > 0:
            pairs.insert(0, (0.0, pairs[0][1]))
        # Precompute the elapsed seconds at each change, so a lookup is local.
        self.points, elapsed = [], 0.0
        for i, (when, qpm) in enumerate(pairs):
            if i:
                prev_when, prev_qpm, prev_secs = self.points[-1]
                elapsed = prev_secs + (when - prev_when) * 4.0 * 60.0 / prev_qpm
            self.points.append((when, qpm, elapsed))
        self.tempo = pairs[0][1]

    def __call__(self, moment):
        when, qpm, secs = self.points[0]
        for point in self.points:
            if point[0] <= moment:
                when, qpm, secs = point
            else:
                break
        return secs + (float(moment) - when) * 4.0 * 60.0 / qpm

    def __repr__(self):
        return ("Clock(" + ", ".join(f"{w:g}@{q:g}" for w, q, _s in self.points)
                + ")")


def seconds(whole, clock):
    """Whole notes to seconds, through a Clock (or a bare tempo in bpm)."""
    if callable(clock):
        return clock(whole)
    return whole * 4.0 * 60.0 / clock


def phrase_split(notes, clock, gap=0.6):
    """Break the line at rests long enough to breathe in."""
    phrases, current = [], []
    for n in notes:
        if current:
            prev = current[-1]
            rest = seconds(n["when"] - (prev["when"] + prev["dur"]), clock)
            if rest > gap:
                phrases.append(current)
                current = []
        current.append(n)
    if current:
        phrases.append(current)
    return phrases


def sung_notes(notes):
    """Merge tied notes; keep melismata as their own notes on the same syllable."""
    out = []
    for n in notes:
        if n.get("tied") and out:
            out[-1]["dur"] += n["dur"]
            continue
        out.append(dict(n))
    return out


def predicted_lengths(voice, groups, clock, head=0.25, tail=0.35):
    """Ask `dsdur` how each syllable's time divides between its phonemes.

    The model is given the whole phrase as a sequence of words -- the syllables
    plus the silences between them -- because a consonant's length depends on
    what precedes it. `word_dur` is the time the *score* gives each syllable, so
    the model decides only the split inside it, never when the next syllable
    starts. That division of labour is what keeps the singer on the beat while
    still using the bank's own timing.

    Returns a list parallel to `groups`, each entry a list of seconds per
    phoneme, or None if the bank has no `dsdur` or the model declined.
    """
    model = getattr(voice, "predictors", None) and voice.predictors.duration
    if model is None:
        return None
    sil = voice.silence()
    words, index_of = [], {}
    cursor = None
    for gi, g in enumerate(groups):
        if not g["phones"]:
            continue
        start = seconds(g["notes"][0]["when"], clock)
        end = seconds(g["notes"][-1]["when"] + g["notes"][-1]["dur"], clock)
        gap = head if cursor is None else start - cursor
        if gap > 1e-3:
            words.append({"phones": [sil], "seconds": gap, "midi": 0})
        index_of[len(words)] = gi
        words.append({"phones": list(g["phones"]), "seconds": max(end - start, 1e-3),
                      "midi": int(g["notes"][0]["pitch"])})
        cursor = end
    if not words:
        return None
    words.append({"phones": [sil], "seconds": tail, "midi": 0})

    got = model.predict(words)
    if got is None:
        return None
    voice.predictors.used.add("dsdur")
    out = [None] * len(groups)
    for wi, gi in index_of.items():
        out[gi] = got[wi]
    return out


def phonemize(voice, phrase, clock, warn):
    """Turn one phrase into [(phoneme, start_s, end_s)], vowels on the beat."""
    notes = sung_notes(phrase)
    t0 = seconds(notes[0]["when"], clock)

    # Group notes by syllable: a syllable's note plus any melisma notes after it.
    groups, cur = [], None
    for n in notes:
        if n.get("syllable") and not n.get("melisma"):
            cur = {"syllable": n["syllable"], "word": n.get("word") or n["syllable"],
                   "syllabic": n.get("syllabic", "single"), "notes": [n]}
            groups.append(cur)
        elif cur is not None:
            cur["notes"].append(n)
        else:                                   # melisma with nothing before it
            cur = {"syllable": None, "word": None, "syllabic": "single", "notes": [n]}
            groups.append(cur)

    # Words: a run of groups whose syllabic marks say they belong together.
    words, wcur = [], []
    for g in groups:
        wcur.append(g)
        if g["syllabic"] in ("single", "end") or g["syllable"] is None:
            words.append(wcur)
            wcur = []
    if wcur:
        words.append(wcur)

    # Phonemes first, for every syllable in the phrase, because `dsdur` is
    # asked about the phrase as a whole rather than one syllable at a time.
    for word in words:
        text = word[0]["word"] or ""
        phon = lookup(voice, text) if text else None
        if text and phon is None:
            warn.add(text)
            phon = [voice.symbols and next(iter(voice.symbols)) or "a"]
        parts = syllabify(voice, phon or [], len(word)) if phon else [[] for _ in word]
        for group, phones in zip(word, parts):
            group["phones"] = list(phones)

    lengths = predicted_lengths(voice, groups, clock)

    timeline = []
    for gi, group in enumerate(groups):
        phones = group["phones"]
        # Either the singer's own model, or the constants it replaces.
        predicted = lengths[gi] if lengths else None
        length_of = ((lambda i, p: predicted[i]) if predicted
                     else (lambda i, p: voice.consonant_len(p)))
        start = seconds(group["notes"][0]["when"], clock)
        end = seconds(group["notes"][-1]["when"] + group["notes"][-1]["dur"], clock)
        vowel_at = next((i for i, p in enumerate(phones) if voice.is_vowel(p)), None)
        if vowel_at is None:
            onset, rest = list(enumerate(phones)), []
        else:
            onset = list(enumerate(phones))[:vowel_at]
            rest = list(enumerate(phones))[vowel_at:]

        # Onset consonants sit before the beat, taking time from whatever
        # precedes them -- the previous phoneme, or the head padding.
        lead = sum(length_of(i, p) for i, p in onset)
        room = start - (timeline[-1][1] if timeline else t0 - 0.5)
        scale = min(1.0, room / lead) if lead > 0 and room > 0 else (0 if lead else 1)
        cursor = start - lead * scale
        if timeline and cursor < timeline[-1][2]:
            timeline[-1] = (timeline[-1][0], timeline[-1][1], cursor)
        for i, p in onset:
            d = max(length_of(i, p) * scale, MIN_PHONEME_S)
            timeline.append((p, cursor, cursor + d))
            cursor += d

        # The vowel holds the note. A final consonant cluster is taken off
        # the end of the last note of the syllable.
        coda = [(i, p) for i, p in rest[1:] if not voice.is_vowel(p)]
        tail = sum(length_of(i, p) for i, p in coda)
        tail = min(tail, max(0.0, (end - start) * 0.4))
        vowel_end = end - tail
        if rest:
            timeline.append((rest[0][1], start, max(start + 0.02, vowel_end)))
            cursor = max(start + 0.02, vowel_end)
            share = tail / max(sum(length_of(i, p) for i, p in coda), 1e-9)
            for i, p in rest[1:]:
                d = length_of(i, p) * share if (i, p) in coda else 0.03
                d = max(d, MIN_PHONEME_S)
                timeline.append((p, cursor, cursor + d))
                cursor += d
        else:
            timeline.append((voice.silence(), start, end))

    return timeline, notes


def fill_silences(voice, timeline, head=0.25, tail=0.35):
    """Pad the phrase, plug gaps with silence, drop anything squeezed to nothing.

    Consonants are laid down backwards from the beat they precede, so a fast
    syllable can overrun the one before it and truncate its final consonant.
    A phoneme trimmed to zero length is worse than an absent one -- the model
    still allocates it a frame, and a one-frame stop reads as a click -- so a
    consonant with no room left is dropped rather than kept at zero.
    """
    sil = voice.silence()
    out = [(sil, timeline[0][1] - head, timeline[0][1])]
    for ph, a, b in timeline:
        a = max(a, out[-1][2])
        if b - a < MIN_PHONEME_S * 0.5:
            continue
        if a > out[-1][2] + 1e-6:
            out.append((sil, out[-1][2], a))
        out.append((ph, a, b))
    out.append((sil, out[-1][2], out[-1][2] + tail))
    return out


# --------------------------------------------------------------------------
# pitch
# --------------------------------------------------------------------------

def midi_to_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def written_pitch(notes, clock, start_s, frames, frame_s):
    """The pitch line exactly as written: one MIDI number per frame, no shaping.

    This is the score's own answer, and it has two jobs. It is the fallback
    when no `dspitch` model is available, via `f0_curve()` below, and it is
    what `dspitch` is *conditioned on* when one is: the model renders a
    deviation around the written notes rather than a melody of its own.
    """
    t = start_s + np.arange(frames) * frame_s
    pitch = np.zeros(frames)
    spans = [(seconds(n["when"], clock), seconds(n["when"] + n["dur"], clock),
              float(n["pitch"])) for n in notes]
    for a, b, p in spans:
        pitch[(t >= a) & (t < b)] = p
    pitch[t < spans[0][0]] = spans[0][2]
    pitch[t >= spans[-1][1]] = spans[-1][2]
    # A rest between two notes leaves a hole; hold the note before it, so the
    # curve handed to the vocoder is continuous even where nothing sounds.
    for i in range(1, frames):
        if pitch[i] == 0:
            pitch[i] = pitch[i - 1]
    return pitch, spans, t


def f0_curve(notes, clock, start_s, frames, frame_s, vibrato=True, seed=0):
    """A singer's pitch line through the phrase's notes, shaped by hand.

    A flat f0 per note is what makes synthetic singing sound synthetic, so
    three things are added: portamento across note changes (fast for small
    intervals, slower for leaps), vibrato that fades in on notes long enough to
    hold, and a slow random drift of a few cents.

    This is the stand-in for a `dspitch` model, and it is what `--literal-pitch`
    selects when a bank has one: the shapes here are generic where the model's
    are that singer's own, but they are also exactly reproducible and they never
    scoop into a note, which is what a score-following video wants.
    """
    rng = random.Random(seed)
    pitch, spans, t = written_pitch(notes, clock, start_s, frames, frame_s)

    for i in range(1, len(spans)):
        prev, cur = spans[i - 1][2], spans[i][2]
        if prev == cur:
            continue
        edge = spans[i][0]
        # 40 ms for a step, up to 110 ms for a wide leap
        width = min(0.11, 0.04 + 0.012 * abs(cur - prev))
        m = (t > edge - width / 2) & (t < edge + width / 2)
        if m.any():
            x = (t[m] - (edge - width / 2)) / width
            pitch[m] = prev + (cur - prev) * (x * x * (3 - 2 * x))

    if vibrato:
        phase = rng.uniform(0, 2 * math.pi)
        for a, b, _p in spans:
            if b - a < 0.55:
                continue
            m = (t >= a) & (t < b)
            local = t[m] - a
            onset = np.clip((local - 0.30) / 0.35, 0, 1)
            rate = 5.4 + rng.uniform(-0.3, 0.3)
            pitch[m] += 0.35 * onset * np.sin(2 * math.pi * rate * local + phase)

    drift = np.cumsum(np.array([rng.gauss(0, 1) for _ in range(frames)]))
    if frames > 1 and np.ptp(drift) > 0:
        k = max(1, int(0.5 / frame_s))
        drift = np.convolve(drift, np.ones(k) / k, mode="same")
        drift = drift / (np.max(np.abs(drift)) or 1) * 0.06
        pitch += drift

    return midi_to_hz(pitch).astype(np.float32)


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

def note_spans(timeline, notes, clock):
    """The phrase as a note sequence for `dspitch`: [(midi or None, seconds)].

    It has to cover the phoneme timeline exactly and contiguously -- head
    padding, the rests between notes and the tail are all notes as far as the
    model is concerned, just ones marked as rests.
    """
    start, end = timeline[0][1], timeline[-1][2]
    spans, cursor = [], start
    for n in notes:
        a = max(seconds(n["when"], clock), cursor)
        b = min(seconds(n["when"] + n["dur"], clock), end)
        if b <= cursor:
            continue
        if a > cursor + 1e-6:
            spans.append((None, a - cursor))
        spans.append((int(n["pitch"]), b - a))
        cursor = b
    if end > cursor + 1e-6:
        spans.append((None, end - cursor))
    return spans or [(None, max(end - start, 1e-3))]


def render_phrase(voice, timeline, notes, clock, steps, variance, depth=1.0,
                  voice_mode=None, literal_pitch=False, expressiveness=1.0):
    """One phrase of phonemes and pitch through acoustic + vocoder."""
    frame_s = voice.frame_s
    start_s = timeline[0][1]
    edges = [round((b - start_s) / frame_s) for _ph, _a, b in timeline]
    durations, prev = [], 0
    for e in edges:                       # cumulative rounding, never negative
        durations.append(max(1, e - prev))
        prev = max(e, prev + 1)
    total = sum(durations)

    tokens = []
    for ph, _a, _b in timeline:
        tok = voice.token(ph)
        if tok is None:
            tok = voice.token(voice.silence())
        tokens.append(tok)

    predictors = getattr(voice, "predictors", None)
    phones = [ph for ph, _a, _b in timeline]
    ph_seconds = [d * frame_s for d in durations]
    written, _spans, _t = written_pitch(notes, clock, start_s, total, frame_s)

    f0, pitch_model = None, predictors and predictors.pitch
    if pitch_model is not None and not literal_pitch:
        curve = pitch_model.predict(phones, ph_seconds,
                                    note_spans(timeline, notes, clock),
                                    written, total, expressiveness, steps)
        if curve is not None:
            f0 = midi_to_hz(np.asarray(curve, dtype=np.float64)).astype(np.float32)
            predictors.used.add("dspitch")
    if f0 is None:
        f0 = f0_curve(notes, clock, start_s, total, frame_s)

    meta = {i.name: i for i in voice.acoustic.get_inputs()}
    avail = set(meta)

    def shaped(name, value, dtype):
        """Match the rank the model declares.

        `depth` and `speedup` are declared with shape [] -- true scalars, not
        one-element vectors. Feeding shape (1,) fails at run time with a shape
        mismatch that names the tensor but not the fix.
        """
        rank = len(meta[name].shape)
        return np.array(value, dtype=dtype) if rank == 0 else \
            np.array([value], dtype=dtype)
    feed = {
        "tokens": np.array([tokens], dtype=np.int64),
        "durations": np.array([durations], dtype=np.int64),
        "f0": f0[None, :],
    }
    speedup = max(1, 1000 // max(steps, 1))
    while 1000 % speedup and speedup > 1:
        speedup -= 1
    if "speedup" in avail:
        feed["speedup"] = shaped("speedup", speedup, np.int64)
    if "steps" in avail:
        feed["steps"] = shaped("steps", steps, np.int64)
    if "depth" in avail:
        # Shallow diffusion: depth is where denoising starts, capped by the
        # bank's max_depth in whichever unit that bank states it.
        if "float" in meta["depth"].type:
            # A `use_continuous_acceleration` export takes depth as a fraction
            # of the schedule and states its own ceiling: CANARY and TRITON are
            # exported with `max_depth: 0.6` and were never trained to denoise
            # from further back than that. The integer export below states the
            # same ceiling as a step count, which is why the two branches read
            # `max_depth` so differently.
            cap = float(voice.cfg.get("max_depth", 1.0))
            feed["depth"] = shaped("depth", min(depth, cap if 0 < cap <= 1.0 else 1.0),
                                   np.float32)
        else:
            cap = int(voice.cfg.get("max_depth", 1000))
            d = min(int(depth * 1000) if depth <= 1.0 else int(depth), cap)
            feed["depth"] = shaped("depth", max(speedup, d // speedup * speedup),
                                   np.int64)
    # The variance parameters are log-domain, roughly dB, where 0 is unity and
    # -96 is silence. A flat 0 therefore means "full" everywhere, which is why
    # a bank rendered without `dsvariance` sounds even rather than silent.
    # `--variance` stays an offset on top of whatever the model says, matching
    # how OpenUtau applies a user's curves.
    curves = {}
    if predictors and predictors.variance is not None:
        wanted = [n for n in ("energy", "breathiness", "voicing", "tension")
                  if n in avail]
        if not wanted:
            # A bank can ship dsvariance and an acoustic model that asks for
            # none of it. Nothing is lost and nothing should be warned about.
            predictors.used.add("dsvariance")
            predictors.not_needed.add("dsvariance")
        else:
            got = predictors.variance.predict(phones, ph_seconds, written,
                                              total, steps)
            if got:
                curves = {k: v for k, v in got.items() if k in wanted}
                if curves:
                    predictors.used.add("dsvariance")
    for name in ("energy", "breathiness", "voicing", "tension"):
        if name in avail:
            base = curves.get(name)
            feed[name] = (np.full((1, total), variance[name], dtype=np.float32)
                          if base is None else
                          (np.asarray(base, dtype=np.float32)[None, :total]
                           + variance[name]))
    if "velocity" in avail:
        feed["velocity"] = np.ones((1, total), dtype=np.float32)
    if "gender" in avail:
        feed["gender"] = np.zeros((1, total), dtype=np.float32)
    if "languages" in avail:
        feed["languages"] = np.zeros((1, len(tokens)), dtype=np.int64)
    if "spk_embed" in avail:
        _name, emb = voice.speaker_embedding(voice_mode)
        if emb is None:
            die("this acoustic model wants spk_embed but dsconfig.yaml lists no "
                "speakers -- the bank is packaged inconsistently.")
        # One fixed voice mode held across the phrase. OpenUtau varies this per
        # frame to crossfade between modes; a score has nowhere to say that.
        feed["spk_embed"] = np.tile(emb, (1, total, 1)).astype(np.float32)

    missing = avail - set(feed)
    if missing:
        die(f"this acoustic model wants inputs this script does not supply: "
            f"{', '.join(sorted(missing))}. Try a different bank, or render it "
            f"in OpenUtau.")
    feed = {k: v for k, v in feed.items() if k in avail}

    mel = voice.acoustic.run(None, feed)[0]
    vbase = str(voice.vocoder_cfg.get("mel_base", voice.mel_base))
    if vbase != voice.mel_base:
        mel = mel * (2.30259 if vbase == "e" else 0.434294)

    vin = {i.name for i in voice.vocoder.get_inputs()}
    vfeed = {"mel": mel.astype(np.float32)}
    if "f0" in vin:
        vfeed["f0"] = f0[None, :]
    wave_out = voice.vocoder.run(None, {k: v for k, v in vfeed.items() if k in vin})[0]
    return np.asarray(wave_out, dtype=np.float32).reshape(-1), start_s


def preview_phrase(voice, timeline, notes, clock):
    """The same phoneme timeline and pitch curve, through the built-in voice."""
    frame_s = voice.frame_s
    start_s = timeline[0][1]
    frames = int(round((timeline[-1][2] - start_s) / frame_s)) + 1
    f0 = f0_curve(notes, clock, start_s, frames, frame_s)
    return voice.pv.render(timeline, f0, frame_s), start_s


def write_wav(path, samples, rate):
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

VARIANCE_PARAMETERS = ("energy", "breathiness", "voicing", "tension")


def parse_variance(spec):
    """`--variance E,B,V,T` to {parameter: dB offset}.

    All four or none: `zip` used to truncate silently against a short list, so
    `--variance 0,0` built a dict of two and the render died later on a
    KeyError naming a tensor rather than the flag.
    """
    try:
        numbers = [float(x) for x in spec.split(",")]
    except ValueError:
        numbers = []
    if len(numbers) != len(VARIANCE_PARAMETERS):
        die("--variance wants four numbers -- energy,breathiness,voicing,"
            f"tension -- e.g. 0,0,0,0 (got {spec!r})")
    return dict(zip(VARIANCE_PARAMETERS, numbers))


def load_vocals(source, outdir):
    src = Path(source)
    if src.suffix == ".json":
        return json.loads(src.read_text())
    print("  extracting the vocal line...")
    subprocess.run([sys.executable, str(HERE / "vocal_score.py"),
                    str(src), "-o", str(outdir)], check=True)
    js = outdir / f"{src.stem}-vocals.json"
    if not js.exists():
        die("vocal_score.py produced no vocals JSON")
    return json.loads(js.read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="score.ly or <stem>-vocals.json")
    ap.add_argument("--voice", help="DiffSinger voicebank directory")
    ap.add_argument("--inspect", action="store_true",
                    help="print the bank's layout and every model's ONNX "
                         "interface, then exit")
    ap.add_argument("--preview", action="store_true",
                    help="use the built-in formant voice instead of a voicebank: "
                         "robotic, instant, and enough to check the alignment")
    ap.add_argument("--phonemizer", metavar="PATH",
                    help="the bank's OpenUtau phonemizer plugin (.dll) or a "
                         "folder holding one; found automatically if it sits "
                         "near the bank")
    ap.add_argument("--vocoder", help="vocoder package directory (nsf_hifigan)")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--line", type=int, default=1, help="which lyric line to sing")
    ap.add_argument("--voice-mode", metavar="NAME",
                    help="for multi-speaker banks: which voice mode to sing in")
    ap.add_argument("--steps", type=int, default=20,
                    help="diffusion steps: more is slower and smoother")
    ap.add_argument("--depth", type=float, default=1.0, metavar="D",
                    help="shallow-diffusion depth 0-1, where the model exposes "
                         "it: lower starts denoising closer to the answer")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--no-vibrato", action="store_true")
    ap.add_argument("--literal-timing", action="store_true",
                    help="ignore the bank's dsdur model and split syllables "
                         "with the built-in constants instead")
    ap.add_argument("--literal-pitch", action="store_true",
                    help="ignore the bank's dspitch model and follow the "
                         "written notes, with synthetic portamento and vibrato "
                         "-- what a score-following video wants")
    ap.add_argument("--expressiveness", type=float, default=1.0, metavar="X",
                    help="how far dspitch may depart from the written notes, "
                         "0 to 1 (0 reproduces them exactly)")
    ap.add_argument("--variance", default="0,0,0,0", metavar="E,B,V,T",
                    help="energy,breathiness,voicing,tension in dB, added on "
                         "top of dsvariance where a bank has one and used flat "
                         "where it does not (0 is unity, -96 is silence)")
    args = ap.parse_args()

    if args.inspect:
        if not args.voice:
            die("--inspect needs --voice /path/to/voicebank")
        inspect_bank(args.voice, args.vocoder)
        return
    if not args.source:
        die("give me a score.ly or a <stem>-vocals.json (or use --inspect)")

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    doc = load_vocals(args.source, outdir)
    lines = {l["line"]: l for l in doc["lines"]}
    if args.line not in lines:
        die(f"no lyric line {args.line} (found: {sorted(lines)})")
    line = lines[args.line]
    clock = Clock(doc.get("tempo_map"), doc.get("tempo"))

    if args.preview:
        voice = PreviewVoice()
        print("  voice   built-in formant preview (no voicebank): "
              "timing and pitch are real, the timbre is not")
    elif args.voice:
        wanted = tuple(k for k, off in (("duration", args.literal_timing),
                                        ("pitch", args.literal_pitch),
                                        ("variance", False)) if not off)
        voice = Voice(args.voice, args.vocoder, args.phonemizer,
                      args.voice_mode, wanted)
        print(f"  voice   {voice.dir.name}: {len(voice.phonemes)} phonemes, "
              f"{len(voice.entries)} dictionary entries, {voice.sample_rate} Hz")
        if voice.speakers:
            chosen = args.voice_mode or voice.speakers[0]
            print(f"  modes   {', '.join(Path(s).name for s in voice.speakers)}"
                  f"  -> singing as {Path(chosen).name}")
        if voice.plugin_path is None:
            print(f"  words   no phonemizer plugin matches this bank: "
                  f"{len(voice.entries)} dsdict words, then letter-to-sound rules")
        else:
            agreed = voice.plugin_agreement
            import phonemizer as ph_mod
            enough = agreed and agreed[1] >= ph_mod.AGREEMENT_MINIMUM
            print(f"  words   {voice.plugin_path.name}"
                  + (f", agreeing with the bank's own dictionary on "
                     f"{100 * agreed[0] / agreed[1]:.0f}% of {agreed[1]} words"
                     if enough else ", too few words in common with the bank's "
                     "dictionary to check it against"))
        for name in getattr(voice, "unreadable_dicts", []):
            print(f"  ! {name} is not valid yaml and was skipped")
        for note in voice.predictors.notes:
            print(f"  ! {note}")
        print(f"  models  acoustic + vocoder, plus {voice.predictors.summary()}")
    else:
        die("pass --voice /path/to/voicebank, or --preview to hear the line "
            "through the built-in formant voice")

    variance = parse_variance(args.variance)

    warn = set()
    phrases = phrase_split(line["notes"], clock)
    total_s = max(seconds(n["when"] + n["dur"], clock) for n in line["notes"]) + 1.0
    track = np.zeros(int(total_s * voice.sample_rate) + voice.sample_rate, dtype=np.float32)

    for i, phrase in enumerate(phrases, 1):
        timeline, notes = phonemize(voice, phrase, clock, warn)
        timeline = fill_silences(voice, timeline)
        if args.preview:
            audio, start_s = preview_phrase(voice, timeline, notes, clock)
        else:
            audio, start_s = render_phrase(voice, timeline, notes, clock,
                                           args.steps, variance, args.depth,
                                           args.voice_mode, args.literal_pitch,
                                           args.expressiveness)
        # The head padding and the first consonant can begin before beat 0.
        # Clamping the position would slide the whole phrase late; trim instead.
        at = int(start_s * voice.sample_rate)
        if at < 0:
            audio, at = audio[-at:], 0
        end = min(len(track), at + len(audio))
        track[at:end] += audio[:end - at] * args.gain
        print(f"  phrase {i}/{len(phrases)}: {len(timeline)} phonemes, "
              f"{len(audio) / voice.sample_rate:.1f}s at {start_s:.1f}s")

    # Say what actually ran. A bank without dspitch is common and fine; a run
    # that quietly sounded worse because a folder was missing or a model
    # declined is not, and it is invisible from the audio alone.
    predictors = getattr(voice, "predictors", None)
    if predictors is not None:
        for folder, model, flag, what in (
                ("dsdur", predictors.duration, args.literal_timing,
                 "phoneme durations from the constant table"),
                ("dspitch", predictors.pitch, args.literal_pitch,
                 "written pitch with synthetic portamento and vibrato"),
                ("dsvariance", predictors.variance, False,
                 "flat variance inputs")):
            if model is not None and folder in predictors.used:
                if folder in predictors.not_needed:
                    print(f"  {folder} not used: this acoustic model asks for "
                          "no variance inputs")
                continue
            if flag:
                print(f"  {folder} disabled by flag: {what}")
            elif model is not None:
                print(f"  ! {folder} is present but declined this line: {what}")
                if model.declined:
                    print(f"    {model.declined}")
        for name, phones in predictors.unknown_phonemes().items():
            print(f"  ! {name} has no token for {', '.join(phones)}; "
                  "sung as silence in that model's view of the line")

    plugged = getattr(voice, "from_plugin", set())
    guessed = getattr(voice, "from_g2p", set())
    if plugged:
        print(f"  {len(plugged)} word(s) from the phonemizer plugin's dictionary")
    if guessed:
        print(f"  {len(guessed)} word(s) from the plugin's neural G2P: "
              f"{', '.join(sorted(guessed)[:8])}"
              f"{' ...' if len(guessed) > 8 else ''}")
    spelled = getattr(voice, "spelled_out", set())
    if spelled:
        print(f"  ! spelled out by rule, pronunciation is a guess: "
              f"{', '.join(sorted(spelled))}")
    if warn:
        print(f"  ! no pronunciation at all, sung as a placeholder: "
              f"{', '.join(sorted(warn))}")
        print("    respell them in \\lyricmode, or add them to a copy of the "
              "bank's dsdict yaml.")

    peak = float(np.max(np.abs(track))) or 1.0
    if peak > 1.0:
        track /= peak
    suffix = "-vocal-preview.wav" if args.preview else "-vocal.wav"
    out = outdir / f"{Path(doc['score']).stem}{suffix}"
    write_wav(out, track, voice.sample_rate)
    print(f"  -> {out.name}  ({len(track) / voice.sample_rate:.1f}s)")
    print(f"\n  mix it into the score:\n"
          f"    python3 scripts/render.py {doc['score']} --vocal {out}")


if __name__ == "__main__":
    cli(main, "sing.py")
