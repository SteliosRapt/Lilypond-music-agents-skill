\version "2.24.3"
%% Lead sheet template: chord symbols, melody, lyrics.
%% For guitar diagrams add:  \new FretBoards \harmony  above the ChordNames.
#(set-global-staff-size 19)

\paper { #(set-paper-size "a4") ragged-last-bottom = ##f }

\header {
  title = "Song Title"
  composer = "original composition"
  tagline = ##f
}

global = { \key f \major \time 4/4 \tempo 4 = 96 }
breaks = { \repeat unfold 3 { s1 s1 s1 s1 \break } s1 s1 s1 s1 }

harmony = \chordmode {
  f1 | d:m7 | bes:maj7 | c:7 |
  f1 | d:m7 | bes:maj7 | c:7 |
  a:m7 | d:m7 | g:m7 | c:7.9- |
  f1 | bes:maj7 | f1 | c:7 |
}

melody = {
  \global
  \clef treble
  a'4 c''4 d''4 c''4 | a'2. f'4 |
  g'4 bes'4 c''4 bes'4 | g'1 |
  a'4 c''4 d''4 f''4 | e''2. c''4 |
  d''4 c''4 bes'4 a'4 | g'1 |
  c''4 c''4 bes'8 a'8 g'4 | f'2 r2 |
  bes'4 bes'4 a'8 g'8 f'4 | e'2 r2 |
  a'4 c''4 f''2 ~ | f''2 d''4 c''4 |
  a'1 ~ | a'1 \bar "|."
}

words = \lyricmode {
  Write the first line here now,
  words be -- neath each note.
  Hy -- phens split a word,
  un -- der -- scores ex -- tend __
  notes that hold, and skips
  pass a bar of rest,
  ev -- 'ry syl -- la -- ble
  lands. __
}

\score {
  <<
    \new ChordNames \harmony
    \new Staff \with { midiInstrument = "acoustic grand" }
      \new Voice = "lead" \melody
    \new Lyrics \lyricsto "lead" \words
    \new Devnull { \breaks }
  >>
  \layout { }
  \midi { }
}
