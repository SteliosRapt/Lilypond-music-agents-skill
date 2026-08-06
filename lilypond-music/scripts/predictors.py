"""The optional DiffSinger predictors: `dsdur`, `dspitch`, `dsvariance`.

A bank ships more than an acoustic model. Beside it sit up to three small
models that supply the things a score does not state:

    dsdur/       how a syllable's time divides between its phonemes
    dspitch/     the singer's expressive deviation around the written notes
    dsvariance/  energy / breathiness / voicing / tension curves

Each lives in its own folder with its own `dsconfig.yaml`, its own phoneme
table, its own hop size and its own speaker embeddings, and each is a *pair* of
models: a linguistic encoder runs first and its output feeds the predictor.
Folders are self-contained -- a `linguistic.onnx` from one bank cannot be used
with a `pitch.onnx` from another, and the phoneme tables genuinely differ
(TIGER's dspitch table is 60 entries where its acoustic table is 68), so every
model is fed ids from the table in its own folder.

WHAT THE TENSORS MEAN
---------------------
The ONNX files declare names, shapes and dtypes but not units, and a wrong
guess there produces plausible audio with wrong timing rather than an error.
The conventions below were established by probing TIGER v102 directly, and each
is re-checkable with the same experiment:

`dur.onnx` -> `ph_dur_pred` is **in frames**, at the *dsdur* folder's hop size.
Doubling `word_dur` roughly doubles the vowel's share and leaves the consonants
almost unchanged (l: 14.9 -> 15.5 frames, ae: 37.2 -> 106.8), which is both the
proof that the units are frames and the reason the model is worth using: it
knows a consonant does not stretch with the note. The per-word sums come back
close to `word_dur` but not equal to it (57.8 against 60), so each word is
normalised onto the time the score actually gives it.

`pitch.onnx` -> `pitch_pred` is **in MIDI note numbers per frame**, not Hz.
`expr` is expressiveness in 0..1: at `expr=0` the output reproduces the input
`pitch` curve to a median 0.5 cents, at `expr=1` it deviates by a median 20
cents, which is the singer's own scooping and drift. `retake=False` likewise
returns the input curve unchanged (1.3 cents), so `retake` is "predict this
frame" and is set everywhere the pipeline wants a prediction.

**Predicted pitch inside a rest is meaningless.** With `note_rest` true the
model emits values around MIDI -2 -- roughly 4 Hz. Those frames are replaced
with the written curve rather than passed to the vocoder.

`variance.onnx` -> the four parameters are **inputs as well as outputs** in the
banks that ship one: you hand it the curves as they stand and `retake` says
which to re-predict, which is how OpenUtau keeps a hand-drawn energy curve while
re-rolling breathiness. Flat zeros mean "no curve drawn" -- 0 is unity in the
log domain, not silence.

WHAT THE FOLDERS DISAGREE ABOUT
-------------------------------
Nothing above is uniform across banks, and each difference below is one a bank
declared rather than one worth guessing at: the acceleration input is `speedup`
or `steps` depending on the export (see `_Model.acceleration`), the phoneme
table is line-numbered text or json with explicit ids (`_phoneme_table`), the
hop size is the folder's own, and the speaker list is the folder's own and need
not match the acoustic model's. A model that declares anything else is declined
with its name recorded, never fed a guess.

    from predictors import load_predictors
    pred = load_predictors(bank_dir, voice_mode="tiger_fresh")
    pred.duration.predict(words)          # -> seconds per phoneme, per word
    pred.pitch.predict(...)               # -> MIDI numbers per frame
"""

import json

import numpy as np

from pathlib import Path


def _load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def resample_curve(curve, frames):
    """Stretch a per-frame curve onto a different frame count.

    A predictor folder declares its own `hop_size`, and it is not promised to
    match the acoustic model's -- OpenUtau resamples for exactly this reason.
    TIGER happens to use 512 throughout, so this is a no-op there and must not
    be assumed away for other banks.
    """
    curve = np.asarray(curve, dtype=np.float32)
    if len(curve) == frames:
        return curve
    if len(curve) < 2:
        return np.full(frames, float(curve[0]) if len(curve) else 0.0, np.float32)
    src = np.linspace(0.0, 1.0, len(curve))
    dst = np.linspace(0.0, 1.0, frames)
    return np.interp(dst, src, curve).astype(np.float32)


