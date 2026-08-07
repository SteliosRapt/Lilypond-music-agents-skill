# lilypond-music

A [Claude Code](https://claude.com/claude-code) skill for writing music: plain
text in, engraved notation and a performance out, and — if the score has lyrics
— the words actually sung.

```
score.ly  →  score.pdf    engraved notation
          →  score.midi   performance data
          →  score.mp3    synthesised audio
          →  score.mp4    the pages, with a playhead on each note as it sounds
          →  score-vocal.wav   the words, sung, by a neural singing model
```

The whole thing turns on LilyPond being a *compiler*: the same source produces
the picture and the sound, so a video showing the score while the music plays
can be built with guaranteed agreement between the two, rather than by lining
up two artefacts by hand.

[`songs/`](songs/) is a worked example — "Tide and Lantern", 28 bars of
unaccompanied SATB with a different neural voicebank on each part —
and [`songs/notes.md`](songs/notes.md) records exactly how it was made.

## Layout

```
lilypond-music/        the skill itself; this is what you install
  SKILL.md             what Claude reads first
  references/          the detail: notation, audio, video, singing
  assets/              templates, and the LilyPond instrumentation the tools need
  scripts/             the pipeline
songs/                 a finished piece, its score, and how it was made
docs/                  notes for working on the skill rather than with it
```

## Installing it as a skill

Copy or symlink `lilypond-music/` into your skills directory — `~/.claude/skills/`
for personal use, or `.claude/skills/` in a project — and Claude picks it up by
its `SKILL.md` front matter. Nothing else is needed to *read* the skill; the
tools below need their own dependencies.

## Using it directly

```bash
bash lilypond-music/scripts/setup.sh                       # once (~1 min)
python3 lilypond-music/scripts/render.py score.ly -o out/  # everything, verified
```

`setup.sh` installs lilypond, fluidsynth, a General MIDI soundfont and ffmpeg.
For the singing half:

```bash
bash lilypond-music/scripts/setup-singing.sh               # onnxruntime, pyyaml
python3 lilypond-music/scripts/sing.py score.ly --preview -o out/   # no bank needed
```

`--preview` sings the line through a built-in formant synthesiser: robotic,
instant, and enough to check that every syllable lands where you meant it to.
The real thing needs a voicebank, which you supply yourself — see below.

`lilypond-music/SKILL.md` is the full guide, and it is written to be read by a
person as well as by an agent.

## Voicebanks and vocoders are not in this repository

Deliberately. English DiffSinger banks are almost all non-commercial, several
forbid redistribution, and the community vocoders are CC BY-NC-SA 4.0. Nothing
here bundles or downloads one.

`lilypond-music/references/singing-synthesis.md` section 2 lists the four banks
that have actually been run through this pipeline — TIGER, CANARY, TRITON and
LIEE — with their release URLs and their terms. Read the terms; they differ, and
all four are non-commercial. `scripts/dev/bank_check.py` will qualify a bank
nobody here has tried in about two minutes.

## Working on it

```bash
python3 lilypond-music/scripts/dev/test_units.py    # under a second, no deps
python3 lilypond-music/scripts/dev/selftest.py      # the whole pipeline, ~2 min
```

Green before you start and green after every commit. `docs/development.md` is
the map: what each module is for, the numbers a change must not move, and what
is still open. `docs/pipeline-notes.md` is the record of what was established
about the DiffSinger models by probing them, which is the expensive knowledge in
this repository and mostly is not written down anywhere else.

## Licence

[MIT](LICENSE), for this repository's own contents: the scripts, the skill, the
documentation, and the music in `songs/`.

That covers the code and nothing else. The things this skill *drives* are not
MIT and are not redistributed here: the DiffSinger voicebanks are all
non-commercial and several forbid redistribution, and the community vocoders are
CC BY-NC-SA 4.0. `lilypond-music/references/singing-synthesis.md` section 2
names each one with its terms. Using this repository's code is one permission;
using a bank with it is a separate one you take up with that bank.
