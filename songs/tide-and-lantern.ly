\version "2.24.3"
%% "Tide and Lantern" -- unaccompanied SATB, one DiffSinger voicebank per part.
%%
%%   Soprano  LIEE : Immortal Idol MM 2.8   (the only bank measured to hold C6)
%%   Alto     CANARY v106
%%   Tenor    TIGER v102
%%   Bass     TRITON v106
%%
%% D dorian, ending on a Picardy D add9. Every part stays between C3 and E5,
%% which is where all four banks were measured to sing at full level -- the
%% probe is in songs/notes.md. The tenor carries the tune because TIGER is the
%% quietest of the four and needs to be the thing in front.
%%
%% Every held note is a TIE rather than a `__` extender: a tied note takes no
%% syllable, so each part's syllable count is exactly its number of untied
%% notes, and a miscount shows up as a LilyPond warning instead of as a line
%% that sings one word late from bar 12 onwards.
%%
%% Bars 21-24 are a canon at the fifth, entering B-T-A-S two beats apart, on a
%% motif built from D-F-G-A-C only: every vertical combination it can produce
%% is a subset of Dm11, so the stagger stays consonant however the parts line
%% up against each other.

#(set-global-staff-size 18)
\paper { #(set-paper-size "a4") ragged-last-bottom = ##f }

\header {
  title = "Tide and Lantern"
  subtitle = "for four synthetic voices, unaccompanied"
  composer = "original composition"
  tagline = ##f
}

global = { \key d \dorian \time 4/4 \tempo 4 = 72 }

%% ---------------------------------------------------------------- soprano
sopranoMusic = {
  \global \clef treble
  % 1-4  intro: enters last, on the ninth
  R1 | R1 | R1 | e''1~ |
  % 5-12  verse: a hummed line over the tenor's tune
  e''1 | d''1~ | d''1 | c''1 |
  d''1~ | d''1 | c''1~ | c''1 |
  % 13-20  chorus
  r4 c''4 bes'4 a'4~ | a'1 |
  r4 c''4 bes'4 g'4~ | g'1 |
  r4 d''4 c''4 a'4~  | a'1 |
  a'4 bes'4 c''4 a'4 | d''1 |
  % 21-24  canon, fourth entry, bar 22 beat 3
  R1 | r2 a'8 a'8 c''4 | d''4 e''4 d''4 c''4 | a'2 r2 |
  % 25-28  tag
  e''1~ | e''1~ | e''1~ | e''1 \bar "|."
}

sopranoWords = \lyricmode {
  mm mm mm mm mm
  hold the line hold the light hold the line un -- til we come home
  light on the wa -- ter hold the line
  home
}

%% ------------------------------------------------------------------- alto
altoMusic = {
  \global \clef treble
  % 1-4  third voice in
  R1 | R1 | f'1~ | f'1 |
  % 5-12
  f'1~ | f'1 | d'1~ | d'1 |
  f'1~ | f'1 | d'1 | c'1 |
  % 13-20
  r4 f'4 f'4 f'4~ | f'1 |
  r4 f'4 f'4 f'4~ | f'1 |
  r4 f'4 f'4 f'4~ | f'1 |
  e'4 f'4 g'4 f'4 | a'1 |
  % 21-24  canon, third entry, bar 22 beat 1
  R1 | d'8 d'8 f'4 g'4 a'4 | g'4 f'4 d'2 | R1 |
  % 25-28
  R1 | fis'1~ | fis'1~ | fis'1 \bar "|."
}

altoWords = \lyricmode {
  ooh ooh ooh ooh ooh ooh
  hold the line hold the light hold the line un -- til we come home
  light on the wa -- ter hold the line
  home
}

%% ------------------------------------------------------------------ tenor
tenorMusic = {
  \global \clef "treble_8"
  % 1-4  second voice in
  R1 | a1~ | a1~ | a1 |
  % 5-12  the tune
  a4 c'4 d'4 c'4 | a2 g4 f4 | g4 a4 c'4 a4 | g2. e4 |
  a4 c'4 d'4 e'4 | f'2 d'4 c'4 | c'4 d'4 c'4 a4 | g2 f2 |
  % 13-20
  r4 d'4 d'4 d'4~ | d'1 |
  r4 d'4 d'4 d'4~ | d'1 |
  r4 d'4 d'4 d'4~ | d'1 |
  c'4 d'4 e'4 d'4 | f'1 |
  % 21-24  canon, second entry, bar 21 beat 3
  r2 a8 a8 c'4 | d'4 e'4 d'4 c'4 | a2 r2 | R1 |
  % 25-28
  R1 | R1 | a1~ | a1 \bar "|."
}

tenorWords = \lyricmode {
  ooh
  The tide comes in a -- gain to --
  night and eve -- ry lan -- tern
  burns a lit -- tle low -- er than
  it did when we were young.
  hold the line hold the light hold the line un -- til we come home
  light on the wa -- ter hold the line
  home
}

%% ------------------------------------------------------------------- bass
bassMusic = {
  \global \clef bass
  % 1-4  the first voice heard
  d1~ | d1~ | d1~ | d1 |
  % 5-12
  d1~ | d1 | g1~ | g1 |
  d1~ | d1 | g2 c2 | f1 |
  % 13-20
  r4 bes4 bes4 bes4~ | bes1 |
  r4 g4 g4 g4~ | g1 |
  r4 d4 d4 d4~ | d1 |
  a4 a4 a4 a4 | d1 |
  % 21-24  canon, first entry, bar 21 beat 1
  d8 d8 f4 g4 a4 | g4 f4 d2 | R1 | R1 |
  % 25-28
  d1~ | d1~ | d1~ | d1 \bar "|."
}

bassWords = \lyricmode {
  ooh ooh ooh ooh ooh ooh ooh
  hold the line hold the light hold the line un -- til we come home
  light on the wa -- ter hold the line
  home
}

\score {
  \new ChoirStaff <<
    \new Staff \with { instrumentName = "S." shortInstrumentName = "S." }
      \new Voice = "soprano" \sopranoMusic
    \new Lyrics \lyricsto "soprano" \sopranoWords
    \new Staff \with { instrumentName = "A." shortInstrumentName = "A." }
      \new Voice = "alto" \altoMusic
    \new Lyrics \lyricsto "alto" \altoWords
    \new Staff \with { instrumentName = "T." shortInstrumentName = "T." }
      \new Voice = "tenor" \tenorMusic
    \new Lyrics \lyricsto "tenor" \tenorWords
    \new Staff \with { instrumentName = "B." shortInstrumentName = "B." }
      \new Voice = "bass" \bassMusic
    \new Lyrics \lyricsto "bass" \bassWords
  >>
  \layout { }
  \midi { }
}
