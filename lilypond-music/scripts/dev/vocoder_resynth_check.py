#!/usr/bin/env python3
"""Drive the REAL vocoder from a mel computed with the release's own parameters.

    python3 scripts/dev/vocoder_resynth_check.py out/score-vocals.json \\
        --vocoder ~/voices/pc_nsf_hifigan -o out/

Analysis-resynthesis: take the formant preview voice, compute the exact mel the
vocoder was trained on, and hand it straight back with the pitch curve. If the
weights and the mel conventions agree, what comes out is recognisably the same
line in a voice-shaped timbre. If any mel parameter is wrong -- base, scale,
fmin, fmax, power -- it comes out as noise.

That makes this the instrument for one specific question: when a render turns
to noise, is the vocoder at fault or the acoustic model? This path never touches
the acoustic model. If it comes back clean, the vocoder and its config are
innocent.

It reports two numbers rather than asking you to listen: the correlation
between the mel it handed the vocoder and the mel of what came back, and how
far the pitch of the output sits from the curve it was given. Exit status is 0
only if the first is at least 0.90.

Needs `librosa` (bash scripts/setup-singing.sh --dev).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import sing  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vocals", help="a <stem>-vocals.json from vocal_score.py")
    ap.add_argument("--vocoder", required=True,
                    help="vocoder package directory (holding vocoder.yaml)")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--line", type=int, default=1)
    args = ap.parse_args()

    import librosa
    import onnxruntime as ort
    import yaml

    vdir = Path(args.vocoder).expanduser().resolve()
    # The package ships two yamls: vocoder.yaml has the mel parameters and
    # oudep.yaml is packaging metadata. Choosing by name is not enough -- pick
    # the one that actually declares a sample rate.
    cfg = {}
    for path in sorted(vdir.glob("*.yaml")):
        found = yaml.safe_load(path.read_text()) or {}
        if found.get("sample_rate"):
            cfg = found
            break
    if not cfg:
        sys.exit(f"no vocoder config with mel parameters in {vdir}")

    SR, HOP = cfg["sample_rate"], cfg["hop_size"]
    WIN, NFFT = cfg["win_size"], cfg["fft_size"]
    NMEL, FMIN, FMAX = cfg["num_mel_bins"], cfg["mel_fmin"], cfg["mel_fmax"]
    print(f"vocoder: {cfg.get('name', vdir.name)} | mel_base {cfg.get('mel_base')} "
          f"| scale {cfg.get('mel_scale')}")

    doc = json.loads(Path(args.vocals).read_text())
    lines = {line["line"]: line for line in doc["lines"]}
    if args.line not in lines:
        sys.exit(f"no lyric line {args.line} (found: {sorted(lines)})")
    line = lines[args.line]
    clock = sing.Clock(doc.get("tempo_map"), doc.get("tempo"))

    voice = sing.PreviewVoice()
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    model = vdir / cfg["model"] if cfg.get("model") else next(vdir.glob("*.onnx"))
    sess = ort.InferenceSession(str(model), opts,
                                providers=["CPUExecutionProvider"])

    def analyse(signal):
        """The mel exactly as the release specifies it.

        Magnitude (not power) mel, slaney filterbank, then log compression with
        a 1e-5 floor. Getting any of base, scale, fmin, fmax or power wrong here
        is the difference between a voice and noise.
        """
        mel = librosa.feature.melspectrogram(
            y=signal.astype(np.float64), sr=SR, n_fft=NFFT, hop_length=HOP,
            win_length=WIN, n_mels=NMEL, fmin=FMIN, fmax=FMAX, power=1.0,
            center=True, htk=(cfg.get("mel_scale") == "htk"), norm="slaney")
        return (np.log(np.clip(mel, 1e-5, None)) if cfg.get("mel_base") == "e"
                else np.log10(np.clip(mel, 1e-5, None)))

    total = int((max(sing.seconds(n["when"] + n["dur"], clock)
                     for n in line["notes"]) + 1) * SR)
    track = np.zeros(total + SR, np.float32)
    correlations, cents = [], []

    for k, phrase in enumerate(sing.phrase_split(line["notes"], clock), 1):
        timeline, notes = sing.phonemize(voice, phrase, clock, set())
        timeline = sing.fill_silences(voice, timeline)
        audio, start_s = sing.preview_phrase(voice, timeline, notes, clock)

        mel = analyse(audio)
        n_frames = mel.shape[1]
        f0 = sing.f0_curve(notes, clock, start_s, n_frames, HOP / SR)
        out = sess.run(None, {"mel": mel.T[None].astype(np.float32),
                              "f0": f0[None].astype(np.float32)})[0].reshape(-1)

        # Analysis-resynthesis is only a check if it is measured. Re-analysing
        # the output against the mel that produced it says whether the weights
        # and the config agree; comparing the tracked pitch against the curve
        # the vocoder was handed says whether it followed it.
        again = analyse(out)
        width = min(mel.shape[1], again.shape[1])
        a, b = mel[:, :width].ravel(), again[:, :width].ravel()
        correlations.append(float(np.corrcoef(a, b)[0, 1]))
        tracked, voiced, _p = librosa.pyin(out.astype(np.float64), fmin=65,
                                           fmax=1000, sr=SR, hop_length=HOP)
        want = f0[:len(tracked)]
        good = voiced[:len(want)] & np.isfinite(tracked[:len(want)]) & (want > 0)
        if good.any():
            cents.append(np.abs(1200 * np.log2(tracked[:len(want)][good]
                                               / want[good])))
        print(f"  phrase {k}: {n_frames} frames -> {len(out) / SR:.2f}s "
              f"(mel {mel.min():.1f}..{mel.max():.1f}, "
              f"f0 {f0.min():.0f}-{f0.max():.0f} Hz), "
              f"mel correlation {correlations[-1]:.3f}")

        at = int(start_s * SR)
        if at < 0:
            out, at = out[-at:], 0
        end = min(len(track), at + len(out))
        track[at:end] += out[:end - at]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(track))) or 1.0
    dst = outdir / "vocoder-resynth.wav"
    sing.write_wav(dst, track / peak * 0.9, SR)

    overall = float(np.mean(correlations)) if correlations else 0.0
    median = float(np.median(np.concatenate(cents))) if cents else float("nan")
    print(f"\nmel correlation {overall:.3f} across {len(correlations)} phrase(s)"
          f"\npitch preserved to a median {median:.1f} cents"
          f"\npeak {peak:.3f} -> {dst}")
    # Below about 0.9 the resynthesis is not tracking the mel it was given, and
    # a bank rendering as noise is the vocoder's fault rather than the acoustic
    # model's. The bound is loose because the input here is a formant voice,
    # which is further from the vocoder's training data than any real bank.
    ok = overall >= 0.90
    print("the vocoder and its config agree" if ok else
          "MISMATCH -- check mel base, scale, fmin, fmax and power")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
