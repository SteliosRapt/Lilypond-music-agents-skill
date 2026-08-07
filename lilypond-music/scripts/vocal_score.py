#!/usr/bin/env python3
"""Extract the sung lines of a LilyPond score as a singing-synthesis score.

    python3 scripts/vocal_score.py score.ly -o out/

Writes, per lyric line found:

    out/<stem>-<voice>-<n>.musicxml    one monophonic part, syllables attached
    out/<stem>-vocals.json             the same data, canonical, for other engines

and prints what it found.  MusicXML is the lingua franca of the open singing
synthesisers -- NNSVS and Sinsy read it directly, ESPnet's SVS recipes and the
OpenUtau/DiffSinger tools import it -- so it is the handover format even though
nothing here goes near a general-purpose .ly -> MusicXML converter.

The reason for that distinction is worth stating.  Converting a whole score to
MusicXML is a translation problem nobody has solved well: python-ly's exporter
drops `\\lyricsto` lines silently and crashes outright on `\\addlyrics`, which is
to say it fails precisely on the vocal writing this needs.  But a singing
synthesiser does not want the score.  It wants one monophonic line with a
syllable on each note, and *that* can be built upwards from data LilyPond hands
over about its own interpretation (assets/lyrics.ily) rather than downwards by
translating an engraving.  The output is small, well-formed and complete,
because it only ever describes something simple.
"""

import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
LYRICS_ILY = HERE.parent / "assets" / "lyrics.ily"

# 3360 = 2^5*3*5*7 per quarter: duples, triplets, quintuplets and septuplets all
# land on integers, so no tuplet turns into a rounded duration.
DIVISIONS = 3360

TYPES = [
    (4, "whole"), (2, "half"), (1, "quarter"), (Fraction(1, 2), "eighth"),
    (Fraction(1, 4), "16th"), (Fraction(1, 8), "32nd"), (Fraction(1, 16), "64th"),
]
# (actual, normal) ratios tried in order, so an ordinary note is never reported
# as a tuplet of itself. Each normal is the largest power of two below its
# actual, which is what "3 in the time of 2" and "7 in the time of 4" mean.
TUPLETS = [(1, 1), (3, 2), (5, 4), (6, 4), (7, 4), (9, 8), (11, 8), (13, 8)]
SHARP_SPELL = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
               ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
FLAT_SPELL = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
              ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]


# ---------------------------------------------------------------- extraction

def instrument(score, work):
    """Run LilyPond with the reporting engraver and return its stderr."""
    work.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["lilypond", f"-dinclude-settings={LYRICS_ILY}", "-dno-point-and-click",
         # The report shares one stream -- stderr -- with everything else
         # LilyPond says, so anything else it says can land in the middle of a
         # line. Two things used to, and both corrupt a part silently rather
         # than loudly:
         #
         #   * progress markers. `@NOTE [16]"bass" 12.5 0.25 58` files that
         #     note under a voice nothing else uses, which is to say it deletes
         #     it, and a part one note short sings every syllable after it one
         #     note early. `--loglevel=WARN` stops them and keeps every warning.
         #   * `-dbackend=null`, which used to be passed here for speed, makes
         #     LilyPond fail at book output on *every* score -- exit 1 and a
         #     Guile backtrace, harmless in itself because the interpretation
         #     has already been reported by then, except that the backtrace is
         #     printed into this stream too. It landed inside a syllable on a
         #     four-part score and turned `"ter"` into a fragment of init.ly.
         #
         # Drawing the page instead costs a quarter of a second on a 28-bar
         # score and the stream comes back with nothing in it but the report.
         "--loglevel=WARN",
         "-o", str(work / "probe"), str(score)],
        capture_output=True, text=True)
    if proc.returncode != 0 and "@NOTE" not in proc.stderr:
        sys.exit(f"lilypond failed:\n{proc.stderr[-2000:]}")
    return proc.stderr


TOKEN = re.compile(r'^@(\w+)\s+(.*)$')
PROGRESS = re.compile(r'\[\d+\]')
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def unquote(s):
    m = QUOTED.match(s.strip())
    return m.group(1).replace('\\"', '"') if m else s.strip()


