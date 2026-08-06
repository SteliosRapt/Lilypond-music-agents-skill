# Handoff: the lilypond-music skill

The job this file originally described -- wiring the DiffSinger `dsdur` and
`dspitch` predictors into `lilypond-music/scripts/sing.py` -- is **done and
verified against TIGER v102**. What follows is the state of the skill, what was
established about the models along the way, and what is left.

**If you have been sent here to improve the code rather than to use it, read
`handoff-refactor.md` instead** -- unit tests, a shared MIDI reader, splitting
`sing.py`, and the one behavioural question still open (section 7 below). This
file is the record of what the skill does and what was established about the
models; that one is the queue of work.

Run `python3 lilypond-music/scripts/dev/selftest.py` before believing any of it.
It builds a stub voicebank, renders a score written to break the pipeline, and
checks 51 invariants in about a minute. `--video` adds playhead verification;
`--voice ~/voices/tiger --vocoder ~/voices/pc_nsf_hifigan` swaps the stub for a
real bank.

---

## 1. Where the pipeline stands

```
score.ly
  → assets/lyrics.ily          instrumented LilyPond run; syllable↔note alignment
  → scripts/vocal_score.py     → <stem>-vocals.json + one .musicxml per verse
  → scripts/sing.py            → <stem>-vocal.wav
  → scripts/render.py --vocal  → mixed mp3/mp4
```

Verified working, with real files:

- The LilyPond extractor, on the vocal template and on `scripts/dev/torture.ly`
  (pickup, mid-score metre *and* tempo changes, ties, melismata, `_`, two
  verses, a bar filled exactly by one whole note). All MusicXML measures
  balance.
- `render.py` end to end on all three templates: PDF, MIDI, mp3, mp4, playhead
  sync verified by sampling frames.
- **The acoustic model, the vocoder, `dsdur` and `dspitch`, with TIGER v102.**
  A real render tracks the written notes to a median 4.3 cents with `dspitch`
  on and 1.1 cents with `--literal-pitch`; every vowel starts exactly on its
  written note onset either way.
- **The vocoder on its own**, by analysis-resynthesis through
  `scripts/dev/vocoder_resynth_check.py`: mel correlation 0.978 against the
  source signal, pitch preserved to a median 1.7 cents. The vocoder half of the
  contract is confirmed, not assumed.
- `dsvariance` against a stub only -- no bank to hand ships one. The code path
  is driven entirely by what the model declares and falls back to flat inputs
  if anything is unrecognised, so an unfamiliar variant degrades rather than
  guesses.
- `scripts/preview_voice.py` (`--preview`): a formant synthesiser needing no
  bank, running the same `phonemize()` and pitch code as the real path.

## 2. What the predictors' tensors mean

Established by probing TIGER directly, not from documentation. The experiments
and the numbers are in the module docstring of `scripts/predictors.py`; the
short version:

- `dur.onnx` → `ph_dur_pred` is **in frames** at the `dsdur` folder's own hop
  size. Per-word sums land near `word_dur` but not on it, so each word is
  normalised onto the time the score gives it.
- `pitch.onnx` → `pitch_pred` is **in MIDI note numbers per frame**, not Hz.
  `expr` is expressiveness in 0..1 (at 0 it reproduces the input curve to 0.5
  cents); `retake` is "predict this frame".
- **Predicted pitch inside a rest is meaningless** -- about MIDI -2, or 4 Hz.
  Those frames keep the written curve.
- Each predictor folder has **its own phoneme table**, and they differ inside
  one bank: TIGER's `dspitch` table is 60 entries where its acoustic table is
  68. Tokenise with the table in the folder you are calling.

**One bound is imposed on the model rather than taken from it.** Asked to
divide a 5.7-second word, TIGER returns 3.2 seconds of `f` for "flame": its
consonant predictions are stable to about a second and a half and diverge past
that. So `dsdur` is asked about a syllable of ordinary length and the surplus
goes to the vowel. `DurationPredictor.MAX_WORD_S` is that bound and the
measurements behind it are beside it.

## 3. The contract that was already established

### Acoustic model (from OpenUtau's `DiffSingerRenderer.cs`, then TIGER)

TIGER v102's actual acoustic inputs: `tokens`, `durations`, `f0`, `gender`
`[1,n_frames]`, `velocity` `[1,n_frames]`, `spk_embed` `[1,n_frames,256]`,
`depth` and `speedup`. **`depth` and `speedup` are declared with shape `[]`** --
true scalars. Passing shape `(1,)` fails with a shape error naming the tensor
but not the cause.

