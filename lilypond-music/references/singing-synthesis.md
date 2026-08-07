# Singing the vocal line

Contents:
1. What this pipeline is
2. Getting a voicebank
3. Running it
4. How the score becomes phonemes
5. How the pitch curve is built
6. Writing lyrics that sing well
7. What is not modelled
8. What differs between banks
9. Qualifying a bank you have not used before
10. Several banks at once: a cappella and choral work
11. Troubleshooting
12. Why not MusicXML conversion
13. The other engines, and why English narrows the field

---

## 1. What this pipeline is

    score.ly  --(lyrics.ily)-->  note/syllable table
              --(vocal_score.py)-->  score-vocals.json + one .musicxml per verse
              --(sing.py)-->  score-vocal.wav
              --(render.py --vocal)-->  score.mp3 / score.mp4 with the singer in it

`sing.py` drives a **DiffSinger** voicebank directly through onnxruntime: the
same ONNX files OpenUtau loads, minus the GUI. DiffSinger is the open singing
synthesiser with a real English voicebank ecosystem, which is the only reason it
is the one wired up here (see section 13).

`sing.py` is the command; behind it are `voicebank.py` (a bank on disk, and the
built-in preview voice), `phonemes.py` (words and notes to a phoneme timeline),
`score_time.py` (score moments to seconds) and `predictors.py` (the bank's
optional models). Nothing below names them — everything in this document is
about the command line — but that is where to look when a section says "the
pipeline does X".

The vocal is rendered as a separate wav aligned to beat 0, then mixed with the
fluidsynth instrumental. Keeping it separate is deliberate: it can be balanced
with `--vocal-gain`, replaced without re-rendering the instruments, or handed to
a DAW.

## 2. Getting a voicebank

Not bundled, not downloaded automatically, and this matters legally rather than
technically: **most English DiffSinger banks are non-commercial**, several
forbid redistribution, and the community NSF-HiFiGAN vocoders are
CC BY-NC-SA 4.0. Read the terms of the specific bank before publishing anything
made with it, and do not use one to imitate a real person's voice.

### Where to get one

**Banks that have actually been run through this pipeline.** Each was
downloaded from its own GitHub release and checked with
`scripts/dev/bank_check.py` (section 9); the numbers in the last column are that
tool's output. They are listed because they were tested, not because they are
the best-sounding — that is a matter of taste and of the voice you want.

| bank | release | size | what it exercises | result |
|---|---|---|---|---|
| **TIGER** v102 | `spicytigermeat/tiger_diffsinger`, tag `v102`, `TIGER_DS_v102_PACK.zip` | 540 MB | the classic export; 7 voice modes; `dsdur` + `dspitch` | everything used, notes tracked to a few cents |
| **CANARY** v106 | `spicytigermeat/canary_diffsinger`, tag `v106`, `CANARY_DS_v106_PACK.zip` | 520 MB | continuous-acceleration export; 3 modes | same, once `dspitch` was taught the newer input name |
| **TRITON** v106 | `spicytigermeat/triton_diffsinger`, tag `v106`, `TRITON_DS_v106_PACK.zip` | 518 MB | continuous acceleration; 4 modes | same |
| **LIEE : Immortal Idol** MM 2.8 | `julieraptor/DIFFSINGER-LIEE-Immortal-Idol`, tag `MM2.8` | 283 MB | json phoneme tables, a real `dsvariance`, no voice modes, log-e mel, 18 language dictionaries | sings and times correctly; **no English phonemizer plugin ships with it**, so pronunciations are guessed unless you supply them |

Download them with `curl -L -o bank.zip <release-asset-url>`; the release
assets are plain files. What is inside is a pack rather than a bank: a `Voice
Library/` folder holding another `.zip` (that one is the bank) and an `OpenUTAU
Plugins/` folder next to it. Unzip both, keep them together, and point
`--voice` at the directory that has `dsconfig.yaml` directly inside it.

**Keep each bank in its own directory.** `sing.py` looks for the bank's
phonemizer plugin in the directory containing the bank, so two banks unpacked
side by side put four plugins in scope. It now picks by checking each candidate
against the bank's own dictionary rather than by size, but the layout that
cannot go wrong is one bank per folder.

**The vocoder** — https://github.com/openvpi/vocoders/releases. Take the newest
release's `.oudep` attachment; it is a zip, so rename and unzip it. The current
line is PC-NSF-HiFiGAN (44.1 kHz, hop 512, 128 mel bins); the 2024.02
NSF-HiFiGAN release is also fine. Note that some are trained on log-e mel
spectrograms and others on log-10 — the `mel_base` field in the two config files
says which, and `sing.py` converts between them, but only if the configs are
honest about it.

