---
name: lilypond-music
description: Write, engrave, and play back original music with LilyPond, and turn a score into a video where a playhead follows the notation bar by bar. Use this skill whenever the user asks to compose, notate, arrange, transcribe, harmonise, or "write music", asks for sheet music, a score, a PDF of notation, a MIDI file, or an audio rendering of music, asks for a scrolling-score or "sheet music with the music playing" video, or asks about LilyPond, staves, clefs, lyrics under a melody, chord charts, guitar tab, drum notation, or instrument parts, or asks for the words of a song to be actually sung -- a vocal, a singer, singing synthesis, a neural voice on the melody -- including several singers at once: an a cappella arrangement, close harmony, a choir, SATB, a canon or round, backing vocals, or a different voice on each part -- even if they never say "LilyPond" and even if they only ask for "a short piece" or "something that sounds like X".
---

# Writing and visualising music with LilyPond

LilyPond is a text-to-notation compiler: plain-text source in, professionally
engraved PDF plus a MIDI performance out. That combination is what makes the
whole workflow possible -- the *same* source produces the picture and the sound,
so a video that shows the score while the music plays can be built with
guaranteed agreement between the two.

The end product this skill is built around:

    score.ly  ->  score.pdf   engraved notation
              ->  score.midi  performance data
              ->  score.mp3   synthesised audio
              ->  score.mp4   the pages, with a playhead on each note as it sounds

and, if the score has lyrics and a voicebank is installed:

    score.ly  ->  score-vocal.wav   the words, sung, by a neural singing model

## Quick start

```bash
bash scripts/setup.sh                       # once per machine (~1 min)
python3 scripts/render.py score.ly -o out/  # everything, verified
```

Useful flags: `--size 1920x1080` (landscape; default is 1080x1920 portrait),
`--no-video`, `--fps 30`, `--pickup 1` (score starts with `\partial 4`),
`--playhead d64a3a --highlight ffc36a --bg 100f12` (hex colours),
`--playhead-mode bars` (one linear sweep per bar instead of per-note anchors),
`--list-tracks` then `--mix "koto=-3,voice=+4/-0.2"` (per-part gain in dB and
stereo balance -- the fix for a score that is notated right but sounds
unbalanced) and `--eq "koto=warm,drums=hp:120,pad=distant"` (per-part tone -- the
fix for parts that are balanced and still fight each other),
`--verify 8` (sample 8 frames to confirm sync), `--keep-temp` (inspect
intermediates in `out/.<stem>-work/`), `--vocal out/score-vocal.wav` (mix in a
sung line, with `--vocal-gain` and `--vocal-eq`).

Start from a template in `assets/templates/` rather than a blank file:
`solo-piano.ly`, `ensemble-voice.ly` (voice + lyrics + winds + strings + koto +
drums + piano + drone), `lead-sheet.ly` (melody, chord symbols, lyrics).

## Workflow

1. **Decide the forces first** -- which instruments, which clefs, how many
   staves. Restructuring a score's context tree later is more disruptive than
   rewriting its notes.
2. **Write the music one part at a time**, each as a named variable, then
   assemble them in `\score`. See `references/notation-recipes.md`.
3. **Compile early and often.** `lilypond score.ly` after every few bars. Bar
   checks (`|` at the end of every bar) turn a rhythm slip into a precise error
   message instead of a mysteriously wrong score.
4. **Read the warnings.** "Barcheck failed", "clashing note columns", and
   "warning: stem does not fit" are all telling you something real.
5. **Run the pipeline** (`render.py`) for audio and video.
6. **Check the verification table** it prints. If it says the playhead sync
   failed, do not hand over the video -- diagnose it (see below).
7. **Listen, then check the balance.** Hairpins over held notes are performed
   for you as expression ramps -- read the "shaped with CC11" list the run
   prints and check it matches the swells you wrote. Then
   `render.py score.ly --list-tracks` prints
   every part with the velocity spread it received and how much of its time is
   spent in held notes. Two failures show up there and nowhere else: a part with
   one velocity got no dynamics at all, and a mostly-sustained part cannot be
   shaped by hairpins. Correct the loudness of parts against each other with
   `--mix`, not by rewriting dynamics in the score -- a `\mp` koto and a `\mp`
   shakuhachi are the same velocity and about 8 dB apart, which is a property of
   the soundfont, not of the music.
8. **Then correct the tone, if levels were not enough.** A part that is loud
   enough and still inaudible is masked, not quiet, and `--eq` is the fix:
   `--eq "koto=clear"` takes 2 dB out of the mud band on the part that is
   covering the melody. `references/audio-and-midi.md` section 10 is the whole
   equaliser: the preset table with measured numbers, what to reach for by
   symptom, and the traps (chief among them that the master low-pass runs
   *after* your EQ, so boosting 11 kHz does nothing).

