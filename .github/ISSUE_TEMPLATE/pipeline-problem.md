---
name: Something in the pipeline went wrong
about: A score that will not engrave, render, sync or sing
labels: bug
---

**The score.** Attach the `.ly`, or the smallest version of it that still goes
wrong. Most bugs here are a property of one score's structure and are not
reproducible without it.

**The command**, exactly as you ran it:

```
python3 lilypond-music/scripts/render.py ...
```

**What it printed.** Paste the whole run, not just the last line —
`render.py` and `sing.py` both print a bar count, an anchor count and a
verification table, and the line that disagrees with what you expected is
usually the whole diagnosis.

**What you expected instead.**

**Versions.** `lilypond --version`, `ffmpeg -version | head -1`, `python3 -V`,
and your OS. For a singing problem, also the bank and what
`python3 lilypond-music/scripts/dev/bank_check.py <bank>` says about it.