All four banks above ship their own vocoder in `dsvocoder/`, which is used
automatically and is the one the bank was trained against; `--vocoder` is for
banks that do not. Check the bank's own docs before substituting one — a few
are trained against fish-diffusion's HiFi-GAN rather than openvpi's, and the
wrong one produces noise rather than a worse voice.

**The wiki index is not currently usable from a script.** The DiffSinger Wiki's
"Voicebanks supporting English" category (diffsinger.miraheze.org) is still the
community index, but it now answers automated requests with a bot challenge, so
it cannot be fetched from here — expect to open it in a browser, or to find
banks through their authors' GitHub releases as above.

You need two things:

- **A bank** — a directory containing `dsconfig.yaml`, an acoustic `.onnx`, a
  phoneme table and a `dsdict*.yaml` dictionary. English banks are distributed
  as `.zip` or `.oudep` files (an `.oudep` is a zip; rename it). Look for banks
  advertising an ARPAbet or "ARPA+" phoneme set.
- **A vocoder package** — usually `nsf_hifigan`, from openvpi's vocoder
  releases, or the `dsvocoder/` folder the bank already carries.
- **A phonemizer plugin** for the bank's language, which is where a bank's
  actual vocabulary lives — its `dsdict` is a few hundred to ten thousand
  words, and the plugin carries 130,000. It is usually the `.dll` in the pack's
  `OpenUTAU Plugins/` folder. Section 8 is about picking the right one.

The minimal layout, and the four names every part of it also goes by:

```
mybank/
  dsconfig.yaml      acoustic:, phonemes:, hop_size, sample_rate, speakers...
  acoustic.onnx      or dsacoustic/<name>.onnx, wherever dsconfig points
  phonemes.txt       one phoneme per line, line number = token id
                     (or *.phonemes.json, an object of explicit ids)
  dsdict-en.yaml     symbols (phoneme -> type) + entries (word -> phonemes)
  dsvocoder/         the bank's own vocoder, used in preference to --vocoder
    tgm_hifigan.onnx
    vocoder.yaml     must agree with dsconfig on sample_rate, hop_size,
                     mel bins and the mel band edges
  dsdur/ dspitch/ dsvariance/    optional predictors, each self-contained
```

The sanity check the script performs first is that mel parameters match: an
acoustic model and a vocoder trained on different mel definitions do not sound
worse, they produce noise.

Dependencies: `bash scripts/setup-singing.sh` (onnxruntime, pyyaml, numpy).
CPU-only is fine — a 30-second line takes a couple of minutes at 20 steps.

## 3. Running it

Audition first, with no voicebank at all:

```bash
python3 scripts/sing.py score.ly --preview -o out/
```

`--preview` sings the line through `scripts/preview_voice.py`, a formant
synthesiser with letter-to-sound rules instead of a dictionary. It sounds
like a robot choir, deliberately: everything it gets right is everything the
pipeline decides *before* the neural model is involved -- syllable placement,
melismata, phrasing, portamento, vibrato -- because it runs the same
`phonemize()` and `f0_curve()` the voicebank path runs. A syllable landing late
here lands late in the real render. Timbre and diction are the voicebank's job
and the preview says nothing about them.

Then the real thing:

```bash
python3 scripts/vocal_score.py score.ly -o out/            # inspect the extraction
python3 scripts/sing.py score.ly --voice ~/voices/mybank -o out/
python3 scripts/render.py score.ly --vocal out/score-vocal.wav --vocal-gain -1
```

Useful flags:

| flag | what it does |
|---|---|
| `--inspect` | print everything the bank declares -- configs, tables, every model's ONNX interface -- and stop. The first thing to run on an unfamiliar bank. |
| `--line 2` | sing the second verse |
| `--steps 40` | diffusion steps; 8 to 20 is enough to audition, 20-40 for a take |
| `--depth 0.6` | shallow diffusion depth, where the model exposes it |
| `--voice-mode NAME` | for multi-speaker banks |
| `--literal-timing` | ignore `dsdur`; split syllables with the built-in constants |
| `--literal-pitch` | ignore `dspitch`; follow the written notes with synthetic portamento and vibrato. **Use this for a score video.** |
| `--expressiveness 0.5` | how far `dspitch` may depart from the written notes, 0 to 1 |
| `--variance E,B,V,T` | offsets in dB on the variance curves (0 is unity, -96 silence) |
| `--no-vibrato` | flat held notes on the fallback pitch path |
| `--vocoder` | when the vocoder lives outside the bank |
| `--phonemizer PATH` | the plugin to pronounce with, when the automatic choice is wrong or there is none nearby |

