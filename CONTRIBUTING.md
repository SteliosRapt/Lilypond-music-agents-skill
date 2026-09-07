# Contributing

Two commands govern everything here. Green before you start, green after every
commit:

```bash
python3 lilypond-music/scripts/dev/test_units.py    # ~90 checks, under a second
python3 lilypond-music/scripts/dev/selftest.py      # those plus ~90 more, ~2 min
```

`test_units.py` needs nothing but numpy. `selftest.py` needs the toolchain
(`bash lilypond-music/scripts/setup.sh`) and, for the singing half,
`bash lilypond-music/scripts/setup-singing.sh --dev`. Both run in CI on every
pull request, alongside `flake8 --max-line-length=100 --extend-ignore=E731` and
`python3 tools/check_repo.py`.

**[`docs/development.md`](docs/development.md) is the real guide** — what each
module is for, the measured numbers a change must not move, the conventions
that hold across every file, and what is still open. Read it before changing
code. [`docs/pipeline-notes.md`](docs/pipeline-notes.md) is the other half: what
was established about the DiffSinger models by probing them, which is the
expensive knowledge in this repository and mostly is not written down anywhere
else.

## Four things not to do

These are the ones that have cost time before, and they are explained at
length in `docs/development.md` section 3:

- **Don't rewrite the prose.** About a quarter of the source is explanation,
  much of it recording approaches that were tried and failed. Move it with the
  code it explains; don't summarise it away.
- **Don't change the CLI surface.** Flags, defaults and output formats are
  documented in `SKILL.md` and four reference files, and an agent reading the
  skill types exactly what those say.
- **Don't turn the byte-level MIDI handling into a library call.** `mido` and
  friends re-encode events; the code here copies chunks verbatim so running
  status and the channel-10 drum mapping survive.
- **Don't merge modules that merely look similar.** `midi_timing`, `midi_split`
  and `midi_expression` share a file format, not a purpose.

## Commits

One commit per coherent change, with the self-test green. Don't mix a refactor
and a behaviour change — if you find a real bug on the way, commit the fix on
its own, with the failing check added before the fix. Where a change could
plausibly have moved one of `docs/development.md`'s measured numbers,
re-measure it and put the result in the commit message.

## Reporting something broken

A pipeline bug is much easier to fix with the score attached. The most useful
report is the `.ly` file, the exact command, and what the run printed —
`render.py` and `sing.py` both print a verification table, and the line that
disagrees with what you expected is usually the whole diagnosis.

## Documentation

The Markdown here is read by agents as well as by people, and an agent types
the paths it reads. `tools/check_repo.py` resolves every relative link and
path-shaped code span in every Markdown file, so a renamed script fails CI
rather than silently sending a reader nowhere.

To regenerate the README's images and animation after a change to the
pipeline's output:

```bash
python3 tools/make_media.py
```

## Licence

Contributions are accepted under this repository's [MIT licence](LICENSE). Note
that the voicebanks and vocoders this code *drives* are not MIT, are all
non-commercial, and are not redistributed here — please don't add one, or a
script that downloads one.
