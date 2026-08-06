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
A DiffSinger bank is a directory holding `dsconfig.yaml`, an `acoustic.onnx`,
a `phonemes.txt` (one phoneme per line; the line number is its token id), a
`dsdict*.yaml` grapheme-to-phoneme dictionary, and a separate vocoder package
(usually nsf_hifigan) with its own config and ONNX. Banks are made for
OpenUtau; this script drives the same files directly so nothing here needs a
GUI. Point `--voice` at the bank directory and `--vocoder` at the vocoder
directory (or drop the vocoder inside the bank as `vocoder/`).

Almost every English bank is licensed for non-commercial use, and several
forbid redistribution or synthesis of real people. Read the bank's terms; this
script deliberately does not bundle or download one.

WHAT THIS DOES AND DOES NOT MODEL
---------------------------------
It runs the two models every bank ships -- the acoustic model and the vocoder
-- and supplies phoneme durations and the pitch curve itself, rather than
calling the optional `dsdur`/`dspitch`/`dsvariance` predictors that only some
banks include. That is the deliberate trade. Written durations and written
pitch are exactly what a score already specifies, and a predictor asked to
guess them from the notes would be guessing at something we know. What is lost
is the learned *deviation*: the human tendency to scoop into a note, to shorten
an unstressed vowel, to breathe. Some of that is put back synthetically here
(portamento, vibrato on held notes, breaths in the rests); the rest is the
honest difference between this and a bank driven from OpenUtau by hand.

If the bank's acoustic model demands variance inputs (energy, breathiness,
voicing, tension) they are supplied flat, which sounds slightly more even than
the same bank in OpenUtau. `--variance` overrides the levels.
"""

import argparse
import json
import math
import random
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

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
    sys.exit(f"sing.py: {msg}")


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        die("pyyaml is missing -- pip install pyyaml (or rerun scripts/setup.sh)")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------
# the voicebank
# --------------------------------------------------------------------------

class Voice:
    """A DiffSinger bank: config, phoneme table, dictionary, ONNX sessions."""

    def __init__(self, path, vocoder=None):
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

        # A vocoder trained on a different mel definition does not merely sound
        # worse, it produces noise; the numbers must match exactly.
        for key in ("sample_rate", "hop_size", "num_mel_bins", "mel_fmin", "mel_fmax"):
            a, b = self.cfg.get(key), self.vocoder_cfg.get(key)
            if a is not None and b is not None and abs(float(a) - float(b)) > 1e-5:
                die(f"acoustic model and vocoder disagree on {key} ({a} != {b}). "
                    "This bank needs its matching vocoder package.")

    def _load_phonemes(self, path):
        if not path.exists():
            die(f"phoneme list {path} not found")
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        lines = path.read_text(encoding="utf-8").splitlines()
        return {p.strip(): i for i, p in enumerate(lines) if p.strip()}

    def _load_dict(self):
        """Read dsdict*.yaml: phoneme types plus the word -> phonemes table."""
        cands = sorted(self.dir.glob("dsdict*.yaml"))
        preferred = [p for p in cands if p.name in ("dsdict-en.yaml", "dsdict.yaml")]
        for path in (preferred + cands):
            data = load_yaml(path) or {}
            entries = {}
            for e in data.get("entries", []) or []:
                g = str(e.get("grapheme", "")).lower()
                ph = e.get("phonemes") or e.get("phones") or []
                if g and ph:
                    entries.setdefault(g, [str(x) for x in ph])
            symbols = {str(s["symbol"]): str(s.get("type", "")).lower()
                       for s in (data.get("symbols") or []) if s.get("symbol")}
            if entries:
                self.dict_path = path
                return symbols, entries
        die("no usable dsdict*.yaml in the bank. English banks ship one; "
            "without it there is no way to turn words into phonemes.")

    def _find_vocoder(self):
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
        model = cfg.get("model")
        path = (vdir / model) if model else None
        if path is None or not path.exists():
            onnxs = list(vdir.glob("*.onnx"))
            if not onnxs:
                die(f"no .onnx in the vocoder directory {vdir}")
            path = onnxs[0]
        return cfg, onnxruntime.InferenceSession(
            str(path), opts, providers=["CPUExecutionProvider"])

    def speaker_embedding(self, name=None):
        """Load a voice mode's `.emb`: a raw little-endian float32 vector."""
        if not self.speakers:
            return None, None
        chosen = name or self.speakers[0]
        if chosen not in self.speakers:
            die(f"voice mode {chosen!r} not in this bank. Available: "
                f"{', '.join(self.speakers)}")
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

    for d in sorted(bank.glob("dsdict*.yaml")):
        data = load_yaml(d) or {}
        types = {}
        for s in (data.get("symbols") or []):
            types[str(s.get("type", "?"))] = types.get(str(s.get("type", "?")), 0) + 1
        print(f"\n{d.name}: {len(data.get('entries') or [])} entries, "
              f"{len(data.get('symbols') or [])} symbols "
              f"({', '.join(f'{k} {v}' for k, v in sorted(types.items()))})")
        if data.get("replacements"):
            print(f"  replacements: {len(data['replacements'])}")

    ph = bank / str((load_yaml(cfg_path) or {}).get("phonemes", "phonemes.txt")) \
        if cfg_path.exists() else None
    if ph and ph.exists():
        lines = [l for l in ph.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"\n{ph.name}: {len(lines)} phonemes, first 12: {' '.join(lines[:12])}")

    for emb in sorted(bank.glob("*.emb")):
        print(f"  {emb.name}: {np.fromfile(emb, dtype='<f4').size} floats")

    opts = onnxruntime.SessionOptions()
    opts.log_severity_level = 3
    opts.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL

    dirs = [bank] + [d for d in sorted(bank.iterdir()) if d.is_dir()]
    if vocoder:
        dirs.append(Path(vocoder).expanduser().resolve())
    for d in dirs:
        for sub in sorted(d.glob("*.yaml")):
            if d != bank:
                data = load_yaml(sub) or {}
                print(f"\n{d.name}/{sub.name}")
                for k in sorted(data):
                    print(f"  {k}: {data[k]}")
        for onnx_file in sorted(d.glob("*.onnx")):
            rel = onnx_file.relative_to(bank) if bank in onnx_file.parents else onnx_file.name
            mb = onnx_file.stat().st_size / 1e6
            print(f"\n=== {rel}  ({mb:.1f} MB)")
            try:
                s = onnxruntime.InferenceSession(str(onnx_file), opts,
                                                 providers=["CPUExecutionProvider"])
            except Exception as e:
                print(f"  ! could not load: {str(e)[:200]}")
                continue
            for i in s.get_inputs():
                print(f"  IN   {i.name:<16} {i.type:<16} {i.shape}")
            for o in s.get_outputs():
                print(f"  OUT  {o.name:<16} {o.type:<16} {o.shape}")

    print("\nPaste this whole output when asking for the duration, pitch and "
          "variance models to be wired up.")


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

