"""Drive the REAL vocoder from a mel computed with the release's own parameters.

Analysis-resynthesis: take the formant preview, compute the exact mel the
vocoder was trained on, hand it back with the pitch curve. If the weights and
the mel conventions agree, what comes out is recognisably the same line in a
voice-shaped timbre. If any mel parameter is wrong, it comes out as noise.
"""
import sys, json, wave
import numpy as np, librosa, yaml, onnxruntime as ort
sys.path.insert(0, '/home/claude/skill/lilypond-music/scripts')
import sing, preview_voice

cfg = yaml.safe_load(open('/home/claude/voc/vocoder.yaml'))
SR, HOP, WIN, NFFT = cfg['sample_rate'], cfg['hop_size'], cfg['win_size'], cfg['fft_size']
NMEL, FMIN, FMAX = cfg['num_mel_bins'], cfg['mel_fmin'], cfg['mel_fmax']
print("vocoder:", cfg['name'], "| mel_base", cfg['mel_base'], "| scale", cfg['mel_scale'])

doc = json.load(open('eout/ensemble-voice-vocals.json'))
line, tempo = doc['lines'][0], doc['tempo']
voice = sing.PreviewVoice()
opts = ort.SessionOptions(); opts.log_severity_level = 3
sess = ort.InferenceSession('/home/claude/voc/' + cfg['model'], opts,
                            providers=['CPUExecutionProvider'])

total = int((max(sing.seconds(n['when']+n['dur'], tempo) for n in line['notes']) + 1) * SR)
track = np.zeros(total + SR, np.float32)

for k, phrase in enumerate(sing.phrase_split(line['notes'], tempo), 1):
    tl, notes = sing.phonemize(voice, phrase, tempo, set())
    tl = sing.fill_silences(voice, tl)
    audio, start_s = sing.preview_phrase(voice, tl, notes, tempo)

    # mel exactly as the release specifies: magnitude (not power) mel, slaney
    # filterbank, then natural-log compression with a 1e-5 floor
    mel = librosa.feature.melspectrogram(y=audio.astype(np.float64), sr=SR, n_fft=NFFT,
                                         hop_length=HOP, win_length=WIN, n_mels=NMEL,
                                         fmin=FMIN, fmax=FMAX, power=1.0, center=True,
                                         htk=(cfg['mel_scale'] == 'htk'), norm='slaney')
    mel = np.log(np.clip(mel, 1e-5, None)) if cfg['mel_base'] == 'e' else \
          np.log10(np.clip(mel, 1e-5, None))
    n_frames = mel.shape[1]

    f0 = sing.f0_curve(notes, tempo, start_s, n_frames, HOP / SR)
    out = sess.run(None, {"mel": mel.T[None].astype(np.float32),
                          "f0": f0[None].astype(np.float32)})[0].reshape(-1)
    print(f"  phrase {k}: {n_frames} frames -> {len(out)/SR:.2f}s "
          f"(mel {mel.min():.1f}..{mel.max():.1f}, f0 {f0.min():.0f}-{f0.max():.0f} Hz)")

    at = int(start_s * SR)
    if at < 0: out, at = out[-at:], 0
    end = min(len(track), at + len(out))
    track[at:end] += out[:end-at]

peak = float(np.max(np.abs(track))) or 1.0
sing.write_wav('pout/vocoder-resynth.wav', track / peak * 0.9, SR)
print("peak", round(peak, 3), "-> pout/vocoder-resynth.wav")
