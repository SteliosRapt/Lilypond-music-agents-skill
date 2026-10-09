#!/usr/bin/env python3
"""Regenerate the images and the animation the README shows.

    python3 tools/make_media.py

The README claims this skill turns text into engraved notation, audio and a
video with a playhead on it. Screenshots of that claim are worth more than the
sentence, but only if they are the real output rather than a mock-up, so they
are built here by running the actual pipeline and nothing else.

Needs the pipeline's own dependencies (`bash lilypond-music/scripts/setup.sh`)
plus PyMuPDF for the PDF pages. Writes into `docs/media/`, which is committed:
regenerating is for when the pipeline's output changes, not for every clone.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"
RENDER = ROOT / "lilypond-music" / "scripts" / "render.py"

# The demo's paper is 280x132mm, so the video is given the same shape and the
# frame is filled by notation rather than letterboxed into a black surround.
VIDEO_SIZE = "1600x754"
# 12 fps is enough for a playhead and halves the file against 24. Engraved
# notation is flat colour on white, so 64 colours is more than it can use.
GIF_FPS = 12
GIF_WIDTH = 900
# Thirteen seconds from bar four, which is the window that contains the system
# change at 18.1s. That change is the part worth showing: a playhead sweeping
# one line proves nothing that a straight edge could not fake, and a playhead
# arriving at the right note on the next line is the whole claim.
GIF_START, GIF_LENGTH = 11, 13


def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd[:3])} ...")
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          **kwargs)
    if proc.returncode != 0:
        sys.exit(f"failed: {' '.join(str(c) for c in cmd)}\n"
                 f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    return proc.stdout


def page_png(pdf, dst, dpi=150, page=0):
    """One page of a PDF, as a PNG, trimmed to its ink."""
    import pymupdf
    doc = pymupdf.open(pdf)
    pix = doc[page].get_pixmap(dpi=dpi)
    pix.save(dst)
    doc.close()
    print(f"  -> {dst.relative_to(ROOT)}  ({dst.stat().st_size // 1024} KB)")


def gif(mp4, dst, fps=GIF_FPS, width=GIF_WIDTH, start=None, length=None):
    """An mp4 as a looping GIF, palettised in one pass over the whole clip.

    Generating the palette from the clip rather than per frame is what keeps
    the staff lines from shimmering: a per-frame palette re-quantises the same
    grey differently in successive frames, which reads as noise on exactly the
    thin horizontal lines a score is made of.
    """
    window = (["-ss", str(start)] if start is not None else []) + \
             (["-t", str(length)] if length is not None else [])
    run(["ffmpeg", "-v", "error", "-y", *window, "-i", mp4, "-vf",
         f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
         f"[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
         "-loop", "0", dst])
    print(f"  -> {dst.relative_to(ROOT)}  ({dst.stat().st_size // 1024} KB)")


def main():
    if not shutil.which("lilypond"):
        sys.exit("no lilypond -- run: bash lilypond-music/scripts/setup.sh")
    MEDIA.mkdir(parents=True, exist_ok=True)
    work = MEDIA / ".work"

    print("rendering docs/media/demo.ly through the pipeline ...")
    out = run([sys.executable, RENDER, MEDIA / "demo.ly", "-o", work,
               "--size", VIDEO_SIZE, "--verify", "6"])
    print("".join(f"  | {line}\n" for line in out.splitlines()
                  if "onsets" in line or "sync" in line))
    if "playhead sync: verified" not in out:
        sys.exit("the demo's playhead did not verify -- not shipping that")

    print("building the README's media ...")
    gif(work / "demo.mp4", MEDIA / "playhead.gif",
        start=GIF_START, length=GIF_LENGTH)
    page_png(work / "demo.pdf", MEDIA / "demo-score.png")
    page_png(ROOT / "songs" / "tide-and-lantern.pdf",
             MEDIA / "tide-and-lantern-score.png", dpi=120)
    # The rendered audio is worth linking to, and it is small.
    shutil.copyfile(work / "demo.mp3", MEDIA / "demo.mp3")
    print(f"  -> {(MEDIA / 'demo.mp3').relative_to(ROOT)}  "
          f"({(MEDIA / 'demo.mp3').stat().st_size // 1024} KB)")

    shutil.rmtree(work, ignore_errors=True)
    total = sum(p.stat().st_size for p in MEDIA.iterdir() if p.is_file())
    print(f"\ndocs/media is {total / 1048576:.1f} MB")


if __name__ == "__main__":
    main()