## Singing the words

A score with lyrics can be sung, not just played, by a neural singing
synthesiser:

```bash
python3 scripts/sing.py score.ly --preview -o out/             # no voicebank needed
bash scripts/setup-singing.sh                                  # once, for the real thing
python3 scripts/sing.py score.ly --voice ~/voices/mybank -o out/
python3 scripts/render.py score.ly --vocal out/score-vocal.wav
```

**Start with `--preview`.** It sings the line through a built-in formant
synthesiser: robotic, instant, no download. The timing, the syllable placement
and the pitch curve are the real ones -- computed by the same code that feeds
the voicebank -- so anything wrong there is wrong in the real render too, and
audible in seconds rather than minutes. Only the timbre is fake.

The voicebank is a separate download and is not bundled: English DiffSinger
banks are almost all non-commercial and several forbid redistribution. Read the
bank's terms. `references/singing-synthesis.md` section 2 lists the banks that
have actually been run through this pipeline, with their release URLs and what
each one is good for -- TIGER, CANARY, TRITON and LIEE all work, and they differ
in ways that matter (voice modes, phoneme sets, whether an English phonemizer
ships with them). Section 8 is what varies between banks, and
`python3 scripts/dev/bank_check.py ~/voices/mybank` qualifies an unfamiliar one
in about two minutes: what it declares, which models were actually used, where
its pronunciations came from, and how far the rendered notes sit from the
written ones.

**Unpack one bank per directory.** The phonemizer plugin -- the `.dll` in the
pack, which is where a bank's real vocabulary lives -- is looked for beside the
bank, and it must be the one for the language you are singing. Every run prints
which plugin it chose and how far that plugin agrees with the bank's own
dictionary; a low number there means the words are being pronounced by another
language's rules, which sounds fluent and is wrong.

A bank usually ships more than the acoustic model, and all of it is used: a
`dsdur` model decides how each syllable's time divides between its consonants
and its vowel, and a `dspitch` model supplies that singer's own deviation
around the written notes. Every run prints which models it used. **For a score
video, add `--literal-pitch`** -- a model that scoops into a note is doing what
a singer does, and it visibly disagrees with a playhead drawn on exact onsets.

Two things are worth knowing before writing the vocal part:

- **Hyphenate on singable syllables** (`si -- lence`, not `sil -- ence`). The
  hyphenation decides which consonant is heard on which note, and words are
  reassembled from it before being looked up in the bank's dictionary --
  "lan" and "terns" phonemise to nothing like "lanterns".
- **Leave rests to breathe in.** A rest over 0.6s becomes a phrase boundary.

### Several singers, or none of them accompanied

A score with more than one named vocal part can have a different bank on each
of them, which is how an a cappella arrangement, a close-harmony group or an
SATB choir gets made. One command does the lot:

```bash
python3 scripts/sing_ensemble.py score.ly -o out/ \
    --voice soprano=~/voices/liee --voice alto=~/voices/canary \
    --voice tenor=~/voices/tiger  --voice bass=~/voices/triton
```

It renders each part with its own bank, prints what each one actually used,
writes a dry stem per part for a DAW, and mixes them with a measured balance
(banks differ by about 5 dB), a choir's placement and a built reverb, because
four dry mono stems summed flat sound like four separate booths.
`songs/tide-and-lantern.ly` is a worked example and `songs/notes.md` records
the command that made it.

**For a score video of an unaccompanied piece, mute the instrumental** --
`render.py` performs the score's MIDI as well, so without
`--mix "soprano=mute,alto=mute,..."` a piano doubles the choir.

`references/singing-synthesis.md` section 10 is the whole workflow: what to
check before rendering anything, the nine things that save time in the order
they save it, and a table of what each common mistake sounds like. The two that
cost the most: a part whose syllable count disagrees with its note count sings
a syllable early from that bar onwards, and `--expressiveness` left at 1.0
gives four singers each drifting 20 cents, which is a chord that never settles.

`python3 scripts/vocal_score.py score.ly -o out/` runs just the extraction and
prints what it found -- sung notes, melismata, words -- which is the fastest way
to check that the lyrics line up with the notes the way you intended. It also
writes one `.musicxml` per verse, the handover format for other singing
synthesisers (NNSVS, ESPnet) and for MuseScore.

## What LilyPond can do

Extensive detail lives in `references/scope-and-capabilities.md`; the short
version is that the notation side is close to comprehensive and the audio side
is deliberately basic.

