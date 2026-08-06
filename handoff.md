# Handoff: wiring the DiffSinger predictors into the lilypond-music skill

You have been given three files:

| File | What it is |
|---|---|
| `lilypond-music.skill` | The skill bundle. A zip; unzip it and work inside `lilypond-music/`. |
| `pc_nsf_hifigan_44_1k_hop512_128bin_2025_02.oudep` | The vocoder. A zip with a renamed suffix. |
| A voicebank zip (TIGER or similar) | The acoustic model, predictors, phoneme table and dictionary. |

**The job:** wire in the `dsdur` and `dspitch` predictors. The acoustic model,
the vocoder and the bank's own phonemizer plugin are all working and verified
against TIGER v102 — that happened after this document was first drafted, so
treat sections 3–5 as settled fact rather than as things to establish.

**You can download banks directly.** `github.com`, `api.github.com` and
`release-assets.githubusercontent.com` are all reachable from bash. TIGER:

```bash
curl -sL -o tiger.zip https://github.com/spicytigermeat/tiger_diffsinger/releases/download/v102/TIGER_DS_v102_PACK.zip
```

566 MB in about seven seconds. An earlier draft of this file claimed the
release CDN was blocked; that was wrong and cost a session's worth of work.
The release page's asset list is at
`https://github.com/OWNER/REPO/releases/expanded_assets/TAG` if you need to
find a filename.

Read this whole file before writing code. Section 8 is the actual plan.

---

## 1. Where the pipeline stands

```
score.ly
  → assets/lyrics.ily          instrumented LilyPond run; reports syllable↔note alignment
  → scripts/vocal_score.py     → <stem>-vocals.json + one .musicxml per verse
  → scripts/sing.py            → <stem>-vocal.wav
  → scripts/render.py --vocal  → mixed mp3/mp4
```

Verified working, with real files, in the previous session:

- The LilyPond extractor, on a real vocal template and a torture case (ties,
  slurs, two verses, `_` skips, melismata). All MusicXML measures balance.
- `sing.py` end to end against a synthetic bank built by
  `scripts/dev/make_stub_bank.py` — real ONNX graphs declaring the real
  interface. This caught three genuine bugs and is the recommended way to test
  anything new before touching the 500 MB bank.
- The **real vocoder**, by analysis-resynthesis: mel computed from a known
  signal with the release's own parameters, fed back with the pitch curve. Mel
  correlation 0.944, pitch preserved to a median 1.8 cents. The vocoder half of
  the contract is confirmed, not assumed.
- `scripts/preview_voice.py`: a formant synthesiser (`--preview`) that needs no
  bank. It runs the same `phonemize()` and `f0_curve()` as the real path, so it
  is the fast way to check syllable alignment and phrasing.

- **The acoustic model, with TIGER v102.** A real render of the test score
  tracks the written notes to a median 3 cents. Both `tiger-vocal-solo.mp3` and
  the mixed `tiger-with-ensemble.mp3` came out of this pipeline.
- **The bank's phonemizer**, via `scripts/phonemizer.py` — see section 5a.

**Never verified:** the duration and pitch predictors, which are not called.

## 2. Why the predictors are missing, and why that was wrong

The previous session justified skipping them on the grounds that "a score
already states durations and pitch." That reasoning is wrong and should not be
repeated:

- **`dsdur` does not predict note durations.** It predicts *phoneme* durations
  within each note — how a syllable's time divides between consonant and vowel,
  and how far a consonant leads the beat. `sing.py` currently hardcodes this as
  a table of constants (`CONSONANT_S`: stop 55 ms, fricative 90 ms, …) scaled
  into whatever gap exists. That is a crude stand-in for a model trained on
  this singer. **Replace it.**
- **`dsvariance` fills inputs the acoustic model is asking for.** When the
  acoustic model declares `energy`, `breathiness`, `voicing` or `tension`,
  `sing.py` currently supplies flat zeros. This is the single clearest quality
  loss in the pipeline. **Replace it.**
- **`dspitch` renders that singer's expressive deviation from the written
  notes**, conditioned on those notes — it is not guessing the melody. It
  should replace the synthetic portamento and vibrato in `f0_curve()`, with one
  caveat worth keeping as a flag rather than a decision: this skill's other
  half draws a playhead on exact note onsets in a video, and a model that
  scoops hard into notes can visibly disagree with it. Default to the model,
  keep `--literal-pitch` to fall back to `f0_curve()`.