A run prints what it is singing with:

```
  voice   tiger: 68 phonemes, 10075 dictionary entries, 44100 Hz
  modes   tiger_fresh, tiger_disco, ...  -> singing as tiger_fresh
  words   diffs_en_tgm_alpha.dll, agreeing with the bank's own dictionary on 68% of 500 words
  models  acoustic + vocoder, plus dsdur, dspitch
```

If a model is present and was not used, it says so, says what was used instead,
and names the input it could not supply. That line is worth reading: everything
else about a degraded render sounds plausible.

The `words` line is the other one to read. It names the phonemizer plugin that
will pronounce anything outside the bank's own dictionary, and how far that
plugin and the bank agree about the words they both know — high means the right
plugin for this bank, low would mean a plugin for another language, and
`no phonemizer plugin matches this bank` means every unlisted word is being
spelled out by rule. Section 8 explains why that number exists.

`sing.py` accepts either the `.ly` (it runs the extraction for you) or an
existing `score-vocals.json`, which is the faster loop when you are only
adjusting synthesis settings.

## 4. How the score becomes phonemes

`assets/lyrics.ily` reports LilyPond's own syllable-to-note assignment, so
melismata, ties, `--`, `__` and `_` are resolved by the program that already
had to resolve them in order to print the page. Everything downstream works
from that table. See the header of that file for the reporting format.

The steps that follow are where the singing-specific decisions live:

**Words, not syllables, are looked up.** `Lan -- terns` is two syllables on two
notes, but "lan" and "terns" phonemise to nothing like "lanterns". The word is
reassembled from the hyphens, looked up once, and the resulting phonemes are
split back across the notes by maximal onset: consonants between two vowels
belong to the syllable that follows, except that a cluster of two or more
leaves one behind as a coda.

**Vowels land on the beat; consonants arrive early.** A singer starts the "l"
of "lantern" before the downbeat so the vowel is on it. Onset consonants are
therefore laid down backwards from the note onset, taking their time from
whatever precedes them, scaled down if there is not enough room. This single
detail is most of the difference between "on the beat" and "late".

**A melisma is a held vowel.** A note carrying no syllable extends the previous
vowel rather than restarting it, so no consonant is re-articulated.

**Rests longer than 0.6s split the line into phrases**, each rendered
separately with its own head and tail padding. Long silences inside one
inference are wasted computation and tend to destabilise the model.

## 5. How the pitch curve is built

Two ways, depending on what the bank has.

**With a `dspitch` model** (the default where one exists) the written pitch line
is handed to the model as a per-frame curve and the model returns its own
version of it: the scoop into a phrase, the drift on a held note, the vibrato
that singer actually uses. It is a deviation *around* the notes, not a melody —
`--expressiveness 0` reproduces the written line to a fraction of a cent, and
1.0, the default, departs from it by a median of about 20 cents.

One detail matters and is not obvious: **inside a rest the model's output is not
a pitch at all.** TIGER returns about MIDI -2, roughly 4 Hz. Those frames are
replaced with the written line and eased back over a few frames, so the vocoder
never sees the step.

**Without one**, or under `--literal-pitch`, three things are layered onto the
note pitches by hand:

- **Portamento** across every change of note, 40 ms for a step and up to 110 ms
  for a wide leap, on a smoothstep rather than a line.
- **Vibrato** on notes held longer than 0.55s: about 5.4 Hz, ±35 cents, fading
  in over the third of a second after the onset, so short notes stay straight.
- **Drift** of a few cents, smoothed over half a second, so sustained notes are
  not mathematically level.

All three are seeded and deterministic — the same score renders the same take.
That, and the fact that they never scoop, is why this path is the right one for
a score-following video even when the bank has something better.

**Tempo changes are followed.** Note times come from the score's whole tempo
map rather than one number, so a staged `rit.` written as several `\tempo`
marks places the sung line the same way LilyPond's own MIDI does. A single
tempo would put every note before the first change at the wrong time.

## 6. Writing lyrics that sing well

- **Hyphenate on singable syllables**, not etymological ones: `si -- lence`,
  not `sil -- ence`. The hyphenation decides which consonant is heard on which
  note.
- **Spell out anything unusual.** Words missing from the bank's dictionary are
  reported by name after the render; the fix is to respell them phonetically in
  `\lyricmode` or to add them to a copy of the bank's dsdict yaml.
- **Give the singer somewhere to breathe.** A rest over 0.6s becomes a phrase
  boundary; a vocal line written with no rests at all is sung in one
  unrelenting stream.
