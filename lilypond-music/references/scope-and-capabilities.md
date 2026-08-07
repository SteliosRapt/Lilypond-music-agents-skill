# What LilyPond can and cannot do

Contents:
1. The model
2. Instrument families and staff types
3. Voices: vocal writing and polyphony
4. Percussion
5. Fretted instruments and tablature
6. Chord symbols, lead sheets, figured bass
7. Transposing instruments and part extraction
8. Non-Western and historical notation
9. Notation vocabulary
10. Input and output formats
11. Limits of the MIDI side
12. Practical ceilings

---

## 1. The model

LilyPond compiles text to engraved notation. There is no canvas and no mouse:
you describe music, and a layout engine decides spacing, beaming, collision
avoidance and page breaks using rules derived from hand-engraving practice.

Three ideas explain most of the syntax:

- **Music expressions** -- `{ c d e f }` is sequential, `<< ... >>` is
  simultaneous, `<c e g>` is a chord. They nest arbitrarily.
- **Contexts** -- `Score` contains `Staff`s, a `Staff` contains `Voice`s, a
  `Lyrics` context attaches to a `Voice`. Contexts hold the engravers that
  actually draw things.
- **Grobs** (graphical objects) -- `NoteHead`, `Stem`, `Slur`, `BarLine`,
  `StaffSymbol`. Anything visible can be overridden:
  `\override Staff.NoteHead.color = #red`.

Understanding those three is enough to read almost any LilyPond source.

## 2. Instrument families and staff types

Everything below is a standard context you can instantiate with `\new`:

| Context | Use |
|---|---|
| `Staff` | any pitched instrument; clef is a property, not a separate type |
| `PianoStaff` | braced keyboard pair, cross-staff beams, `\change Staff` |
| `GrandStaff` | braced pair for other keyboard/harp writing |
| `StaffGroup` | bracketed family (winds, strings, percussion section) |
| `ChoirStaff` | bracketed vocal group, brackets drawn choir-style |
| `DrumStaff` | percussion with drum note names, 1–5 lines |
| `TabStaff` | tablature, automatic or manual string assignment |
| `RhythmicStaff` | rhythm only, one line |
| `MensuralStaff`, `VaticanaStaff`, `GregorianTranscriptionStaff` | early music and chant |
| `Lyrics` | one verse; stack as many as needed |
| `ChordNames`, `FretBoards` | chord symbols and diagrams |
| `FiguredBass` | continuo figures |
| `Devnull` | swallows music; handy for a shared `\break`/`s1` skeleton |

Clefs cover the whole orchestral range including `alto` and `tenor` for viola
and trombone, `treble_8` for guitar and tenor voice, `bass^15` and friends,
plus `percussion` and `tab`. Ambitus, ottava brackets (`\ottava #1`), and
instrument-specific string numbers, fingerings, bowings and harmonics are all
built in.

There is no fixed list of "supported instruments" -- an instrument is a staff
with a clef, a name, and idiomatic markings. What varies by instrument is the
notation vocabulary you reach for (see §9) and the MIDI program you assign
(see `audio-and-midi.md`).

## 3. Voices: vocal writing and polyphony

**Vocal.** Attach syllables to a named voice:

```lilypond
\new Staff \new Voice = "sop" { c'4 d' e' f' }
\new Lyrics \lyricsto "sop" { Sing a -- lit -- tle song }
```

- `--` separates syllables within a word and prints the hyphen
- `__` draws an extension line under a held note
- `_` occupies a note without printing a syllable
- melismata are automatic under slurs and ties
- several `\new Lyrics` blocks stack as verses; `\set stanza = "1."` labels them
- `\lyricmode` lets you write text without quoting every word

**Polyphonic.** Independent lines in one staff:

```lilypond
<< { \voiceOne g'2 f' } \\ { \voiceTwo c'2 d' } >>
```

`\voiceOne`/`\voiceTwo` fix stem directions and rest offsets; `\oneVoice`
returns to normal. This is how you write SATB on two staves, a fugue on one, or
a divisi passage that rejoins.

**Vocal ensembles.** `ChoirStaff` with one `Staff` per part, or two staves with
two voices each. Lyrics can be shared between parts or written separately per
part. `\partCombine` merges two parts onto one staff and prints "a2"/"solo"
automatically.

**And they can be sung, all of them, by different voices.** A score with one
named `Voice` per part and lyrics attached to each is what
`scripts/sing_ensemble.py` takes: a voicebank per part, a dry stem per part,
and an a cappella mix of the lot. Unaccompanied choral writing, close harmony,
canons and rounds are inside the scope of this skill rather than at the edge of
it — `references/singing-synthesis.md` section 10 is the workflow, and
`songs/tide-and-lantern.ly` is a worked SATB example with four different banks
on it. The constraint that matters when writing for them is range: the banks
tested here hold full level between C3 and G5, and a part written outside that
degrades rather than transposes.

## 4. Percussion

`\drummode` replaces pitches with drum names -- `bd` (bass drum), `sn`, `hh`,
`hhc`, `cymc`, `tt` (tam-tam), `wbh`/`wbl` (wood blocks), `tamb`, `cb`, and
about 60 more, all GM-mapped. Presentation is configurable:

```lilypond
\new DrumStaff \with { drumStyleTable = #timbales-style \override StaffSymbol.line-count = #2 }
```

