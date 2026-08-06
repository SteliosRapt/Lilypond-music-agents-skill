\version "2.24.3"
#(set-global-staff-size 13)

\paper {
  #(set-paper-size "a4")
  ragged-last-bottom = ##f
  system-system-spacing.basic-distance = #14
  left-margin = 12\mm
  right-margin = 12\mm
}

\header {
  title = "Lantern Procession"
  subtitle = "voice, shakuhachi, erhu, koto, percussion, piano, drone"
  composer = "original composition"
  tagline = ##f
}

global = { \key g \minor \time 4/4 \tempo 4 = 63 }
breaks = { \repeat unfold 7 { s1 s1 \break } s1 s1 }

%% ---------------- VOICE ----------------
voicePart = {
  \global
  \clef treble
  R1*6 |
  r2 d''4\mp ees''4 |
  d''2 ~ d''8 bes'8 a'4 |
  bes'4 a'4 g'2 |
  d''1 |
  r4 f''4\mf ees''4 d''4 |
  ees''2 d''4 c''4 |
  bes'4 a'4 bes'2 ~ |
  bes'1\> |
  R1\! |
  R1 |
}

voiceWords = \lyricmode {
  Lan -- terns drift, the wa -- ter holds no name.
  Ash and si -- lence, blos -- som in the flame.
}

%% ---------------- SHAKUHACHI ----------------
shakuhachi = {
  \global
  \clef treble
  R1*2 |
  r2 r4 \acciaccatura g''8 a''4\p |
  bes''2. aes''4 |
  g''2 ~ g''8 r8 ees''8 r8 |
  d''1 |
  R1*4 |
  r4 a''8\mf bes''8 ~ bes''4 aes''8 g''8 |
  a''2 ~ a''8 g''8 ees''4 |
  d''4\< ees''4 fis''4 g''4 |
  aes''2 a''2\! |
  d''1\pp |
  R1 |
}

%% ---------------- ERHU ----------------
erhu = {
  \global
  \clef treble
  R1*2 |
  d'8^"pizz."\p r8 a'8 r8 d''4 r4 |
  bes'8 r8 a'8 r8 g'4 r4 |
  R1 |
  r2 d''4^"arco"\mp( ees''4 |
  d''2) bes'4 a'4 |
  g'1 |
  bes'2 a'2 |
  g'2 fis'4 g'4 |
  a'1\< |
  bes'1 |
  <a' d''>2\mf <bes' ees''>2 |
  <a' cis''>1 |
  d''1\> |
  R1\! |
}

%% ---------------- KOTO ----------------
koto = {
  \global
  \clef treble
  d'16\p g' a' bes' d'' bes' a' g' d'16 g' a' bes' d'' bes' a' g' |
  d'16 ees' g' a' bes' a' g' ees' d'16 ees' g' a' bes' a' g' ees' |
  d'16 g' a' bes' d'' bes' a' g' d'16 g' a' bes' d'' bes' a' g' |
  d'16 ees' g' a' bes' a' g' ees' d'16 ees' g' a' bes' a' g' ees' |
  d'16 g' a' bes' d'' bes' a' g' d'16 g' a' bes' d'' bes' a' g' |
  d'16 ees' g' a' bes' a' g' ees' d'16 ees' g' a' bes' a' g' ees' |
  d'8\mp g' a' bes' d'' bes' a' g' |
  d'8 ees' g' a' bes' a' g' ees' |
  d'8 g' a' bes' d'' bes' a' g' |
  d'8 ees' g' a' bes' a' g' ees' |
  d'16\f g' a' bes' d'' bes' a' g' d'16 g' a' bes' d'' bes' a' g' |
  d'16 ees' g' a' bes' a' g' ees' d'16 ees' g' a' bes' a' g' ees' |
  d'16 g' a' bes' d'' bes' a' g' d'16 g' a' bes' d'' bes' a' g' |
  d'16 ees' g' a' bes' a' g' ees' d'16 ees' g' a' bes' a' g'\> ees' |
  d'16 g' a' bes' d''4 ~ d''2\! |
  <d' g' a' d''>1\pp \arpeggio |
}