def parse(stream):
    """Turn the @-lines into note lists per voice and syllable lists per line."""
    notes, ties, syls, hyphens, extenders = {}, {}, {}, {}, {}
    line_voice, voice_staff, meta = {}, {}, {}
    metres, tempos = {}, []

    for raw in stream.splitlines():
        # Belt and braces against the progress markers `--loglevel=WARN` is
        # there to prevent: a `[16]` sitting between a tag and its voice name
        # is not a parse error, it is a note filed under a voice that does not
        # exist. A syllable that genuinely reads "[16]" is not a thing.
        raw = PROGRESS.sub("", raw).strip()
        m = TOKEN.match(raw)
        if not m:
            continue
        tag, rest = m.group(1), m.group(2)

        # LilyPond writes its own progress to this same stream, and it can
        # land inside one of these lines. A malformed report is not worth
        # aborting a render for.
        try:
            if tag == "NOTE":
                voice = unquote(rest)
                _, when, dur, pitch = rest.rsplit(" ", 3)
                notes.setdefault(voice, []).append(
                    {"when": float(when), "dur": float(dur), "pitch": int(pitch)})
            elif tag == "TIE":
                voice = unquote(rest)
                ties.setdefault(voice, set()).add(float(rest.rsplit(" ", 1)[1]))
            elif tag == "VOICE":
                idx, name = rest.split(" ", 1)
                voice_staff[unquote(name)] = int(idx)
            elif tag == "LYR":
                idx, name = rest.split(" ", 1)
                line_voice[int(idx)] = unquote(name)
            elif tag == "SYL":
                idx, when, text = rest.split(" ", 2)
                syls.setdefault(int(idx), []).append(
                    {"when": float(when), "text": unquote(text)})
            elif tag == "HYPH":
                idx, when = rest.split()
                hyphens.setdefault(int(idx), set()).add(float(when))
            elif tag == "EXT":
                idx, when = rest.split()
                extenders.setdefault(int(idx), set()).add(float(when))
            elif tag == "META":
                idx, when, sig, qpm = rest.split()
                num, den = sig.split("/")
                staff = int(idx)
                metres.setdefault(staff, []).append(
                    (float(when), int(num), int(den)))
                meta.setdefault(staff, {}).setdefault("beats", int(num))
                meta[staff].setdefault("beat_type", int(den))
                if float(qpm) > 0:
                    # Priority 0: a metre change only reports the tempo in
                    # force as it passes, and `\time` written before `\tempo`
                    # at the same moment reports the tempo it is about to
                    # replace. A real @TEMPO at the same moment must win.
                    tempos.append((float(when), 0, float(qpm)))
            elif tag == "KEY":
                idx, fifths = rest.split()
                meta.setdefault(int(idx), {})["fifths"] = int(fifths)
            elif tag == "TEMPO":
                when, qpm = rest.split()[:2]
                tempos.append((float(when), 1, float(qpm)))
        except (ValueError, IndexError):
            continue

    for v in notes:
        notes[v].sort(key=lambda n: n["when"])
    for s in metres:
        metres[s] = sorted(set(metres[s]))
    return dict(notes=notes, ties=ties, syls=syls, hyphens=hyphens,
                extenders=extenders, line_voice=line_voice,
                voice_staff=voice_staff, meta=meta, metres=metres,
                tempo_map=tempo_map(tempos))


def tempo_map(events):
    """[(moment, quarters per minute), ...], deduplicated, always starting at 0.

    A score with a `rit.` written as staged `\\tempo` marks changes speed
    several times, and a single number cannot describe it. Reporting the whole
    map is what lets everything downstream place a note in seconds the way
    LilyPond's own MIDI does, rather than by multiplying by one tempo and
    drifting from the moment of the first change.
    """
    out = []
    for when, _priority, qpm in sorted(events):
        if qpm <= 0:
            continue
        if out and abs(out[-1][1] - qpm) < 1e-9:
            continue
        if out and abs(out[-1][0] - when) < 1e-9:
            out[-1] = (when, qpm)
            continue
        out.append((when, qpm))
    if not out:
        return [[0.0, 60.0]]
    if out[0][0] > 0:
        out.insert(0, (0.0, out[0][1]))
    return [[w, q] for w, q in out]


# ------------------------------------------------------------------ assembly

