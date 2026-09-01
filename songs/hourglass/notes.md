# "Hourglass" — how it was made

Four voices, celesta, violins, cello, timpani and piano; 51 bars in four
tempi, about three and a half minutes. The four vocal parts are sung by neural
voicebanks — one bank per part, actual words — and mixed over the instrumental
that `lilypond-music/scripts/render.py` performs from the score's own MIDI. The
last section of these notes is how that was done and what was measured
afterwards.

    hourglass.template.ly   the score as written: everything except two passages
    make_score.py           generates those two passages and writes hourglass.ly
    hourglass.ly            the score to compile
    hourglass.pdf           engraved, one system a page
    hourglass.midi          performance data
    hourglass.mp3           the rendering: sung voices over the instrumental
    hourglass.mp4           the score video, playhead on every staff
    stems/*.flac            one voicebank each, dry, aligned to beat 0

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

## The instrumental, and how it is balanced

The released mix is the sung voices over this instrumental; the section after
next is the singing. On its own — the choir sung by the soundfont's
`choir aahs` and `voice oohs` instead of by voicebanks — it is:

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

The four vocal lines are sung by neural voicebanks through
`sing_ensemble.py`, one bank per part, and mixed over the fluidsynth
instrumental with `render.py --vocal`. The synthetic `choir aahs` and
`voice oohs` staves are muted in that mix, so nothing doubles the singers.

    stems/*.flac   each part dry, aligned to beat 0, lossless

| part | bank | mode | why |
|---|---|---|---|
| Soprano | CANARY v106 | `canary_arc` | the most even of the three across the range, and the soprano is the only part that has to hold E♭5 |
| Alto | TRITON v106 | `triton_tempest` | a different colour of the same bank as the bass, two octaves away from it |
| Tenor | TIGER v102 | `tiger_fresh` | carries the retrograde tune in part IV, buried in the middle of the texture, and TIGER is the quietest bank — the part that must be heard should not be the one fighting to be heard |
| Bass | TRITON v106 | `triton_gale` | measured the most even of the three below C3, which is where this part lives |

### Three banks, not four

`songs/tide-and-lantern.ly` uses four, the fourth being LIEE. This piece does
not, for two reasons that are specific to it. LIEE is the only one of the four
measured to hold C6, and nothing here goes above E5, so its one clear advantage
is unused. And it ships no English phonemizer plugin, so every word outside the
twenty English entries in its dictionary would be guessed by letter-to-sound
rules — for "Tide and Lantern" that was nine words to transcribe by hand, but
this piece sings 48, which is a different proposition. The three tigermeat banks
all carry the same `diffs_en_tgm_alpha` plugin and its 133,102-word dictionary.
Alto and bass therefore share TRITON in two of its four voice modes; they are
two octaves apart and never in unison.

### The low-register probe those choices came from

The documented range for all these banks is C3–G5, and this piece's bass goes
below it: F2 in the hocket and A♭2 in part IV. So it was measured rather than
assumed — one note per beat, `--literal-pitch` so any error is the bank's, then
autocorrelation pitch tracking and a level per note:

| | F2 | G2 | A♭2 | B♭2 | C3 | D3 | E♭3 | G3 |
|---|---|---|---|---|---|---|---|---|
| TIGER | −25c, −36 dB | −1c, −37 dB | −4c, −34 dB | −1c, −28 dB | −11c, −29 dB | +1c, −26 dB | −1c, −25 dB | +2c, −26 dB |
| CANARY | −4c, −28 dB | +3c, −21 dB | +0c, −18 dB | −0c, −18 dB | −9c, −21 dB | −5c, −17 dB | +3c, −20 dB | −2c, −21 dB |
| TRITON | −8c, −28 dB | −2c, −27 dB | −4c, −27 dB | +2c, −23 dB | −6c, −23 dB | −4c, −21 dB | −1c, −21 dB | −2c, −24 dB |

All three hold pitch down there. What separates them is level: TIGER is 8–11 dB
quieter than the others across the whole probe and 25 cents flat on F2, which
rules it out for this bass line; TRITON is the most even, 6.5 dB from its
quietest note to its loudest against CANARY's 10. Hence TRITON on the bass and
TIGER, the quiet one, on the tenor tune where a boost costs nothing.

### One word had to be taught

Every word in the piece resolves through the plugin's dictionary except the
hocket's `’ry`, the second half of "ev'ry", which the score writes as its own
syllable. Cleaned of punctuation it is looked up as `ry`, and the neural G2P
guesses `r ay` — the bank sings "rye". The fix is one line appended to each
bank's `dsdur/dsdict-en.yaml`, in the convention its own entries use
(`every: [eh, v, r, iy]`):

```yaml
- {grapheme: "ry", phonemes: [r, iy]}
```

The bank's own dictionary is consulted before the plugin, so that entry wins,
and no note or syllable in the score had to move for it.

### What singing it found: an off-by-one in the underlay

Putting words in the air exposed a defect that the engraving hid. In part I the
alto, tenor and bass hum one syllable — `Mm __ _ _ _ _ _ _ _` — and that lyric
supplied one fewer slot than those parts have syllable-bearing notes, because
`__` draws an extender without consuming a note while each `_` consumes one.
LilyPond does not warn about this. It simply takes the next word in the stream,
so each of the three pulled the first word of the hocket back onto the last
note of part I and then ran one syllable ahead of the soprano for the rest of
the piece. By the tutti at bar 27, where all four voices are supposed to land
on the same syllable together, they were singing four different words:

    26.500  S=Turn   A=the    T=the    B=the
    26.625  S=the    A=glass  T=glass  B=glass

The fix is one more `_` in each of the three underlays, and it is the whole
diff: three lines, no pitch, rhythm, dynamic or structure touched anywhere.
That is checkable rather than asserted — the MIDI before and after the fix
carries the same 3,776 note events, identical in track, tick, channel, pitch
and velocity, and differs only in the lyric meta-events LilyPond writes
alongside them.
Afterwards each hummed part takes exactly one syllable in part I, and every
step of the hocket distributes as designed — one voice per syllable, then two,
then two, then all four on the same word:

| step | onsets | voices per onset | all on the same syllable |
|---|---|---|---|
| 1, solo rotation | 28 | 1 | yes |
| 2, octave pairs | 28 | 2 | yes |
| 3, register pairs | 28 | 2 | yes |
| 4, tutti | 28 | 4 | yes |

The lesson generalises, and it is sharper than the skill's existing advice to
prefer ties to extenders: a part whose lyric is *shorter* than its note count
does not fail, it borrows from the next phrase, and the error surfaces bars
later in a different section. Counting `_` against syllable-bearing notes is
worth doing wherever a part hums under a texture.

### What was measured afterwards

Every note of every stem, tracked by autocorrelation and compared against the
pitch LilyPond wrote:

| | notes measured | median error | 90th percentile | over 50 cents |
|---|---|---|---|---|
| notes 0.4 s and longer | 104 | 4.0c | 10.5c | 0% |
| notes shorter than 0.4 s | 292 | 10.1c | 53.6c | 12% |

The first row is this repository's own benchmark for `dspitch` (median 4.3
cents, max 10.7) reproduced on a different piece with different banks. The second row is the piece asking for something the models
strain at, and it was predictable: those are the hocket at ♪ = 248 and the
patter of part III, syllables 230–240 ms long, where a duration model has to
place a consonant, a vowel and a release inside a quarter of a second. It is
audible as a slight smearing of the fastest words, not as wrong notes.

### Reproducing the sung version

```bash
python3 lilypond-music/scripts/sing_ensemble.py songs/hourglass/hourglass.ly \
    -o out/ --format flac --stem-format flac --steps 20 --jobs 1 \
    --voice "soprano=$CANARY" --voice "alto=$TRITON" \
    --voice "tenor=$TIGER"    --voice "bass=$TRITON" \
    --mode soprano=canary_arc --mode alto=triton_tempest \
    --mode tenor=tiger_fresh  --mode bass=triton_gale

python3 lilypond-music/scripts/render.py songs/hourglass/hourglass.ly \
    -o out/ --size 1920x1080 --verify 8 --no-normalise \
    --vocal out/hourglass.flac --vocal-gain -5 \
    --mix "soprano=mute,alto=mute,tenor=mute,bass=mute,\
celesta=-1/0.4,violins=0/-0.35,cello=+1/0.3,timpani=-3/0,\
piano upper=-4/-0.15,piano lower=0/-0.15"
```

**`--jobs 1`, not the default 2.** Two parts rendering at once died partway
through the alto with 15 GB of RAM available. The alto's longest phrase is 72
seconds — part IV is one unbroken held vowel — and two of those in flight at
once is what exhausted it. Serial costs about ten minutes more and did not fail.

**A re-render is a different take.** Diffusion is unseeded, so the same command
produces the same arrangement sung slightly differently, which is why the stems
are committed rather than treated as intermediates.

`--vocal-gain -5` puts the choir about 4 dB over the instruments: the sung mix
comes out of `sing_ensemble.py` 9 dB louder than the instrumental, measured as
the median of half-second windows above −45 dB in each.
