# Notation recipes

Snippets are written for LilyPond 2.24 and use absolute octaves (`c'` = middle
C). Each is small enough to paste into a score and adapt.

Contents:
1. Skeleton of a multi-part score
2. Shared attributes and forced line breaks
3. Voice with lyrics, multiple verses
4. Choral SATB
5. Polyphony within one staff
6. Percussion
7. Guitar: tablature, chord diagrams
8. Lead sheet: chord symbols over a melody
9. Transposing instruments and extracted parts
10. Rhythm: tuplets, tremolo, grace notes, pickups
11. Repeats, and what they do to bar counting
12. Dynamics that survive into MIDI
13. Layout control for video

---

## 1. Skeleton of a multi-part score

```lilypond
\version "2.24.3"
global = { \key g \minor \time 4/4 \tempo 4 = 72 }

flute = { \global \clef treble  c''4 d'' ees'' f'' | g''1 | }
cello = { \global \clef bass    c4   d   ees  f    | g1   | }

\score {
  <<
    \new StaffGroup <<
      \new Staff \with { instrumentName = "Flute"
                         midiInstrument = "flute" } \flute
      \new Staff \with { instrumentName = "Cello"
                         midiInstrument = "cello" } \cello
    >>
  >>
  \layout { }
  \midi { }
}
```

`\layout { }` and `\midi { }` must both be present to get both outputs.

## 2. Shared attributes and forced line breaks

Put key/metre/tempo in one variable so a change happens once:

```lilypond
global = { \key d \minor \time 3/4 \tempo 4 = 96 }
```

Force a system break every two bars by feeding a skeleton to a `Devnull`
context, which discards the notes but keeps the breaks:

```lilypond
breaks = { \repeat unfold 7 { s1 s1 \break } s1 s1 }
% ... inside \score's << >>:
\new Devnull { \breaks }
```

Predictable system widths matter for video: two or four bars per system reads
well, "whatever fits" does not.

## 3. Voice with lyrics, multiple verses

```lilypond
melody = { \clef treble c'4 d' e' f' | g'2 e' | }
verseOne = \lyricmode { \set stanza = "1." Morn -- ing comes a -- gain __ }
verseTwo = \lyricmode { \set stanza = "2." Eve -- ning falls so slow __ }

\new Staff \new Voice = "lead" \melody
\new Lyrics \lyricsto "lead" \verseOne
\new Lyrics \lyricsto "lead" \verseTwo
```

- `--` hyphenates within a word; `__` extends a syllable under held notes
- `_` skips a note without printing anything
- tied and slurred notes take one syllable automatically
- `\skip 1` passes over a bar of rest in `\lyricmode`

## 4. Choral SATB

```lilypond
\new ChoirStaff <<
  \new Staff \with { instrumentName = "S" } \new Voice = "sop" \soprano
  \new Lyrics \lyricsto "sop" \words
  \new Staff \with { instrumentName = "A" } \new Voice = "alt" \alto
  \new Lyrics \lyricsto "alt" \words
  \new Staff \with { instrumentName = "T" \clef "treble_8" } \new Voice = "ten" \tenor
  \new Lyrics \lyricsto "ten" \words
  \new Staff \with { instrumentName = "B" \clef bass } \new Voice = "bas" \bass
  \new Lyrics \lyricsto "bas" \words
>>
```

Two-staff SATB instead: soprano/alto share the treble staff via `\voiceOne`/
`\voiceTwo`, tenor/bass share the bass staff.

## 5. Polyphony within one staff

```lilypond
\new Staff <<
  { \voiceOne g'2 a'4 b' | c''1 }
  \\
  { \voiceTwo c'2 c'4 d' | e'1 }
>>
```

`\\` creates simultaneous voices with automatic stem directions. Use
`\oneVoice` to rejoin. For a third line, name voices explicitly rather than
chaining `\\`.

## 6. Percussion