The real blocker was that the exact ONNX tensor contract for these models could
not be established: GitHub refused source fetches of `DiffSingerVariance.cs`
and `DiffSingerPitch.cs`, and reachable documentation describes the
architecture but not the input names. **Do not guess the semantics.** Names can
be read from the file; meanings cannot. Whether `word_dur` is in frames or
seconds, at whose hop size, and how `word_div` encodes syllable boundaries are
exactly the things that fail silently — plausible audio, wrong timing.

## 3. Start here: read the interface off the models

```bash
python3 scripts/sing.py --voice ~/voices/tiger --vocoder ~/voices/pc_nsf_hifigan --inspect
```

This prints `dsconfig.yaml`, the dictionary and phoneme table sizes, any
speaker embeddings with their dimensions, every sub-config in `dsdur/`,
`dspitch/` and `dsvariance/`, and for every `.onnx` in the bank its inputs and
outputs with dtypes and shapes. That output is the specification. Work from it,
not from this document, where the two disagree.

## 4. The contract that IS established

### Acoustic model (confirmed from OpenUtau's `DiffSingerRenderer.cs`, then against TIGER)

TIGER v102's actual acoustic inputs: `tokens`, `durations`, `f0`, `gender`
`[1,n_frames]`, `velocity` `[1,n_frames]`, `spk_embed` `[1,n_frames,256]`,
`depth` and `speedup`. **`depth` and `speedup` are declared with shape `[]`** —
true scalars. Passing shape `(1,)` fails with a shape error naming the tensor
but not the cause. `sing.py` now matches the declared rank.


```
tokens      [1, N]  int64    phoneme ids; index into phonemes.txt by line number
durations   [1, N]  int64    phoneme durations IN FRAMES, sum == T
f0          [1, T]  float32  Hz per frame
speedup     [1]     int64    OR steps [1] int64, and depth [1] (float or int64)
→ mel       [1, T, C] float32
```

Optional inputs, supplied only if the model declares them: `languages` `[1,N]`
int64, `spk_embed` `[1,T,D]` float32, `gender` `[1,T]`, `velocity` `[1,T]`,
`energy` / `breathiness` / `voicing` / `tension` `[1,T]` float32.

Details that matter:

- **Head and tail padding is 8 frames each** (`DiffSingerUtils.headFrames`).
  OpenUtau prepends and appends an `SP` token with those durations. `sing.py`
  achieves the same through `fill_silences()`.
- `frame_ms = hop_size / sample_rate * 1000`.
- **`speedup` derivation**, when the model uses the old discrete acceleration:
  `speedup = max(1, 1000 // steps)`, then decrement until `1000 % speedup == 0`.
  With continuous acceleration it wants `steps` and, if `use_variable_depth`,
  `depth` — as float32 for rectified-flow models, int64 (depth×1000, made
  divisible by speedup) for the older ones. `sing.py` already branches on the
  declared dtype.
- **Variance parameter domain**: these are log-domain, roughly dB, where **0 is
  unity and −96 is silence** (OpenUtau normalises for display as
  `clamp(x,−96,0)/96 + 1`; tension as `clamp(x,−10,10)/20 + 0.5`). User deltas
  are applied as `energy: x + y*12/100`, `breathiness: x + y*12/100`,
  `voicing: x + (y−100)*12/100`, `tension: x + y/20`. So the current flat `0`
  default means "full", which is why it sounds flat rather than silent.

### Vocoder (confirmed against the actual 2025.02 file)

```
mel  [1, n_frames, 128] float32
f0   [1, n_frames]      float32
→ waveform [1, n_samples] float32
```

`vocoder.yaml` from the supplied release: 44100 Hz, hop 512, win/fft 2048, 128
mel bins, fmin 40, fmax 16000, **`mel_base: e`**, `mel_scale: slaney`,
`pitch_controllable: true`.

- **Mel base conversion**: log10→log-e multiply by `2.30259`; log-e→log10 by
  `0.434294`. Banks built against the 2022/2024 vocoders are log-10; this one
  is log-e. `sing.py` converts, but only from what the configs declare.
