%% "Along the Shore" -- the twelve bars in the README's animation.
%%
%% Shaped for a banner rather than a page: two systems of six bars on a
%% wide, short paper, so the frame is filled by notation instead of margin.
%% Everything else is ordinary -- one variable per part, a shared \global, a
%% bar check at the end of every bar, and a \break where each system should
%% end so the layout is the same every time it is engraved.
%%
%%   python3 tools/make_media.py        regenerates the README's media from this

\version "2.24.0"

\header {
  title = "Along the Shore"
  tagline = ##f
}

\paper {
  paper-width = 280\mm
  paper-height = 132\mm
  left-margin = 12\mm
  right-margin = 12\mm
  top-margin = 6\mm
  bottom-margin = 6\mm
  indent = 0
  system-system-spacing.padding = #3
  print-page-number = ##f
}

global = {
  \key d \minor
  \time 4/4
  \tempo 4 = 88
}

melody = {
  \global
  d''4\mp a' bes' c'' |
  d''8 c'' bes'4 a'2 |
  f'4\< g' a' bes'\! |
  a'2 g' |
  f'4\mf g' a' d'' |
  c''4 bes' a' g' |
  \break
  a'4\< bes' c''2\! |
  d''1\> |
  d''4\mp c'' bes' a' |
  g'4 a' bes' c'' |
  e''4\> d''2.\! |
  r1 |
}

words = \lyricmode {
  The lamps come on a -- long the shore,
  the wa -- ter takes them all
  and turns them slow -- ly out to sea,
  and holds them there
  un -- til the morn -- ing finds the shore a -- gain.
}

upper = {
  \global
  <d' f' a'>2 <d' f' a'> |
  <d' f' a'>1 |
  <d' f' bes'>1 |
  <d' g' bes'>1 |
  <d' f' bes'>1 |
  <c' f' a'>1 |
  <d' g' bes'>1 |
  <cis' e' g'>1 |
  <d' f' a'>1 |
  <d' f' bes'>1 |
  <cis' e' g'>1 |
  <d' f' a'>1 |
}

lower = {
  \global
  \clef bass
  d2 a | d a | bes f | g d |
  bes2 f | f c | g d | a e |
  d2 a | bes f | a e | d1 |
}

\score {
  <<
    \new Staff \with { instrumentName = "Voice" midiInstrument = "voice oohs" }
      \new Voice = "singer" \melody
    \new Lyrics \lyricsto "singer" \words
    \new PianoStaff \with { instrumentName = "Piano" } <<
      \new Staff \with { midiInstrument = "acoustic grand" } \upper
      \new Staff \with { midiInstrument = "acoustic grand" } \lower
    >>
  >>
  \layout { }
  \midi { }
}
