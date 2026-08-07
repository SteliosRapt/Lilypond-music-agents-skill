\version "2.24.3"
%% The fixture bank_check.py sings. Deliberately ordinary, which is the point:
%% torture.ly exists to break the *extraction*, and a bank being qualified
%% should be answering for its own behaviour rather than for a pickup bar and a
%% mid-score metre change. Every note is inside any bank's trained range, the
%% words are common enough that a real dictionary has them, and there is one
%% held note (the tie), one melisma (`__`) and one rest long enough to split the
%% line into two phrases -- the three things whose timing a bank can get wrong.
\header { tagline = ##f }

global = { \key c \major \time 4/4 \tempo 4 = 90 }

melody = {
  \global
  \clef treble
  e'4 g'4 a'4 g'4 | c''2. a'4 | g'4 e'4 f'4 g'4( | a'1) |
  r2 e'4 g'4 | a'2 g'4 e'4 | f'4 g'4 a'2 ~ | a'1 \bar "|."
}

%% One syllable per note, counted: ten then the extender over the slurred bar 4,
%% then eight, the last of them tied across the final barline. `__` only
%% extends a syllable where there is a real melisma to extend -- without the
%% slur in bar 3 LilyPond drops it and slides every later syllable one note
%% along, which the vowel-onset check below then reports as the bank's fault.
words = \lyricmode {
  Lan -- terns drift a -- long the qui -- et wa -- ter. __
  Morn -- ing finds the ri -- ver still now.
}

\score {
  <<
    \new Staff \with { midiInstrument = "acoustic grand" }
      \new Voice = "singer" \melody
    \new Lyrics \lyricsto "singer" \words
  >>
  \layout { }
  \midi { }
}