class _Model:
    """One predictor folder: config, phoneme table, sessions, embeddings."""

    def __init__(self, folder, onnxruntime, opts, voice_mode=None):
        self.dir = Path(folder)
        self.name = self.dir.name
        self.cfg = _load_yaml(self.dir / "dsconfig.yaml")
        self.hop = int(self.cfg.get("hop_size", 512))
        self.sample_rate = int(self.cfg.get("sample_rate", 44100))
        self.frame_s = self.hop / self.sample_rate
        self.phonemes = self._phoneme_table()
        self._ort, self._opts = onnxruntime, opts
        self._sessions = {}
        self.speakers = [str(s) for s in (self.cfg.get("speakers") or [])]
        self.embedding = self._embedding(voice_mode)
        self.unknown = set()
        self.declined = None

    # -- loading ----------------------------------------------------------

    def _phoneme_table(self):
        """This folder's phoneme table, in either format banks use.

        Most banks write one phoneme per line and let the line number be the
        token id. The multi-language exports (LIEE's `mm_*.phonemes.json`) ship
        a json object with explicit ids instead, and those ids start at 1 --
        reading that file as lines makes every phone unknown, which is not an
        error anywhere: the model is simply handed a line of pure silence and
        returns a confident prediction about it.
        """
        rel = self.cfg.get("phonemes", "phonemes.txt")
        path = self.dir / str(rel)
        if not path.exists():
            raise FileNotFoundError(f"{self.name}: no phoneme table at {path}")
        if path.suffix.lower() == ".json":
            table = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(table, dict):
                return {str(k): int(v) for k, v in table.items()}
            return {str(p): i for i, p in enumerate(table)}
        lines = path.read_text(encoding="utf-8").splitlines()
        return {p.strip(): i for i, p in enumerate(lines) if p.strip()}

    def session(self, key):
        """Lazily open the ONNX file `dsconfig.yaml` lists under `key`."""
        if key not in self._sessions:
            rel = self.cfg.get(key)
            if not rel:
                raise KeyError(f"{self.name}/dsconfig.yaml has no '{key}' entry")
            self._sessions[key] = self._ort.InferenceSession(
                str(self.dir / str(rel)), self._opts,
                providers=["CPUExecutionProvider"])
        return self._sessions[key]

    def _embedding(self, voice_mode):
        """This folder's own speaker vector, chosen to match the acoustic mode.

        The folders do not agree on how many speakers they have -- TIGER's
        `dsdur` lists seven and its `dspitch` exactly one -- so the acoustic
        model's voice mode is matched by name where it exists and the first
        entry is used where it does not.
        """
        if not self.speakers:
            return None
        want = Path(str(voice_mode)).name if voice_mode else None
        chosen = next((s for s in self.speakers if Path(s).name == want),
                      self.speakers[0])
        path = self.dir / f"{chosen}.emb"
        if not path.exists():
            return None
        return np.fromfile(path, dtype="<f4").astype(np.float32)

    # -- helpers ----------------------------------------------------------

    def tokens(self, phones, silence="SP"):
        """Phoneme ids in *this* folder's table.

        The tables differ between folders in the same bank, so a phone that the
        acoustic model knows can be absent here. Those are counted and replaced
        with silence rather than aborting a render.
        """
        out = []
        for p in phones:
            tok = self.phonemes.get(p)
            if tok is None:
                tok = self.phonemes.get(p.lower(), self.phonemes.get(p.upper()))
            if tok is None:
                self.unknown.add(p)
                tok = self.phonemes.get(silence, 0)
            out.append(tok)
        return np.array([out], dtype=np.int64)

    def frames_of(self, seconds):
        """Durations in seconds -> whole frames at *this* folder's hop size.

        Rounded cumulatively rather than one at a time, so a run of short
        phonemes cannot drift away from the timeline it came from, and never
        below one frame -- a zero-length phoneme is still allocated a frame by
        the model, and reads as a click.
        """
        out, elapsed, emitted = [], 0.0, 0
        for s in seconds:
            elapsed += float(s)
            edge = int(round(elapsed / self.frame_s))
            out.append(max(1, edge - emitted))
            emitted = max(edge, emitted + 1)
        return np.array(out, dtype=np.int64)

    def spk(self, length):
        """`spk_embed` tiled to whatever axis this model wants it on."""
        if self.embedding is None:
            return None
        return np.tile(self.embedding, (1, length, 1)).astype(np.float32)

    def declared(self, key):
        return {i.name: i for i in self.session(key).get_inputs()}

    def acceleration(self, steps):
        """Both spellings of the acceleration input, for the model to pick from.

        Two export conventions are in circulation. The older one declares
        `speedup`; the `use_continuous_acceleration` export (CANARY v106,
        TRITON v106, LIEE MM 2.8) declares `steps`. Offering only one is not a
        graceful degradation -- an input the feed does not carry makes the whole
        call decline in `refuses()` below, which is how CANARY's dspitch went
        quietly unused while the run reported the folder as found.

        The same number goes into either, which is exact for `steps` and
        deliberately conservative for `speedup`: a stride of `steps` through the
        1000-step schedule is a finer prediction than the caller asked for, not
        a coarser one, and it is what these predictors have always been given.
        """
        n = np.array(max(1, int(steps)), np.int64)
        return {"speedup": n, "steps": n}

    def refuses(self, key, feed):
        """True if this model declares an input the caller cannot supply.

        Guessing at an unrecognised tensor produces confident nonsense rather
        than an error, so the model is declined instead -- but the reason is
        kept, because "present and not used" is worth a line of output and the
        name of the missing input is the whole diagnosis for the next bank.
        """
        missing = sorted(set(self.declared(key)) - set(feed))
        if missing:
            self.declined = (f"{self.name} declares inputs this script does not "
                             f"supply: {', '.join(missing)}")
        return bool(missing)

    def run(self, key, feed, *outputs):
        """Run a model and pick outputs by name rather than by position.

        Positional unpacking is what breaks first when a model has one more
        output than expected, and models do: a bank is free to expose
        intermediates the caller has no use for.
        """
        session = self.session(key)
        names = [o.name for o in session.get_outputs()]
        declared = {i.name: i for i in session.get_inputs()}
        got = session.run(None, {k: v for k, v in feed.items() if k in declared})
        table = dict(zip(names, got))
        return [table.get(name) for name in outputs] if outputs else got


