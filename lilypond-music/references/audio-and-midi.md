# From score to sound

Contents:
1. Getting MIDI out of a score
2. Assigning instruments
3. Channels, and the 15-part ceiling
4. Dynamics, tempo, and what actually reaches the MIDI
5. articulate.ly
6. Soundfonts
7. fluidsynth
8. Mastering with ffmpeg
9. Rendering stems and mixing
10. Sanity checks

---

## 1. Getting MIDI out of a score

A `\score` block emits MIDI only if it contains `\midi { }`. Keep `\layout { }`
alongside it to get both page and performance from one compile:

```lilypond
\score { \music \layout { } \midi { } }
```

Output lands next to the PDF as `name.midi` (or `name-1.midi`, `name-2.midi`
when a file has several `\score` blocks -- check which one you are feeding to
the synthesiser).

## 2. Assigning instruments

Set the General MIDI program per staff:

```lilypond
\new Staff \with { midiInstrument = "shakuhachi" } \flutePart
```

The name must match LilyPond's table exactly. An unrecognised name does not
raise an error -- it falls back to piano, which is easy to miss when you have
eight staves. The authoritative list is in `gm-instruments.md`.

Useful when writing atmospheric or non-Western music: `koto`, `shamisen`,
`shakuhachi`, `sitar`, `banjo`, `kalimba`, `fiddle`, `taiko drum`, `agogo`,
`tinkle bell`, `steel drums`, `pan flute`, `ocarina`, `shanai`, and the eight
`pad *` programs for drones. `choir aahs`, `voice oohs` and `pad 4 (choir)`
cover wordless vocal textures.

A `DrumStaff` needs no `midiInstrument`; it is routed to the percussion channel
automatically.

## 3. Channels, and the 15-part ceiling

MIDI has 16 channels and channel 10 is reserved for percussion, so a score can
carry 15 distinct melodic parts. LilyPond's default `midiChannelMapping =
#'staff` gives each staff its own channel. Alternatives:

```lilypond
\score { \music \midi { \context { \Score midiChannelMapping = #'instrument } } }
```

`#'instrument` shares a channel between staves using the same program (more
parts, but they can no longer be panned or balanced separately);
`#'voice` gives every voice its own channel (fewer parts, useful for divisi).

Verify what you actually got by dumping the file -- a three-line script that
walks the tracks and prints channel/program per track will catch a staff that
silently defaulted to piano.

## 4. Dynamics, tempo, and what actually reaches the MIDI

**Reaches it:** note pitches and durations, `\tempo` marks including mid-score
changes, absolute dynamics (`\ppp` … `\fff`) as note velocity, hairpins
(`\<` `\>`) as interpolated velocity, `\partial` and unfolded repeats, grace
notes, drum mapping, and per-staff instruments.

**Does not:** slurs and phrasing, articulation marks (unless you use
`articulate.ly`), text directions, fermatas (they are printed but not held),
pedal marks, and anything about tone or timbre beyond the program number.

Practical consequence: write hairpins even where a performer would supply the
shaping by instinct. A piece marked only `\p` at the start plays as a flat wall
of identical velocities, and that -- not the notes -- is usually why a rendering
sounds mechanical.

**The one place hairpins do nothing: a held note.** Velocity is fixed when a
note starts and cannot change while it sounds, so `<d a>1\>` -- a whole-note
drone with a diminuendo -- plays at a constant level. LilyPond emits no
controller events by default, so nothing else is shaping it either.

`render.py score.ly --list-tracks` reports this directly: for every part it
prints the number of distinct velocities it received and the share of its
sounding time spent inside notes of a whole bar or more, and flags any part that
is mostly sustained. A part at 100% held with a hairpin on the page is a swell
you will never hear.

**`render.py` now performs these automatically.** `assets/hairpins.ily` reports
every hairpin's span, direction and staff; `midi_expression.py` maps those onto
MIDI tracks and writes CC11 expression ramps under the held notes, so the swell
you drew is the swell you hear. It reports what it shaped:

```
  15 held note(s) under a hairpin shaped with CC11 expression:
    singer             4q dim to 50%
    pad 2 (warm)       4q dim to 75%
```

