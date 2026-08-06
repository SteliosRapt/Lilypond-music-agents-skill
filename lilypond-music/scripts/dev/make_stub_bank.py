"""Build a fake DiffSinger bank: real ONNX graphs with the real I/O contract.

    python3 scripts/dev/make_stub_bank.py

Written so the pipeline can be tested end to end without a voicebank -- the
graphs compute nonsense, but they declare exactly the inputs and outputs a real
bank declares, which is what catches shape, dtype and frame-arithmetic bugs.
It found three of them.

To test the duration/pitch/variance chain, extend this with stubs for
linguistic.onnx, dur.onnx, pitch.onnx and variance.onnx once their real
interfaces are known (see handoff.md).
"""

import numpy as np, onnx, yaml
from onnx import helper as H, TensorProto as T
from pathlib import Path

bank = Path("stubvoice"); bank.mkdir(exist_ok=True)
voc = bank / "vocoder"; voc.mkdir(exist_ok=True)
MEL, HOP, SR = 128, 512, 44100

# ---- acoustic: tokens, durations, f0, speedup -> mel [1,T,MEL]
ones_mel = H.make_tensor("ones_mel", T.FLOAT, [1, MEL], np.ones(MEL, np.float32))
neg1 = H.make_tensor("neg1", T.INT64, [1], [-1])
nodes = [
    H.make_node("Unsqueeze", ["f0", "axm1"], ["f0e"]),
    H.make_node("MatMul", ["f0e", "ones_mel"], ["mel_raw"]),
    H.make_node("Mul", ["mel_raw", "scale"], ["mel"]),
    # touch the int inputs so they are genuinely required
    H.make_node("ReduceSum", ["durations", "ax01"], ["dsum"], keepdims=1),
    H.make_node("ReduceSum", ["tokens", "ax01"], ["tsum"], keepdims=1),
    H.make_node("Add", ["dsum", "tsum"], ["checksum"]),
    H.make_node("Squeeze", ["checksum", "ax01"], ["chk1"]),
    H.make_node("Add", ["chk1", "speedup"], ["chk2"]),
    H.make_node("Cast", ["chk2"], ["chkf"], to=T.FLOAT),
]
graph = H.make_graph(
    nodes, "acoustic",
    [H.make_tensor_value_info("tokens", T.INT64, [1, "N"]),
     H.make_tensor_value_info("durations", T.INT64, [1, "N"]),
     H.make_tensor_value_info("f0", T.FLOAT, [1, "T"]),
     H.make_tensor_value_info("speedup", T.INT64, [1])],
    [H.make_tensor_value_info("mel", T.FLOAT, [1, "T", MEL]),
     H.make_tensor_value_info("chkf", T.FLOAT, [1])],
    [ones_mel,
     H.make_tensor("axm1", T.INT64, [1], [-1]),
     H.make_tensor("ax01", T.INT64, [2], [0, 1]),
     H.make_tensor("scale", T.FLOAT, [1], [0.001])])
m = H.make_model(graph, opset_imports=[H.make_opsetid("", 13)])
onnx.checker.check_model(m); onnx.save(m, bank / "acoustic.onnx")

# ---- vocoder: mel, f0 -> waveform [1, T*HOP]
nodes = [
    H.make_node("Unsqueeze", ["f0", "axm1"], ["f0e"]),
    H.make_node("MatMul", ["f0e", "ones_hop"], ["frames"]),
    H.make_node("Reshape", ["frames", "flat"], ["wav_raw"]),
    H.make_node("ReduceMean", ["mel"], ["melm"], axes=[2], keepdims=0),
    H.make_node("Mul", ["wav_raw", "tiny"], ["waveform"]),
]
graph = H.make_graph(
    nodes, "vocoder",
    [H.make_tensor_value_info("mel", T.FLOAT, [1, "T", MEL]),
     H.make_tensor_value_info("f0", T.FLOAT, [1, "T"])],
    [H.make_tensor_value_info("waveform", T.FLOAT, [1, "L"]),
     H.make_tensor_value_info("melm", T.FLOAT, [1, "T"])],
    [H.make_tensor("ones_hop", T.FLOAT, [1, HOP], np.ones(HOP, np.float32)),
     H.make_tensor("axm1", T.INT64, [1], [-1]),
     H.make_tensor("ax2", T.INT64, [1], [2]),
     H.make_tensor("flat", T.INT64, [2], [1, -1]),
     H.make_tensor("tiny", T.FLOAT, [1], [0.0001])])
m = H.make_model(graph, opset_imports=[H.make_opsetid("", 13)])
onnx.checker.check_model(m); onnx.save(m, voc / "vocoder.onnx")

# ---- config, phonemes, dictionary (ARPAbet-ish, lowercase like real EN banks)
common = dict(sample_rate=SR, hop_size=HOP, win_size=2048, fft_size=2048,
              num_mel_bins=MEL, mel_fmin=40, mel_fmax=16000,
              mel_base="10", mel_scale="slaney")
(bank / "dsconfig.yaml").write_text(yaml.safe_dump(
    dict(phonemes="phonemes.txt", acoustic="acoustic.onnx", vocoder="vocoder",
         **common)))
(voc / "vocoder.yaml").write_text(yaml.safe_dump(
    dict(name="stub_hifigan", model="vocoder.onnx", **common)))

vowels = ["aa", "ae", "ah", "ao", "aw", "ay", "eh", "er", "ey", "ih", "iy",
          "ow", "oy", "uh", "uw"]
cons = {"b": "stop", "ch": "affricate", "d": "stop", "dh": "fricative",
        "f": "fricative", "g": "stop", "hh": "aspirate", "jh": "affricate",
        "k": "stop", "l": "liquid", "m": "nasal", "n": "nasal", "ng": "nasal",
        "p": "stop", "r": "liquid", "s": "fricative", "sh": "fricative",
        "t": "stop", "th": "fricative", "v": "fricative", "w": "semivowel",
        "y": "semivowel", "z": "fricative", "zh": "fricative"}
phones = ["SP", "AP"] + vowels + list(cons)
(bank / "phonemes.txt").write_text("\n".join(phones) + "\n")

entries = {
    "lanterns": ["l", "ae", "n", "t", "er", "n", "z"],
    "drift": ["d", "r", "ih", "f", "t"],
    "the": ["dh", "ah"], "water": ["w", "ao", "t", "er"],
    "holds": ["hh", "ow", "l", "d", "z"], "no": ["n", "ow"],
    "name": ["n", "ey", "m"], "ash": ["ae", "sh"], "and": ["ae", "n", "d"],
    "silence": ["s", "ay", "l", "ah", "n", "s"],
    "blossom": ["b", "l", "aa", "s", "ah", "m"], "in": ["ih", "n"],
    "flame": ["f", "l", "ey", "m"], "hello": ["hh", "ah", "l", "ow"],
    "world": ["w", "er", "l", "d"], "this": ["dh", "ih", "s"],
    "is": ["ih", "z"], "a": ["ah"], "melisma": ["m", "ah", "l", "ih", "z", "m", "ah"],
    "test": ["t", "eh", "s", "t"],
}
(bank / "dsdict-en.yaml").write_text(yaml.safe_dump({
    "symbols": ([{"symbol": p, "type": "vowel"} for p in ["SP", "AP"] + vowels] +
                [{"symbol": k, "type": v} for k, v in cons.items()]),
    "entries": [{"grapheme": g, "phonemes": p} for g, p in entries.items()]},
    sort_keys=False))
print("stub bank written to", bank.resolve())