- **The package ships two yamls.** `vocoder.yaml` has the mel parameters;
  `oudep.yaml` is OpenUtau packaging metadata. Picking the wrong one gives a
  config with no parameters, and the compatibility check then compares `None`
  to `None` and passes. Already fixed in `_load_vocoder`; don't regress it.
- `pitch_controllable` means OpenUtau feeds the *formant-shifted* f0 to the
  acoustic model and the unshifted f0 to the vocoder. With no gender/tone
  shift these are the same array, which is what `sing.py` does.

### What the predictor folders contain (from the OpenUtau voicebank-dev wiki)

Each predictor lives in its own subfolder with its own `dsconfig.yaml`:

```
dsdur/       dsconfig.yaml: phonemes, linguistic: linguistic.onnx, dur: dur.onnx,
             hop_size, sample_rate, predict_dur: true
dspitch/     linguistic.onnx, pitch.onnx, phonemes.txt
dsvariance/  linguistic.onnx, variance.onnx, plus predict_energy /
             predict_breathiness / predict_voicing / predict_tension flags
```

Two things stated explicitly by that wiki:

- A **linguistic encoder runs first** and its output feeds the predictor. So the
  chain is `linguistic.onnx → dur.onnx` / `→ pitch.onnx` / `→ variance.onnx`,
  not a single call.
- **`linguistic.onnx` and `pitch.onnx` from different banks cannot be mixed.**
  Copy whole folders, never individual files. Each folder has its own
  `hop_size`, which may differ from the acoustic model's — OpenUtau has a
  `ResampleCurve` step precisely because variance and acoustic hops differ.
  **Resample predicted curves to the acoustic model's frame count.**

### The predictor interfaces, read from TIGER v102 itself

```
dsdur/files/linguistic.onnx
  IN  tokens [1,n_tokens] int64 · word_div [1,n_words] int64 · word_dur [1,n_words] int64
  OUT encoder_out [1,n_tokens,256] float · x_masks [1,n_tokens] bool
dsdur/files/dur.onnx
  IN  encoder_out · x_masks · ph_midi [1,n_tokens] int64 · spk_embed [1,n_tokens,256]
  OUT ph_dur_pred [1,n_tokens] float
dspitch/files/linguistic.onnx
  IN  tokens [1,n_tokens] int64 · ph_dur [1,n_tokens] int64
  OUT encoder_out · x_masks
dspitch/files/pitch.onnx
  IN  encoder_out · ph_dur · note_midi [1,n_notes] float · note_rest [1,n_notes] bool
      · note_dur [1,n_notes] int64 · pitch [1,n_frames] float · expr [1,n_frames] float
      · retake [1,n_frames] bool · spk_embed [1,n_frames,256] · speedup scalar int64
  OUT pitch_pred [1,n_frames] float
```

Note the two `linguistic.onnx` files differ: the duration one takes word
divisions and word durations (it does not yet know phoneme durations, that
being what it is predicting), the pitch one takes phoneme durations directly.
`dspitch/dsconfig.yaml` declares `use_note_rest: true` and `use_expr: true`,
which is why those tensors are present; a bank without them will not have
them. TIGER's `dsdur` has seven speaker embeddings, its `dspitch` only one.

TIGER has no `dsvariance/` at all, and its root config sets
`use_energy_embed: false` and `use_breathiness_embed: false`, so the variance
work does not apply to this bank. Keep the code path optional.

## 5. Sources, and what each one actually gave

Do not re-search these; this is what they yielded.

- **`github.com/stakira/OpenUtau/blob/cc700f92/OpenUtau.Core/DiffSinger/DiffSingerRenderer.cs`**
  — the whole acoustic and vocoder contract in section 4. The single most
  valuable source. *Fetchable via the DeepWiki page's links; direct guesses at
  blob URLs were refused.*
- **`deepwiki.com/stakira/OpenUtau/4.2-diffsinger-integration`** — component
  map, and it is where the fetchable blob links to the `.cs` files live. Start
  here to reach `DiffSingerVariance.cs` and `DiffSingerPitch.cs`, which are
  what you still need.
- **`.../DiffSingerUtils.cs`** — `headFrames`/`tailFrames` = 8,
  `ResampleCurve`, phoneme-table loading (txt: line number is the id; json: a
  name→id dict).