def seconds(whole, tempo):
    """Whole notes to seconds. A whole note is four quarters at `tempo` bpm."""
    return whole * 4.0 * 60.0 / tempo


def phrase_split(notes, tempo, gap=0.6):
    """Break the line at rests long enough to breathe in."""
    phrases, current = [], []
    for n in notes:
        if current:
            prev = current[-1]
            rest = seconds(n["when"] - (prev["when"] + prev["dur"]), tempo)
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


def phonemize(voice, phrase, tempo, warn):
    """Turn one phrase into [(phoneme, start_s, end_s)], vowels on the beat."""
    notes = sung_notes(phrase)
    t0 = seconds(notes[0]["when"], tempo)

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

    timeline = []
    for word in words:
        text = word[0]["word"] or ""
        phon = lookup(voice, text) if text else None
        if text and phon is None:
            warn.add(text)
            phon = [voice.symbols and next(iter(voice.symbols)) or "a"]
        parts = syllabify(voice, phon or [], len(word)) if phon else [[] for _ in word]

        for group, phones in zip(word, parts):
            start = seconds(group["notes"][0]["when"], tempo)
            end = seconds(group["notes"][-1]["when"] + group["notes"][-1]["dur"], tempo)
            vowel_at = next((i for i, p in enumerate(phones) if voice.is_vowel(p)), None)
            if vowel_at is None:
                onset, rest = phones, []
            else:
                onset, rest = phones[:vowel_at], phones[vowel_at:]

            # Onset consonants sit before the beat, taking time from whatever
            # precedes them -- the previous phoneme, or the head padding.
            lead = sum(voice.consonant_len(p) for p in onset)
            room = start - (timeline[-1][1] if timeline else t0 - 0.5)
            scale = min(1.0, room / lead) if lead > 0 and room > 0 else (0 if lead else 1)
            cursor = start - lead * scale
            if timeline and cursor < timeline[-1][2]:
                timeline[-1] = (timeline[-1][0], timeline[-1][1], cursor)
            for p in onset:
                d = max(voice.consonant_len(p) * scale, MIN_PHONEME_S)
                timeline.append((p, cursor, cursor + d))
                cursor += d

            # The vowel holds the note. A final consonant cluster is taken off
            # the end of the last note of the syllable.
            coda = [p for p in rest[1:] if not voice.is_vowel(p)]
            tail = sum(voice.consonant_len(p) for p in coda)
            tail = min(tail, max(0.0, (end - start) * 0.4))
            vowel_end = end - tail
            if rest:
                timeline.append((rest[0], start, max(start + 0.02, vowel_end)))
                cursor = max(start + 0.02, vowel_end)
                for p in rest[1:]:
                    d = tail / max(len(coda), 1) if p in coda else 0.03
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


def f0_curve(notes, tempo, start_s, frames, frame_s, vibrato=True, seed=0):
    """A singer's pitch line through the phrase's notes.

    A flat f0 per note is what makes synthetic singing sound synthetic, so
    three things are added: portamento across note changes (fast for small
    intervals, slower for leaps), vibrato that fades in on notes long enough to
    hold, and a slow random drift of a few cents.
    """
    rng = random.Random(seed)
    t = start_s + np.arange(frames) * frame_s
    pitch = np.zeros(frames)

    spans = [(seconds(n["when"], tempo), seconds(n["when"] + n["dur"], tempo),
              float(n["pitch"])) for n in notes]
    for i, (a, b, p) in enumerate(spans):
        pitch[(t >= a) & (t < b)] = p
    pitch[t < spans[0][0]] = spans[0][2]
    pitch[t >= spans[-1][1]] = spans[-1][2]

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

