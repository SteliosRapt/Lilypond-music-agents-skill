"""Build a fake DiffSinger bank: real ONNX graphs with the real I/O contract.

    python3 scripts/dev/make_stub_bank.py

Written so the pipeline can be tested end to end without a voicebank -- the
graphs compute nonsense, but they declare exactly the inputs and outputs a real
bank declares, which is what catches shape, dtype and frame-arithmetic bugs.
It found three of them.

The bank it writes has all five models: the acoustic model and vocoder every
bank ships, plus `dsdur/`, `dspitch/` and `dsvariance/` declaring the same
tensors TIGER v102 declares. `dsvariance` is the reason this matters most --
no bank to hand ships one, so these stubs are the only exercise that code path
gets. Each predictor folder carries its own phoneme table, deliberately one
phone shorter than the acoustic model's, because real banks differ that way
and a predictor fed ids from the wrong table fails silently.
"""

import numpy as np, onnx, yaml
from onnx import helper as H, TensorProto as T
from pathlib import Path

bank = Path("stubvoice"); bank.mkdir(exist_ok=True)
voc = bank / "vocoder"; voc.mkdir(exist_ok=True)
MEL, HOP, SR = 128, 512, 44100

# ---- acoustic: tokens, durations, f0, speedup (+ the variance curves) -> mel
# The four variance inputs are declared because a bank that wants them is the
# only way to exercise the dsvariance path; TIGER turns them off.
ones_mel = H.make_tensor("ones_mel", T.FLOAT, [1, MEL], np.ones(MEL, np.float32))
neg1 = H.make_tensor("neg1", T.INT64, [1], [-1])
nodes = [
    H.make_node("Unsqueeze", ["f0", "axm1"], ["f0e"]),
    H.make_node("MatMul", ["f0e", "ones_mel"], ["mel_raw"]),
    H.make_node("Mul", ["mel_raw", "scale"], ["mel"]),
    H.make_node("Add", ["energy", "breathiness"], ["v1"]),
    H.make_node("Add", ["voicing", "tension"], ["v2"]),
    H.make_node("Add", ["v1", "v2"], ["variance_sum"]),
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
     H.make_tensor_value_info("energy", T.FLOAT, [1, "T"]),
     H.make_tensor_value_info("breathiness", T.FLOAT, [1, "T"]),
     H.make_tensor_value_info("voicing", T.FLOAT, [1, "T"]),
     H.make_tensor_value_info("tension", T.FLOAT, [1, "T"]),
     H.make_tensor_value_info("speedup", T.INT64, [1])],
    [H.make_tensor_value_info("mel", T.FLOAT, [1, "T", MEL]),
     H.make_tensor_value_info("variance_sum", T.FLOAT, [1, "T"]),
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


# ---- the predictors ------------------------------------------------------
# Each folder is a linguistic encoder feeding a predictor, which is the shape
# of the real thing: two models, not one.  The arithmetic inside is nonsense
# and deliberately so -- what is being tested is that the right tensors, ranks
# and dtypes reach the right model, and that the frame counts agree.

HIDDEN = 256


def save(model, path):
    onnx.checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, path)


def encoder(name, extra):
    """tokens (+ extra int inputs) -> encoder_out [1,N,HIDDEN], x_masks [1,N]."""
    nodes = [
        H.make_node("Cast", ["tokens"], ["tf"], to=T.FLOAT),
        H.make_node("Unsqueeze", ["tf", "axm1"], ["te"]),
        H.make_node("MatMul", ["te", "ones_hidden"], ["encoder_out"]),
        H.make_node("Less", ["tokens", "zero"], ["x_masks"]),
    ]
    # Touch every extra input so onnxruntime genuinely requires it: a stub that
    # ignores a tensor cannot catch a caller that forgets to pass it.
    for k, src in enumerate(extra):
        nodes.append(H.make_node("ReduceSum", [src, "ax01"], [f"s{k}"], keepdims=1))
    graph = H.make_graph(
        nodes + [H.make_node("Identity", ["s0"], ["touched"])], name,
        [H.make_tensor_value_info("tokens", T.INT64, [1, "n_tokens"])] +
        [H.make_tensor_value_info(src, T.INT64, [1, dim]) for src, dim in
         zip(extra, ("n_words", "n_words"))],
        [H.make_tensor_value_info("encoder_out", T.FLOAT, [1, "n_tokens", HIDDEN]),
         H.make_tensor_value_info("x_masks", T.BOOL, [1, "n_tokens"]),
         H.make_tensor_value_info("touched", T.INT64, [1, 1])],
        [H.make_tensor("ones_hidden", T.FLOAT, [1, HIDDEN], np.ones(HIDDEN, np.float32)),
         H.make_tensor("axm1", T.INT64, [1], [-1]),
         H.make_tensor("ax01", T.INT64, [2], [0, 1]),
         H.make_tensor("zero", T.INT64, [1], [0])])
    return H.make_model(graph, opset_imports=[H.make_opsetid("", 13)])