- **OpenUtau voicebank-development wiki** (mirrored at
  `github-wiki-see.page/m/xunmengshe/OpenUtau/wiki/Voicebank-Development` and
  `.../Phonemizer`) — the `dsdict.yaml` format (`symbols` with types
  vowel/stop/affricate/aspirate/liquid/nasal/fricative/semivowel, plus
  `entries` mapping grapheme→phonemes, plus optional `replacements`), the
  `dsconfig.yaml` fields, and the predictor folder layouts above.
- **`github.com/openvpi/vocoders/releases`** — the vocoder. CC BY-NC-SA 4.0.
- **`github.com/spicytigermeat/tiger_diffsinger`** — TIGER. Licensed
  **CC BY-NC-ND 4.0 + Commons Clause**; multi-speaker ("voice modes"). The ND
  term matters: do not redistribute modified models.
- **`github.com/Jobsecond/diffsinger-onnx-infer`** — a C++ CLI taking a `.ds`
  file with `--dur --variance --pitch` flags. **Archived Sept 2024, Windows
  only, no releases.** Worth reading as a second opinion on the contract; not
  worth depending on.
- **`github.com/openvpi/DiffSinger`** (docs/BestPractices.md) — the `.ds` JSON
  format, phoneme id assignment (one padding index before real phonemes).
- Dead end, do not retry: **`python-ly`'s MusicXML exporter**. It silently
  drops `\lyricsto` lines and crashes on `\score { \new Staff … \addlyrics … }`
  with `AttributeError: 'list' object has no attribute 'pickup'`. This is why
  the pipeline extracts from LilyPond directly instead of converting.

## 5a. The phonemizer plugin — solved, do not replace with CMUdict

A bank's `dsdict-*.yaml` is a *small* word list: TIGER's holds about 10,000
entries, so "lantern", "silence", "blossom" and "flame" all miss. That is not a
broken bank. The real pronunciation data lives in the OpenUtau phonemizer
plugin the pack ships beside the voice library
(`OpenUTAU Plugins/diffs_en_tgm_alpha.dll`).

Those plugins are .NET assemblies with **a plain zip appended inside the
binary** — find `PK\x03\x04` and open from there. Inside:

- `dict.txt` — 133,102 words in the bank's own phone set
- `phones.txt` — 43 phones with articulation types
- `g2p.onnx` — a neural grapheme-to-phoneme model for everything else

`scripts/phonemizer.py` implements all of this and is already wired into
`sing.py` (auto-discovered, or `--phonemizer PATH`).

**Do not substitute CMUdict.** This phone set has `dr` and `tr` as single
affricates: "drift" is `dr ih f t`, where CMUdict gives `d r ih f t` — wrong
symbols and wrong phoneme count against the bank's inventory.

The G2P model is an **RNN-transducer**, not an attention seq2seq, which is why
naive decoding produces garbage. `t` is a position in the input spelling;
the model emits a blank to advance it. Conventions, recovered from the graph's
vocabulary sizes (encoder 32, decoder 47) and checked against the bundled
dictionary:

```
graphemes: id = 5 + index into ["'", a..z]     (5 reserved + 27 symbols = 32)
phones:    id = 4 + index into phones.txt      (4 reserved + 43 phones = 47)
blank = 2, and the decoder history starts as [2]
loop: while position < len(word): pred = f(src, history, position)
      pred == blank -> position += 1;  else -> emit and append to history
```

## 6. Bugs already found and fixed — do not reintroduce

- Vocoder config picked from the wrong yaml (see section 4).
- MusicXML measures: a bar filled exactly by one whole note used to swallow the
  next bar. Fixed with a deferred `ensure_measure()`.
- LilyPond writes its own progress (`[16]`) onto the same stderr as the
  instrumented report, sometimes *inside* a line. The parser tolerates
  malformed lines; `-dbackend=null` avoids most of it.
- Semivowels (`w`, `y`) must not be treated as the syllable nucleus, or
  "world" becomes `[w][er l d]` and the note lands on the `w`. OpenUtau's
  dictionary format calls them semivowels; English treats them as onsets.
- Consonants laid backwards from the beat can squeeze the preceding phoneme to
  zero length. A zero-length phoneme is worse than an absent one — the model
  still allocates it a frame and a one-frame stop reads as a click. They are
  dropped instead.
