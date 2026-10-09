---
name: A voicebank behaves differently
about: A DiffSinger bank that will not load, sings the wrong words, or sounds wrong
labels: voicebank
---

**Which bank**, and where it was released. Four have been run through this
pipeline and are described in
`lilypond-music/references/singing-synthesis.md` section 2; anything else is
new here.

**What `bank_check.py` says.** This is the first thing to run and it answers
most of the question in about two minutes:

```
python3 lilypond-music/scripts/dev/bank_check.py ~/voices/mybank
```

Paste its whole output: what the bank declares, which models were used, where
its pronunciations came from, and how far the rendered notes sat from the
written ones.

**What went wrong.** If the words are wrong rather than the sound, say which
words, and paste what
`python3 lilypond-music/scripts/phonemizer.py ~/voices/mybank <word>` gives for
them — a bank pronouncing English by another language's rules sounds fluent and
is wrong.

**Please don't attach the bank itself.** They are all non-commercial and
several forbid redistribution. A link to its own release page is right.