class DurationPredictor(_Model):
    """`dsdur`: how a word's time divides between its phonemes.

    One bound is applied to the model rather than taken from it. A syllable
    held for five seconds is far outside anything a singer was recorded doing
    to a single word, and TIGER extrapolates by stretching the *consonants*
    with the note: `f l ey m` given a 5.7-second word comes back with 3.2
    seconds of `f`. Measured across word lengths, its consonant predictions
    stay flat and sensible up to about a second and a half and diverge past it:

        word_dur    f        l        ey       m
        0.46 s      0.116    0.047    0.343    0.116
        1.51 s      0.097*   0.071*   1.185*   0.157*      (* within a phrase)
        5.71 s      3.183    0.553    2.221    0.447

    So the model is asked about a syllable of ordinary length and the surplus
    is left to the vowel, which is where a held note's time actually goes.
    """

    KEYS = ("linguistic", "dur")
    MAX_WORD_S = 1.5

    def predict(self, words):
        """words = [{'phones': [...], 'seconds': float, 'midi': int}, ...]

        Returns a parallel list of per-phoneme durations in seconds, each word
        summing to the seconds it was given or to MAX_WORD_S, whichever is
        less, or None if the model declares an input this code does not know
        how to fill.
        """
        phones = [p for w in words for p in w["phones"]]
        if not phones:
            return None
        word_div = np.array([[len(w["phones"]) for w in words]], np.int64)
        # At least one frame per phoneme, or a word of two phonemes given one
        # frame comes back as a division by zero; and no more than MAX_WORD_S,
        # for the reason in the class docstring.
        ceiling = int(round(self.MAX_WORD_S / self.frame_s))
        word_dur = np.array([[min(max(int(round(w["seconds"] / self.frame_s)),
                                      len(w["phones"])), ceiling)
                              for w in words]], np.int64)
        tokens = self.tokens(phones)

        enc, masks = self.run("linguistic",
                              {"tokens": tokens, "word_div": word_div,
                               "word_dur": word_dur},
                              "encoder_out", "x_masks")

        feed = {"encoder_out": enc, "x_masks": masks,
                "ph_midi": np.array([[w["midi"] for w in words
                                      for _ in w["phones"]]], np.int64)}
        spk = self.spk(len(phones))
        if spk is not None:
            feed["spk_embed"] = spk
        if set(self.declared("dur")) - set(feed):
            return None
        pred = self.run("dur", feed, "ph_dur_pred")[0][0]

        out, at = [], 0
        for w, frames in zip(words, word_dur[0]):
            seg = np.maximum(pred[at:at + len(w["phones"])], 1e-6)
            at += len(w["phones"])
            # The raw prediction lands near the word's length but not on it, so
            # normalise: the score decides when the next syllable starts, the
            # model only decides how the time inside is shared out.
            out.append((seg / seg.sum() * float(frames) * self.frame_s).tolist())
        return out