- A phrase whose head padding starts before beat 0 must be *trimmed*, not
  clamped, or the whole phrase slides late.

## 7. How to test without burning hours

1. **`scripts/dev/make_stub_bank.py`** builds a bank whose ONNX graphs declare
   the real interface and compute nonsense. Extend it with `linguistic.onnx`,
   `dur.onnx`, `pitch.onnx` and `variance.onnx` stubs once `--inspect` has told
   you their real signatures. Test shape and frame arithmetic here first — it
   runs in seconds.
2. **`scripts/dev/vocoder_resynth_check.py`** drives the real vocoder from a mel
   computed off a known signal. If output ever turns to noise, run this to
   determine whether the vocoder or the acoustic model is at fault.
3. **`--preview`** for anything about alignment, phrasing or note timing. No
   models, instant.
4. Only then the real bank. Use `--steps 8` while iterating; 20+ for a take.
   CPU inference is minutes per phrase on a large acoustic model.

Useful checks on real output: the number of samples should equal
`sum(durations) * hop_size`; the vowel of each syllable should start within a
frame or two of its note onset (compare against the JSON); pitch measured with
`librosa.yin` should track the written notes within a few cents outside
portamento.

## 8. The plan

**Step 1 — the linguistic encoder.** Shared by both predictors, and where the
`word_div` / `word_dur` encoding is decided. `word_div` is the count of
phonemes per word and `word_dur` the word's length in frames, but verify that
against the model rather than trusting this sentence: feed a known one-word
phrase and check `encoder_out` is not degenerate. Both folders declare
`hop_size: 512`, matching the acoustic model, so no curve resampling is needed
for TIGER — do not assume that for other banks.

**Step 2 — `dsdur`.** Replace `CONSONANT_S` and the hand-rolled lead-in scaling
in `phonemize()`. The model returns phoneme durations; the existing code
already knows how to turn a phoneme timeline into frame durations, so the
change is narrower than it looks. Keep `--literal-timing` to fall back, and
keep the constants as the fallback path for banks with no `dsdur/`.

**Step 3 — `dsvariance`** (skip for TIGER, which ships none). Feed its outputs into the acoustic model's
`energy`/`breathiness`/`voicing`/`tension` inputs instead of flat zeros,
resampled to the acoustic frame count. Keep `--variance E,B,V,T` as an additive
offset on top of the prediction rather than a replacement — that matches
OpenUtau's delta functions in section 4.

**Step 4 — `dspitch`.** Replace `f0_curve()` on the main path. Keep
`f0_curve()` behind `--literal-pitch`, both for banks without `dspitch/` and
for scores being turned into playhead videos.

**Step 5 — re-verify and document.** Re-run the stub tests, then a real render.
Update `references/singing-synthesis.md` section 7 ("What is not modelled"),
which currently states these predictors are deliberately unused — that text is
now wrong and is the first thing a future reader will believe.

## 9. Suggested flags, once wired

```
--literal-timing     use the constant table instead of dsdur
--literal-pitch      use the synthetic portamento/vibrato instead of dspitch
--variance E,B,V,T   offsets applied on top of dsvariance (default 0,0,0,0)
--voice-mode NAME    multi-speaker banks; already implemented
--steps N            diffusion steps (8 to audition, 20-40 for a take)
--depth D            shallow diffusion depth, if the model exposes it
```

Defaults should use every model the bank provides, and fall back silently and
report what it fell back to. A bank without `dspitch/` is common; a run that
quietly sounds worse because a folder was missing is not acceptable — print
which predictors were used.

## 10. Environment notes

- GitHub (pages, API and `release-assets.githubusercontent.com`) and `pypi.org`
  are reachable from bash; Hugging Face and Google Drive are not. Banks hosted
  on GitHub releases can just be downloaded — see the top of this file.
- `bash scripts/setup-singing.sh` installs `onnxruntime`, `pyyaml`, `numpy`.
  `librosa` is needed only for `vocoder_resynth_check.py`.
- `.oudep` files are zips. Unzip and point `--vocoder` at the directory that
  directly contains the `.onnx`, not its parent.
- Licensing to carry into any user-facing text: the vocoder is CC BY-NC-SA 4.0;
  TIGER is CC BY-NC-ND 4.0 + Commons Clause. Non-commercial, and do not
  redistribute modified weights.
