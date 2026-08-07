#!/usr/bin/env python3
"""Qualify one DiffSinger voicebank against this pipeline, with measurements.

    python3 scripts/dev/bank_check.py ~/voices/canary
    python3 scripts/dev/bank_check.py ~/voices/mybank --vocoder ~/voices/pc_nsf_hifigan

`selftest.py --voice` asks whether the *pipeline* still works with a bank in
place. This asks the other question -- whether a particular bank works -- and it
is the first thing to run on an unfamiliar one, because the ways a bank goes
wrong are mostly silent. It renders `dev/bank-check.ly` twice, once as the bank
would normally be sung and once with `--literal-pitch`, and reports:

  declares    the acoustic model's inputs, the predictor folders, the voice
              modes, the mel parameters, and which vocoder was paired with it
  uses        which of those the pipeline actually fed, and the reason for any
              model it found and then declined
  words       whether a phonemizer plugin was found that agrees with the bank's
              own dictionary -- the difference between a bank that can spell
              English and one that is guessing
  sounds      peak and RMS, and NaN or clipped samples, which is what a mel
              mismatch looks like from the outside
  sings       how far the rendered f0 sits from the written notes, per note.
              With `--literal-pitch` this is the pipeline's own accuracy and
              should be a few cents; without it, the difference is the singer.
  places      whether every vowel still starts on its written note onset

Pitch measurement needs `librosa` (`bash scripts/setup-singing.sh --dev`);
without it every other section still runs.

Exit status is 0 if the bank rendered and nothing measured was out of bounds.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from errors import cli                                     # noqa: E402

# A note whose body sits further than this from the written pitch is not
# expressive, it is wrong: a semitone of scoop would be an unusual singer, and
# `--literal-pitch` has no business departing from the score at all.
EXPRESSIVE_CENTS = 100
LITERAL_CENTS = 25

PROBLEMS = []


def note(ok, message):
    if not ok:
        PROBLEMS.append(message)
    return ok


def run(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


# ------------------------------------------------------------------ rendering

def sing(score, bank, vocoder, outdir, steps, extra=()):
    cmd = [sys.executable, SCRIPTS / "sing.py", score, "--voice", bank,
           "-o", outdir, "--steps", steps, *extra]
    if vocoder:
        cmd += ["--vocoder", vocoder]
    proc = run(cmd)
    wav = next(iter(sorted(Path(outdir).glob("*-vocal.wav"))), None)
    return proc, wav


def read_wav(path):
    with wave.open(str(path)) as w:
        sr, n, channels = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768.0
    return (raw.reshape(-1, channels).mean(axis=1) if channels > 1 else raw), sr


# --------------------------------------------------------------- measurements

def pitch_error(wav, vocals_json, line=1):
    """Per-note distance in cents between the render and the written pitch.

    Measured with `librosa.pyin` rather than `yin`: yin reports a pitch for
    every frame including unvoiced ones, and on a breathy consonant it will
    confidently return an octave error that looks exactly like a bank singing
    in the wrong octave. pyin's voiced flag removes that class of false alarm.

    Only the body of each note is looked at -- the first 30% and last 20% are
    dropped -- because the edges are where portamento and consonants live, and
    a bank is not wrong for scooping into a note.
    """
    try:
        import librosa
    except ImportError:
        return None
    audio, sr = read_wav(wav)
    doc = json.loads(Path(vocals_json).read_text())
    tempo = float(doc["tempo"])
    notes = doc["lines"][line - 1]["notes"]
    hop = 256
    f0, _voiced, _prob = librosa.pyin(audio, fmin=65, fmax=1000, sr=sr,
                                      hop_length=hop)
    t = np.arange(len(f0)) * hop / sr
    midi = 69 + 12 * np.log2(np.where(np.isfinite(f0), f0, np.nan) / 440.0)
    out = []
    for n in notes:
        start = n["when"] * 4 * 60.0 / tempo
        end = (n["when"] + n["dur"]) * 4 * 60.0 / tempo
        if end - start < 0.15:
            continue
        body = midi[(t >= start + 0.30 * (end - start))
                    & (t <= end - 0.20 * (end - start))]
        body = body[np.isfinite(body)]
        out.append((n["syllable"] or "~",
                    None if len(body) < 3
                    else round((float(np.median(body)) - n["pitch"]) * 100)))
    return out


def vowel_onsets(bank, vocoder, vocals_json):
    """Every vowel has to start when its note starts, whatever dsdur says.

    This is the invariant the timing side of a bank cannot be allowed to break,
    and it is invisible in the audio unless you already know the line.
    """
    import sing
    doc = json.loads(Path(vocals_json).read_text())
    line = doc["lines"][0]
    clock = sing.Clock(doc.get("tempo_map"), doc.get("tempo"))
    voice = sing.Voice(bank, vocoder, None, None, ("duration", "pitch", "variance"))
    late = []
    for phrase in sing.phrase_split(line["notes"], clock):
        timeline, notes = sing.phonemize(voice, phrase, clock, set())
        onsets = {round(sing.seconds(n["when"], clock), 6) for n in notes}
        late += [(p, round(a, 3)) for p, a, _b in timeline
                 if voice.is_vowel(p) and round(a, 6) not in onsets]
    return late


# ---------------------------------------------------------------------- report

def declared(bank, vocoder):
    """What the bank says about itself, in the four lines that matter."""
    import sing
    cfg = sing.load_yaml(Path(bank).expanduser() / "dsconfig.yaml") or {}
    voice = sing.Voice(bank, vocoder, None, None, ("duration", "pitch", "variance"))
    inputs = {i.name: i for i in voice.acoustic.get_inputs()}
    accel = "steps (continuous acceleration)" if "steps" in inputs else "speedup"
    depth = inputs.get("depth")
    print(f"  declares  {len(voice.phonemes)} phonemes, "
          f"{len(voice.entries)} dsdict words, {voice.sample_rate} Hz, "
          f"hop {voice.hop}, mel base {voice.mel_base}")
    print(f"            acoustic wants: {', '.join(sorted(inputs))}")
    print(f"            acceleration by {accel}"
          + (f", depth as {'a fraction' if 'float' in depth.type else 'a step count'}"
             f" capped at {cfg.get('max_depth', 'nothing')}" if depth is not None
             else ", no shallow diffusion"))
    print(f"            vocoder {Path(voice.vocoder_cfg.get('model', '?')).name}: "
          f"{voice.vocoder_cfg.get('num_mel_bins')} bins, "
          f"{voice.vocoder_cfg.get('sample_rate')} Hz, hop "
          f"{voice.vocoder_cfg.get('hop_size')}, mel base "
          f"{voice.vocoder_cfg.get('mel_base', voice.mel_base)}")
    if voice.speakers:
        print(f"            modes: {', '.join(Path(s).name for s in voice.speakers)}")
    return voice


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank")
    ap.add_argument("--vocoder", help="when the vocoder is not inside the bank")
    ap.add_argument("--steps", default="8",
                    help="diffusion steps; 8 is enough to qualify a bank")
    ap.add_argument("--score", default=str(HERE / "bank-check.ly"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    bank = Path(args.bank).expanduser()
    print(f"bank: {bank}")
    tmp = Path(tempfile.mkdtemp(prefix="bank-check-"))

    declared(bank, args.vocoder)

    print("\n  singing it")
    proc, wav = sing(args.score, bank, args.vocoder, tmp / "sung", args.steps)
    if not note(proc.returncode == 0 and wav, "the bank did not render"):
        print(proc.stdout[-1500:] or proc.stderr[-1500:])
        return finish(tmp, args.keep)
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("words", "models", "!")) or "declined" in line \
                or "declares inputs" in line:
            print(f"    {stripped}")
    note("declined this line" not in proc.stdout,
         "a model was present and declined -- see the reason above")
    note("spelled out by rule" not in proc.stdout,
         "words fell through to letter-to-sound rules: this bank has no "
         "phonemizer plugin the pipeline can use")

    audio, sr = read_wav(wav)
    peak, rms = float(np.abs(audio).max()), float(np.sqrt((audio ** 2).mean()))
    print(f"\n  sounds    {len(audio) / sr:.1f}s, peak "
          f"{20 * np.log10(max(peak, 1e-9)):.1f} dBFS, rms "
          f"{20 * np.log10(max(rms, 1e-9)):.1f} dBFS, "
          f"{int(np.isnan(audio).sum())} NaN samples")
    note(np.isfinite(audio).all(), "the render contains NaN samples")
    note(rms > 0.005, "the render is silent or nearly so")

    vocals = next(iter(sorted((tmp / "sung").glob("*-vocals.json"))))
    for label, extra, bound in (("as sung", (), EXPRESSIVE_CENTS),
                                ("--literal-pitch", ("--literal-pitch",),
                                 LITERAL_CENTS)):
        if extra:
            proc, wav = sing(vocals, bank, args.vocoder, tmp / "literal",
                             args.steps, extra)
            if not note(proc.returncode == 0 and wav,
                        "--literal-pitch did not render"):
                continue
        errs = pitch_error(wav, vocals)
        if errs is None:
            print("\n  sings     (install librosa to measure pitch)")
            break
        tracked = [abs(c) for _s, c in errs if c is not None]
        print(f"\n  sings     {label}: median {np.median(tracked):.0f} cents from "
              f"the written notes, worst {max(tracked):.0f}, "
              f"{len(tracked)}/{len(errs)} notes tracked")
        print("            " + "  ".join(f"{s}={c}" for s, c in errs))
        note(np.median(tracked) < bound,
             f"{label}: median {np.median(tracked):.0f} cents off the written "
             f"notes (bound {bound})")

    late = vowel_onsets(bank, args.vocoder, vocals)
    print(f"\n  places    {'every vowel starts on its written onset' if not late else late[:3]}")
    note(not late, "a vowel does not start on its note's onset")

    return finish(tmp, args.keep)


def finish(tmp, keep):
    print()
    if PROBLEMS:
        for p in PROBLEMS:
            print(f"  ! {p}")
        print(f"\n{len(PROBLEMS)} problem(s). "
              "A bank can still be worth using with some of these -- a missing "
              "plugin costs pronunciation, not correctness -- but nothing here "
              "is invisible once you know to look for it.")
    else:
        print("  no problems: this bank works with the pipeline as documented.")
    if keep:
        print(f"  files kept in {tmp}")
    else:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if PROBLEMS else 0)


if __name__ == "__main__":
    cli(main, "bank_check.py")