def assemble(data, line):
    """Join one lyric line to its voice's notes.

    Every note gets one of three roles.  It begins a syllable, or it continues
    the syllable before it (a melisma -- a note LilyPond passed over when it
    placed the lyrics, because a slur or a `_` told it to), or it is tied to
    the note before it and is not a separate sung note at all.
    """
    voice = data["line_voice"][line]
    notes = data["notes"].get(voice, [])
    ties = data["ties"].get(voice, set())
    hyphens = data["hyphens"].get(line, set())
    extenders = data["extenders"].get(line, set())

    # A syllable of a single space is `_` in \lyricmode: a note deliberately
    # left without a word.  It is an instruction to skip, not something to sing.
    at = {s["when"]: s["text"] for s in data["syls"].get(line, [])
          if s["text"].strip()}

    out, word = [], []
    for n in notes:
        text = at.get(n["when"])
        tied_in = bool(out) and any(abs(t - out[-1]["when"]) < 1e-9 for t in ties) \
            and abs(out[-1]["when"] + out[-1]["dur"] - n["when"]) < 1e-9
        note = {"when": n["when"], "dur": n["dur"], "pitch": n["pitch"],
                "syllable": text, "tied": tied_in,
                "melisma": text is None and not tied_in,
                "hyphen": n["when"] in hyphens,
                "extender": n["when"] in extenders}
        if tied_in:
            out[-1]["ties_out"] = True
        out.append(note)

        # Word reconstruction: a syllable followed by `--` continues the word.
        # English grapheme-to-phoneme conversion needs the whole word, not the
        # fragments -- "lan" and "terns" phonemise to nothing like "lanterns".
        if text is not None:
            word.append(note)
            if not note["hyphen"]:
                for i, w in enumerate(word):
                    w["word"] = "".join(x["syllable"] for x in word)
                    w["syllabic"] = ("single" if len(word) == 1 else
                                     "begin" if i == 0 else
                                     "end" if i == len(word) - 1 else "middle")
                word = []
    for w in word:                                    # unterminated final word
        w["word"] = w["syllable"]
        w["syllabic"] = "single"
    return voice, out


# ------------------------------------------------------------------ MusicXML

def note_type(dur):
    """How a duration in whole notes is printed: (type, dots, actual, normal).

    `actual`/`normal` are the tuplet ratio -- 3 and 2 for a triplet -- and are
    1, 1 for an ordinary note. Both are needed together: MusicXML states a
    tuplet as the *plain* notehead it is drawn with plus a
    `<time-modification>` saying how many fit in the time of how many, so a
    triplet quaver is an `eighth` three-in-the-time-of-two and not a duration
    of its own. Emitting the duration alone leaves an importer with a notehead
    it cannot choose, which is what used to happen to every tuplet here.

    Returns (None, 0, 1, 1) for a length no notehead spells, rather than
    rounding to the nearest one -- the `<duration>` is exact regardless, and a
    wrong notehead is worse than none.
    """
    d = Fraction(dur).limit_denominator(3360)
    for actual, normal in TUPLETS:
        scaled = d * actual / normal
        for base, name in TYPES:
            b = Fraction(base) / 4
            for dots in (0, 1, 2, 3):
                if scaled == b * (2 - Fraction(1, 2 ** dots)):
                    return name, dots, actual, normal
    return None, 0, 1, 1


def sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def add_note(measure, dur, pitch=None, fifths=0, syllable=None, syllabic=None,
             tie=None, slur=None):
    n = ET.SubElement(measure, "note")
    if pitch is None:
        ET.SubElement(n, "rest")
    else:
        step, alter = (FLAT_SPELL if fifths < 0 else SHARP_SPELL)[pitch % 12]
        p = sub(n, "pitch")
        sub(p, "step", step)
        if alter:
            sub(p, "alter", alter)
        sub(p, "octave", pitch // 12 - 1)
    sub(n, "duration", int(round(dur * 4 * DIVISIONS)))
    if tie:
        ET.SubElement(n, "tie", {"type": tie})
    sub(n, "voice", 1)
    kind, dots, actual, normal = note_type(dur)
    if kind:
        sub(n, "type", kind)
        for _ in range(dots):
            ET.SubElement(n, "dot")
    if actual != normal:
        mod = sub(n, "time-modification")
        sub(mod, "actual-notes", actual)
        sub(mod, "normal-notes", normal)
    if syllable:
        lyr = sub(n, "lyric", number="1")
        sub(lyr, "syllabic", syllabic or "single")
        sub(lyr, "text", syllable)
    if tie or slur:
        nots = sub(n, "notations")
        if tie:
            ET.SubElement(nots, "tied", {"type": tie})
        if slur:
            ET.SubElement(nots, "slur", {"type": slur, "number": "1"})
    return n


def write_tempo(measure, qpm):
    """A metronome direction, printed and sounding, at the head of a measure."""
    direction = sub(measure, "direction", placement="above")
    dtype = sub(direction, "direction-type")
    metronome = ET.SubElement(dtype, "metronome")
    sub(metronome, "beat-unit", "quarter")
    sub(metronome, "per-minute", int(round(qpm)))
    sub(direction, "sound", tempo=int(round(qpm)))


def musicxml(notes, meta, tempo_map, part_name, metres=()):
    """Build a one-part MusicXML score from an assembled vocal line.

    Melismata are written as slurs, which is how Sinsy and everything descended
    from it recognise "hold the vowel across these notes" -- the note simply
    carrying no <lyric> is not enough for some importers.

    `metres` and `tempo_map` are lists of changes, not single values. A score
    that changes metre mid-piece has bars of two lengths, and barring the whole
    file at the opening metre puts every measure after the change in the wrong
    place -- silently, since the note durations stay right and only the
    barlines lie.
    """
    changes = sorted(set((Fraction(w).limit_denominator(3360), n, d)
                         for w, n, d in metres)) or [
        (Fraction(0), meta.get("beats", 4), meta.get("beat_type", 4))]
    if changes[0][0] > 0:
        changes.insert(0, (Fraction(0), changes[0][1], changes[0][2]))
    tempo_map = [(Fraction(w).limit_denominator(3360), q) for w, q in tempo_map] \
        or [(Fraction(0), 60.0)]
    fifths = meta.get("fifths", 0)

    def metre_at(moment):
        cur = changes[0]
        for c in changes:
            if c[0] <= moment:
                cur = c
        return cur[1], cur[2]

    root = ET.Element("score-partwise", {"version": "3.1"})
    plist = sub(root, "part-list")
    sp = ET.SubElement(plist, "score-part", {"id": "P1"})
    sub(sp, "part-name", part_name)
    part = ET.SubElement(root, "part", {"id": "P1"})

    beats, beat_type = metre_at(Fraction(0))
    bar = Fraction(beats, beat_type)
    number = 1
    remaining_changes = [c for c in changes if c[0] > 0]
    remaining_tempos = list(tempo_map[1:])

    measure = ET.SubElement(part, "measure", {"number": str(number)})
    attrs = sub(measure, "attributes")
    sub(attrs, "divisions", DIVISIONS)
    key = sub(attrs, "key")
    sub(key, "fifths", fifths)
    time = sub(attrs, "time")
    sub(time, "beats", beats)
    sub(time, "beat-type", beat_type)
    clef = sub(attrs, "clef")
    sub(clef, "sign", "G")
    sub(clef, "line", 2)
    write_tempo(measure, tempo_map[0][1])

    cursor = Fraction(0)

    # Slur every run of melismatic notes back to the note that started it.  That
    # note is not always the one carrying the syllable: a tie can sit between
    # them (`f2 ~ f4` then a slurred run), and the slur has to reach from the
    # last sounding note, not from the last syllable.
    slur_at, i = {}, 0
    while i < len(notes):
        if notes[i]["melisma"] and i > 0:
            j = i
            while j + 1 < len(notes) and notes[j + 1]["melisma"]:
                j += 1
            slur_at[i - 1], slur_at[j] = "start", "stop"
            i = j + 1
        else:
            i += 1

    bar_start = Fraction(0)

    def ensure_measure():
        """Open a new measure if the cursor has reached a barline.

        Deferring this until something is actually about to be written is what
        keeps a bar filled exactly by one whole note from opening an empty bar
        after it -- and, the other way round, stops the note after such a bar
        from being appended to a measure that is already full.

        Barlines are tracked from the last one rather than by `cursor % bar`,
        because after a metre change the two stop agreeing: a 3/4 bar following
        four 4/4 bars starts at 4, and 4 is not a multiple of 3/4.
        """
        nonlocal measure, number, bar, beats, beat_type, bar_start
        if cursor <= bar_start or cursor - bar_start < bar:
            return
        if measure.find("note") is None:
            return
        bar_start += bar
        number += 1
        measure = ET.SubElement(part, "measure", {"number": str(number)})
        while remaining_changes and remaining_changes[0][0] <= bar_start:
            _at, beats, beat_type = remaining_changes.pop(0)
            bar = Fraction(beats, beat_type)
            attrs = sub(measure, "attributes")
            time = sub(attrs, "time")
            sub(time, "beats", beats)
            sub(time, "beat-type", beat_type)
        while remaining_tempos and remaining_tempos[0][0] <= bar_start:
            write_tempo(measure, remaining_tempos.pop(0)[1])

    def fill_to(target):
        """Emit rests, splitting at every barline, until the cursor reaches target."""
        nonlocal cursor
        while cursor < target:
            ensure_measure()
            end = min(target, bar_start + bar)
            add_note(measure, float(end - cursor), fifths=fifths)
            cursor = end

    for i, n in enumerate(notes):
        when = Fraction(n["when"]).limit_denominator(3360)
        dur = Fraction(n["dur"]).limit_denominator(3360)
        fill_to(when)
        ensure_measure()
        add_note(measure, float(dur), pitch=n["pitch"], fifths=fifths,
                 syllable=n["syllable"], syllabic=n.get("syllabic"),
                 tie="start" if n.get("ties_out") else ("stop" if n["tied"] else None),
                 slur=slur_at.get(i))
        cursor = when + dur

    # Pad the final bar so importers do not see a truncated measure.
    if bar_start < cursor < bar_start + bar:
        fill_to(bar_start + bar)

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--stem", help="basename for outputs")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    score = Path(args.score).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or score.stem
    work = outdir / f".{stem}-vocal"

    data = parse(instrument(score, work))
    if not data["line_voice"]:
        sys.exit("no lyrics found in this score -- nothing to sing.\n"
                 "A sung line needs a \\new Lyrics attached to a named Voice, e.g.\n"
                 '  \\new Staff \\new Voice = "singer" \\voicePart\n'
                 '  \\new Lyrics \\lyricsto "singer" \\voiceWords')

    # `tempo` stays for anything that only wants one number; `tempo_map` is
    # the truth, and it is what sing.py places notes with.
    tempo_map = data["tempo_map"]
    doc = {"score": str(score), "tempo": tempo_map[0][1],
           "tempo_map": tempo_map, "lines": []}
    written = []
    for line in sorted(data["line_voice"]):
        voice, notes = assemble(data, line)
        if not notes:
            continue
        meta = data["meta"].get(data["voice_staff"].get(voice, -1), {})
        name = f"{stem}-{voice or 'voice'}-{line + 1}"
        path = outdir / f"{name}.musicxml"
        staff = data["voice_staff"].get(voice, -1)
        musicxml(notes, meta, tempo_map, voice or "Voice",
                 data["metres"].get(staff, [])).write(
            path, encoding="UTF-8", xml_declaration=True)
        written.append(path)

        sung = [n for n in notes if not n["tied"]]
        words = len({n.get("word") for n in notes if n.get("word")})
        text = " ".join(n["syllable"] for n in notes if n["syllable"])
        doc["lines"].append({
            "line": line + 1, "voice": voice, "staff": staff,
            "metres": data["metres"].get(staff, []),
            "musicxml": path.name, "notes": notes})
        print(f"  line {line + 1}  voice {voice!r}  staff "
              f"{data['voice_staff'].get(voice, -1)}: {len(sung)} sung notes, "
              f"{sum(1 for n in notes if n['melisma'])} melismatic, "
              f"{words} words")
        print(f"    {text[:90]}{'...' if len(text) > 90 else ''}")
        print(f"    -> {path.name}")

    js = outdir / f"{stem}-vocals.json"
    js.write_text(json.dumps(doc, indent=2))
    print(f"  -> {js.name}")

    if not args.keep_temp:
        for f in work.glob("*"):
            f.unlink()
        work.rmdir()


if __name__ == "__main__":
    main()
