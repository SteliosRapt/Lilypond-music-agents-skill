\version "2.24.3"
%% Solo piano template: two staves, forced 4-bar systems for clean video.
#(set-global-staff-size 17)

\paper {
  #(set-paper-size "a4")
  ragged-last-bottom = ##f
  left-margin = 15\mm
  right-margin = 15\mm
}

\header {
  title = "Title"
  composer = "original composition"
  tagline = ##f
}

global = { \key d \minor \time 4/4 \tempo 4 = 72 }
breaks = { \repeat unfold 2 { s1 s1 s1 s1 \break } s1 s1 s1 s1 }

upper = {
  \global
  \clef treble
  r4 a'8\p bes'8 ~ bes'4 a'8 g'8 |
  f'2 ~ f'8 e'8 d'4 |
  e'4\< f'4 g'4 a'4 |
  bes'1\! |
  a'8 g'16 f' e'8 d'16 e' f'4 ~ f'8 e'16 d' |
  <d' a'>2\mf <e' bes'>2 |
  <d' g' bes'>1\> |
  d'1\! |
  r4 \acciaccatura f'8 e'4 d'2 |
  cis'1\pp |
  r1 |
  d'1\fermata \bar "|."
}

lower = {
  \global
  \clef bass
  d8\p a, d a, d, a, d a, |
  d8 a, d a, d, a, d a, |
  <bes,, bes,>1 |
  <a,, a,>1 |
  d16 a, d a, d, a, d a, d, a, d a, d, a, d a, |
  <d, d>2\mf <e, e>2 |
  <bes,, bes,>1\> |
  <a,, a,>1\! |
  <d, a,>1\pp |
  <a,, e,>1 |
  <d, a,>1 |
  d,1\fermata \bar "|."
}

\score {
  <<
    \new PianoStaff \with { instrumentName = "Piano" } <<
      \new Staff \with { midiInstrument = "acoustic grand" } \upper
      \new Staff \with { midiInstrument = "acoustic grand" } \lower
    >>
    \new Devnull { \breaks }
  >>
  \layout { }
  \midi { }
}