The ramp is continuous across the hairpin and ignores note boundaries, and that
detail cost two rewrites. Resetting expression to unity at every note onset --
on the theory that velocity carries the overall trend and expression only fills
the gaps within each note -- turns a diminuendo over two whole notes into a
sawtooth: fade down, jump back to full, fade down again. It does not sound like
a diminuendo; it sounds like a note dropping out and returning.

Because a continuous ramp does compound with the velocity ramp LilyPond already
applied, it is not applied everywhere. A hairpin is shaped only when one note
covers at least 40% of its span and lasts at least two quarters. Anything
busier has enough note onsets for velocity to shape it, and expression on top
would just exaggerate a swell that already works. In a typical score that is a
handful of hairpins, not all of them:

```
  6 hairpin(s) over held notes performed as CC11 expression:
    singer             4q dim to 40%
    fiddle             8q cresc from 40%
    pad 2 (warm)       8q dim to 40%
```

Two subtleties worth knowing, because both were bugs before they were features:
a hairpin frequently begins part-way through a note tied over from an earlier
bar, so the *overlap* is measured rather than notes that start inside the span;
and a chord sounds one note per pitch but shapes as one gesture.

**Ease into the ramp, never step into it.** A crescendo's ramp starts at its
quiet end, so writing that value at the hairpin's first tick drops the channel
~9 dB in a single tick -- chopping the release of whatever was still ringing and
producing an obvious bump exactly where the music should be swelling. A
diminuendo has the mirror defect at its close, jumping back to unity in one
tick. Each hairpin is therefore eased into or out of unity over `glide_quarters`
(default one quarter note) on whichever side is not already at unity. Measured
at 50 ms resolution, the difference is the whole depth landing in one window
versus spreading across twenty.

**Depth is not linear in loudness.** Synthesisers apply CC11 on roughly a
`40*log10(cc/127)` curve, so `--swell-depth 0.4` is about 9 dB and 0.6 is about
16 dB. Verify by ear and by measurement rather than by the number: at 16 dB the
quiet end of a crescendo is inaudible, which sounds like a missing note rather
than a swell -- the same failure as the sawtooth, arrived at from the other
direction.

`--no-swell` disables it and leaves the MIDI exactly as LilyPond performed it.
`--swell-depth` (default 0.5) sets how far expression travels across a note
that fills its hairpin entirely. By hand, the same thing is:

```lilypond
%% CC11 automation: works *during* a held note
\set Staff.midiExpression = #0.4   d1 \set Staff.midiExpression = #0.9
%% or re-strike: write the swell as tied repeated notes so new velocities land
d1 ~ d1 ~ d1
```

`midiExpression` (0-1, CC11) is a context property like any other, so it can be
set at any point in the music. Its neighbours are worth knowing: `midiBalance`,
`midiPanPosition`, `midiReverbLevel`, `midiChorusLevel`, and
`midiMinimumVolume`/`midiMaximumVolume`, which compress or expand the dynamic
range a staff's written dynamics map onto.

Fermatas and rits need explicit help:

```lilypond
\tempo 4 = 52  ... \tempo 4 = 40   % staged slowdown, audible in MIDI
```

## 5. articulate.ly

```lilypond
\include "articulate.ly"
\score { \unfoldRepeats \articulate \music \midi { } }
```

`\articulate` realises ornaments (trills, mordents, turns) as actual notes,
shortens staccato notes, lengthens tenuto, applies fermata holds and gradual
tempo changes. It changes the played rhythm, so use it for the playback score
only -- keep a separate `\score` with `\layout` for the page, or the printed
notation may pick up the realisation.

## 6. Soundfonts

Sound quality is dominated by the soundfont, not by LilyPond.

| soundfont | size | notes |
|---|---|---|
| `FluidR3_GM.sf2` | ~140 MB | the sensible default; `apt install fluid-soundfont-gm` |
| `TimGM6mb.sf2` | 6 MB | tiny, noticeably worse |
| GeneralUser GS | ~30 MB | good balance, download separately |
| orchestral SFZ/SF2 sets | GBs | better strings/winds if you can supply them |

Pass a specific one with `render.py --soundfont /path/to.sf2`.

## 7. fluidsynth