- **Keep the line in the bank's range.** Banks are trained on a recorded range,
  usually about two octaves, and pitches far outside it degrade rather than
  transpose.
- **`_` in `\lyricmode` means "no syllable here"**, and reaches this pipeline
  as a syllable whose text is a single space. It is dropped, and the note
  becomes a melisma on the vowel before it.

## 7. What is and is not modelled

The bank ships more than the acoustic model, and the pipeline uses all of it.

| Model | Used | Notes |
|---|---|---|
| acoustic | yes | verified against TIGER v102 |
| vocoder | yes | verified by analysis-resynthesis, 0.963 mel correlation (`dev/vocoder_resynth_check.py`) |
| phonemizer plugin | yes | see `scripts/phonemizer.py` |
| `dsdur` | yes | phoneme durations within a note; replaces the `CONSONANT_S` table |
| `dspitch` | yes | expressive f0 around the written notes; replaces `f0_curve()` |
| `dsvariance` | yes, where a bank has one | verified against LIEE MM 2.8. TIGER, CANARY and TRITON have none: they set `use_energy_embed: false` and ship no `dsvariance/` |

Each is optional, each falls back to the built-in approximation, and the run
prints which ones it actually used. `--literal-timing` and `--literal-pitch`
force the fallbacks.

An earlier version of this document argued the duration and pitch predictors
were unnecessary because the score states durations and pitches. That was
wrong, and it is worth restating why, because it is the whole reason they are
worth calling. `dsdur` predicts *phoneme* durations inside each note -- how a
syllable splits between consonant and vowel -- which the score says nothing
about. `dspitch` renders the singer's deviation around notes it is given, not a
melody of its own. Neither is being asked to guess something the score already
knows.

### What the models are told, and what they are left to decide

The division is deliberate and it is where the timing correctness lives:

- `dsdur` is given each syllable's phonemes and **the length the score gives
  that syllable**, and returns only the split inside it. The vowel still starts
  exactly on the note's onset -- measured across `ensemble-voice.ly`, all 18
  vowels start on a written onset to the microsecond, with the model on and
  off. What changes is the consonants: a mean of about 100 ms and a spread that
  depends on the phone and its context, instead of the table's flat 40-90 ms.
- `dspitch` is given the note sequence and the written pitch line, and returns
  a deviation around it. Measured with `librosa.yin` on a TIGER render, note
  bodies sit a median 4.3 cents from the written pitch with the model (max
  10.7) against 1.1 cents without it. That 4 cents is the singer; the scoops
  between notes are larger and are the audible part.

### Two things worth knowing before turning them on

**Long notes are outside `dsdur`'s experience.** Asked to divide a 5.7-second
word, TIGER returns 3.2 seconds of `f` for "flame". Its consonant predictions
are stable up to about a second and a half and diverge past that, so the model
is asked about a syllable of ordinary length and the surplus is left to the
vowel, which is where a held note's time actually goes. The measurements behind
that bound are in `scripts/predictors.py`.

**`dspitch` and the playhead video disagree.** A model that scoops into a note
is doing what a singer does, and it visibly does not match a playhead drawn on
exact printed onsets. For a score video, render the vocal with
`--literal-pitch`.

`sing.py --inspect` prints every model's ONNX interface, including the
predictor folders, which is the way to check what a new bank actually declares.

## 8. What differs between banks

"A DiffSinger bank" is not one format. The four banks in section 2 differ in
every one of the following ways, and each difference was found by a bank
failing quietly rather than loudly. This section is what to expect from a bank
nobody here has run.

### The acoustic model is exported one of two ways

Older banks (TIGER v102) take an int64 **`speedup`**: the stride through a
1000-step schedule. Newer ones, with `use_continuous_acceleration: true` in
`dsconfig.yaml` (CANARY and TRITON v106, LIEE MM 2.8), take an int64
**`steps`**: the number of steps to take. Both are handled, and so is the
matching split in `depth`:

| | classic | continuous |
|---|---|---|
| step count | `speedup`, a stride | `steps`, a count |
| `depth` | int64, a step count | float, a fraction of the schedule |
| `max_depth` means | 400 out of 1000 steps | 0.6 of the way back |

`--depth` is clamped to whatever the bank declares as its `max_depth`, because
a model exported at 0.6 was never trained to denoise from further back than
that. A bank with no `depth` input at all (LIEE) has no shallow diffusion and
the flag does nothing.

The same split runs through the predictors, and it is where it did real damage:
a `dspitch` that asks for `steps` and is offered only `speedup` declines
outright, so CANARY's pitch model was found, reported as present, and never
called. Both names are offered now, and a model that still declines names the
input it wanted.