```
tokens      [1, N]  int64    phoneme ids; index into phonemes.txt by line number
durations   [1, N]  int64    phoneme durations IN FRAMES, sum == T
f0          [1, T]  float32  Hz per frame
speedup     [1]     int64    OR steps [1] int64, and depth [1] (float or int64)
→ mel       [1, T, C] float32
```

Optional inputs, supplied only if declared: `languages` `[1,N]` int64,
`spk_embed` `[1,T,D]`, `gender` `[1,T]`, `velocity` `[1,T]`, `energy` /
`breathiness` / `voicing` / `tension` `[1,T]`.

- **Head and tail padding is 8 frames each** (`DiffSingerUtils.headFrames`).
  `sing.py` achieves the same through `fill_silences()`.
- `frame_ms = hop_size / sample_rate * 1000`.
- **`speedup` derivation** with the old discrete acceleration:
  `speedup = max(1, 1000 // steps)`, decremented until `1000 % speedup == 0`.
  With continuous acceleration it wants `steps` and, if `use_variable_depth`,
  `depth` -- float32 for rectified-flow models, int64 (depth×1000, made
  divisible by speedup) for older ones. `sing.py` branches on the declared
  dtype.
- **Variance parameters are log-domain, roughly dB, 0 unity and −96 silence.**
  User deltas: `energy: x + y*12/100`, `breathiness: x + y*12/100`,
  `voicing: x + (y−100)*12/100`, `tension: x + y/20`. `--variance` is applied
  as an offset on top of any `dsvariance` prediction, which matches that.

### Vocoder (confirmed against the 2025.02 file)

```
mel  [1, n_frames, 128] float32
f0   [1, n_frames]      float32
→ waveform [1, n_samples] float32
```

`vocoder.yaml`: 44100 Hz, hop 512, win/fft 2048, 128 mel bins, fmin 40, fmax
16000, **`mel_base: e`**, `mel_scale: slaney`, `pitch_controllable: true`.

- **Mel base conversion**: log10→log-e multiply by `2.30259`; log-e→log10 by
  `0.434294`. Banks built against the 2022/2024 vocoders are log-10; this one
  is log-e.
- **The package ships two yamls.** `vocoder.yaml` has the mel parameters;
  `oudep.yaml` is packaging metadata. Picking the wrong one gives a config with
  no parameters, and the compatibility check then compares `None` to `None` and
  passes. Fixed in `_load_vocoder`; don't regress it.

## 4. The phonemizer plugin -- solved, do not replace with CMUdict

A bank's `dsdict-*.yaml` is a *small* word list: TIGER's holds about 10,000
entries, so "lantern", "silence" and "blossom" all miss. The real pronunciation
data is in the OpenUtau phonemizer plugin shipped beside the voice library
(`OpenUTAU Plugins/diffs_en_tgm_alpha.dll`): a .NET assembly with **a plain zip
appended inside the binary** -- find `PK\x03\x04` and open from there. Inside:
`dict.txt` (133,102 words), `phones.txt` (43 phones with types), `g2p.onnx`.
`scripts/phonemizer.py` implements all of it.

**Do not substitute CMUdict.** This phone set has `dr` and `tr` as single
affricates: "drift" is `dr ih f t`, where CMUdict gives `d r ih f t` -- wrong
symbols and wrong phoneme count.

The G2P model is an **RNN-transducer**: `t` is a position in the input
spelling and the model emits a blank to advance it.

```
graphemes: id = 5 + index into ["'", a..z]     (5 reserved + 27 symbols = 32)
phones:    id = 4 + index into phones.txt      (4 reserved + 43 phones = 47)
blank = 2, and the decoder history starts as [2]
loop: while position < len(word): pred = f(src, history, position)
      pred == blank -> position += 1;  else -> emit and append to history
```

## 5. Bugs found and fixed -- do not reintroduce

- Vocoder config picked from the wrong yaml (section 3).
- MusicXML measures: a bar filled exactly by one whole note used to swallow the
  next bar. Fixed with a deferred `ensure_measure()`.
- **A mid-score metre change was dropped from the MusicXML**, which barred
  everything after it wrongly while the note durations stayed right -- an error
  that is invisible unless you count. `@META` now carries its moment and
  measures are tracked from the last barline, not by `cursor % bar`.
