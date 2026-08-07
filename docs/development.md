# Working on the skill

This is the map for changing the code. `pipeline-notes.md` beside it is the
other half — the record of what was established about the DiffSinger models by
probing them, which is the expensive knowledge here and is mostly not written
down anywhere else. Read that one before touching anything under
`scripts/sing.py`, `scripts/voicebank.py` or `scripts/predictors.py`.

---

## 1. The two commands that govern everything

```bash
python3 lilypond-music/scripts/dev/test_units.py    # ~90 checks, under a second
python3 lilypond-music/scripts/dev/selftest.py      # those plus ~90 more, ~2 min
python3 lilypond-music/scripts/dev/selftest.py --video   # + playhead verification
```

`selftest.py` runs the unit tests first and counts them in its own total, so it
is the one command; `test_units.py` on its own is the fast loop, and it needs
nothing but numpy — no lilypond, no ffmpeg, no voicebank. Green before you
start, green after every commit.

With a real voicebank the singing half runs against that bank instead of the two
stubs:

```bash
python3 lilypond-music/scripts/dev/selftest.py --video \
  --voice ~/voices/tiger_pack/TIGER_DS_v102_PACK/"Voice Library" \
  --vocoder ~/voices/pc_nsf_hifigan
```

See `pipeline-notes.md` section 8 for how to get that bank (566 MB, about seven
seconds) and section 6 for the rest of the test tooling.

**Numbers a change must not move.** Record them before you start and compare
after; a change that shifts any of these has changed behaviour, whatever the
tests say:

| measurement | where it comes from | value now |
|---|---|---|
| playhead anchors, `ensemble-voice.ly` | `render.py` stdout | 199 onsets, 8/8 systems, worst residual 1.4 px |
| playhead anchors, `lead-sheet.ly` | same | 44 onsets, 4/4 systems, worst residual 4.3 px |
| the rendered `.midi` | `cmp` against one from before | byte-identical |
| pitch tracking with `dspitch` | `librosa.yin` vs written notes | median 4.3 cents, max 10.7 |
| pitch tracking with `--literal-pitch` | same | median 1.1 cents, max 4.8 |
| vowel onsets | selftest, both timing modes | every one exactly on a written onset |
| vocoder resynthesis | `dev/vocoder_resynth_check.py` | 0.978 mel correlation, 1.7 cents |
| every bank still qualifies | `dev/bank_check.py` on TIGER, CANARY, TRITON | "no problems" on each, and LIEE with only its missing English plugin reported |

`flake8 --max-line-length=100 --extend-ignore=E731` is clean across every
script, and worth keeping that way.

## 2. What the modules are

The pipeline, in the order a score passes through it:

```
score.ly
  → assets/lyrics.ily        instrumented LilyPond run; syllable↔note alignment
  → vocal_score.py           → <stem>-vocals.json + one .musicxml per verse
  → sing.py                  → <stem>-vocal.wav
  → render.py --vocal        → mixed mp3/mp4
```

and what each file is for:

| module | subject |
|---|---|
| `render.py` | the pipeline: engrave, synthesise, animate, verify |
| `lily_layout.py` | page images to system and bar geometry, in pixels |
| `column_map.py` | LilyPond's paper columns to note-level playhead anchors |
| `smf.py` | Standard MIDI File primitives, shared by the three readers below |
| `midi_timing.py` | a tempo map, and bar starts in seconds |
| `midi_split.py` | one file per part, copied byte for byte, for mixing |
| `midi_expression.py` | hairpins velocity cannot perform, written as CC11 |
| `vocal_score.py` | the sung lines, as JSON and as MusicXML |
| `score_time.py` | score moments to seconds; where a phrase breaks for breath |
| `phonemes.py` | words and notes to `[(phoneme, start_s, end_s)]` |
| `voicebank.py` | a DiffSinger bank on disk, and the built-in preview voice |
| `predictors.py` | the bank's optional `dsdur` / `dspitch` / `dsvariance` |
| `phonemizer.py` | the bank's OpenUtau plugin: its dictionary and neural G2P |
| `preview_voice.py` | letter-to-sound rules and a three-formant synthesiser |
| `sing.py` | the pitch curve, the model calls, the command line |
| `errors.py` | `SkillError`, and the one place each tool catches it |

Two conventions hold across all of them:

* **Library paths raise `SkillError`; entry points catch it once.** Every module
  here is both a tool and something the other tools import, and `sys.exit` from
  a library function kills the process of whoever asked it a question. Raise for
  anything the user did or did not do; let a `KeyError` propagate, because that
  is a bug and a traceback is the right report for one.