### Phoneme tables come in two formats

`phonemes.txt` is one phoneme per line and the line number is the token id.
Multi-language banks (LIEE) ship `*.phonemes.json`, an object mapping phoneme
to id — and the ids start at 1, so counting lines gives every phone the wrong
token. Both the bank and each predictor folder can use either.

### The dictionary is not the vocabulary

A bank's `dsdict*.yaml` is a small word list: about 10,000 words for the
tigermeat banks, **208** for LIEE's English. Everything else is expected to
come from an OpenUtau phonemizer plugin, a `.dll` in the pack whose embedded
zip holds a large dictionary — 133,000 words in the tigermeat English plugin,
245,000 in the French one — and a neural G2P for the rest (see
`scripts/phonemizer.py`). Banks also ship dictionaries for languages they were
never trained on, sometimes empty, sometimes not valid yaml — LIEE's
`dsdict-zh-yue.yaml` has a list item outdented by one space. Unparsable ones
are named and skipped rather than fatal.

### The plugin has to be the one for the bank's language

This is the trap that costs the most and shows the least. A pack can carry
several plugins: CANARY's carries an English one and a French one, LIEE's
carries Polish, Filipino, Vietnamese and French and no English at all. Picking
the largest `.dll` nearby — which is all a file listing supports — picks French
for CANARY, and then:

```
lanterns    l en sh ae r n p     G2P (a guess)      # the French plugin
lanterns    l ae n t er n z      dictionary         # the English one
```

Nothing downstream objects, because the French phone set is a *subset* of
CANARY's inventory: every phoneme it returns is one the acoustic model knows.
The bank sings, fluently, in the wrong language's phonology.

So the plugin is chosen by asking each candidate about words the bank's own
dictionary already has an answer for. The matching plugin agrees with all three
tigermeat banks on 68% of 500 shared words; the French one agrees on 2%, the
Filipino one on 0%. Below 35% agreement, or fewer than 20 words in common, no
plugin is used at all — a bank with no plugin falls back to its dsdict plus
letter-to-sound rules, which is worse pronunciation but not another language.
`--phonemizer PATH` overrides the choice, and

```bash
python3 scripts/phonemizer.py /path/to/plugin.dll lanterns drift quiet
```

answers a specific plugin's opinion without rendering anything.

A plugin is not tied to the bank it shipped with: any plugin whose phone set
and conventions match will do, which is what the agreement number is measuring.
It is worth trying for a bank with a small dictionary — but check the number
rather than assuming. The English plugin from the TIGER pack agrees with LIEE
on 1 word out of 18, because LIEE transcribes English differently (`n aa dx`
for "not", a flap where the other banks write `t`), so it is not a substitute
there.

### Voice modes are per model, not per bank

`dsconfig.yaml` lists `speakers:` with a `.emb` file each, and `--voice-mode`
picks one. The predictor folders keep their own lists and they do not have to
match: CANARY's acoustic model has three modes and its `dspitch` exactly one.
The mode is matched by name where the folder has it and the folder's first
entry is used where it does not.

### The vocoder may be inside, named, or missing

