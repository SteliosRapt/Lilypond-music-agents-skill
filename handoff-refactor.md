# Handoff: paying down the code debt in lilypond-music

The skill works. Everything it claims to do, it does, and `handoff.md` is the
record of what was built and what was established about the models. This
document is about the *shape* of the code, not its behaviour, and it exists
because that shape is now the thing most likely to cost the next person a day.

**Nothing here is a bug.** Every task below is a change that must leave the
output byte-for-byte comparable and every check still passing. If a refactor
makes something sound or look different, the refactor is wrong.

Read this whole file first. Section 7 is the order to do it in.

---

## 1. The one command that governs everything

```bash
python3 lilypond-music/scripts/dev/selftest.py          # 83 checks, ~2 min
python3 lilypond-music/scripts/dev/selftest.py --video  # + playhead verification
```

Green before you start, green after every commit. It is not optional and it is
not slow enough to skip. With a real voicebank it runs against that bank
instead of the two stubs -- 56 checks with TIGER, which has no `dsvariance`:

```bash
python3 lilypond-music/scripts/dev/selftest.py --video \
  --voice ~/voices/tiger_pack/TIGER_DS_v102_PACK/"Voice Library" \
  --vocoder ~/voices/pc_nsf_hifigan
```

See `handoff.md` section 8 for how to get that bank (566 MB, about seven
seconds) and section 6 for the rest of the test tooling.

**Numbers a refactor must not move.** Record them before you start and compare
after; a change that shifts any of these has changed behaviour, whatever the
tests say:

| measurement | where it comes from | value now |
|---|---|---|
| playhead anchors, `ensemble-voice.ly` | `render.py` stdout | 199 onsets, 8/8 systems, worst residual 1.4 px |
| playhead anchors, `lead-sheet.ly` | same | 44 onsets, 4/4 systems, worst residual 4.3 px |
| pitch tracking with `dspitch` | `librosa.yin` vs written notes | median 4.3 cents, max 10.7 |
| pitch tracking with `--literal-pitch` | same | median 1.1 cents, max 4.8 |
| vowel onsets | selftest, both timing modes | every one exactly on a written onset |
| vocoder resynthesis | `dev/vocoder_resynth_check.py` | 0.978 mel correlation, 1.7 cents |
| every bank still qualifies | `dev/bank_check.py` on TIGER, CANARY, TRITON | "no problems" on each, and LIEE with only its missing English plugin reported |

## 2. What NOT to do

- **Do not rewrite the prose.** Roughly a quarter of the source is explanation,
  and much of it records approaches that were tried and failed -- the sawtooth
  expression ramp, the CMUdict phone-set trap, `equalizer=t=h` not being a
  shelf, `dsdur` stretching consonants on long notes. That text is the most
  expensive thing in the repository to reproduce. Move it with the code it
  explains; do not summarise it away.
- **Do not change the CLI surface.** Flags, their defaults and their output
  format are documented in `SKILL.md` and three reference files, and an agent
  reading the skill will type exactly what those say.
- **Do not "improve" the byte-level MIDI handling into a library.** `mido` and
  friends re-encode events; `split_tracks` copies chunks verbatim so running
  status, channel assignments and the channel-10 drum mapping survive. That is
  deliberate and it is documented at the top of `midi_split.py`.
- **Do not merge modules that merely look similar.** `midi_timing`,
  `midi_split` and `midi_expression` share a *file format*, not a purpose:
  one builds a tempo map, one copies tracks apart, one rewrites events. Share
  the parsing primitives (task 3), not the parsers.

## 3. Task A -- unit tests for the pure functions (highest value)

**The problem.** There are 83 checks and all of them are end-to-end. A change
to `syllabify()` or `_affine()` has no test that names it; it is caught only if
it happens to move a number at the far end of a 1m20s pipeline. That is a slow,
indirect feedback loop, and it is why the tests currently tell you *that*
something broke rather than *what*.

**What to do.** Add `scripts/dev/test_units.py` -- plain `unittest`, no new
dependencies, running in under a second -- and wire it into `selftest.py` as
its first section so one command still runs everything.

