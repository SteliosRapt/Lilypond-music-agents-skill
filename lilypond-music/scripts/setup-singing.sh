#!/usr/bin/env bash
# Extra dependencies for sing.py. Not needed for engraving, MIDI, audio or video.
#
#   onnxruntime   runs the voicebank's acoustic model and vocoder
#   pyyaml        reads dsconfig.yaml and the dsdict dictionary
#
# Pass --dev to add what scripts/dev/ needs on top: `onnx` to build the stub
# bank the self-test runs against, and `librosa` for the vocoder resynthesis
# check. Neither is needed to sing.
#
# The voicebank itself is NOT installed here, deliberately: English DiffSinger
# banks are almost all non-commercial, some forbid redistribution, and the
# community vocoders are CC BY-NC-SA. Choose one, read its terms, and unzip it
# somewhere you can point --voice at.
set -e

PACKAGES="onnxruntime pyyaml numpy"
[ "${1:-}" = "--dev" ] && PACKAGES="$PACKAGES onnx librosa"

pip install --break-system-packages $PACKAGES 2>/dev/null \
  || pip install $PACKAGES

python3 - <<'EOF'
import onnxruntime, yaml, numpy
print("onnxruntime", onnxruntime.__version__)
print("providers  ", ", ".join(onnxruntime.get_available_providers()))
EOF

cat <<'EOF'

ready. Next:
  1. put a DiffSinger bank somewhere, e.g. ~/voices/mybank
     (dsconfig.yaml + acoustic.onnx + phonemes.txt + dsdict*.yaml)
  2. put its vocoder package in ~/voices/mybank/vocoder/
  3. python3 scripts/sing.py score.ly --voice ~/voices/mybank -o out/
     (--inspect first, to see what that bank actually declares)

See references/singing-synthesis.md for what a bank has to contain.
With --dev, `python3 scripts/dev/selftest.py` checks the whole pipeline
against a stub bank in about a minute.
EOF