class PitchPredictor(_Model):
    """`dspitch`: expressive f0 around the written notes."""

    KEYS = ("linguistic", "pitch")

    def predict(self, phones, ph_seconds, notes, base_midi, frames,
                expressiveness=1.0, steps=10):
        """Return a per-frame MIDI-number curve, or None if unsupported.

        Durations arrive in *seconds* and are converted to this folder's own
        frames here, so a `dspitch` built at a different hop size than the
        acoustic model still lines up; the curve is resampled back to `frames`
        acoustic frames on the way out.

        `notes` is [(midi_or_None, seconds), ...] covering the same span as the
        phonemes -- None meaning a rest. `base_midi` is the written pitch line,
        one value per acoustic frame; it is both the model's input and the
        fallback wherever the model has nothing to say.
        """
        if not phones or frames <= 0:
            return None
        ph_dur = self.frames_of(ph_seconds)
        n = int(ph_dur.sum())
        if n <= 0:
            return None
        note_dur = self._fit(self.frames_of(d for _m, d in notes), n)
        if note_dur is None:
            return None

        enc = self.run("linguistic", {"tokens": self.tokens(phones),
                                      "ph_dur": np.array([ph_dur], np.int64)},
                       "encoder_out")[0]

        base = resample_curve(base_midi, n)
        feed = {
            "encoder_out": enc,
            "ph_dur": np.array([ph_dur], np.int64),
            "note_midi": np.array([[float(m if m is not None else 0.0)
                                    for m, _d in notes]], np.float32),
            "note_rest": np.array([[m is None for m, _d in notes]], bool),
            "note_dur": np.array([note_dur], np.int64),
            "pitch": base[None, :],
            "expr": np.full((1, n), float(expressiveness), np.float32),
            "retake": np.ones((1, n), bool),
            **self.acceleration(steps),
        }
        spk = self.spk(n)
        if spk is not None:
            feed["spk_embed"] = spk
        if self.refuses("pitch", feed):
            return None
        declared = self.declared("pitch")
        for name, meta in declared.items():
            if not len(meta.shape):                  # a true scalar, not [1]
                feed[name] = np.array(feed[name]).reshape(())
        pred = self.run("pitch", feed, "pitch_pred")[0][0]

        # Inside a rest the prediction is not a pitch at all -- TIGER returns
        # about MIDI -2, which is 4 Hz -- so the written line is kept there and
        # eased back in over a few frames so the vocoder sees no step.
        rest = np.zeros(n, bool)
        at = 0
        for (midi, _d), d in zip(notes, note_dur):
            if midi is None:
                rest[at:at + d] = True
            at += d
        pred = np.where(rest, base, pred)
        pred = self._ease(pred, base, rest)
        return resample_curve(pred, frames)

    @staticmethod
    def _fit(counts, total):
        """Nudge a list of frame counts so it sums to `total` exactly."""
        counts = list(int(c) for c in counts)
        if not counts:
            return None
        diff = total - sum(counts)
        if diff:
            biggest = max(range(len(counts)), key=lambda i: counts[i])
            counts[biggest] += diff
            if counts[biggest] < 1:
                return None
        return counts

    @staticmethod
    def _ease(pred, base, rest, width=3):
        """Blend across every rest boundary so the swap leaves no step."""
        out = pred.copy()
        edges = np.flatnonzero(rest[1:] != rest[:-1])
        for e in edges:
            lo, hi = max(e - width, 0), min(e + width + 1, len(out))
            if hi - lo < 2:
                continue
            ramp = np.linspace(0.0, 1.0, hi - lo)
            toward_pred = not rest[min(hi - 1, len(rest) - 1)]
            a, b = (base[lo:hi], pred[lo:hi]) if toward_pred else (pred[lo:hi], base[lo:hi])
            out[lo:hi] = a + (b - a) * ramp
        return out


