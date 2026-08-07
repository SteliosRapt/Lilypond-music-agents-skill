# "Tide and Lantern" — how it was made

An unaccompanied SATB piece, 28 bars, D dorian, about a minute and a half, with
each part sung by a different DiffSinger voicebank through
`lilypond-music/scripts/sing.py`. No instruments, no samples, no click: the
whole record is four neural voices and a synthetic room.

    tide-and-lantern.ly     the score
    tide-and-lantern.pdf    engraved
    stems/{soprano,alto,tenor,bass}.wav    one bank each, dry, aligned to beat 0
    tide-and-lantern.mp3    the mix
    mix.py                  what made the mix from the stems
    liee-english-additions.yaml   nine dictionary entries the soprano needed

## Which bank sings what, and why

| part | bank | why |
|---|---|---|
| Soprano | LIEE : Immortal Idol MM 2.8 | the only one of the four measured to hold C6 at full level, so it gets the top line and the final E5 |
| Alto | CANARY v106 | the most even of the four across the whole range: within 1 dB from C3 to G5 |
| Tenor | TIGER v102 | carries the tune. It is also the quietest bank, which is why the tune is its and not somebody else's — the part you must hear is the part least likely to be buried |
| Bass | TRITON v106 | even, and the darkest of the three tigermeat voices |

### The range probe those choices came from

Nothing in a bank's files states its range, so this was measured: an arpeggio
from C3 to C6, one note per beat, rendered with `--literal-pitch` (so any pitch
error is the bank's, not the pitch model's), then tracked with `librosa.pyin`
and levelled per note.

| | C3 | E3 | G3 | C4 | E4 | G4 | C5 | E5 | G5 | C6 |
|---|---|---|---|---|---|---|---|---|---|---|
| TIGER | ok | ok | ok | ok | ok | ok | ok | ok | −20c, −13 dB | fails, −640c |
| CANARY | ok | ok | ok | ok | ok | ok | −10c | ok | ok | fails, −400c |
| TRITON | ok | ok | ok | ok | ok | ok | ok | ok | ok | fails, −555c |
| LIEE | ok | +10c | ok | ok | ok | ok | ok | ok | ok | ok |

"ok" is within 10 cents of the written pitch at a level within about 4 dB of
that bank's own average. So: write anything between C3 and G5 for any of them,
keep C6 for LIEE, and expect nothing above that from any of them.

## The harmony

D dorian, so the sixth is natural and the leading note is absent — the piece
never has a V–i to lean on, and the motion has to come from the seventh and
ninth chords instead.

- **Bars 1–4, intro.** Entries from the bottom up, one per bar, over a bass D:
  the chord is only complete on bar 4 and it is a Dm add9.
- **Bars 5–12, verse.** The tenor has the tune; the other three sustain a
  Dm9 – Gm11 – Dm9 – Gm7/C7 – F6/9 frame under it. The soprano is humming, not
  singing words — `mm` is one of the twenty English entries LIEE ships with.
- **Bars 13–20, chorus.** Four-part homophony, one syllable at a time, on
  Bbmaj7 – Gm7 – Dm – (Am – Bb/A – Am7 – Dm/A) – Dm. The cadence in bar 19
  moves a chord per beat under a held soprano line.
- **Bars 21–24, canon at the fifth.** The same eight-syllable motif entering
  bass, tenor, alto, soprano two beats apart, alternately on D and on A. The
  motif uses only D F G A C, so every vertical combination the stagger can
  produce is a subset of Dm11 and no entry can collide with another.
- **Bars 25–28, tag.** A Picardy third: D F# A E, held four bars.

## Reproducing it

```bash
bash lilypond-music/scripts/setup.sh          # lilypond, ffmpeg
bash lilypond-music/scripts/setup-singing.sh  # onnxruntime, pyyaml
# the four banks, from their own releases (see references/singing-synthesis.md
# section 2 for the URLs and the licences -- all four are non-commercial)
python3 lilypond-music/scripts/vocal_score.py songs/tide-and-lantern.ly -o out/
# then one render per part; line numbers are as vocal_score.py printed them
python3 lilypond-music/scripts/sing.py out/tide-and-lantern-vocals.json \
    --voice ~/voices/tiger --line 2 --steps 20 --expressiveness 0.7 -o out/tenor
# ... and the same for lines 1 (bass), 3 (alto), 4 (soprano)
python3 songs/mix.py --stems out/stems
```

`--expressiveness 0.7` rather than the default 1.0: `dspitch` deviates from the
written notes by a median 20 cents at 1.0, which is a singer, but four
independent singers each doing that to a held four-note chord is a chord that
does not quite settle. At 0.7 the parts still scoop and drift and the chords
still lock.

## The soprano's vocabulary

LIEE ships no English phonemizer plugin and its English dictionary is 208
entries, of which about twenty are English words and the rest Japanese kana. So
the nine words this piece needed were added to a copy of its
`dsdur/dsdict-en.yaml`, transcribed in the bank's own conventions —
`liee-english-additions.yaml` has them, the conventions, and how they were read
off the bank's existing entries. Without that the soprano still sings, but every
word outside those twenty is spelled out by letter-to-sound rules and comes out
as a guess.

## What was verified, and what was not

Measured, per stem, against the score: pitch of every note body, whether every
vowel starts on its written onset, level and peak, and that no word fell through
to letter-to-sound rules. Those numbers are in the session that produced this.

**Not verified: whether it sounds good.** That needs ears. The likely things to
want changed are the balance and the reverb (both in `mix.py`, both a one-line
edit), the voice mode of any part (`--voice-mode`; TIGER has seven, TRITON four,
CANARY three, and they are quite different characters), and `--expressiveness`.