%% ---------------- PERCUSSION ----------------
percussion = \drummode {
  \time 4/4
  tt1\mp |
  r1 |
  r1 |
  wbh8\p r8 wbl8 r8 wbh8 r8 r4 |
  r1 |
  wbh8 r8 wbl8 r8 wbh4 r4 |
  bd4\mp r4 bd4 r4 |
  bd4 r4 bd8 bd8 r4 |
  bd4 r4 bd4 r4 |
  bd2 r2 |
  bd8\f bd8 wbh8 r8 bd4 wbl4 |
  bd8 bd8 wbh8 r8 bd4 wbl4 |
  bd4 bd4 bd4 bd4 |
  tt1\ff |
  r1 |
  tt1\pp |
}

%% ---------------- PIANO ----------------
pianoUpper = {
  \global
  \clef treble
  R1*4 |
  <d' g'>2\pp <ees' bes'>2 |
  <d' a'>1 |
  <d' g' bes'>1\p |
  <ees' g' bes'>1 |
  <d' fis' a'>1 |
  <d' g' bes'>1 |
  d''16\f ees'' d'' bes' a' bes' d'' ees'' g'' ees'' d'' bes' a' g' ees' d' |
  cis''16 d'' ees'' d'' cis'' d'' fis'' g'' a'' g'' fis'' d'' cis'' d'' bes' a' |
  <fis' a' d''>2 <g' bes' ees''>2 |
  <a' cis'' e''>2\> <a' bes' d''>2 |
  <d' a' d''>1\p |
  <d' a' d''>1\pp |
}

pianoLower = {
  \global
  \clef bass
  <d,, d,>1\pp |
  <d,, d,>1 |
  d,8\p a, d a, d, a, d a, |
  d,8 a, d a, d, a, d a, |
  ees,8 bes, ees bes, ees, bes, ees bes, |
  d,8 a, d a, d, a, d a, |
  d,8 a, d a, d, a, d a, |
  ees,8 bes, ees bes, ees, bes, ees bes, |
  d,8 aes, d aes, d, aes, d aes, |
  d,8 a, d a, d, a, d a, |
  d,16\f a, d a, d, a, d a, d, a, d a, d, a, d a, |
  ees,16 bes, ees bes, ees, bes, ees bes, d,16 a, d a, d, a, d a, |
  <d, d>4 <ees, ees>4 <d, d>4 <cis, cis>4 |
  <d, d>1\> |
  <d, a,>1\p |
  <d, a,>1\pp |
}

%% ---------------- DRONE ----------------
drone = {
  \global
  \clef bass
  <d a>1\ppp |
  <d a>1 |
  <d aes>1 |
  <d a>1 |
  <ees bes>1 |
  <d a>1 |
  <d a>1\pp |
  <ees bes>1 |
  <d aes>1 |
  <d a>1 |
  <d a>1\mp |
  <ees bes>1 |
  <d a>1 |
  <cis gis>1\> |
  <d a>1 |
  <d a>1\ppp |
}

\score {
  <<
    \new Staff \with {
      instrumentName = "Voice"
      shortInstrumentName = "Vo."
      midiInstrument = "choir aahs"
    } \new Voice = "singer" { \voicePart }
    \new Lyrics \lyricsto "singer" \voiceWords

    \new StaffGroup <<
      \new Staff \with {
        instrumentName = "Shakuhachi"
        shortInstrumentName = "Shak."
        midiInstrument = "shakuhachi"
      } { \shakuhachi }
      \new Staff \with {
        instrumentName = "Erhu"
        shortInstrumentName = "Erhu"
        midiInstrument = "fiddle"
      } { \erhu }
      \new Staff \with {
        instrumentName = "Koto"
        shortInstrumentName = "Koto"
        midiInstrument = "koto"
      } { \koto }
    >>

    \new DrumStaff \with {
      instrumentName = "Percussion"
      shortInstrumentName = "Perc."
    } { \percussion }

    \new PianoStaff \with {
      instrumentName = "Piano"
      shortInstrumentName = "Pno."
    } <<
      \new Staff \with { midiInstrument = "acoustic grand" } { \pianoUpper }
      \new Staff \with { midiInstrument = "acoustic grand" } { \pianoLower }
    >>

    \new Staff \with {
      instrumentName = "Drone"
      shortInstrumentName = "Dr."
      midiInstrument = "pad 2 (warm)"
    } { \drone }

    \new Devnull { \breaks }
  >>
  \layout { }
  \midi { }
}