class VariancePredictor(_Model):
    """`dsvariance`: the energy / breathiness / voicing / tension curves.

    Implemented from the folder's declared interface rather than from a fixed
    tensor list, because no bank to hand ships one -- TIGER sets
    `use_energy_embed: false` and has no `dsvariance/` at all. Anything the
    model asks for that is not recognised makes the whole call return None, so
    an unknown variant falls back to flat inputs instead of being fed guesses.
    """

    KEYS = ("linguistic", "variance")
    PARAMETERS = ("energy", "breathiness", "voicing", "tension")

    def wanted(self):
        return [p for p in self.PARAMETERS
                if self.cfg.get(f"predict_{p}", False)]

    def predict(self, phones, ph_seconds, base_midi, frames, steps=10):
        """Return {parameter: per-acoustic-frame curve} or None."""
        if not phones or frames <= 0:
            return None
        ph_dur = self.frames_of(ph_seconds)
        n = int(ph_dur.sum())
        if n <= 0:
            return None
        enc = self.run("linguistic", {"tokens": self.tokens(phones),
                                      "ph_dur": np.array([ph_dur], np.int64)},
                       "encoder_out")[0]

        outputs = [o.name for o in self.session("variance").get_outputs()]
        # `retake` is one flag per parameter per frame, so its last axis is the
        # number of parameters this model predicts -- not the number of tensors
        # it happens to return, which can include intermediates.
        predicted = [o for o in outputs
                     if o.replace("_pred", "") in self.PARAMETERS]
        known = {
            "encoder_out": enc,
            "ph_dur": np.array([ph_dur], np.int64),
            "pitch": resample_curve(base_midi, n)[None, :],
            "expr": np.ones((1, n), np.float32),
            "retake": np.ones((1, n, max(len(predicted), 1)), bool),
            **self.acceleration(steps),
            # The parameters are inputs as well as outputs: the model is handed
            # the curves as they stand and asked to re-predict the ones `retake`
            # marks, which is how OpenUtau lets a user keep a hand-drawn energy
            # curve while re-rolling breathiness. Nothing here draws curves, so
            # they go in flat -- 0 in the log domain is unity, not silence --
            # and `retake` is true everywhere, meaning predict all of it.
            **{p: np.zeros((1, n), np.float32) for p in self.PARAMETERS},
        }
        spk = self.spk(n)
        if spk is not None:
            known["spk_embed"] = spk
        if self.refuses("variance", known):
            return None
        declared = self.declared("variance")
        for name, meta in declared.items():
            if not len(meta.shape):
                known[name] = np.array(known[name]).reshape(())
        got = self.run("variance", known)

        out = {}
        for name, curve in zip(outputs, got):
            key = name.replace("_pred", "")
            if key in self.PARAMETERS:
                out[key] = resample_curve(np.asarray(curve).reshape(-1), frames)
        return out or None


class Predictors:
    """Whichever of the three a bank actually ships."""

    def __init__(self, duration=None, pitch=None, variance=None, notes=()):
        self.duration, self.pitch, self.variance = duration, pitch, variance
        self.notes = list(notes)
        # Which of them a render actually ended up using. A model can be
        # present and still decline -- an unfamiliar input name, a phrase too
        # short to be worth it -- and "found" is not the same claim as "used".
        self.used = set()
        # Present, and correctly not used: an acoustic model that asks for no
        # variance inputs has nothing for dsvariance to supply.
        self.not_needed = set()

    def summary(self):
        have = [name for name, p in (("dsdur", self.duration),
                                     ("dspitch", self.pitch),
                                     ("dsvariance", self.variance)) if p]
        return ", ".join(have) if have else "none"

    def unknown_phonemes(self):
        """Phones a predictor's own table did not have, by folder."""
        out = {}
        for model in (self.duration, self.pitch, self.variance):
            if model is not None and model.unknown:
                out[model.name] = sorted(model.unknown)
        return out


def load_predictors(bank_dir, onnxruntime=None, opts=None, voice_mode=None,
                    want=("duration", "pitch", "variance")):
    """Find and open every predictor folder in a bank.

    A missing folder is the normal case, not an error: most banks ship one or
    two of the three. A folder that is present but unreadable is reported, so a
    run never silently sounds worse because a model failed to load.
    """
    if onnxruntime is None:
        import onnxruntime as ort
        onnxruntime = ort
    if opts is None:
        opts = onnxruntime.SessionOptions()
        opts.log_severity_level = 3

    bank = Path(bank_dir)
    kinds = (("duration", "dsdur", DurationPredictor),
             ("pitch", "dspitch", PitchPredictor),
             ("variance", "dsvariance", VariancePredictor))
    found, notes = {}, []
    for key, folder, cls in kinds:
        path = next((p for p in (bank / folder, bank / folder.replace("ds", "ds_"))
                     if (p / "dsconfig.yaml").exists()), None)
        if path is None or key not in want:
            continue
        try:
            model = cls(path, onnxruntime, opts, voice_mode)
            missing = [k for k in cls.KEYS if not model.cfg.get(k)]
            if missing:
                notes.append(f"{folder}/dsconfig.yaml lists no "
                             f"{', '.join(missing)} -- ignoring the folder")
                continue
            found[key] = model
        except Exception as exc:                      # noqa: BLE001 -- reported
            notes.append(f"{folder} could not be loaded: {str(exc)[:120]}")
    return Predictors(notes=notes, **found)
