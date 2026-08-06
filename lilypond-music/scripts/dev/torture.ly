%% torture.ly -- the awkward cases, in one short score.
%%
%% Used by scripts/dev/selftest.py. Every feature here is one that has broken
%% the pipeline at some point, so it is a fixture rather than a demonstration:
%%
%%   a pickup bar                 the timeline has to start below zero
%%   a mid-score metre change     bar widths stop being uniform
%%   a mid-score tempo change     seconds per bar stop being uniform
%%   ties across a barline        one sung note, two note events
%%   a melisma under a slur       a note with no syllable of its own
%%   `_` in \lyricmode            a syllable that is a single space, to be dropped
%%   two verses                   two lyric lines on one voice
%%   a bar filled by one whole note   the MusicXML measure-splitting edge case
%%   a hairpin over a held note   nothing for velocity to shape
%%   a rest long enough to breathe    a phrase boundary
%%   a word absent from small dictionaries    the G2P path
%%   a chord and a drum staff     parts that are not one monophonic line

\version "2.24.3"

\header { title = "Torture" tagline = ##f }

global = { \key f \major \time 4/4 \tempo 4 = 96 }

melody = {
  \global
  \partial 4 c'4 |
  f'4 g'4 a'4 bes'4 |
  c''2 ~ c''4 a'4 |
  \time 3/4
  bes'4 a'4 g'4 |
  f'2. |
  \time 4/4
  \tempo 4 = 72
  r2 a'4\< bes'4 |
  c''1\> |
  d''1\! |
  r1 |
  f'4 g'8( a'8 ) bes'4 c''4 |
  f'1 |
}

wordsOne = \lyricmode {
  Now
  hear the qua -- si mo -- dal tune,
  a chro -- ma tic
  _
  drift a --
  cross the moon.
}

wordsTwo = \lyricmode {
  Then
  watch the lan -- terns leave the shore,
  in si -- lence
  _
  fall and __
  rise once more.
}

bass = {
  \global
  \partial 4 r4 |
  <f, c>1 |
  <f, c>1 |
  \time 3/4
  <g, d>2. |
  <f, c>2. |
  \time 4/4
  <bes, f>1 |
  <c e>1 |
  <bes, d>1 |
  r1 |
  <f, c>1 |
  <f, c>1 |
}

beat = \drummode {
  \partial 4 r4 |
  bd4 sn4 bd4 sn4 |
  bd4 sn4 bd8 bd8 sn4 |
  \time 3/4
  bd4 sn4 bd4 |
  sn2. |
  \time 4/4
  r1 | r1 | r1 | r1 |
  bd4 sn4 bd4 sn4 |
  bd1 |
}

\score {
  <<
    \new Staff \with { midiInstrument = "choir aahs" }
      \new Voice = "singer" { \melody }
    \new Lyrics \lyricsto "singer" \wordsOne
    \new Lyrics \lyricsto "singer" \wordsTwo
    \new Staff \with { midiInstrument = "acoustic grand" } { \clef bass \bass }
    \new DrumStaff { \beat }
  >>
  \layout { }
  \midi { }
}