**Instruments and staff types.** Any number of staves, in any combination:
ordinary 5-line staves in any clef (treble, bass, alto, tenor, French violin,
percussion, and transposed variants like `treble_8` for tenor voice and guitar);
`PianoStaff` (braced, with cross-staff beaming and pedal marks); `StaffGroup`,
`ChoirStaff` and `GrandStaff` brackets; `DrumStaff` with real drum note names
(`bd`, `sn`, `hh`, `tt`, `wbh`, ...) and choosable one-line or five-line
presentation; `TabStaff` for guitar/bass/lute tablature with automatic string
assignment; `RhythmicStaff`, `MensuralStaff`, `VaticanaStaff` for rhythm-only,
early and chant notation; `FretBoards` and `ChordNames` for lead sheets;
`Lyrics` for any number of verses; `FiguredBass` for continuo.

**Voices in both senses.** Vocal parts with lyrics attached syllable by syllable
(`\lyricsto`), melismata handled automatically across slurs and ties, multiple
verses under one line, divisi via `\voiceOne`/`\voiceTwo`, and separate
`ChoirStaff` layouts for SATB. Also "voices" in the polyphonic sense: several
independent rhythmic lines sharing one staff, each with its own stem direction.

**Transposing instruments.** Write in concert pitch and print in B-flat, or the
reverse, with `\transposition` and `\transpose`; extract individual parts from
the same source with `\tag`, so score and parts can never drift apart.

**Idiomatic marks.** Articulations, ornaments, trills with accidentals, glissandi,
arpeggios, tremolo, harmonics, bowing and fingering, pedal lines, breath marks,
multi-measure rests, cue notes, ossia staves, grace notes, tuplets of any ratio,
polymetric and mid-score metre changes, microtonal accidentals, custom time
signatures, figured bass, and non-Western systems via the bundled `arabic.ly`,
`makam.ly` and `gregorian.ly`.

**What it does not do.** LilyPond's MIDI is a literal performance: correct
pitches, rhythms, tempo changes and dynamics, no phrasing or humanisation. It is
not a DAW and there is no mixing, no articulation switching, no sample libraries.
Audio quality is entirely a function of the soundfont plus whatever post-
processing you apply (`references/audio-and-midi.md` covers how far you can push
it). There is also no GUI, no real-time playback, and no automatic
orchestration -- LilyPond engraves what you write, it does not compose.

## Writing music that engraves and plays well

- **One variable per part, `\global` for shared attributes.** Put key, metre and
  tempo in a `global` variable included at the head of every part, so a metre
  change is made once instead of once per staff.
- **End every bar with `|`.** This is a bar check, not decoration; it costs
  nothing and catches the most common class of error.
- **Absolute octaves for anything intricate.** `\relative` is compact but an
  error early in a run silently transposes everything after it. `c'` is middle
  C; `c''` an octave up; `c,` an octave down.
- **Write dynamics as hairpins**, not text. `\<` ... `\!` becomes an actual
  velocity ramp in the MIDI, so the music swells audibly rather than just
  visually. Every `\<` or `\>` needs a terminating `\!` or a new dynamic mark.
  A hairpin drawn *across a single held note* cannot work that way, since
  velocity is fixed at note-on -- but `render.py` detects those and performs
  them as CC11 expression ramps, so `<d a>1\>` is audible too. It prints what
  it shaped; `--no-swell` turns it off.
- **Prefer `R1` to `r1` for whole-bar rests** in ensemble parts: `R` produces
  proper multi-measure rests and compresses in extracted parts.
- **Force the layout you want to animate.** `\break` every 2 or 4 bars gives
  systems of predictable width, which reads far better in a video than
  LilyPond's default of packing as much as fits.
- **Let the harmony carry the character.** A scale choice does more work than any
  amount of notation trickery -- e.g. hirajoshi (D E-flat G A B-flat) reads as
  eerie and Japanese because of its minor second and missing sixth. Chromatic
  passing tones against a static drone will sound unsettled no matter the
  instrumentation.

## Common errors

**Rhythm doesn't add up.** LilyPond says "barcheck failed at ...". Count in
sixteenths: a 4/4 bar is 16 units, `4` = 4, `8.` = 3, `16` = 1. Fix the bar it
names, not the one after it.

**Hairpin never closes.** "unterminated crescendo" -- add `\!` or a dynamic.

**`\repeat tremolo` produces stem-direction warnings.** Split it into
beat-length groups: four `\repeat tremolo 4 { <..>32 <..>32 }` instead of one
`\repeat tremolo 16`.

**Grace notes with `\acciaccatura` at the very start of a bar** can collide with
clefs and time signatures; move them after the first beat or use `\grace`.

**Lyrics drift out of alignment.** Every note gets a syllable unless it is tied
or slurred. Use `--` between syllables of a word, `_` for a note that should
extend the previous syllable, and `\skip 1` in `\lyricmode` to pass over a rest.

