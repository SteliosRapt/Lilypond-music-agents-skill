# "Hourglass" — how it was made

Four voices, celesta, violins, cello, timpani and piano; 51 bars in four
tempi, about three and a half minutes. Everything you hear is the score's own
MIDI through the General MIDI soundfont via `lilypond-music/scripts/render.py`
— no voicebank was used, so the choir is the soundfont's `choir aahs` and
`voice oohs`, and the words are on the page rather than in the air. The
vocal lines are written so that a bank can sing them (see the end).

    hourglass.template.ly   the score as written: everything except two passages
    make_score.py           generates those two passages and writes hourglass.ly
    hourglass.ly            the score to compile
    hourglass.pdf           engraved, one system a page
    hourglass.midi          performance data
    hourglass.mp3           the rendering
    hourglass.mp4           the score video, playhead on every staff

## The idea

The piece is a palindrome with the glass turned in the middle.

**I. Sand** (bars 1–10, 5/4, ♩ = 48, C minor). The soprano sings a twenty-note
tune over three humming voices, a C drone, a celesta figure of six bells
(c g e♭ – d g c) and sustained violins. Words: *Fall, we shall, as sand,
through the narrow hour; ev'ry grain a name, ev'ry name a light.*

**II. Turning** (bars 11–30, 7/8). Four devices at once:

- **The tempo is the form.** ♪ = 112, 128, 144, 160 for one bar each, then 176,
  200, 224, 248 for four bars each. The MIDI performs every step.
- **The mode darkens with the tempo.** C aeolian → C dorian → C harmonic minor →
  C phrygian dominant. The same seven-note line is re-spelt in each.
- **Hocket.** Each 7/8 bar carries one seven-syllable line (*Turn the glass and
  count the grain*), and the four voices sing it one syllable each in rotation,
  soprano–alto–tenor–bass, each in its own octave, so the tune is heard across
  three octaves and nobody holds it for more than a quaver. Seven syllables
  against four voices means the rotation only realigns every four bars, which
  is why a step is four bars long. Step two sings it in octave pairs (S+T,
  A+B), step three in register pairs (S+A, T+B), step four all together with
  the piano doubling in octaves.
- **A Shepard rise in the violins.** 7/8 was chosen because a seven-note scale
  fills the bar exactly. Two voices a bar apart each climb one octave per bar,
  crescendo on the low octave and diminuendo on the high one, so a line is
  always rising and never arrives — twenty bars of ascent.

The piano's ostinato and the timpani group the bar 2+2+3; the celesta's tick
and the cello group it 3+2+2 against them.

**III. Shatter** (bars 31–38, 4/4, ♩ = 132). *Break!* on a cluster (F A♭ B♭ C),
then *let it break, let it spill* in parallel thirds doubled at the octave over
tremolo strings, piano and timpani; a held cluster swelling to *fff*; three
stabs; and a general pause the MIDI honours (a hidden ♩ = 44 on the empty bar).
The celesta is silent through all of it and is what is left ticking after.

**IV. Glass** (bars 42–51, 5/4, Tempo I, E♭ major). The tenor sings the
soprano's tune from part I *backwards* — the same twenty pitches and durations
in reverse order, an octave down — and the words reverse with it: the opening
*Fall, we shall, as sand* returns as *sand, as shall we fall*, which is the
same five words read from the end. The harmony is re-read from C minor into its
relative major, so the identical pitches that were grief in part I are consent
here: the retrograde opens on A♭maj7 and closes on E♭. The celesta's six bells
come back reversed as well (c g d – e♭ g c), and a staged ritardando
(♩ = 48 → 44 → 40 → 36 → 32, hidden marks) lets the last chord settle. The
final sound is a single high E♭ on the celesta: the last grain.

## Craft notes

- The retrograde is exact. Bars 3–10 read as durations are
  `2. 2 | 4 4 2. | 2 4 2 | 2. 2 | 2. 4 4 | 4 2. r4 | 2 4 2 | 4 1`, and because
  every bar of 5/4 is the same length the reversed stream falls into whole bars
  again: `1 4 | 2 4 2 | r4 2. 4 | 4 4 2. | 2 2. | 2 4 2 | 2. 4 4 | 2 2.`.
