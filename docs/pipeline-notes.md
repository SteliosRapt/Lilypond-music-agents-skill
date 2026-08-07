# What was established about the pipeline

This is the record: what the singing half of the skill does, and — more
valuably — what was established about the DiffSinger models by probing them,
because the ONNX contract is only partly documented in prose and a wrong guess
at a tensor's meaning produces plausible audio rather than an error.

**For changing the code rather than understanding it, read `development.md`**
beside this: the module map, the numbers a change must not move, and what is
still open.

Run `python3 lilypond-music/scripts/dev/selftest.py` before believing any of it.
It builds a stub voicebank, renders a score written to break the pipeline, and
checks 180-odd invariants in about two minutes, against stub banks in both of
the export conventions real banks use. `--video` adds playhead verification;
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

`sing.py` is four modules: `voicebank.py` for a bank on disk, `phonemes.py` for
words and notes to a phoneme timeline, `score_time.py` for moments to seconds,
and `sing.py` itself for the pitch curve and the model calls.

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
  `scripts/dev/vocoder_resynth_check.py`: on `dev/torture.ly`, mel correlation
  0.963 against the mel it was handed (0.944, 0.967 and 0.977 across the three
  phrases) and pitch preserved to a median 3.1 cents. The vocoder half of the
  contract is confirmed, not assumed -- and the check now prints those numbers
  and exits non-zero below 0.90, rather than asking you to listen. Two earlier
  drafts of these notes quoted a single phrase each, which is why one said 0.944
  and another 0.978.
- **Three more banks, since**: CANARY v106, TRITON v106 and LIEE MM 2.8, each
  downloaded from its own GitHub release and put through
  `scripts/dev/bank_check.py`. All four sing, place every vowel on its written
  onset, and track the written notes to a couple of cents under
  `--literal-pitch`. What they cost in code is section 9 below;
  `references/singing-synthesis.md` sections 8 and 9 are the user-facing
  version.
- `dsvariance` **against a real bank at last** -- LIEE MM 2.8 ships one, and it
  predicts all four parameters. It takes the four curves back as *inputs*
  alongside a retake mask, which the stub did not, so the model was being
  declined until that was fixed. The code path is still driven entirely by what
  the model declares and falls back to flat inputs if anything is unrecognised,
  so an unfamiliar variant degrades rather than guesses.
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
  `phonemes.fill_silences()` achieves the same.
- `frame_ms = hop_size / sample_rate * 1000`.
- **`speedup` derivation** with the old discrete acceleration:
  `speedup = max(1, 1000 // steps)`, decremented until `1000 % speedup == 0`.
  With continuous acceleration it wants `steps` and, if `use_variable_depth`,
  `depth` -- float32 for rectified-flow models, int64 (depth×1000, made
  divisible by speedup) for older ones. `sing._depth_input()` branches on the declared
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
  passes. Fixed in `voicebank.Voice._load_vocoder`; don't regress it.

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

1. **`scripts/dev/selftest.py`** -- the whole pipeline, 83 checks, about two
   minutes. Everything below is what it drives.
2. **`scripts/dev/bank_check.py`** -- one bank rather than the pipeline: what it
   declares, which models were fed, where the words came from, and measured
   pitch and vowel placement. Run it first on a bank nobody here has tried.
3. **`scripts/dev/make_stub_bank.py`** builds a bank whose ONNX graphs declare
   the real interface and compute nonsense, including all three predictors. Its
   `dspitch` returns the written pitch plus exactly a quarter tone, so a caller
   that ignores the prediction fails rather than sounding slightly different.
   `--continuous` writes the same bank in the newer export convention, and the
   self-test runs against both.
4. **`scripts/dev/vocoder_resynth_check.py`** drives the real vocoder from a mel
   computed off a known signal. If output ever turns to noise, this says whether
   the vocoder or the acoustic model is at fault. Needs `librosa`.
5. **`--preview`** for anything about alignment, phrasing or note timing.
6. Only then the real bank. `--steps 8` while iterating, 20+ for a take.

Useful checks on real output: sample count should equal
`sum(durations) * hop_size`; each syllable's vowel should start within a frame
or two of its note onset; pitch measured with `librosa.yin` should track the
written notes within a few cents outside portamento.

## 7. What is left

Moved to `development.md` section 4, so the queue of work lives in one place:
`dsvariance` having met exactly one real bank, the pronunciation gap on banks
that ship no English phonemizer plugin, voice-mode crossfading, per-phrase
expressiveness, and notes crossing a barline in the MusicXML.

## 8. Environment notes

