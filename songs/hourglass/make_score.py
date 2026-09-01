#!/usr/bin/env python3
"""Writes hourglass.ly.

Most of the score is written out by hand in the template at the bottom of this
file.  Two passages are combinatorial and are generated instead:

  * the hocket (bars 15-30): one seven-syllable line per 7/8 bar, passed
    between the four voices one syllable at a time -- solo rotation, then
    pairs, then all four -- across three octaves and four modes, with the
    piano doubling it from step 3;
  * the Shepard rise in the violins (bars 11-30): two voices a bar apart,
    each climbing an octave per bar and fading at the top, so that the
    strings seem to rise forever.

Run it from anywhere:  python3 make_score.py   -> hourglass.ly beside it.
"""
import os

MODES = {
    "aeolian":  ["c", "d", "ees", "f", "g", "aes", "bes"],
    "dorian":   ["c", "d", "ees", "f", "g", "a",   "bes"],
    "harmonic": ["c", "d", "ees", "f", "g", "aes", "b"],
    "phrygdom": ["c", "des", "e", "f", "g", "aes", "bes"],
}
STEP_MODE = ["aeolian", "dorian", "harmonic", "phrygdom"]
STEP_TEMPO = [176, 200, 224, 248]
STEP_DYN = ["\\mp", "\\mf", "\\f", "\\ff"]

# the line: (scale degree, octave) where octave 0 starts on c'' (soprano register)
LINES = [
    [(1, 0), (2, 0), (3, 0), (2, 0), (1, 0), (7, -1), (1, 0)],
    [(5, -1), (6, -1), (7, -1), (6, -1), (5, -1), (4, -1), (5, -1)],
    [(3, 0), (2, 0), (1, 0), (7, -1), (6, -1), (5, -1), (4, -1)],
    [(4, -1), (5, -1), (6, -1), (7, -1), (1, 0), (2, 0), (3, 0)],
]
TEXTS = [
    ["Turn", "the", "glass", "and", "count", "the", "grain,"],
    ["turn", "the", "glass", "and", "name", "the", "hour,"],
    ['"ev-"', '"’ry"', "grain", "a", "year", "of", "light,"],
    ['"ev-"', '"’ry"', "year", "a", "grain", "of", "night."],
]
VOICES = ["S", "A", "T", "B"]
# written octave relative to the soprano: the tenor is in treble_8, so its
# written pitch equals the soprano's and sounds an octave lower; the bass is
# written two octaves down and sounds there.
WRITTEN_SHIFT = {"S": 0, "A": 0, "T": 0, "B": -2}


def octmark(n):
    return "'" * n if n >= 0 else "," * (-n)


def note(mode, degree, octave, shift=0):
    return MODES[mode][degree - 1] + octmark(octave + 2 + shift)


def assignment(step, k):
    """which voices sing syllable k (0..27 within the step)"""
    if step == 0:
        return [VOICES[k % 4]]                       # S A T B S A T | B S A ...
    if step == 1:
        return ["S", "T"] if k % 2 == 0 else ["A", "B"]   # octave pairs
    if step == 2:
        return ["S", "A"] if k % 2 == 0 else ["T", "B"]   # register pairs
    return VOICES[:]                                       # tutti


def rests(n):
    out = []
    while n >= 2:
        out.append("r4")
        n -= 2
    if n:
        out.append("r8")
    return out


def bar_of(events):
    out, run = [], 0
    for e in events:
        if e is None:
            run += 1
        else:
            out += rests(run)
            run = 0
            out.append(e + "8")
    out += rests(run)
    return out


def with_dynamic(tokens, dyn):
    """attach dyn to the first note token"""
    for i, t in enumerate(tokens):
        if not t.startswith("r"):
            tokens[i] = t + dyn
            break
    return tokens


def hocket():
    notes = {v: [] for v in VOICES}
    words = {v: [] for v in VOICES}
    piano = []
    for step in range(4):
        mode = STEP_MODE[step]
        header = f"    %% step {step+1}: {mode}, ♪ = {STEP_TEMPO[step]}"
        for v in VOICES:
            notes[v].append(header)
        if step >= 2:
            piano.append(header)
        first = {v: True for v in VOICES}
        pfirst = True
        for bar in range(4):
            evs = {v: [None] * 7 for v in VOICES}
            for i in range(7):
                k = bar * 7 + i
                for v in assignment(step, k):
                    deg, oc = LINES[bar][i]
                    evs[v][i] = note(mode, deg, oc, WRITTEN_SHIFT[v])
                    words[v].append(TEXTS[bar][i])
            for v in VOICES:
                toks = bar_of(evs[v])
                if first[v] and any(not t.startswith("r") for t in toks):
                    toks = with_dynamic(toks, STEP_DYN[step])
                    first[v] = False
                if step == 3 and bar == 3:
                    toks[0] += "\\<"
                    toks[-1] += "\\!"
                notes[v].append("    " + " ".join(toks) + " |")
            if step >= 2:
                toks = []
                for i in range(7):
                    deg, oc = LINES[bar][i]
                    hi = note(mode, deg, oc)
                    lo = note(mode, deg, oc, -1)
                    toks.append(f"<{lo} {hi}>8")
                if pfirst:
                    toks[0] += STEP_DYN[step]
                    pfirst = False
                if step == 3 and bar == 3:
                    toks[0] += "\\<"
                    toks[-1] += "\\!"
                piano.append("    " + " ".join(toks) + " |")
    return notes, words, piano


def shepard():
    modes = ["aeolian"] * 8 + ["dorian"] * 4 + ["harmonic"] * 4 + ["phrygdom"] * 4

    def bar(mode, low, last):
        octave = 1 if low else 2
        ns = [MODES[mode][d] + "'" * octave for d in range(7)]
        ns[0] += "8" + ("\\pp\\<" if low else "\\mf\\>")
        if last:
            ns[-1] += "\\!"
        return "    " + " ".join(ns) + " |"

    va, vb = [], []
    for i in range(20):
        va.append(bar(modes[i], low=(i % 2 == 0), last=(i == 19)))
        vb.append("    s8*7 |" if i == 0 else
                  bar(modes[i], low=((i - 1) % 2 == 0), last=(i == 19)))
    return va, vb


def block(name, lines):
    return name + " = {\n" + "\n".join(lines) + "\n}\n"


notes, words, piano = hocket()
va, vb = shepard()

generated = ["%% ---- generated by make_score.py: the hocket, bars 15-30 ----\n"]
for v in VOICES:
    generated.append(block(f"hocket{v}", notes[v]))
    generated.append(f"hocket{v}Words = \\lyricmode {{\n    " + " ".join(words[v]) + "\n}\n")
generated.append(block("hocketPiano", piano))
generated.append("%% ---- generated by make_score.py: the Shepard rise, bars 11-30 ----\n")
generated.append(block("shepardOne", va))
generated.append(block("shepardTwo", vb))
GENERATED = "\n".join(generated)

TEMPLATE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "hourglass.template.ly"), encoding="utf-8").read()
out = TEMPLATE.replace("%%GENERATED%%", GENERATED)
dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hourglass.ly")
open(dest, "w", encoding="utf-8").write(out)
print("wrote", dest)
