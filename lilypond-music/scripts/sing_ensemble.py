#!/usr/bin/env python3
"""Sing every part of a score, one voicebank per part, and mix them.

    python3 scripts/sing_ensemble.py score.ly -o out/ \
        --voice soprano=~/voices/liee --voice alto=~/voices/canary \
        --voice tenor=~/voices/tiger  --voice bass=~/voices/triton

`sing.py` renders one line with one bank. Everything above that -- matching
parts to banks, rendering them without the four runs fighting over the CPU,
noticing that one of them quietly fell back to letter-to-sound rules, and
balancing four dry mono stems into something that sounds like a room with
people in it -- is this script, because doing it by hand is where the mistakes
live. It writes:

    out/stems/<part>.wav     each part dry, aligned to beat 0, ready for a DAW
    out/<stem>.mp3           the mix (`--format flac` or `wav` for lossless)

and prints one line per part saying which models the bank actually used and
where its pronunciations came from, which is the report worth reading before
listening to two minutes of anything.

WHAT THIS IS FOR
----------------
Unaccompanied writing: a cappella choral pieces, close-harmony groups, canons,
anything where the voices *are* the arrangement. For voices over instruments,
render the parts here and pass the mix to `render.py --vocal` instead, which
puts them on top of the fluidsynth performance.

THE THREE THINGS THAT GO WRONG
------------------------------
1. **A part sings one syllable early from bar N onwards.** Its syllable count
   and its note count disagree. `vocal_score.py` prints "N sung notes, M
   melismatic" per part -- a melisma you did not write is the tell. Prefer ties
   to `__` extenders: a tied note takes no syllable, so the count is just the
   untied notes.
2. **A part is fluent and wrong.** Its bank found a phonemizer plugin for
   another language, or none at all. The `words` line below says which, and
   `--phonemizer` overrides it.
3. **Held chords never settle.** Four singers each deviating a median 20 cents
   from the written pitch is four soloists. `--expressiveness 0.7` (the default
   here, against `sing.py`'s 1.0) keeps the scoops and lets the chords lock.
"""

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from errors import SkillError, cli                        # noqa: E402

HERE = Path(__file__).resolve().parent

# Balance and placement. The banks are not equally loud -- TIGER comes out
# about 5 dB under CANARY on the same line -- so every part is measured and
# pulled to one level first; `--gain` is then the musical decision on top.
TARGET_RMS_DB = -20.0
# Mutually prime delays in ms, so no two taps coincide and the tail does not
# flutter into a metallic ring. Offset left and right to widen the room.
TAPS_L, TAPS_R = "43|97|173|251", "61|113|191|277"
DECAYS = "0.35|0.26|0.19|0.13"


def die(msg):
    raise SkillError(msg)


def pairs(values, what, cast=str):
    """--flag part=value, repeated."""
    out = {}
    for item in values or []:
        if "=" not in item:
            die(f"--{what} wants part=value, got {item!r}")
        name, _, value = item.partition("=")
        try:
            out[name.strip()] = cast(value.strip())
        except ValueError:
            die(f"--{what} {item!r}: {value!r} is not a number")
    return out


# ------------------------------------------------------------------ rendering