- Release *assets* on github.com download fine
  (`github.com/<owner>/<repo>/releases/download/<tag>/<file>`), as does
  `raw.githubusercontent.com` and `pypi.org`. What is currently blocked: the
  GitHub **API** (403), github.com HTML pages from `curl` (403, though the
  WebFetch tool reaches them), and `diffsinger.miraheze.org`, which answers
  automated requests with a bot challenge. Asset filenames therefore have to
  come from a rendered releases page rather than from the API.
- The four banks used, all as `curl -sL -o x.zip <url>`, each a few seconds:
  - `github.com/spicytigermeat/tiger_diffsinger/releases/download/v102/TIGER_DS_v102_PACK.zip` (540 MB)
  - `github.com/spicytigermeat/canary_diffsinger/releases/download/v106/CANARY_DS_v106_PACK.zip` (520 MB)
  - `github.com/spicytigermeat/triton_diffsinger/releases/download/v106/TRITON_DS_v106_PACK.zip` (518 MB)
  - `github.com/julieraptor/DIFFSINGER-LIEE-Immortal-Idol/releases/download/MM2.8/Diffsinger.LIEE.Immortal.Idol.MM.2.8.JubiLIEE.2025.1.1.zip` (283 MB)

  The tigermeat packs hold `Voice Library/` (a zip inside a folder; unzip it and
  point `--voice` at the result) and `OpenUTAU Plugins/`. LIEE's holds one zip
  whose contents are the bank, with its plugins in `Phonemizers/` inside it.
- `bash scripts/setup.sh` installs lilypond, fluidsynth, a GM soundfont and
  ffmpeg. `bash scripts/setup-singing.sh` adds onnxruntime and pyyaml;
  `--dev` also adds `onnx` and `librosa`, which the dev scripts need.
- `.oudep` files are zips. Unzip and point `--vocoder` at the directory that
  directly contains the `.onnx`, not its parent.
- Licensing to carry into any user-facing text: the vocoder is CC BY-NC-SA 4.0;
  TIGER and CANARY are CC BY-NC-ND 4.0 + Commons Clause; TRITON is an MIT-shaped
  licence with a non-commercial clause and a prohibition on training image
  generators on its art; LIEE ships its terms as a scanned PDF in the pack plus
  publishing guidelines in its README (credit the bank, tag `#LIEEREY`). All
  four: non-commercial, and do not redistribute modified weights.

## 9. What the other three banks cost in code

Every one of these was a silent failure -- the render succeeded and sounded
plausible -- which is why each now has a line of output or a check behind it.

- **`dspitch` declined on CANARY and TRITON.** Their `pitch.onnx` declares
  `steps` where TIGER's declares `speedup`, and an unrecognised input makes the
  whole call return None. Both names are offered now
  (`_Model.acceleration()`), and a model that still declines records which
  input it wanted (`_Model.refuses()`), which `sing.py` prints.
- **`dsvariance` declined on LIEE.** Its variance model takes the four
  parameter curves as inputs as well as outputs -- OpenUtau feeds the user's
  curves and a retake mask. Flat zeros go in now, meaning "no user curve",
  which is unity in the log domain.
- **LIEE's phoneme tables are json**, with ids that start at 1. The predictors
  read only line-numbered `phonemes.txt`, so every phone was unknown and each
  model was fed a line of pure silence -- reported, but only as a warning.
- **LIEE ships a dsdict that is not valid yaml** (`dsdict-zh-yue.yaml`, one list
  item outdented by a space). `--inspect` died on it. Dictionary scans are
  tolerant now; configs are still read strictly.
- **The phonemizer plugin was chosen by file size.** With more than one bank
  unpacked side by side, or with a pack that ships several languages, that
  picks the wrong language: CANARY's own pack carries a French phonemizer
  larger than its English one, and it renders "lanterns" as `l en sh ae r n p`
  with no complaint, because French phones are a subset of CANARY's inventory.
  Candidates are now scored against the bank's own dsdict -- the right plugin
  agrees on 68% of 500 shared words, the French one on 2% -- and a bank with no
  matching plugin gets none rather than the best of a bad set.
  `scripts/phonemizer.py <plugin.dll>` also honours an explicit path now; it
  used to search from it and answer about a different plugin.
- **`--depth` was not clamped** to the `max_depth` a continuous-acceleration
  bank declares (0.6 for CANARY and TRITON, where the classic export states the
  same thing as a step count out of 1000).
- **The mel band edges were never compared** when the two configs spelled them
  differently (`fmin` in a bank's own vocoder.yaml, `mel_fmin` in openvpi's):
  the check compared a number against None and passed.

`make_stub_bank.py --continuous` now writes a stub in the newer export
convention -- `steps`, fractional depth, json phoneme tables, variance curves as
inputs -- and `selftest.py` runs the singing half against both stubs, so none of
the above can be dropped again without a failing check.
