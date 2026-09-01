# The score-following video pipeline

What `scripts/render.py` builds: each page of the engraved score as a still
frame, a soft band highlighting the system currently sounding, and a playhead
that lands on each printed note at the moment it sounds, page-turning in time
with the music.

Contents:
1. The two problems, and how they are solved
2. Geometry: the colour-coded analysis pass
3. Timing: reading the tempo map out of the MIDI
4. Where the notes are: the paper-column map
5. Assembling the video
6. ffmpeg traps (one of which fails silently)
7. Verification
8. Customising
9. Extending

---

## 1. The two problems, and how they are solved

An animated score needs two things that live in different worlds:

- **Where** is bar 17 on the page, in pixels?
- **When** does bar 17 sound, in seconds?

Both are derived from artefacts LilyPond already produces, never hand-entered.
And because LilyPond does not space notes proportionally to their duration, the
*where* has to be answered per note rather than per bar -- see section 4.
Hand-maintained tempo maps and hard-coded pixel coordinates are the two things
that guarantee the animation will drift the moment the score is edited.

## 2. Geometry: the colour-coded analysis pass

The score is compiled **twice at the same resolution**:

```bash
lilypond --formats=pdf,png -dresolution=200 -o base score.ly
lilypond -dinclude-settings=assets/analysis-colors.ily --formats=png \
         -dresolution=200 -o analysis score.ly
```

`analysis-colors.ily` recolours three grobs and nothing else, so spacing is
identical and pixel coordinates measured on the analysis render are valid for
the display render:

| colour | grob | what it gives us |
|---|---|---|
| red | `BarLine`, `SpanBar` | x of every bar boundary |
| green | `SystemStartBar`/`Brace`/`Bracket`/`Square` | one continuous line per system, spanning exactly its staves |
| blue | `StaffSymbol` | left edge of each system |

`-dinclude-settings` injects a top-level `\layout` block before the score is
processed, so the user's `.ly` file needs no modification.

**Order matters.** Resolve the green bands first, then look for red *inside* a
band. Systems are left- and right-aligned, so a barline at the right margin of
system 1 shares its x with the one in system 5; scanning the whole page for red
columns merges every system into one. The green start-delimiters, split by
vertical gaps, give the bands cleanly -- and because every system's delimiter
sits at the same x, that column cluster must also be split by vertical gaps.

Single-staff scores draw no system delimiter; `lily_layout.py` falls back to
grouping blue staff-line rows in that case.

Why not detect barlines from a normal black render? Note stems are also vertical
runs the height of a staff. Filtering them out works until it doesn't, and the
failure is silent. Colour makes the question unambiguous.

## 3. Timing: reading the tempo map out of the MIDI

`midi_timing.py` walks the MIDI file, collects tempo (`FF 51`) and time
signature (`FF 58`) meta events, and converts bar boundaries from ticks to
seconds. Every `\tempo` mark in the score is therefore honoured automatically,
including mid-piece changes, and edits to the score cannot desynchronise the
video.

Standalone use:

```bash
python3 scripts/midi_timing.py score.midi        # bar-by-bar table
python3 scripts/midi_timing.py score.midi 1      # score starts with \partial 4
```

The printed bar count and the MIDI bar count are compared before rendering; a
mismatch is reported with its usual causes (pickup, `\repeat volta`, unusual
metre change).

## 4. Where the notes are: the paper-column map

Knowing the pixels of bar 17 and the seconds of bar 17 is not enough to put the
playhead on a note, because printed distance is not proportional to elapsed
time. LilyPond's default spacing gives each doubling of a note's length one
fixed increment more room, not twice the room, so in a bar of half + quarter +
quarter the second quarter is printed at about 43% of the bar's width while it
sounds at 50% of the bar's time. A playhead swept linearly across the bar is
exactly right at both barlines and visibly wrong in between -- on a real score
the gap reaches a couple of hundred pixels and half a second.

`assets/paper-columns.ily` fixes this at the source. A *paper column* is the
grob LilyPond creates for every distinct moment at which anything is engraved,
and after line breaking it knows its own horizontal position. Hooking
`after-line-breaking` -- a read-only callback that runs once layout is final --
prints one line per column:

```
@COL 0 1 3/16  37.164966867090826
      ^ ^  ^    x within its system, in staff spaces
      | |  moment in whole notes, as an exact rational
      | 1 = musical column (note/chord/rest onset), 0 = barline or clef
      index of the containing system
```

`column_map.py` turns that into anchors:

- **Which system.** Systems have no printed identity, so the ily hands out
  indices by object identity in order of first appearance, and the consumer
  sorts systems by earliest moment. A column at a line break is reported twice
  -- once at the right margin of the system ending there, once at x=0 of the
  next -- and the system index is what tells the two apart. (Disambiguating by
  x instead breaks on a ragged last system, whose line end is nowhere near the
  right margin.)
- **Which pixel.** Column x is in LilyPond units relative to its system, which
  says nothing about the page. The non-musical columns *are* the barlines the
  colour pass already measured in pixels, so the two lists describe the same
  objects in two coordinate systems: least-squares fit `x_px = a·x_lily + b` per
  system, and check the residual. On a well-behaved score it lands under 2px;
  anything worse means the dump and the page image are describing different
  engravings, and that system falls back to bar-linear.

  The two lists are not always the same length, and equality was once required.
  A breakable column can exist where no barline is *printed* -- a lyric extender
  ending mid-bar is enough, and `lead-sheet.ly` has one, which cost its last
  system every note-level anchor. A surplus of a few boundaries is now resolved
  by fitting each candidate subset, keeping the system's own edges, and taking
  the best; the residual check still decides whether to believe the result.