- **A mid-score tempo change silently rescaled the whole sung line.** The
  extractor kept one tempo, and which one depended on event order. It now
  reports the whole tempo map and `sing.py` places notes through a `Clock`.
- LilyPond writes its own progress (`[16]`) onto the same stderr as the
  instrumented report, sometimes *inside* a line. The parser tolerates
  malformed lines; `-dbackend=null` avoids most of it.
- Semivowels (`w`, `y`) must not be treated as the syllable nucleus, or
  "world" becomes `[w][er l d]` and the note lands on the `w`.
- Consonants laid backwards from the beat can squeeze the preceding phoneme to
  zero length. A zero-length phoneme is worse than an absent one -- the model
  still allocates it a frame and a one-frame stop reads as a click. Dropped.
- A phrase whose head padding starts before beat 0 must be *trimmed*, not
  clamped, or the whole phrase slides late.
- `--inspect` walked one directory level, so it never reached
  `dsdur/files/*.onnx` -- exactly the models it exists to describe -- and
  printed 690 KB dictionaries in full. It recurses and summarises now.
- `equalizer=...:t=h:w=0.7` is not a shelf. `t` names the *unit* of the width,
  not the shape; that spelling gives a bell 0.7 Hz wide, which measured as a
  0.02 dB change. Shelves are `highshelf`/`lowshelf`.

## 6. How to test without burning hours

1. **`scripts/dev/selftest.py`** -- the whole pipeline, 51 checks, about a
   minute. Everything below is what it drives.
2. **`scripts/dev/make_stub_bank.py`** builds a bank whose ONNX graphs declare
   the real interface and compute nonsense, including all three predictors. Its
   `dspitch` returns the written pitch plus exactly a quarter tone, so a caller
   that ignores the prediction fails rather than sounding slightly different.
3. **`scripts/dev/vocoder_resynth_check.py`** drives the real vocoder from a mel
   computed off a known signal. If output ever turns to noise, this says whether
   the vocoder or the acoustic model is at fault. Needs `librosa`.
4. **`--preview`** for anything about alignment, phrasing or note timing.
5. Only then the real bank. `--steps 8` while iterating, 20+ for a take.

Useful checks on real output: sample count should equal
`sum(durations) * hop_size`; each syllable's vowel should start within a frame
or two of its note onset; pitch measured with `librosa.yin` should track the
written notes within a few cents outside portamento.

## 7. What is left

- **`dsvariance` has never met a real bank.** The code is written from declared
  interfaces and tested against a stub. When a bank with one turns up, check
  the `retake` axis order and that the returned curves are in the log domain
  the acoustic model expects before trusting the sound.
- **Voice-mode crossfading.** OpenUtau varies `spk_embed` per frame to blend
  modes; one fixed mode is held across a phrase here, because a score has
  nowhere to say otherwise.
- **`--expressiveness` is per render, not per phrase.** A score cannot yet ask
  for a straighter chorus and a freer verse.
- **Notes crossing a barline are not split in the MusicXML.** LilyPond cannot
  write one without a tie, so it does not arise from a valid score, but an
  importer fed a hand-edited JSON could see an over-full measure.

## 8. Environment notes

- GitHub (pages, API and `release-assets.githubusercontent.com`) and `pypi.org`
  are reachable from bash; Hugging Face and Google Drive are not. TIGER:
  `curl -sL -o tiger.zip https://github.com/spicytigermeat/tiger_diffsinger/releases/download/v102/TIGER_DS_v102_PACK.zip`
  -- 566 MB in about seven seconds. The pack holds `Voice Library/` (point
  `--voice` at it, after unzipping the zip inside) and `OpenUTAU Plugins/`.
- `bash scripts/setup.sh` installs lilypond, fluidsynth, a GM soundfont and
  ffmpeg. `bash scripts/setup-singing.sh` adds onnxruntime and pyyaml;
  `--dev` also adds `onnx` and `librosa`, which the dev scripts need.
- `.oudep` files are zips. Unzip and point `--vocoder` at the directory that
  directly contains the `.onnx`, not its parent.
- Licensing to carry into any user-facing text: the vocoder is CC BY-NC-SA 4.0;
  TIGER is CC BY-NC-ND 4.0 + Commons Clause. Non-commercial, and do not
  redistribute modified weights.