```lilypond
\new DrumStaff \with { instrumentName = "Perc." }
\drummode {
  \time 4/4
  bd4 sn8 sn8 bd4 sn4 |
  tt1 |                       % tam-tam / gong
  wbh8 r8 wbl8 r8 hh4 cymc4 | % wood blocks, hi-hat, crash
}
```

One-line staff for a single instrument:

```lilypond
\new DrumStaff \with {
  \override StaffSymbol.line-count = #1
  drumStyleTable = #percussion-style
}
```

Full drum vocabulary: `references/gm-instruments.md`.

## 7. Guitar: tablature, chord diagrams

```lilypond
music = { e16 b' g' e' b g e2 }

\new StaffGroup <<
  \new Staff { \clef "treble_8" \music }
  \new TabStaff { \music }
>>
```

Custom tuning and chord diagrams:

```lilypond
\new TabStaff \with { stringTunings = \stringTuning <d, a, d g b e'> } { \music }
\new FretBoards \chordmode { c1 g a:m f }
```

## 8. Lead sheet: chord symbols over a melody

```lilypond
harmony = \chordmode { c1 | a:m7 | d:9 | g:7.5+ | }

<<
  \new ChordNames \harmony
  \new Staff \new Voice = "lead" \melody
  \new Lyrics \lyricsto "lead" \words
>>
```

## 9. Transposing instruments and extracted parts

```lilypond
% B-flat clarinet: written pitch on the page, concert pitch in MIDI
\new Staff \with { instrumentName = "Cl. in B♭" midiInstrument = "clarinet" }
{ \transposition bes \clarinetPart }

% same source, printed in concert pitch for the conductor's score
\transpose bes c' \clarinetPart

% score-only and part-only material from one source
\tag #'score { \cueDuring #"fl" #UP s1 }
\tag #'part  { R1 }
% then: \keepWithTag #'part \music
```

## 10. Rhythm: tuplets, tremolo, grace notes, pickups

```lilypond
\tuplet 3/2 { c'8 d' e' }            % triplet
\tuplet 5/4 { c'16 d' e' f' g' }     % quintuplet

\repeat tremolo 4 { c'32 g' }        % one beat of measured tremolo
% split long tremolos into beat-length groups; a single
% \repeat tremolo 16 triggers stem-direction warnings

\acciaccatura g'8 a'4                % crushed grace note
\appoggiatura g'8 a'4                % accented grace note
\grace { g'16 a'16 } b'4             % unslashed grace group

\partial 4 g'4 |                     % pickup: pass --pickup 1 to render.py
```

## 11. Repeats, and what they do to bar counting

```lilypond
\repeat volta 2 { c'1 | d'1 | }
\alternative { { e'1 } { f'1 } }
```

The page shows the music once; the MIDI plays it twice. `render.py` compares
printed bars against MIDI bars and warns when they disagree. For a video, either
write the repeat out with `\repeat unfold`, or produce a separate unfolded score
for playback:

```lilypond
\score { \unfoldRepeats \music \midi { } }   % playback
\score { \music \layout { } }                % page
```

## 12. Dynamics that survive into MIDI

```lilypond
c'1\pp | d'2\< e'2 | f'1\f | g'1\> | a'1\!
```

Absolute marks set velocity; hairpins interpolate between them. Rules that
matter: every `\<`/`\>` must end with `\!` or a new dynamic; a hairpin with no
target dynamic ramps to a default step, so state where it is going; and
dynamics attach to the note they follow, so put them on the note where the
change starts.

For richer playback:

```lilypond
\include "articulate.ly"
\score { \articulate \music \midi { } }
```

## 13. Layout control for video

```lilypond
#(set-global-staff-size 15)     % smaller staves fit more systems per page

\paper {
  #(set-paper-size "a4")
  ragged-last-bottom = ##f
  system-system-spacing.basic-distance = #16
  left-margin = 14\mm
  right-margin = 14\mm
}
```

Landscape pages (`"a4landscape"`) give wider systems, which suits a 16:9 video;
portrait suits 9:16. Fewer bars per system means a slower-moving playhead and
more legible notation on a phone.
