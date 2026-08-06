#!/usr/bin/env bash
# Install everything the pipeline needs. Safe to re-run.
#
#   lilypond            engraving + MIDI export
#   fluidsynth          MIDI -> audio
#   fluid-soundfont-gm  the General MIDI sample set fluidsynth plays through
#   ffmpeg              audio mastering + video assembly
#
# Debian/Ubuntu. On macOS: brew install lilypond fluid-synth ffmpeg
# (and fetch a GM soundfont, e.g. FluidR3_GM.sf2, then pass --soundfont).
set -e

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"

$SUDO apt-get update -qq
$SUDO apt-get install -y lilypond fluidsynth fluid-soundfont-gm ffmpeg
python3 -c "import numpy, PIL" 2>/dev/null || pip install --break-system-packages numpy pillow

echo
lilypond --version | head -1
fluidsynth --version 2>&1 | head -1
ffmpeg -version | head -1
ls /usr/share/sounds/sf2/ 2>/dev/null || echo "no soundfont in /usr/share/sounds/sf2 -- pass --soundfont"
echo "ready."