- Every held note in the voices is a tie, not an extender, so a part's
  syllable count is exactly its number of untied notes and a miscount is a
  LilyPond warning. `vocal_score.py` confirms all four parts end on
  *sand, as shall we fall* with no melisma that was not written.
- Hairpins over held notes (the humming chords in part I, the cluster in
  part III, the final chord) are performed by `render.py` as CC11 expression
  ramps, so they are audible; the run prints which ones it shaped.
- `make_score.py` exists because rotating 28 syllables over four voices, three
  octaves and four modes by hand is exactly the kind of counting that goes
  wrong silently. The template is the score; the generator only fills two
  named passages.

## Reproducing it

```bash
bash lilypond-music/scripts/setup.sh
python3 songs/hourglass/make_score.py           # writes hourglass.ly
python3 lilypond-music/scripts/render.py songs/hourglass/hourglass.ly \
    -o out/ --size 1920x1080 --verify 8 --no-normalise \
    --mix "soprano=+0.5/-0.25,alto=+2.5/0.25,tenor=+1/-0.1,bass=+4/0.1,\
celesta=-1/0.4,violins=0/-0.35,cello=+1/0.3,timpani=-3/0,\
piano upper=-4/-0.15,piano lower=0/-0.15"
```

The `--mix` numbers came from measuring, not guessing: a first render with
`--mix "1=0" --keep-temp` leaves one stem per part, and the median momentary
loudness of each stem *while it sounds* (`ffmpeg -af ebur128`, windows above
−40 LUFS) was:

| part | LUFS | | part | LUFS |
|---|---|---|---|---|
| soprano | −24.1 | | violins | −24.9 |
| alto | −27.6 | | cello | −26.5 |
| tenor | −25.8 | | timpani | −19.9 |
| bass | −29.9 | | piano upper | −18.5 |
| celesta | −24.1 | | piano lower | −27.1 |

So the soundfont's piano and timpani sat 5–10 dB above the choir, and the bass
voice was the quietest thing in the score. The mix pulls those two down, lifts
the inner and lower voices, and places the ensemble: choir in a shallow
semicircle in front, violins left, cello right, celesta off to the right where
its tick reads as a separate object. No EQ was needed.

`--no-normalise` is new, and this piece is why. The mastering chain's
`dynaudnorm` levels the whole track, which is right for most scores and wrong
for one whose form *is* its dynamics: with it, the *fff* of part III came out
2 dB above the *pp* of part I; without it, 13 dB. The flag is documented in
`references/audio-and-midi.md` section 8.

The video passed verification only after a fix to `render.py`: each page's
segment was being cut to a whole number of frames, always rounded up, and
eighteen pages of that put the playhead nine pixels behind the audio on the
slow bars of part IV. Page boundaries are now snapped to the frame grid
(`lilypond-music/references/video-pipeline.md` section 6 has the detail).

## Singing it

The soprano, alto and tenor lines are written inside C3–G5, the range where
all four banks in `references/singing-synthesis.md` section 2 were measured to
hold full level; the bass goes below it, to F2 in the hocket and A♭2 in
part IV, so probe a bank there before committing to it:

```bash
python3 lilypond-music/scripts/sing_ensemble.py songs/hourglass/hourglass.ly -o out/ \
    --voice soprano=~/voices/liee --voice alto=~/voices/canary \
    --voice tenor=~/voices/tiger  --voice bass=~/voices/triton --literal-pitch
python3 lilypond-music/scripts/render.py songs/hourglass/hourglass.ly \
    --vocal out/hourglass.mp3 --mix "soprano=mute,alto=mute,tenor=mute,bass=mute"
```

The hocket is the passage to listen to first: syllables a quaver long at
♪ = 248 are at the edge of what a duration model will place cleanly, and
`--preview` will show whether each bank lands them before a full render.