```bash
fluidsynth -ni -F out.wav -r 44100 -g 1.0 -R 1 /usr/share/sounds/sf2/FluidR3_GM.sf2 score.midi
```

- `-ni` no MIDI input, no shell
- `-F` render to file instead of playing
- `-r` sample rate
- `-g` gain; raise for quiet scores, lower if the mix clips
- `-R 1` fluidsynth's own reverb

The rendered WAV runs past the last note by the length of the release tails --
which is why the audio is a few seconds longer than the music. That difference
is expected and the video pipeline accounts for it.

## 8. Mastering with ffmpeg

The chain used by `render.py`, in order:

```bash
ffmpeg -i raw.wav -af "\
aecho=0.85:0.9:70|130|220|400:0.35|0.25|0.18|0.1,\
highpass=f=35,lowpass=f=9500,\
dynaudnorm=p=0.65:m=5,\
afade=t=out:st=<end-3>:d=3" -c:a libmp3lame -b:a 192k out.mp3
```

- `aecho` with four short taps approximates a hall tail; cheap and effective for
  sparse music. For something better, record or download an impulse response and
  use `afir`.
- `highpass`/`lowpass` remove synthesis rumble and the brittle top end typical of
  GM samples.
- `dynaudnorm` evens out level without pumping; drop it if the piece depends on a
  wide dynamic range, since it will partly undo your hairpins.
- Always fade the tail, or the reverb ends in a click.

## 9. Balance between parts

A correctly notated score can still render badly balanced, because two things
decide how loud a part sounds and neither is under the composer's control: the
velocity LilyPond derives from the written dynamic, and how loud that
soundfont's samples happen to be. A `\mp` koto and a `\mp` shakuhachi are the
same velocity and about 8 dB apart. Writing `\ff` on the quiet part to
compensate is the wrong fix -- it corrupts the printed score to work around a
synthesiser.

`render.py --mix` fixes it downstream instead. Each staff is split out of the
MIDI into its own file (`midi_split.py`), synthesised separately, and summed
with its own gain and stereo balance:

```bash
python3 scripts/render.py score.ly -o out/ --list-tracks
python3 scripts/render.py score.ly -o out/ \
  --mix "singer=+3.5,shakuhachi=-3/0.35,koto=-2.5/-0.2,drums=+3,pad=mute"
```

Keys match a part number or any substring of its name or GM sound. Gain is in
decibels, `mute` silences a part, and the value after `/` is stereo balance from
-1 to 1. Splitting copies track chunks verbatim, so running status, channel
assignments and the channel-10 drum mapping survive untouched.

**Choose the numbers by measuring, not by ear alone.** Integrated loudness over
the whole piece under-reads a part that only plays occasionally; what matters is
how loud a part is *while it sounds*:

```bash
ffmpeg -i stem.wav -af ebur128 -f null - 2>&1 | grep -o "M: *-\?[0-9.]*"
```

Take the median of those momentary values per part and the picture is usually
obvious -- a continuous ostinato sitting 8 dB above a melody, or a percussion
part 10 dB below everything else and effectively absent.

Name the contexts (`\new Staff = "Koto"`) and the names show up in the part
list; otherwise parts are labelled by their GM sound, which leaves two piano
staves both called `acoustic grand` and distinguishable only by number.

For anything `--mix` cannot express -- a different soundfont per part, a real
convolution reverb, per-part EQ -- render the stems and take them into a DAW:

```bash
ffmpeg -i voice.wav -i koto.wav -i drone.wav -filter_complex \
 "[0]volume=1.0[a];[1]volume=0.7,highpass=f=200[b];[2]volume=0.5[c];\
  [a][b][c]amix=inputs=3:normalize=0" mix.wav
```

This is the point at which a LilyPond rendering starts sounding like a
production rather than a demo.

## 10. Sanity checks

- Compare the MIDI's length against the expected duration:
  `python3 scripts/midi_timing.py score.midi` prints every bar's start time and
  the total. If bar 1 is not at 0.000s, you have a pickup.
- If a part is inaudible, check the program actually assigned to its channel
  before assuming the synthesiser is at fault: `--list-tracks` prints the
  program, channel and note count for every part.
- If everything sounds like piano, an instrument name is misspelled.
- If the ending clicks, the fade is too late or the tail too short.