* **Imports of `onnxruntime` are lazy, everywhere.** `sing.py --preview` has to
  work on a machine with none of the singing dependencies installed, and
  `selftest.py` checks that it does by making `onnxruntime` unimportable.

## 3. What NOT to do

- **Do not rewrite the prose.** Roughly a quarter of the source is explanation,
  and much of it records approaches that were tried and failed -- the sawtooth
  expression ramp, the CMUdict phone-set trap, `equalizer=t=h` not being a
  shelf, `dsdur` stretching consonants on long notes. That text is the most
  expensive thing in the repository to reproduce. Move it with the code it
  explains; do not summarise it away.
- **Do not change the CLI surface.** Flags, their defaults and their output
  format are documented in `SKILL.md` and four reference files, and an agent
  reading the skill will type exactly what those say.
- **Do not "improve" the byte-level MIDI handling into a library.** `mido` and
  friends re-encode events; `split_tracks` copies chunks verbatim so running
  status, channel assignments and the channel-10 drum mapping survive. That is
  deliberate and it is documented at the top of `midi_split.py`.
- **Do not merge modules that merely look similar.** `midi_timing`,
  `midi_split` and `midi_expression` share a *file format*, not a purpose:
  one builds a tempo map, one copies tracks apart, one rewrites events. They
  share the parsing primitives, in `smf.py`, and nothing above them.
- **Do not substitute CMUdict for a bank's phonemizer plugin.** These phone sets
  have `dr` and `tr` as single affricates: "drift" is `dr ih f t`, where CMUdict
  gives `d r ih f t` -- wrong symbols and wrong phoneme count.

## 4. What is still open

**`dsvariance` has met exactly one real bank.** `VariancePredictor` was written
from declared interfaces; LIEE MM 2.8 ships a variance model and it predicts all
four parameters, but LIEE's *acoustic* model asks for only `tension` of the
four, so the other three are computed and discarded. A bank with
`use_energy_embed: true` would be the first real test of energy reaching the
acoustic model. Three things to check when one turns up, in this order, because
each fails silently:

1. **The `retake` axis.** It is fed as `[1, n_frames, n_parameters]` with the
   parameter axis ordered by the model's own output names. A bank that orders
   its outputs differently from `PARAMETERS` swaps energy and breathiness, and
   the result is merely a bit odd rather than obviously broken.
2. **The domain.** These are log-domain, roughly dB, 0 unity and −96 silence
   (`pipeline-notes.md` section 3). A model returning 0..1 instead reads to the
   acoustic model as near-silence. Print the returned range before listening to
   anything.
3. **Then listen**, with `--variance 0,0,0,0` and again with a clear offset, and
   confirm the offsets move the sound in the direction the flag says.

Done when `singing-synthesis.md` section 7 names a bank rather than a stub.

**The pronunciation gap on non-tigermeat banks.** LIEE ships 208 English words
and no English phonemizer plugin, so anything else is spelled out by rule. The
plugin format is understood (`pipeline-notes.md` section 4) and any matching
plugin can be pointed at with `--phonemizer`; what is missing is one that
matches LIEE's phone conventions. Extending a copy of its `dsdict-en.yaml` is
the practical answer and nothing automates it —
`songs/liee-english-additions.yaml` is nine words done by hand.

**Voice-mode crossfading.** OpenUtau varies `spk_embed` per frame to blend
modes; one fixed mode is held across a phrase here, because a score has nowhere
to say otherwise.

**`--expressiveness` is per render, not per phrase.** A score cannot yet ask for
a straighter chorus and a freer verse.

**Notes crossing a barline are not split in the MusicXML.** LilyPond cannot
write one without a tie, so it does not arise from a valid score, but an
importer fed a hand-edited JSON could see an over-full measure.

**Tuplets are exported but not fixtured.** `note_type` reports the printed note
plus a `<time-modification>` and `test_units.py` covers the arithmetic, but
`dev/torture.ly` has no tuplet in it, so nothing checks the MusicXML end to end.
Adding one to the fixture moves every count the self-test asserts, which is why
it has not been done casually.

## 5. How to commit

One commit per coherent change, with the self-test green and the section-1
numbers re-measured in the message wherever the change could plausibly have
moved them. Do not mix a refactor and a behaviour change in one commit -- if you
find a real bug on the way (it happens; most of the ones fixed so far were found
exactly like this), commit the fix on its own, with the failing check added
before the fix.
