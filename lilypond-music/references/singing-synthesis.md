# Singing the vocal line

Contents:
1. What this pipeline is
2. Getting a voicebank
3. Running it
4. How the score becomes phonemes
5. How the pitch curve is built
6. Writing lyrics that sing well
7. What is not modelled
8. Troubleshooting
9. Why not MusicXML conversion
10. The other engines, and why English narrows the field

---

## 1. What this pipeline is

    score.ly  --(lyrics.ily)-->  note/syllable table
              --(vocal_score.py)-->  score-vocals.json + one .musicxml per verse
              --(sing.py)-->  score-vocal.wav
              --(render.py --vocal)-->  score.mp3 / score.mp4 with the singer in it

`sing.py` drives a **DiffSinger** voicebank directly through onnxruntime: the
same ONNX files OpenUtau loads, minus the GUI. DiffSinger is the open singing
synthesiser with a real English voicebank ecosystem, which is the only reason it
is the one wired up here (see section 10).

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

**The vocoder** — https://github.com/openvpi/vocoders/releases. Take the newest
release's `.oudep` attachment; it is a zip, so rename and unzip it. The current
line is PC-NSF-HiFiGAN (44.1 kHz, hop 512, 128 mel bins); the 2024.02
NSF-HiFiGAN release is also fine. Note that some are trained on log-e mel
spectrograms and others on log-10 — the `mel_base` field in the two config files
says which, and `sing.py` converts between them, but only if the configs are
honest about it.

**A bank** — the DiffSinger Wiki's "Voicebanks supporting English" category
(diffsinger.miraheze.org) is the working index. Known-good starting points are
TIGER (github.com/spicytigermeat/tiger_diffsinger, CC BY-NC-ND + Commons
Clause, multi-speaker with several voice modes) and its sibling CANARY. Banks
ship as `.zip` or `.oudep` intended for drag-and-drop into OpenUtau; unzip
instead and point `--voice` at the folder holding `dsconfig.yaml`.

Check the bank's own docs for which vocoder it expects — a few are trained
against fish-diffusion's HiFi-GAN rather than openvpi's, and the wrong one
produces noise rather than a worse voice.

You need two things:

- **A bank** — a directory containing `dsconfig.yaml`, `acoustic.onnx`,
  `phonemes.txt`, and a `dsdict*.yaml` dictionary. English banks are
  distributed as `.zip` or `.oudep` files (an `.oudep` is a zip; rename it).
  Look for banks advertising an ARPAbet or "ARPA+" phoneme set.
- **A vocoder package** — usually `nsf_hifigan`, from openvpi's vocoder
  releases. Put it in the bank as `vocoder/`, or pass `--vocoder`.

```
mybank/
  dsconfig.yaml      acoustic: acoustic.onnx, phonemes: phonemes.txt, hop_size, sample_rate...
  acoustic.onnx
  phonemes.txt       one phoneme per line; the line number IS the token id
  dsdict-en.yaml     symbols (phoneme -> type) + entries (word -> phonemes)
  vocoder/
    nsf_hifigan.onnx
    vocoder.yaml     must agree with dsconfig on sample_rate, hop_size, mel bins, fmin/fmax
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

Useful flags: `--line 2` (sing the second verse), `--steps 40` (slower, smoother
— 8 to 20 is enough to audition), `--no-vibrato`, `--variance E,B,V,T`, and
`--vocoder` when the vocoder lives outside the bank.

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

A flat f0 per note is the single loudest tell of synthetic singing, so three
things are layered onto the note pitches:

- **Portamento** across every change of note, 40 ms for a step and up to 110 ms
  for a wide leap, on a smoothstep rather than a line.
- **Vibrato** on notes held longer than 0.55s: about 5.4 Hz, ±35 cents, fading
  in over the third of a second after the onset, so short notes stay straight.
- **Drift** of a few cents, smoothed over half a second, so sustained notes are
  not mathematically level.

All three are seeded and deterministic — the same score renders the same take.

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

The bank ships more than the acoustic model, and the pipeline should use all of
it. Current state:

| Model | Used | Notes |
|---|---|---|
| acoustic | yes | verified against TIGER v102 |
| vocoder | yes | verified by analysis-resynthesis, 0.944 mel correlation |
| phonemizer plugin | yes | see `scripts/phonemizer.py` |
| `dsdur` | **not yet** | phoneme durations within a note; would replace the `CONSONANT_S` table |
| `dspitch` | **not yet** | expressive f0 around the written notes; would replace `f0_curve()` |
| `dsvariance` | n/a for TIGER | this bank sets `use_energy_embed: false` and ships no `dsvariance/` |

An earlier version of this document argued the duration and pitch predictors
were unnecessary because the score states durations and pitches. That was
wrong. `dsdur` predicts *phoneme* durations inside each note -- how a syllable
splits between consonant and vowel -- which the score says nothing about, and
`dspitch` renders the singer's deviation around notes it is given rather than
guessing a melody. Both are better than the hand-written approximations here.

Their interfaces are known (read them with `sing.py --inspect`); wiring them is
the next piece of work.

## 8. Troubleshooting

**"no lyrics found in this score".** The line needs a *named* voice:
`\new Voice = "singer" \voicePart` with `\new Lyrics \lyricsto "singer"`.
`\addlyrics` works too, but naming the voice is what lets multiple verses and
multiple sung staves be told apart.

**"wants inputs this script does not supply".** The bank's acoustic model
declares an input beyond the documented contract. That bank needs OpenUtau; the
message lists what was missing.

**"voice mode X not in this bank".** Multi-speaker banks list their modes in
`dsconfig.yaml` under `speakers:`, each with a matching `<name>.emb` beside it.
The startup line prints the list; `--voice-mode` picks one, and the first is the
default.

**Words in the dictionary but the wrong syllable count.** Reported as a warning
and the phonemes are spread evenly, which sounds like a mispronunciation rather
than a crash. Re-hyphenate the word in `\lyricmode` to match the dictionary's
vowel count.

**Everything is a semitone off / notes in the wrong octave.** Check the score's
`\transposition` — the extractor reports sounding pitch, and a transposing
vocal part is unusual but possible.

**The singer is late.** Almost always a consonant cluster with no room before
the beat: the previous note ends exactly where the next begins and the onset
gets scaled into whatever gap exists. Shorten the previous note or re-hyphenate.

**Output is noise.** Mismatched vocoder. The script checks sample rate, hop
size, mel bins and fmin/fmax, but a vocoder trained with a different mel scale
or base can still slip through if the config files lie about it.

## 9. Why not MusicXML conversion

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

## 10. The other engines, and why English narrows the field

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
