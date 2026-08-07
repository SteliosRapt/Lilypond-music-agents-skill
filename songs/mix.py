#!/usr/bin/env python3
"""Mix the four sung parts of "Tide and Lantern" into one a cappella track.

    python3 songs/mix.py                      # stems/ -> tide-and-lantern.mp3
    python3 songs/mix.py --dry                # print the ffmpeg command only

`render.py --vocal` exists to put one sung line on top of a fluidsynth
instrumental, and this piece has no instrumental: four voices, nothing else. So
the mix is done here instead, and it is only three decisions.

**Balance is measured, not guessed.** The banks are not equally loud -- TIGER
comes out about 5 dB below CANARY on the same line -- so each stem is measured
and pulled to a common RMS before the musical offsets below are applied. Those
offsets are the actual mixing decision: the tune slightly forward, the bass
solid under it, the inner parts back.

**Placement is a choir stood in a semicircle**, not a hard pan: a voice panned
hard to one side stops being part of a chord and starts being a soloist.

**The reverb is the room the piece needs.** Unaccompanied voices with no tail
sound like four people in separate booths, which is exactly what they are. FFmpeg
has no reverb filter, so this builds one: a bank of delays whose times are
mutually prime (so the taps never line up into a flutter), low-passed because a
real room absorbs treble faster than it absorbs everything else, and mixed in
at about a fifth of the dry level.
"""

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

#      stem       dB offset   pan (-1 left .. +1 right)
PARTS = [("soprano", -1.0, -0.30),
         ("alto",    -2.0, +0.30),
         ("tenor",   +1.0, -0.12),   # the tune
         ("bass",     0.0, +0.10)]

TARGET_RMS_DB = -20.0
# Mutually prime delays in ms, so no two taps coincide and the tail does not
# flutter. The pairs are offset left/right to widen the room.
TAPS_L = "43|97|173|251"
TAPS_R = "61|113|191|277"
DECAYS = "0.35|0.26|0.19|0.13"


def rms_db(path):
    """How loud this part is *while singing*, in dBFS.

    Not the RMS of the file: these parts rest for different amounts of the
    piece -- the soprano is silent for three bars and out of the canon for two
    more -- and a plain RMS makes the one that rests most look quiet and get
    boosted for it. Only frames within 30 dB of the part's own loudest frame
    count, which is silence, breaths and reverb tails excluded.
    """
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64)
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    x /= 32768.0
    step = int(rate * 0.05)
    frames = x[:len(x) // step * step].reshape(-1, step)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    voiced = energy[energy > energy.max() / 31.6]        # -30 dB from the peak
    return 20 * np.log10(max(np.sqrt((voiced ** 2).mean()), 1e-9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stems", default=str(HERE / "stems"))
    ap.add_argument("-o", "--out", default=str(HERE / "tide-and-lantern.mp3"))
    ap.add_argument("--wet", type=float, default=0.22, help="reverb level, 0-1")
    ap.add_argument("--dry", action="store_true", help="print the command, run nothing")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is missing -- bash lilypond-music/scripts/setup.sh")
    stems = Path(args.stems)
    files = [(name, stems / f"{name}.wav", off, pan) for name, off, pan in PARTS]
    for name, path, _o, _p in files:
        if not path.exists():
            sys.exit(f"no stem for {name} at {path} -- render the parts first")

    chains, inputs = [], []
    for i, (name, path, offset, pan) in enumerate(files):
        gain = TARGET_RMS_DB - rms_db(path) + offset
        print(f"  {name:8} {rms_db(path):6.1f} dBFS rms  ->  {gain:+5.1f} dB, "
              f"pan {pan:+.2f}")
        inputs += ["-i", str(path)]
        # Equal-power-ish placement from one mono source, then the highpass that
        # keeps four separate DC-ish rumbles from stacking up in the sum.
        left, right = (1 - pan) / 2, (1 + pan) / 2
        chains.append(f"[{i}:a]highpass=f=70,volume={gain:.2f}dB,"
                      f"pan=stereo|c0={left:.3f}*c0|c1={right:.3f}*c0[p{i}]")

    mixed = "".join(f"[p{i}]" for i in range(len(files)))
    graph = ";".join(chains) + ";" + (
        f"{mixed}amix=inputs={len(files)}:normalize=0[dry];"
        f"[dry]asplit=2[dry1][rv];"
        f"[rv]aecho=0.9:0.85:{TAPS_L}:{DECAYS},"
        f"aecho=0.9:0.8:{TAPS_R}:{DECAYS},"
        f"lowpass=f=5500,highpass=f=180,volume={args.wet:.2f}[wet];"
        f"[dry1][wet]amix=inputs=2:normalize=0,"
        # A quiet unaccompanied piece with one loud final chord: bring the whole
        # thing to a sensible level, then catch the peak rather than clip it.
        f"loudnorm=I=-16:TP=-1.5:LRA=11,alimiter=limit=0.95[out]")

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph,
           "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "320k", args.out]
    if args.dry:
        print(" ".join(cmd))
        return
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(proc.stderr[-2000:])
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