# dsdur: linguistic(tokens, word_div, word_dur) -> dur(+ph_midi, spk_embed)
save(encoder("dur_linguistic", ["word_div", "word_dur"]),
     bank / "dsdur/files/linguistic.onnx")
graph = H.make_graph(
    [H.make_node("ReduceMean", ["encoder_out"], ["e"], axes=[-1], keepdims=0),
     H.make_node("Cast", ["ph_midi"], ["m"], to=T.FLOAT),
     H.make_node("Add", ["e", "m"], ["sum"]),
     H.make_node("Abs", ["sum"], ["pos"]),
     H.make_node("Add", ["pos", "one"], ["ph_dur_pred"]),
     H.make_node("ReduceMean", ["spk_embed"], ["spk"], axes=[-1], keepdims=0),
     H.make_node("Cast", ["x_masks"], ["mask"], to=T.FLOAT)],
    "dur",
    [H.make_tensor_value_info("encoder_out", T.FLOAT, [1, "n_tokens", HIDDEN]),
     H.make_tensor_value_info("x_masks", T.BOOL, [1, "n_tokens"]),
     H.make_tensor_value_info("ph_midi", T.INT64, [1, "n_tokens"]),
     H.make_tensor_value_info("spk_embed", T.FLOAT, [1, "n_tokens", HIDDEN])],
    [H.make_tensor_value_info("ph_dur_pred", T.FLOAT, [1, "n_tokens"]),
     H.make_tensor_value_info("spk", T.FLOAT, [1, "n_tokens"]),
     H.make_tensor_value_info("mask", T.FLOAT, [1, "n_tokens"])],
    [H.make_tensor("axm1", T.INT64, [1], [-1]),
     H.make_tensor("one", T.FLOAT, [1], [1.0])])
save(H.make_model(graph, opset_imports=[H.make_opsetid("", 13)]),
     bank / "dsdur/files/dur.onnx")