All four banks tested ship `dsvocoder/`, which is found automatically, so
`--vocoder` is only needed for a bank that ships none. Note which way round
that preference runs: an explicit `--vocoder` **overrides** the bank's own, so
passing one on a bank that does not need it is not harmless — a mismatched
vocoder produces noise rather than a worse voice. LIEE also names an external
dependency
(`vocoder: pc_nsf_hifigan_44.1k_hop512_128bin_2025.02`) and ships that same
vocoder inside. What must match is the mel definition — sample rate, hop, bin
count and band edges — and it is checked before anything is rendered, allowing
for the two spellings in circulation (`mel_fmin`/`mel_fmax` in openvpi's
packages, `fmin`/`fmax` in banks' own). The log base is *converted* rather than
required to match, so a log-10 bank and a log-e vocoder are compatible.

Substituting one that does match is safe, and measurably so: TIGER (log-10)
rendered through openvpi's PC-NSF-HiFiGAN (log-e) tracks the written notes
exactly as it does through its own vocoder, 0.7 dB quieter, with mel spectra
correlating at 0.907 — against 0.905 for two renders through TIGER's *own*
vocoder, because diffusion is stochastic and no two takes are identical. The
swap is indistinguishable from rendering twice. CANARY and LIEE were checked
the same way and behave the same. What is not safe is a vocoder whose mel
definition differs, and that is what the check exists for.

## 9. Qualifying a bank you have not used before

```bash
python3 scripts/dev/bank_check.py ~/voices/mybank
```

Two or three minutes. It renders `scripts/dev/bank-check.ly` twice, and reports
what the bank declares, which models the pipeline actually fed, where the words
came from, and three measurements: the audio's peak and RMS and whether it
contains NaN, how far each note's body sits from the written pitch, and whether
every vowel still starts on its written onset. Its exit status is non-zero if
anything is out of bounds. CANARY v106, in full:

```
  declares  116 phonemes, 10075 dsdict words, 44100 Hz, hop 512, mel base 10
            acoustic wants: depth, durations, f0, gender, spk_embed, steps, tokens, velocity
            acceleration by steps (continuous acceleration), depth as a fraction capped at 0.6
            vocoder tgm_hifigan_v105.onnx: 128 bins, 44100 Hz, hop 512, mel base 10
            modes: canary_arc, canary_spark, canary_voltage

  singing it
    words   diffs_en_tgm_alpha.dll, agreeing with the bank's own dictionary on 68% of 500 words
    models  acoustic + vocoder, plus dsdur, dspitch

  sounds    23.3s, peak -3.9 dBFS, rms -17.1 dBFS, 0 NaN samples
  sings     as sung: median 1 cents from the written notes, worst 11, 20/20 notes tracked
  sings     --literal-pitch: median 1 cents from the written notes, worst 9, 20/20 notes tracked
  places    every vowel starts on its written onset

  no problems: this bank works with the pipeline as documented.
```

What the output means:

- **`sings --literal-pitch: median N cents`** is the pipeline's own accuracy
  with the model's expression turned off, and it should be a couple of cents.
  Tens of cents means the bank and the score disagree about tuning; hundreds
  means an octave or a transposition problem.
- **`sings as sung`** should be close to it in the note *bodies* — the
  difference between the two is mostly at the edges, which is where a singer
  scoops and where the measurement deliberately does not look.
- **`words ... no phonemizer plugin matches this bank`** means English is being
  spelled out by rule. The bank still sings; it just does not know how to
  pronounce anything outside its own dictionary (section 8).
- **`a model was present and declined`** with the input it wanted is the line
  that matters most on an unfamiliar bank: it is the one failure that costs
  quality without costing correctness, and it is inaudible.
- **NaN samples, or an RMS near silence**, is a mel mismatch. Check the
  vocoder before anything else.

`sing.py --inspect` is the shorter version — every config and every model's
ONNX interface, no rendering — and is the thing to read when `bank_check.py`
reports an input nobody supplies.

`scripts/dev/selftest.py --voice ~/voices/mybank --vocoder ...` asks the other
question: whether the *pipeline* still behaves with that bank in place. It runs
the same checks it runs against its stub banks, and it builds those stubs in
both export conventions, so a change that quietly drops support for one of them
fails there rather than on a user's bank.

## 10. Several banks at once: a cappella and choral work

A score with four named vocal parts and four banks installed is a choir. This
is a supported use of the skill, not a trick played on it: `sing.py` renders
one line with one bank, and `scripts/sing_ensemble.py` is the layer above that
renders every part and mixes them.

```bash
python3 scripts/sing_ensemble.py score.ly -o out/ \
    --voice soprano=~/voices/liee  --voice alto=~/voices/canary \
    --voice tenor=~/voices/tiger   --voice bass=~/voices/triton \
    --mode tenor=tiger_fresh --gain tenor=+1 --pan soprano=-0.30
```

It extracts once, checks every part name against the score before rendering
anything, runs the parts two at a time, prints per part which models that bank
actually used and where its words came from, and writes:

```
out/stems/<part>.wav     each part dry and aligned to beat 0, for a DAW
out/<score>.mp3          the mix   (--format flac or wav for lossless)
```

`songs/tide-and-lantern.ly` in this repository is a worked example: 28 bars of
unaccompanied SATB, one bank per part, with `songs/notes.md` recording the
command that made it and every decision behind it.

### Why the mix is here and not in render.py

`render.py --vocal` puts **one** sung line on top of a fluidsynth instrumental.
An a cappella piece has no instrumental, and four dry mono stems summed flat
sound like four people in four separate booths. So `sing_ensemble.py` does the
three things that turns them into an ensemble, and each is one line to change:

- **Balance is measured.** The banks are not equally loud — TIGER comes out
  about 5 dB under CANARY on the same line — so every part is pulled to a
  common level *measured while it is singing* (a part that rests more would
  otherwise look quiet and get boosted for it), and `--gain part=dB` is then
  the musical decision on top.
- **Placement is a semicircle**, outer voices wide and inner voices close, and
  nothing past a third of the way out: hard panning stops a voice being part of
  a chord and turns it into a soloist. `--pan part=x` overrides.
- **There is a room.** FFmpeg has no reverb filter, so one is built from a bank
  of mutually prime delays, low-passed the way a real room absorbs treble.
  `--wet 0` turns it off.

For voices *with* instruments, render the parts here and hand the mix to
`render.py --vocal out/score.mp3`.

### The score video of an a cappella piece

`render.py` always performs the score's MIDI, so an unaccompanied piece needs
its instrumental muted explicitly — otherwise you get a piano doubling the
choir:

```bash
python3 scripts/render.py score.ly --size 1920x1080 \
    --vocal out/score.flac --mix "soprano=mute,alto=mute,tenor=mute,bass=mute"
```

`--list-tracks` prints the part names to mute. Read the playhead verification
table it prints afterwards; if it did not say "verified", do not hand the video
over.

### Best practice, in the order it saves you time

1. **Audition the arrangement before you commit to it.** `--steps 8` renders
   about four times faster than 20 and says everything about balance, timing
   and whether the harmony works. Take the take at 20 or more.
2. **Ties, not extenders.** A tied note takes no syllable, so a part's syllable
   count is exactly its number of untied notes and a miscount becomes a
   LilyPond warning. `__` only extends a syllable where there is a real melisma
   — a slur or a tie — and where there is not, LilyPond drops it and slides
   every later syllable one note along.
3. **Check the extraction before rendering anything.** `vocal_score.py` prints
   "N sung notes, M melismatic, W words" per part and the syllable stream it
   resolved. A melisma you did not write is a miscount; read the words back.
4. **Write inside C3–G5.** That is where all four tested banks hold full level
   (section 2). Probe an unfamiliar one before writing for it.
5. **Give the tune to the quietest bank.** The part that must be heard should
   not be the one fighting to be heard.
6. **`--expressiveness 0.7` for ensembles**, which is this script's default
   against `sing.py`'s 1.0. At 1.0 each part deviates from the written pitch by
   a median 20 cents, which is one singer being human and four singers being a
   chord that never settles.
7. **One bank per directory.** The phonemizer plugin is looked for beside the
   bank, and a pack can carry several languages (section 8).
8. **`--literal-pitch` for a score video.** A model that scoops into a note is
   doing what a singer does and visibly disagrees with a playhead drawn on
   exact printed onsets.
9. **Design overlaps around the harmony.** In a canon, restrict the motif to
   the notes of one chord — the canon in `tide-and-lantern.ly` uses D-F-G-A-C
   only, so every vertical combination its four staggered entries can produce
   is a subset of Dm11 and no entry can collide with another.

### Common mistakes

| what you hear | what happened | fix |
|---|---|---|
| one part sings a syllable early from some bar onwards | its syllable count and note count disagree | count untied notes per part; prefer ties to `__` |
| a part is fluent and pronounces nothing correctly | its bank found a plugin for another language, or none | read the `words` line; `--phonemizer part=PATH` |
| a word comes out mangled in one part only | that bank's dictionary lacks it, so it was spelled out by rule | the run says which words; respell them in `\lyricmode`, or add them to the bank's dsdict |
| a word you added to a dsdict is still missing | YAML: `on`, `no`, `yes`, `off` unquoted are booleans, so the entry is stored under `True` | quote every grapheme |
| held chords never settle | every part deviating independently | lower `--expressiveness` |
| one part sits on top of everything | flat summing, or normalising over silence | this script measures while singing; `--gain part=dB` for the rest |
| a piano is doubling the choir in the video | `render.py` performed the score's MIDI | `--mix "part=mute,..."` for every part |
| the top of a phrase is thin or buzzing | that bank's trained range has run out | check the range probe; move the part or change bank |

## 11. Troubleshooting

**"no lyrics found in this score".** The line needs a *named* voice:
`\new Voice = "singer" \voicePart` with `\new Lyrics \lyricsto "singer"`.
`\addlyrics` works too, but naming the voice is what lets multiple verses and
multiple sung staves be told apart.

**"wants inputs this script does not supply".** The bank's acoustic model
declares an input beyond the documented contract. That bank needs OpenUtau; the
message lists what was missing.

**"dspitch is present but declined this line".** The model was found and not
called, and the line under it names the input it asked for. Two names are
already handled either way round (`steps` and `speedup`, section 8); anything
else is a bank exported by a toolchain generation this pipeline has not met.
The render is not wrong, it is just the fallback: written pitch with synthetic
portamento. `sing.py --inspect` shows the full interface.

**A word is sung fluently and is not the word you wrote.** Look at the `words`
line. A low agreement percentage, or a plugin whose name mentions another
language, means English is being pronounced by that language's rules — see
section 8. Ask the plugin directly with `scripts/phonemizer.py`, and pass
`--phonemizer` to override the choice.

**"no phonemizer plugin matches this bank".** Nothing near the bank agrees with
its own dictionary, so unlisted words are spelled out by rule. Either the
plugin was left behind when the bank was unpacked — it lives in the pack's
`OpenUTAU Plugins/` folder, beside `Voice Library/` — or the bank ships none
for your language, as LIEE does for English. Respell the words in
`\lyricmode`, or add them to a copy of the bank's `dsdict-en.yaml`.

**"<name>.yaml is not valid yaml and was skipped".** A hand-edited dictionary
in the bank, usually for a language you are not singing. Harmless unless it is
the one you needed.

**"voice mode X not in this bank".** Multi-speaker banks list their modes in
`dsconfig.yaml` under `speakers:`, each with a matching `<name>.emb` beside it.
The startup line prints the list; `--voice-mode` picks one, and the first is the
default.

**Words in the dictionary but the wrong syllable count.** Reported as a warning
and the phonemes are spread evenly, which sounds like a mispronunciation rather
than a crash. Re-hyphenate the word in `\lyricmode` to match the dictionary's
vowel count.

**A word comes out wrong and you want to know why before re-rendering.** Ask
the bank directly:

```bash
python3 scripts/phonemizer.py ~/voices/tiger lanterns drift quasimodal
#   lanterns    l ae n t er n z            dictionary
#   drift       dr ih f t                  dictionary
#   quasimodal  k w aa s ah m ow dx ah l   G2P (a guess)
```

Anything marked as a guess came from the plugin's neural grapheme-to-phoneme
model rather than its 133,000-word dictionary, and that is where mispronounced
words come from. The fix is to respell the word in `\lyricmode`, and the vowel
count is what the hyphenation has to match.

**Everything is a semitone off / notes in the wrong octave.** Check the score's
`\transposition` — the extractor reports sounding pitch, and a transposing
vocal part is unusual but possible.

**The singer is late.** Almost always a consonant cluster with no room before
the beat: the previous note ends exactly where the next begins and the onset
gets scaled into whatever gap exists. Shorten the previous note or re-hyphenate.

**Output is noise.** Mismatched vocoder. The script checks sample rate, hop
size, mel bins and fmin/fmax, but a vocoder trained with a different mel scale
or base can still slip through if the config files lie about it.

## 12. Why not MusicXML conversion

The obvious route is `.ly -> MusicXML -> singing synthesiser`, since MusicXML
is what NNSVS and Sinsy read. It does not survive contact with real vocal
scores: `python-ly`'s exporter drops `\lyricsto` lines silently and raises
`AttributeError: 'list' object has no attribute 'pickup'` on
`\score { \new Staff … \addlyrics … }` — it fails exactly on the writing this
needs, and it prints its warnings to stdout, into the XML.

`vocal_score.py` still emits MusicXML, but builds it *outward* from one
monophonic line rather than translating a whole score. That direction is
tractable because the output only ever has to describe something simple: one
part, one voice, syllables, ties, slurs for melismata. It is what to hand to
NNSVS, ESPnet or MuseScore, and it is generated from the same table `sing.py`
uses, so the two can never disagree about what is being sung.

## 13. The other engines, and why English narrows the field

| Engine | Input | Voices | Headless |
|---|---|---|---|
| DiffSinger (openvpi) | phonemes + f0 via ONNX | community banks incl. **English** | yes — this pipeline |
| NNSVS | MusicXML via pysinsy | mostly Japanese | yes, builds sinsy from C++ |
| ESPnet2 SVS (Muskits) | score, VISinger2 | pretrained models are zh/ja only | yes, pip + model zoo |
| Sinsy | MusicXML | Japanese, HMM not neural | yes |
| OpenUtau | USTX/MIDI | everything above | no, GUI |

English is the constraint. ESPnet's pretrained VISinger2 models are Chinese and
Japanese; NNSVS ships Japanese and its English support is community work. The
English banks that exist are DiffSinger banks built for OpenUtau, which is why
this pipeline speaks OpenUtau's file layout rather than any research toolkit's.

If the target language is Japanese or Chinese, the `.musicxml` from
`vocal_score.py` is the better handover: NNSVS and ESPnet take it more or less
directly and their pretrained models are stronger than anything English on
offer.
