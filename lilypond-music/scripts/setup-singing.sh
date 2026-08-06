#!/usr/bin/env bash
# Extra dependencies for sing.py. Not needed for engraving, MIDI, audio or video.
#
#   onnxruntime   runs the voicebank's acoustic model and vocoder
#   pyyaml        reads dsconfig.yaml and the dsdict dictionary
#
# The voicebank itself is NOT installed here, deliberately: English DiffSinger
# banks are almost all non-commercial, some forbid redistribution, and the
# community vocoders are CC BY-NC-SA. Choose one, read its terms, and unzip it
# somewhere you can point --voice at.
set -e

pip install --break-system-packages onnxruntime pyyaml numpy 2>/dev/null \
  || pip install onnxruntime pyyaml numpy

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

See references/singing-synthesis.md for what a bank has to contain.
EOF