def extract(score, outdir):
    """Run the extraction once, for every part, and return the vocals doc."""
    if str(score).endswith(".json"):
        return json.loads(Path(score).read_text()), Path(score)
    proc = subprocess.run(
        [sys.executable, HERE / "vocal_score.py", str(score), "-o", str(outdir)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"extraction failed:\n{proc.stdout[-1500:]}{proc.stderr[-1500:]}")
    print(proc.stdout.rstrip())
    found = sorted(Path(outdir).glob("*-vocals.json"))
    if not found:
        die("the extraction wrote no *-vocals.json")
    return json.loads(found[0].read_text()), found[0]


REPORT = re.compile(r"^\s{2}(voice|modes|words|models)\s+(.*)$")
TROUBLE = ("declined this line", "spelled out by rule", "no pronunciation",
           "has no token for", "not valid yaml")


def render_part(args, vocals, part, line, bank, outdir):
    """One part through sing.py. Returns (wav, report lines, warnings)."""
    work = outdir / "parts" / part
    cmd = [sys.executable, HERE / "sing.py", str(vocals), "--voice", str(bank),
           "--line", str(line), "--steps", str(args.steps),
           "--expressiveness", str(args.expressiveness), "-o", str(work)]
    if args.vocoder:
        cmd += ["--vocoder", args.vocoder]
    if part in args.mode:
        cmd += ["--voice-mode", args.mode[part]]
    if part in args.phonemizer:
        cmd += ["--phonemizer", args.phonemizer[part]]
    if args.literal_pitch:
        cmd += ["--literal-pitch"]
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        return None, [], [f"{part}: render failed\n{proc.stdout[-800:]}"]
    wavs = sorted(work.glob("*-vocal.wav"))
    if not wavs:
        return None, [], [f"{part}: sing.py wrote no wav"]
    report = [m.group(0).strip() for m in
              (REPORT.match(l) for l in proc.stdout.splitlines()) if m]
    warnings = [l.strip() for l in proc.stdout.splitlines()
                if any(t in l for t in TROUBLE)]
    return wavs[0], report, warnings


# --------------------------------------------------------------------- mixing

def rms_db(path):
    """How loud a part is *while singing*, in dBFS.

    Not the RMS of the file: parts rest for different amounts of a piece, and a
    plain RMS makes the one that rests most look quiet and get boosted for it.
    Only frames within 30 dB of that part's own loudest frame count.
    """
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64)
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    x /= 32768.0
    step = max(int(rate * 0.05), 1)
    frames = x[:len(x) // step * step].reshape(-1, step)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    voiced = energy[energy > energy.max() / 31.6]
    return 20 * np.log10(max(np.sqrt((voiced ** 2).mean()), 1e-9))


def default_pan(index, total):
    """A choir stood in a semicircle: outer parts wide, inner parts close.

    The outermost voices -- the top and bottom of the texture -- go widest and
    the inner ones stay near the middle, which is where a choir puts them and
    also what keeps the inner harmony from smearing. Sides alternate so no two
    neighbouring parts land on top of each other. Nothing goes past a third of
    the way out: hard panning stops a voice being part of a chord and makes it
    a soloist.
    """
    if total < 2:
        return 0.0
    rank = min(index, total - 1 - index)          # 0 for the outermost pair
    depth = max((total - 1) // 2, 1)
    side = -1 if index % 2 == 0 else 1
    return round(side * 0.30 * (1 - 0.6 * rank / depth), 3)


def mix(args, stems, out):
    if not shutil.which("ffmpeg"):
        die("ffmpeg is missing -- bash scripts/setup.sh")
    codecs = {".wav": ["-c:a", "pcm_s24le"], ".flac": ["-c:a", "flac"],
              ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"]}
    if out.suffix.lower() not in codecs:
        die(f"unknown output format {out.suffix!r} -- use .wav, .flac or .mp3")

    chains, inputs = [], []
    for i, (part, path) in enumerate(stems):
        gain = TARGET_RMS_DB - rms_db(path) + args.gain.get(part, 0.0)
        pan = args.pan.get(part, default_pan(i, len(stems)))
        print(f"  {part:10} {rms_db(path):6.1f} dBFS while singing  ->  "
              f"{gain:+5.1f} dB, pan {pan:+.2f}")
        inputs += ["-i", str(path)]
        left, right = (1 - pan) / 2, (1 + pan) / 2
        chains.append(f"[{i}:a]highpass=f=70,volume={gain:.2f}dB,"
                      f"pan=stereo|c0={left:.3f}*c0|c1={right:.3f}*c0[p{i}]")

    joined = "".join(f"[p{i}]" for i in range(len(stems)))
    graph = ";".join(chains) + ";" + (
        f"{joined}amix=inputs={len(stems)}:normalize=0[dry];"
        f"[dry]asplit=2[dry1][rv];"
        # No reverb filter exists in ffmpeg, so this builds one: a bank of
        # delays, low-passed because a real room absorbs treble faster than it
        # absorbs everything, high-passed so the tail does not muddy the bass.
        f"[rv]aecho=0.9:0.85:{TAPS_L}:{DECAYS},aecho=0.9:0.8:{TAPS_R}:{DECAYS},"
        f"lowpass=f=5500,highpass=f=180,volume={args.wet:.2f}[wet];"
        f"[dry1][wet]amix=inputs=2:normalize=0,"
        # loudnorm resamples to 192 kHz internally and leaves it there, which
        # quadruples a lossless master for nothing.
        f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100,"
        f"alimiter=limit=0.95[out]")
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
           *codecs[out.suffix.lower()], str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"ffmpeg failed:\n{proc.stderr[-1500:]}")


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score", help="score.ly, or a <stem>-vocals.json")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--voice", action="append", metavar="PART=BANK",
                    help="which bank sings which part; repeat once per part")
    ap.add_argument("--mode", action="append", metavar="PART=NAME", default=[],
                    help="voice mode for a multi-speaker bank")
    ap.add_argument("--phonemizer", action="append", metavar="PART=PATH",
                    default=[], help="override the plugin chosen for a part")
    ap.add_argument("--gain", action="append", metavar="PART=DB", default=[],
                    help="musical offset on top of the measured balance")
    ap.add_argument("--pan", action="append", metavar="PART=X", default=[],
                    help="-1 hard left to +1 hard right (default: a semicircle)")
    ap.add_argument("--vocoder", help="for banks that ship none")
    ap.add_argument("--steps", type=int, default=20,
                    help="diffusion steps: 8 to audition, 20-40 for a take")
    ap.add_argument("--expressiveness", type=float, default=0.7,
                    help="how far dspitch may leave the written notes, 0 to 1. "
                         "Lower than sing.py's default on purpose: independent "
                         "deviation in every part unsettles held chords")
    ap.add_argument("--literal-pitch", action="store_true",
                    help="no dspitch anywhere. Use it for a score video")
    ap.add_argument("--jobs", type=int, default=2,
                    help="parts to render at once (onnxruntime takes ~2 cores)")
    ap.add_argument("--wet", type=float, default=0.22, help="reverb level, 0-1")
    ap.add_argument("--format", default="mp3", choices=("mp3", "wav", "flac"),
                    help="the mix")
    ap.add_argument("--stem-format", default="wav", choices=("wav", "flac"),
                    help="flac is lossless and about a third the size, which "
                         "matters if the stems are being kept rather than "
                         "treated as intermediates")
    ap.add_argument("--no-mix", action="store_true", help="stems only")
    args = ap.parse_args()
    args.mode = pairs(args.mode, "mode")
    args.phonemizer = pairs(args.phonemizer, "phonemizer")
    args.gain = pairs(args.gain, "gain", float)
    args.pan = pairs(args.pan, "pan", float)

    outdir = Path(args.outdir).expanduser().resolve()
    (outdir / "stems").mkdir(parents=True, exist_ok=True)
    doc, vocals = extract(args.score, outdir)
    catalogue = [(l["line"], l["voice"]) for l in doc["lines"]]
    # A named voice usually means one line, but a voice with two verses under
    # it means two, and then the name alone does not say which. Line numbers
    # are always unambiguous, so both are accepted as the key.
    by_voice = {}
    for number, voice in catalogue:
        by_voice.setdefault(voice, []).append(number)
    listing = ", ".join(f"{v} (line {n})" for n, v in catalogue)
    if not args.voice:
        die(f"no --voice given. This score's parts are: {listing}"
            f"\n  e.g. --voice {catalogue[0][1]}=~/voices/tiger")
    banks = pairs(args.voice, "voice", lambda p: Path(p).expanduser())

    lines = {}
    for part in banks:
        if part.isdigit() and int(part) in dict(catalogue):
            lines[part] = int(part)
        elif part in by_voice and len(by_voice[part]) == 1:
            lines[part] = by_voice[part][0]
        elif part in by_voice:
            die(f"{part!r} has {len(by_voice[part])} lyric lines "
                f"({', '.join(str(n) for n in by_voice[part])}) -- more than one "
                f"verse under one voice. Name the line instead: "
                f"--voice {by_voice[part][0]}=<bank>")
        else:
            die(f"no part named {part!r} in this score. It has: {listing}")
    for part, bank in banks.items():
        if not (bank / "dsconfig.yaml").exists():
            die(f"{bank} has no dsconfig.yaml -- is that a bank directory?")
    sung = set(lines.values())
    silent = [f"{voice} (line {n})" for n, voice in catalogue if n not in sung]
    if silent:
        print(f"  ! no bank for {', '.join(silent)}: "
              "those parts are not in the mix")

    print(f"\n  rendering {len(banks)} part(s), {args.jobs} at a time, "
          f"{args.steps} steps"
          + (", literal pitch" if args.literal_pitch else
             f", expressiveness {args.expressiveness}"))
    ordered = [(p, lines[p], banks[p]) for p in
               sorted(banks, key=lambda p: lines[p])]
    results, problems = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(render_part, args, vocals, part, line, bank,
                               outdir): part
                   for part, line, bank in ordered}
        for future in concurrent.futures.as_completed(futures):
            part = futures[future]
            wav, report, warnings = future.result()
            results[part] = (wav, report, warnings)
            if wav is None:
                problems += warnings

    if problems:
        die("\n".join(problems))

    print()
    stems, keep = [], []
    for part, _line, _bank in ordered:
        wav, report, warnings = results[part]
        target = outdir / "stems" / f"{part}.wav"
        shutil.copyfile(wav, target)
        stems.append((part, target))
        keep.append(target)
        print(f"  {part}")
        for line in report:
            print(f"      {line}")
        for line in warnings:
            print(f"      ! {line.lstrip('! ')}")

    # The mix reads the wavs; the kept copies can be flac. Encoding after the
    # mix rather than before keeps one code path through ffmpeg's filtergraph.
    if args.stem_format == "flac":
        for path in keep:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                            "-c:a", "flac", str(path.with_suffix(".flac"))],
                           check=True)

    if args.no_mix:
        for path in keep if args.stem_format == "flac" else []:
            path.unlink()
        print(f"\n  -> {outdir / 'stems'}")
        return
    out = outdir / f"{Path(doc['score']).stem}.{args.format}"
    print()
    mix(args, stems, out)
    if args.stem_format == "flac":
        for path in keep:
            path.unlink()
    print(f"  -> {out}")
    print(f"  -> {outdir / 'stems'}  (dry, aligned to beat 0)")


if __name__ == "__main__":
    cli(main, "sing_ensemble.py")