**Bar counts disagree between print and MIDI.** `render.py` reports this. Usual
causes: a pickup bar (pass `--pickup`), `\repeat volta` (the print shows the
music once, the MIDI plays it twice -- use `\repeat unfold` for the MIDI version
or accept the mismatch and skip the video), or a mid-score metre change the
timeline handles but the layout splits unusually.

**The video renders but nothing moves.** See
`references/video-pipeline.md`; the ffmpeg-specific traps are documented there,
including one that fails *silently*. Always trust the verification table over a
glance at a thumbnail.

## Reference files

- `references/scope-and-capabilities.md` -- what LilyPond covers, in detail:
  instrument families, staff types, vocal and choral writing, tablature,
  percussion, transposition and part extraction, non-Western notation, and the
  boundaries of the MIDI side.
- `references/notation-recipes.md` -- copy-paste snippets for ensembles, lyrics
  and verses, drums, tab, chord symbols, tuplets, tremolo, repeats, pickups,
  polyphony, cross-staff writing, and layout control.
- `references/audio-and-midi.md` -- MIDI instrument assignment, the full General
  MIDI name list, drum note names, channel mapping, dynamics-to-velocity,
  `articulate.ly`, soundfonts, the ffmpeg mastering chain, balancing parts, and
  the equaliser (section 10: presets with measured curves, symptom-to-fix,
  frequency map, and the traps).
- `references/video-pipeline.md` -- how `render.py` builds the animation, the
  colour-coded layout analysis, deriving the timeline from MIDI, the ffmpeg
  pitfalls, and how to extend it (scrolling, note-level highlighting, karaoke).
- `references/gm-instruments.md` -- all 128 General MIDI instrument names as
  LilyPond spells them, plus the drum note vocabulary.
- `references/singing-synthesis.md` -- the vocal pipeline: obtaining and
  licensing a voicebank (section 2 names four that were tested and where to get
  them), how syllables are assigned to notes and phonemes to syllables, how the
  pitch curve is built, what is not modelled, what differs between banks and how
  to qualify a new one (sections 8 and 9), troubleshooting, and why converting
  the score to MusicXML is the wrong way round.

## Scripts

- `scripts/setup.sh` -- installs lilypond, fluidsynth, a GM soundfont, ffmpeg.
- `scripts/render.py` -- the pipeline described above.
- `scripts/lily_layout.py` -- extracts system and bar geometry from page images.
- `scripts/midi_timing.py` -- derives bar start times from a MIDI file; run it
  standalone (`python3 scripts/midi_timing.py score.midi`) to sanity-check a
  tempo map.
- `scripts/setup-singing.sh` -- extra dependencies for the vocal pipeline.
- `scripts/vocal_score.py` -- extracts the sung lines: which syllable is on
  which note, as LilyPond itself resolved it. Writes JSON plus one MusicXML per
  verse.
- `scripts/sing.py` -- renders those with a DiffSinger voicebank through
  onnxruntime, or with the built-in preview voice (`--preview`).
  `--inspect` prints everything a bank declares, which is where to start with an
  unfamiliar one. It is the pitch curve and the model calls; the bank itself is
  `scripts/voicebank.py`, turning words and notes into a phoneme timeline is
  `scripts/phonemes.py`, and score moments to seconds is
  `scripts/score_time.py`.
- `scripts/sing_ensemble.py` -- one bank per part for a whole score: renders
  them all, reports what each bank used, writes a stem each, and mixes them
  into an a cappella track. The one command for choral and close-harmony work.
- `scripts/predictors.py` -- the bank's optional `dsdur`, `dspitch` and
  `dsvariance` models: what each tensor means, how that was established, and
  the bounds placed on them.
- `scripts/preview_voice.py` -- the preview voice: letter-to-sound rules and a
  three-formant synthesiser, for auditioning a line without a voicebank.
- `scripts/phonemizer.py` -- reads the bank's own OpenUtau phonemizer plugin
  (dictionary plus neural G2P) without OpenUtau. Run it directly
  (`python3 scripts/phonemizer.py ~/voices/mybank lanterns drift`) to see how a
  bank will pronounce a word, and whether that pronunciation is a guess.
- `scripts/dev/test_units.py` -- 90-odd unit tests over the pure functions, in
  under a second and with no lilypond, ffmpeg or voicebank needed. The fastest
  way to find out whether a change broke something, and the one that names what.
- `scripts/dev/selftest.py` -- runs those first, then the whole pipeline against
  a score written to break it, checking 180-odd invariants in total against stub
  banks in both of the export conventions real banks use. Run it after changing
  any of the above; `--video` includes playhead verification, `--voice` uses a
  real bank instead of a stub.
- `scripts/dev/bank_check.py` -- qualifies one voicebank: what it declares,
  which of its models the pipeline actually fed, where its pronunciations came
  from, and measured pitch and vowel placement on a rendered line. The first
  thing to run on a bank nobody has tried here.