def render_phrase(voice, timeline, notes, tempo, steps, variance, depth=1.0,
                  voice_mode=None):
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

    f0 = f0_curve(notes, tempo, start_s, total, frame_s)

    avail = {i.name for i in voice.acoustic.get_inputs()}
    feed = {
        "tokens": np.array([tokens], dtype=np.int64),
        "durations": np.array([durations], dtype=np.int64),
        "f0": f0[None, :],
    }
    if "speedup" in avail:
        speedup = max(1, 1000 // max(steps, 1))
        while 1000 % speedup and speedup > 1:
            speedup -= 1
        feed["speedup"] = np.array([speedup], dtype=np.int64)
    if "steps" in avail:
        feed["steps"] = np.array([steps], dtype=np.int64)
    if "depth" in avail:
        kind = next(i for i in voice.acoustic.get_inputs() if i.name == "depth")
        feed["depth"] = (np.array([depth], dtype=np.float32)
                         if "float" in kind.type else
                         np.array([int(depth * 1000)], dtype=np.int64))
    for name, level in (("energy", variance["energy"]),
                        ("breathiness", variance["breathiness"]),
                        ("voicing", variance["voicing"]),
                        ("tension", variance["tension"])):
        if name in avail:
            feed[name] = np.full((1, total), level, dtype=np.float32)
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


def preview_phrase(voice, timeline, notes, tempo):
    """The same phoneme timeline and pitch curve, through the built-in voice."""
    frame_s = voice.frame_s
    start_s = timeline[0][1]
    frames = int(round((timeline[-1][2] - start_s) / frame_s)) + 1
    f0 = f0_curve(notes, tempo, start_s, frames, frame_s)
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
    ap.add_argument("--vocoder", help="vocoder package directory (nsf_hifigan)")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--line", type=int, default=1, help="which lyric line to sing")
    ap.add_argument("--voice-mode", metavar="NAME",
                    help="for multi-speaker banks: which voice mode to sing in")
    ap.add_argument("--steps", type=int, default=20,
                    help="diffusion steps: more is slower and smoother")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--no-vibrato", action="store_true")
    ap.add_argument("--variance", default="0,0,0,0", metavar="E,B,V,T",
                    help="flat energy,breathiness,voicing,tension levels")
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
    tempo = doc["tempo"]

    if args.preview:
        voice = PreviewVoice()
        print("  voice   built-in formant preview (no voicebank): "
              "timing and pitch are real, the timbre is not")
    elif args.voice:
        voice = Voice(args.voice, args.vocoder)
        print(f"  voice   {voice.dir.name}: {len(voice.phonemes)} phonemes, "
              f"{len(voice.entries)} dictionary entries, {voice.sample_rate} Hz")
        if voice.speakers:
            chosen = args.voice_mode or voice.speakers[0]
            print(f"  modes   {', '.join(voice.speakers)}  -> singing as {chosen}")
    else:
        die("pass --voice /path/to/voicebank, or --preview to hear the line "
            "through the built-in formant voice")

    try:
        variance = dict(zip(("energy", "breathiness", "voicing", "tension"),
                            [float(x) for x in args.variance.split(",")]))
    except ValueError:
        die("--variance wants four numbers, e.g. 0,0,0,0")

    warn = set()
    phrases = phrase_split(line["notes"], tempo)
    total_s = max(seconds(n["when"] + n["dur"], tempo) for n in line["notes"]) + 1.0
    track = np.zeros(int(total_s * voice.sample_rate) + voice.sample_rate, dtype=np.float32)

    for i, phrase in enumerate(phrases, 1):
        timeline, notes = phonemize(voice, phrase, tempo, warn)
        timeline = fill_silences(voice, timeline)
        if args.preview:
            audio, start_s = preview_phrase(voice, timeline, notes, tempo)
        else:
            audio, start_s = render_phrase(voice, timeline, notes, tempo,
                                           args.steps, variance,
                                           voice_mode=args.voice_mode)
        # The head padding and the first consonant can begin before beat 0.
        # Clamping the position would slide the whole phrase late; trim instead.
        at = int(start_s * voice.sample_rate)
        if at < 0:
            audio, at = audio[-at:], 0
        end = min(len(track), at + len(audio))
        track[at:end] += audio[:end - at] * args.gain
        print(f"  phrase {i}/{len(phrases)}: {len(timeline)} phonemes, "
              f"{len(audio) / voice.sample_rate:.1f}s at {start_s:.1f}s")

    if warn:
        print(f"  ! not in the bank's dictionary, sung as a placeholder: "
              f"{', '.join(sorted(warn))}")
        print("    add them to a copy of the bank's dsdict yaml, or respell them "
              "in \\lyricmode.")

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
    main()