# dspitch: linguistic(tokens, ph_dur) -> pitch(+ the note sequence and curves)
save(encoder("pitch_linguistic", ["ph_dur"]), bank / "dspitch/files/linguistic.onnx")
pitch_inputs = [
    H.make_tensor_value_info("encoder_out", T.FLOAT, [1, "n_tokens", HIDDEN]),
    H.make_tensor_value_info("ph_dur", T.INT64, [1, "n_tokens"]),
    H.make_tensor_value_info("note_midi", T.FLOAT, [1, "n_notes"]),
    H.make_tensor_value_info("note_rest", T.BOOL, [1, "n_notes"]),
    H.make_tensor_value_info("note_dur", T.INT64, [1, "n_notes"]),
    H.make_tensor_value_info("pitch", T.FLOAT, [1, "n_frames"]),
    H.make_tensor_value_info("expr", T.FLOAT, [1, "n_frames"]),
    H.make_tensor_value_info("retake", T.BOOL, [1, "n_frames"]),
    H.make_tensor_value_info("spk_embed", T.FLOAT, [1, "n_frames", HIDDEN]),
    H.make_tensor_value_info("speedup", T.INT64, []),
]
graph = H.make_graph(
    # a quarter-tone above the written line, so a caller that silently ignores
    # the prediction and keeps its own curve is visible in the output
    [H.make_node("Add", ["pitch", "half"], ["shifted"]),
     H.make_node("Mul", ["expr", "shifted"], ["pitch_pred"]),
     H.make_node("Cast", ["retake"], ["rt"], to=T.FLOAT),
     H.make_node("ReduceMean", ["spk_embed"], ["spk"], axes=[-1], keepdims=0),
     H.make_node("ReduceMean", ["encoder_out"], ["enc"], axes=[-1], keepdims=0),
     H.make_node("Cast", ["note_rest"], ["nr"], to=T.FLOAT),
     H.make_node("Cast", ["note_dur"], ["nd"], to=T.FLOAT),
     H.make_node("Add", ["nr", "note_midi"], ["notes_touched"]),
     H.make_node("Add", ["notes_touched", "nd"], ["notes_sum"]),
     H.make_node("Cast", ["ph_dur"], ["pd"], to=T.FLOAT),
     H.make_node("Cast", ["speedup"], ["sp"], to=T.FLOAT),
     H.make_node("Mul", ["pd", "sp"], ["ph_touched"])],
    "pitch", pitch_inputs,
    [H.make_tensor_value_info("pitch_pred", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("rt", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("spk", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("enc", T.FLOAT, [1, "n_tokens"]),
     H.make_tensor_value_info("notes_sum", T.FLOAT, [1, "n_notes"]),
     H.make_tensor_value_info("ph_touched", T.FLOAT, [1, "n_tokens"])],
    [H.make_tensor("axm1", T.INT64, [1], [-1]),
     H.make_tensor("half", T.FLOAT, [1], [0.5])])
save(H.make_model(graph, opset_imports=[H.make_opsetid("", 13)]),
     bank / "dspitch/files/pitch.onnx")

# dsvariance: linguistic(tokens, ph_dur) -> variance(four curves out)
save(encoder("var_linguistic", ["ph_dur"]), bank / "dsvariance/files/linguistic.onnx")
graph = H.make_graph(
    [H.make_node("Mul", ["pitch", "small"], ["energy_pred"]),
     H.make_node("Mul", ["expr", "small"], ["breathiness_pred"]),
     H.make_node("Mul", ["pitch", "smaller"], ["voicing_pred"]),
     H.make_node("Mul", ["expr", "smaller"], ["tension_pred"]),
     H.make_node("Cast", ["retake"], ["rt"], to=T.FLOAT),
     H.make_node("ReduceMean", ["rt"], ["retaken"], axes=[-1], keepdims=0),
     H.make_node("ReduceMean", ["spk_embed"], ["spk"], axes=[-1], keepdims=0),
     H.make_node("ReduceMean", ["encoder_out"], ["enc"], axes=[-1], keepdims=0),
     H.make_node("Cast", ["ph_dur"], ["pd"], to=T.FLOAT),
     H.make_node("Cast", ["speedup"], ["sp"], to=T.FLOAT),
     H.make_node("Mul", ["pd", "sp"], ["ph_touched"])],
    "variance",
    [H.make_tensor_value_info("encoder_out", T.FLOAT, [1, "n_tokens", HIDDEN]),
     H.make_tensor_value_info("ph_dur", T.INT64, [1, "n_tokens"]),
     H.make_tensor_value_info("pitch", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("expr", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("retake", T.BOOL, [1, "n_frames", 4]),
     H.make_tensor_value_info("spk_embed", T.FLOAT, [1, "n_frames", HIDDEN]),
     H.make_tensor_value_info("speedup", T.INT64, [])],
    [H.make_tensor_value_info("energy_pred", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("breathiness_pred", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("voicing_pred", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("tension_pred", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("retaken", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("spk", T.FLOAT, [1, "n_frames"]),
     H.make_tensor_value_info("enc", T.FLOAT, [1, "n_tokens"]),
     H.make_tensor_value_info("ph_touched", T.FLOAT, [1, "n_tokens"])],
    [H.make_tensor("axm1", T.INT64, [1], [-1]),
     H.make_tensor("small", T.FLOAT, [1], [-0.02]),
     H.make_tensor("smaller", T.FLOAT, [1], [-0.01])])
save(H.make_model(graph, opset_imports=[H.make_opsetid("", 13)]),
     bank / "dsvariance/files/variance.onnx")

# Predictor phoneme tables are deliberately NOT the acoustic table: real banks
# differ (TIGER's dspitch table is eight phones shorter), and a caller that
# tokenises with the wrong table has to be caught here rather than by ear.
for folder, drop in (("dsdur", []), ("dspitch", ["zh"]), ("dsvariance", ["zh"])):
    files = bank / folder / "files"
    (files / "phonemes.txt").write_text(
        "\n".join(p for p in phones if p not in drop) + "\n")
    np.ones(HIDDEN, np.float32).tofile(files / "speaker.emb")
    (bank / folder / "dsconfig.yaml").write_text(yaml.safe_dump(dict(
        phonemes="files/phonemes.txt", linguistic="files/linguistic.onnx",
        hop_size=HOP, sample_rate=SR, speakers=["files/speaker"],
        **({"dur": "files/dur.onnx", "predict_dur": True} if folder == "dsdur" else
           {"pitch": "files/pitch.onnx", "use_expr": True, "use_note_rest": True}
           if folder == "dspitch" else
           {"variance": "files/variance.onnx", "predict_energy": True,
            "predict_breathiness": True, "predict_voicing": True,
            "predict_tension": True}))))

print("stub bank written to", bank.resolve())
print("  acoustic + vocoder + dsdur + dspitch + dsvariance")