The functions worth testing, in rough order of how much a silent break would
cost, with the case that actually matters for each:

| function | the case that matters |
|---|---|
| `vocal_score.note_type` | dotted and tuplet durations; the boundary where a duration stops being representable |
| `vocal_score.tempo_map` | `@META` and `@TEMPO` at the same moment -- the tempo-change bug came from exactly this tie, and priority 1 must win |
| `sing.Clock` | a moment before, at and after a change; that elapsed time accumulates across two changes rather than restarting |
| `sing.syllabify` | maximal onset; a dictionary/hyphenation mismatch (fewer vowels than notes) falling back without raising |
| `sing.phrase_split` | a rest exactly at the 0.6 s boundary |
| `sing.fill_silences` | a consonant squeezed to zero being dropped, not kept at zero |
| `column_map._affine` | a perfect fit returning residual 0; a degenerate input (all x equal) returning None |
| `column_map.system_transform` | equal counts; one surplus boundary picking the right subset; three surplus refusing |
| `render.eq_stage` | every preset name; `hp:`/`lp:`; a bell with and without `/Q`; three malformed forms |
| `render.parse_mix` | `mute`, a `/pan` clamped past ±1, a key matching two parts |
| `render.master_chain` | the filter order, and that `--band` reaches the chain |
| `predictors.resample_curve` | a length change up and down; a single-element curve |
| `preview_voice.letters_to_phonemes` | magic `e`, doubled consonants, word-initial `y` |
| `midi_expression._write_varlen` | round-trips against `_read_varlen` for 0, 127, 128, 0x0FFFFFFF |

`syllabify`, `fill_silences` and `phonemize` need a `voice`. Do not build a
bank for that -- they use only `is_vowel`, `consonant_len` and `silence`, so a
ten-line fake object is enough and makes the test readable.

**Done when** `python3 scripts/dev/test_units.py` runs in under a second, the
suite fails if you invert any single condition in the functions above, and
`selftest.py` runs it first and counts its results in the total.

## 4. Task B -- one MIDI reader instead of three

**The problem.** `_read_varlen` is copied byte-identically into
`midi_timing.py`, `midi_split.py` and `midi_expression.py`, and each of the
three also walks the `MThd`/`MTrk` chunk list itself (`midi_timing.py:47`,
`midi_split.py:126`, `midi_expression.py:193`). Three copies is where a fix
lands twice and misses once.

**What to do.** Add `scripts/smf.py` holding only the primitives:

```python
read_varlen(data, i) -> (value, i)
write_varlen(value)  -> bytes
header(data)         -> (fmt, ntracks, division)      # validates MThd, raises on junk
chunks(data)         -> [(kind, start, end, blob), ...]
```

Then import those in the three modules and delete the copies. Keep
`_scan_track`, `_parse_track` and `parse_midi` exactly where they are -- they
have different jobs and merging them is the trap in section 2.

**Trap.** `midi_split` writes a two-track file with a hand-built header
(`b"MThd" + struct.pack(">IHHH", 6, 1, 2, division)`). If `smf.py` grows a
`write_header`, it must produce those same bytes; a format-0 file or a wrong
track count changes what fluidsynth renders.

**Done when** `grep -c "def _read_varlen" scripts/*.py` returns nothing,
`selftest.py` is green, and a rendered `.midi` is byte-identical to one
rendered before the change (`cmp` them -- this is worth doing explicitly).

## 5. Task C -- split `sing.py`, and shorten the two `main()`s

**The problem.** `sing.py` is 1195 lines doing four jobs: loading a bank,
turning words into a phoneme timeline, building a pitch curve, and being a CLI.
`render.py:main` is 172 lines and `sing.py:main` is 165. The median function in
the repository is 12 lines, so these are the outliers, and they are where
someone new loses the thread.

**What to do**, in this order, running the self-test between each:

1. Move `Voice`, `PreviewVoice` and `inspect_bank` into `scripts/voicebank.py`.
   They are about *a bank on disk* and nothing else in `sing.py` is.
2. Move `lookup`, `syllabify`, `predicted_lengths`, `phonemize`,
   `fill_silences` and `CONSONANT_S` into `scripts/phonemes.py`. This is the
   part with the real subtleties (semivowels, backward-laid consonants, dropped
   zero-length phonemes) and it deserves to be readable on its own.