- **Which second.** LilyPond's moments and its MIDI share an origin, so a moment
  is a tick is a second through the tempo map already parsed
  (`midi_timing.moment_converter`). Nothing is matched against MIDI note-on
  events, which is what makes ties, rests, grace notes and unfolded repeats
  harmless: a rest is an anchor like any other, and the playhead passes it
  correctly.

Grace notes share their main note's moment, so one moment can carry two columns
in a system; the larger x is the main note and the playhead sweeps through the
grace on its way in.

`--playhead-mode bars` restores the old one-ramp-per-bar behaviour, and any
system whose fit fails falls back to it automatically.

## 5. Assembling the video

One segment per page, then concatenate and mux:

- The page image is cropped to a **shared ink bounding box** across all pages
  (so the notation doesn't jump on page turns) and pasted onto a coloured canvas.
- The active-system band is a `drawbox` with a constant rectangle and an
  `enable='between(t,...)'` window.
- The playhead is an `overlay` of a small RGBA image, whose `x` is a nested
  `if(lt(t,...),...)` expression covering every gap between anchors in that
  system, and whose `y` is the system's band top. One case per printed onset
  rather than per bar makes the expression longer but no harder for ffmpeg. Each system gets its own playhead image sized to that
  band, so the marker never spills into neighbouring staves.
- Segments are concatenated with the concat demuxer and the mp3 is muxed in.

## 6. ffmpeg traps

**`drawbox` ignores time-dependent position expressions.** This one is nasty
because it fails *silently*: `drawbox=x='10+100*t':...` draws nothing at all, no
warning, no error, while the identical filter with a constant `x` draws
correctly. `enable=` expressions on the same filter work, which makes it look
like `t` is available. Use `overlay` for anything that moves -- its `x`/`y`
expressions are evaluated per frame and it is the well-trodden path.

**A looping image input never ends.** `-loop 1 -i page.png` produces frames
forever. Putting `-t` on only the first input is not enough: `overlay` keeps
pulling frames from the *other* looping input long after the background stopped,
and the encode runs until something kills it. Give every looping input its own
`-t`, and add `-shortest`. Symptom: a video whose duration is ten times what it
should be, or an encode that never finishes.

**Frame quantisation when probing.** `ffmpeg -ss T -i video -frames:v 1` returns
the frame at or just before `T`, so a moving playhead legitimately lags the
mathematically exact position by up to one frame of travel. At 24 fps with wide
bars that is several pixels -- the verification tolerance accounts for it rather
than treating it as an error.

**Concatenating segments.** Use the concat demuxer with `-c:v copy`; re-encoding
each segment twice costs time and quality for nothing.

**Segment lengths are rounded to whole frames, and the rounding adds up.**
`-t 12.573` on a 24 fps segment produces 302 frames, which is 12.583 s: every
page boundary that does not fall on the frame grid lands late by up to one
frame, and the concat demuxer simply butts the segments together, so after
eighteen pages the playhead can trail the audio by several frames. On a slow
5/4 bar that was a 9 px miss in verification. `render.py` therefore snaps each
page's start and end to the frame grid before cutting the segment and draws
the playhead against the snapped start, which bounds the error at half a frame
for the whole piece instead of letting it grow with the page count.

**Encoder speed.** Dozens of stacked filters slow encoding to well below
real time. Prefer few filters with piecewise expressions over many filters with
`enable` windows, and use `-preset veryfast` -- for a static page with one moving
element there is nothing for a slower preset to find.

## 7. Verification

`render.py --verify N` samples N frames, finds the playhead by colour, and
compares its measured x against the position computed from the anchor table.
Samples land *between* anchors, never on them: a bar-linear playhead is exactly
right at every barline, so a check that probed only bar boundaries would pass
whether or not the note-level map was working. Samples inside the opening and
closing fades are skipped -- the playhead is faded to black there and reports
MISSING, which looks like a failure and is not.

```
  bar     time    expected x   measured x
    1     1.90s       390.3        390.5   ok
   11    40.00s       322.8        318.5   ok
  playhead sync: verified
```

Run it every time. A missing, mis-scaled or drifting playhead is very hard to
notice by eye in a thumbnail -- and a video that *looks* plausible while being
wrong is worse than one that obviously fails.

## 8. Customising

```bash
python3 scripts/render.py score.ly -o out/ \
  --size 1920x1080 --fps 30 \
  --playhead 4aa3d6 --highlight 9fd4ff --bg f7f4ec \
  --resolution 300 --gain 1.2 --no-reverb
```

- Portrait `1080x1920` suits phones and portrait pages; `1920x1080` suits
  landscape pages -- match the page orientation to the frame or the score ends
  up small in a wide letterboxed strip.
- `--resolution` is the page render DPI; raise it for large frames, since the
  page is scaled down to fit, never up.
- Light theme: `--bg f7f4ec --highlight ffd88a --playhead c0392b`.
- Fewer bars per system (`\break` in the score) makes the playhead move more
  slowly and the notation read more clearly on a small screen.

## 9. Extending

The geometry and timeline are the hard parts; once you have them, other
visualisations are small changes to `build_video`:

- **Scrolling instead of paging** -- render one very tall page
  (`\paper { page-count = 1 }` with a custom paper height), then animate the
  background's `y` with an `overlay` expression so the active system stays
  centred.
- **Note-head highlighting** -- the anchor table already says which moment is
  sounding; colouring `NoteHead` in the analysis pass would give the heads
  themselves, so the current note could glow rather than just be swept past.
- **Karaoke-style lyrics** -- the `Lyrics` context sits below its staff inside
  the system band; colour `LyricText` in the analysis pass to get syllable
  positions, then sync them to the anchor table.
- **Per-part colour** -- colour each staff's notes differently in the display
  render to make an ensemble texture readable at a glance.