Built-in styles include `drums-style`, `timbales-style`, `congas-style`,
`bongos-style` and `percussion-style` (one line). In MIDI, a `DrumStaff` is
routed to channel 10 automatically -- you do not set a program for it.

## 5. Fretted instruments and tablature

`TabStaff` prints tablature and works out string/fret assignments from the
tuning (`\set TabStaff.stringTunings = \stringTuning <e, a, d g b e'>`), or you
can force them with `\4` style string indicators. Standard notation and tab can
be shown together from a single music variable. `FretBoards` prints chord
diagrams, with a large library of predefined shapes; `\storePredefinedDiagram`
adds your own. Bends, slides, hammer-ons, palm mute and harmonics all have
notation.

## 6. Chord symbols, lead sheets, figured bass

`\chordmode` writes chords as `c1:maj7 a:m7 d:9 g:7.5+`, printed by
`ChordNames` in several naming conventions (jazz, German, Italian) and fully
customisable via `chordNameExceptions`. Combine `ChordNames` + melody `Staff` +
`Lyrics` for a lead sheet -- see `assets/templates/lead-sheet.ly`.
`FiguredBass` handles continuo figures with correct alignment and accidentals.

## 7. Transposing instruments and part extraction

- `\transpose from to { music }` rewrites the pitches.
- `\transposition bes` tells LilyPond the instrument sounds a tone lower, so
  MIDI plays concert pitch while the staff shows written pitch.
- `\tag #'score` / `\tag #'part` with `\keepWithTag` produces the full score and
  the individual parts from one source -- cues in the part, tacet bars
  compressed, no risk of the two drifting apart.
- `\partial`, `\repeat volta`, `\alternative` and `\segno`-style road maps are
  all supported, including `\unfoldRepeats` to expand them for playback.

## 8. Non-Western and historical notation

Bundled include files extend the pitch and accidental systems:
`arabic.ly` and `hel-arabic.ly` (quarter tones, maqam key signatures),
`makam.ly` (Turkish komas), `gregorian.ly` (neumes, ligatures),
`bagpipe.ly` (pipe embellishments), plus note-name languages (`english.ly`,
`italiano.ly`, `deutsch.ly`, ...). Microtonal accidentals in general are
available through `\alterBroken` and custom `KeySignature` handling; other
tunings can be notated even when MIDI cannot reproduce them.

## 9. Notation vocabulary

A non-exhaustive list of things that are one command each: articulations
(`-.` `->` `-^` `--` `-!` `-_`), fermatas, trills with printed accidentals,
mordents/turns/prall, glissando, arpeggio (with arrow variants), tremolo
(`\repeat tremolo`), harmonics (natural and artificial), string/fingering
numbers, bowing marks, pedal brackets, breath marks, caesura, multi-measure
rests, cue notes (`\cueDuring`), ossia staves, grace notes (`\grace`,
`\acciaccatura`, `\appoggiatura`), tuplets of any ratio (`\tuplet 5/4`),
metre changes mid-piece, polymetric staves, cross-staff beaming (`\change`),
clef changes, `\ottava`, rehearsal marks, text spanners, custom bar lines,
repeat structures, and colour/size overrides on any grob.

## 10. Input and output formats

**Out:** PDF, PNG, SVG, PostScript, EPS, MIDI. `-dcrop` gives tightly cropped
output; `-dpreview` renders just the first system; `--formats=pdf,png` produces
several at once.

**In:** `.ly` source; `musicxml2ly` imports MusicXML from Sibelius/Finale/
MuseScore; `midi2ly` imports MIDI (rhythmically literal -- expect to clean up);
`abc2ly` imports ABC. `lilypond-book` embeds engraved music in LaTeX, HTML or
DocBook.

## 11. Limits of the MIDI side

LilyPond's MIDI output is a literal reading of the score:

- **It does render**: pitches, rhythms, tempo and tempo changes, dynamics and
  hairpins (as velocity), per-staff instrument assignment, drum channel routing,
  repeats when unfolded, grace notes.
- **It does not render**: phrasing, rubato, articulation-dependent sample
  switching, string/valve idiom, room sound, or balance beyond what velocity
  gives you. Slurs affect nothing audibly by default.
- `\include "articulate.ly"` and wrapping the MIDI score in `\articulate`
  improves matters -- it realises ornaments, shortens staccato notes, applies
  ritardandi -- at the cost of slightly altering the played rhythms.

Anything beyond that belongs to the synthesis stage, and `render.py` covers the
practical part of it: hairpins over held notes performed as CC11 expression,
per-part gain and stereo balance (`--mix`), per-part and master equalisation
(`--eq`, `--master-eq`), reverb, band limits and levelling. See
`audio-and-midi.md`, sections 8 to 10.

## 12. Practical ceilings

- Scores of several hundred pages compile fine; compile time grows roughly
  linearly, a few seconds per page at typical density.
- 15 melodic staves plus drums is the MIDI limit per score (16 channels, one
  reserved for percussion). Beyond that, split into multiple `\score` blocks or
  set `midiChannelMapping = #'instrument` to share channels between staves with
  the same instrument.
- Very dense systems (8+ staves with 16th-note activity) engrave correctly but
  need a larger page or a smaller `set-global-staff-size` to stay legible --
  and for video, force short systems with `\break`.