3. Leave `Clock`, `written_pitch`, `f0_curve`, `note_spans`, `render_phrase`
   and `main` in `sing.py`. Measured, those two moves are about 350 and 190
   lines, leaving roughly 650 -- a coherent module about turning a score into
   audio, and the point at which to stop. If you want it smaller, the next cut
   is the pitch section, not another slice off the top.
4. In both `main()`s, lift the argument *validation* into small functions
   (`parse_band`, `resolve_predictors`) so `main` reads as a sequence of steps.
   Do not lift the steps themselves into a class; the linear shape is right.

**Trap.** `predictors.py` is imported inside `Voice.__init__` and
`preview_voice` inside `PreviewVoice.__init__`, deliberately -- `sing.py
--preview` must work with no `onnxruntime` installed. Keep the imports lazy and
keep `--preview` working without the singing dependencies; the self-test does
not currently catch this, so add a check that it does.

**Done when** no module is over ~700 lines, no function over ~80, `--preview`
still runs in an environment without `onnxruntime`, and every import in
`SKILL.md`'s script list still resolves.

## 6. Task D -- two smaller things

**`sys.exit` from importable modules.** 25 calls in `sing.py`, 15 in
`render.py`. It is fine for a CLI and hostile to reuse: `selftest.py` already
has to run them as subprocesses rather than import them. Convert the ones in
library-ish code paths to raising a `SkillError` (or `ValueError`) and catch it
once in `main()`, printing exactly the message it prints today. The message
text is documented in places -- keep it.

**Two broad `except Exception`** (`sing.py:202`, `sing.py:443`,
`predictors.py:488`). These are deliberate: a bank that fails to load should be
reported and skipped, not crash a render. But they would swallow a programming
error just as quietly. Narrow them to what onnxruntime and zipfile actually
raise, and let anything else through.

## 7. Task E -- verify `dsvariance` against a real bank

This is the one open *behavioural* question and it is research, not a refactor,
so do it last or hand it off separately.

`VariancePredictor` was written from declared interfaces and tested against a
stub, because no bank to hand ships a `dsvariance/` folder -- TIGER sets
`use_energy_embed: false` and has none. The stub proves the shapes are right.
It cannot prove the *meanings* are.

**Find a bank with one.** A bank ships it if `dsvariance/dsconfig.yaml` exists
with `predict_energy` / `predict_breathiness` / `predict_voicing` /
`predict_tension` flags, and its acoustic model declares matching inputs. Run
`sing.py --voice PATH --inspect` and read, rather than trusting a download
page.

**Then check three things, in this order**, because each one fails silently:

1. **The `retake` axis.** It is fed as `[1, n_frames, n_parameters]` with the
   parameter axis ordered by the model's own output names. If a bank orders its
   outputs differently from `PARAMETERS`, energy and breathiness swap and the
   result is merely a bit odd rather than obviously broken.
2. **The domain.** These are log-domain, roughly dB, 0 unity and −96 silence
   (`handoff.md` section 3). If a model returns something in 0..1 instead, the
   acoustic model reads it as near-silence. Print the returned range before
   listening to anything.
3. **Then listen**, with `--variance 0,0,0,0` and again with a clear offset, and
   confirm the offsets move the sound in the direction the flag says.

**Done when** `singing-synthesis.md` section 7 says `dsvariance` is verified
against a named bank rather than against a stub, and `handoff.md` section 7
loses its first bullet.

## 8. The order, and how to commit it

Task A first: unit tests are what make B and C safe, and writing them will
teach you the functions you are about to move. Then B (small, mechanical,
proves the tests work). Then C, one numbered step per commit. Then D. Then E,
or hand E to someone with a bank.

One commit per task, each with the self-test green and the section-1 numbers
re-measured in the message where the task could plausibly have moved them.
Do not mix a refactor and a behaviour change in one commit -- if you find a
real bug on the way (it happens; three of the five fixed so far were found
exactly like this), commit the fix on its own first, with the failing check
added to the self-test before the fix.
