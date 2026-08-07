"""Words and notes to a phoneme timeline: what the acoustic model is fed.

This is the part of the singing pipeline with the real subtleties in it, and
none of them are visible from the audio afterwards:

  * a syllable's consonants land *before* the beat, because a singer starts the
    "l" of "lantern" early so the vowel arrives on time. They are borrowed from
    whatever precedes them, and a syllable fast enough to overrun the one before
    it truncates it (`phonemize`, `fill_silences`).
  * only true vowels are syllable nuclei. OpenUtau's dictionaries call `w` and
    `y` semivowels, and letting one be the nucleus turns "world" into
    [w][er l d] with the note on the w (`voice.is_vowel`, in voicebank.py).
  * consonants between two vowels belong to the syllable that follows them,
    which is what singers do (`syllabify`).
  * a phoneme squeezed to zero length is worse than an absent one: the model
    still allocates it a frame and a one-frame stop reads as a click, so it is
    dropped (`fill_silences`).

Nothing here imports a voicebank. Every function takes a `voice` and asks it
only for `is_vowel`, `consonant_len`, `silence` and `entries`/`guess`, which is
what lets the built-in preview voice and a real DiffSinger bank run the same
code -- and what makes all of it testable against a ten-line fake.

    from phonemes import phonemize, fill_silences
    timeline, notes = phonemize(voice, phrase, clock, warn)   # [(ph, t0, t1)]
    timeline = fill_silences(voice, timeline)
"""

import re

from score_time import seconds

# Consonants are short and, crucially, land *before* the beat: a singer starts
# the "l" of "lantern" early so the vowel arrives on time.  These are seconds,
# by phoneme class, and they are borrowed from the end of the preceding note.
CONSONANT_S = {"stop": 0.055, "affricate": 0.090, "fricative": 0.090,
               "aspirate": 0.070, "nasal": 0.060, "liquid": 0.050,
               "semivowel": 0.050}
DEFAULT_CONSONANT_S = 0.065
# Below about a frame and a half the acoustic model has nothing to work with and
# the phoneme is heard as a click rather than a consonant.
MIN_PHONEME_S = 0.02

WORD_CLEAN = re.compile(r"[^A-Za-z'’-]+")


def lookup(voice, word):
    """The bank's dictionary, or letter-to-sound when previewing."""
    w = WORD_CLEAN.sub("", word).lower().replace("’", "'")
    if not w:
        return None
    for key in (w, w.replace("'", ""), w.rstrip("-")):
        if key in voice.entries:
            return list(voice.entries[key])
    return voice.guess(w) if hasattr(voice, "guess") else None


def syllabify(voice, phonemes, count):
    """Split a word's phonemes into `count` syllables, one vowel each.

    Maximal onset: consonants between two vowels belong to the syllable that
    follows them, which is what singers do -- "lan-tern" is sung [l a][n t er n]
    only because the notation says so; left alone, an English singer sings
    [l a n][t er n] and lands the t with the second note.
    """
    if count <= 1:
        return [list(phonemes)]
    vowels = [i for i, p in enumerate(phonemes) if voice.is_vowel(p)]
    if len(vowels) != count:
        # The dictionary and the hyphenation disagree.  Spreading the vowels we
        # have over the notes we have is wrong but audible-as-intended; failing
        # here would kill the whole render over one word.
        groups = [[] for _ in range(count)]
        for i, p in enumerate(phonemes):
            groups[min(i * count // max(len(phonemes), 1), count - 1)].append(p)
        return [g or [phonemes[-1]] for g in groups]

    groups, start = [], 0
    for n, v in enumerate(vowels):
        if n + 1 < len(vowels):
            nxt = vowels[n + 1]
            # leave one consonant as this syllable's coda only if there are two
            # or more between the vowels
            split = v + 1 if nxt - v <= 2 else nxt - 1
        else:
            split = len(phonemes)
        groups.append(list(phonemes[start:split]))
        start = split
    return groups


def sung_notes(notes):
    """Merge tied notes; keep melismata as their own notes on the same syllable."""
    out = []
    for n in notes:
        if n.get("tied") and out:
            out[-1]["dur"] += n["dur"]
            continue
        out.append(dict(n))
    return out


def predicted_lengths(voice, groups, clock, head=0.25, tail=0.35):
    """Ask `dsdur` how each syllable's time divides between its phonemes.

    The model is given the whole phrase as a sequence of words -- the syllables
    plus the silences between them -- because a consonant's length depends on
    what precedes it. `word_dur` is the time the *score* gives each syllable, so
    the model decides only the split inside it, never when the next syllable
    starts. That division of labour is what keeps the singer on the beat while
    still using the bank's own timing.

    Returns a list parallel to `groups`, each entry a list of seconds per
    phoneme, or None if the bank has no `dsdur` or the model declined.
    """
    model = getattr(voice, "predictors", None) and voice.predictors.duration
    if model is None:
        return None
    sil = voice.silence()
    words, index_of = [], {}
    cursor = None
    for gi, g in enumerate(groups):
        if not g["phones"]:
            continue
        start = seconds(g["notes"][0]["when"], clock)
        end = seconds(g["notes"][-1]["when"] + g["notes"][-1]["dur"], clock)
        gap = head if cursor is None else start - cursor
        if gap > 1e-3:
            words.append({"phones": [sil], "seconds": gap, "midi": 0})
        index_of[len(words)] = gi
        words.append({"phones": list(g["phones"]), "seconds": max(end - start, 1e-3),
                      "midi": int(g["notes"][0]["pitch"])})
        cursor = end
    if not words:
        return None
    words.append({"phones": [sil], "seconds": tail, "midi": 0})

    got = model.predict(words)
    if got is None:
        return None
    voice.predictors.used.add("dsdur")
    out = [None] * len(groups)
    for wi, gi in index_of.items():
        out[gi] = got[wi]
    return out


def phonemize(voice, phrase, clock, warn):
    """Turn one phrase into [(phoneme, start_s, end_s)], vowels on the beat."""
    notes = sung_notes(phrase)
    t0 = seconds(notes[0]["when"], clock)

    # Group notes by syllable: a syllable's note plus any melisma notes after it.
    groups, cur = [], None
    for n in notes:
        if n.get("syllable") and not n.get("melisma"):
            cur = {"syllable": n["syllable"], "word": n.get("word") or n["syllable"],
                   "syllabic": n.get("syllabic", "single"), "notes": [n]}
            groups.append(cur)
        elif cur is not None:
            cur["notes"].append(n)
        else:                                   # melisma with nothing before it
            cur = {"syllable": None, "word": None, "syllabic": "single", "notes": [n]}
            groups.append(cur)

    # Words: a run of groups whose syllabic marks say they belong together.
    words, wcur = [], []
    for g in groups:
        wcur.append(g)
        if g["syllabic"] in ("single", "end") or g["syllable"] is None:
            words.append(wcur)
            wcur = []
    if wcur:
        words.append(wcur)

    # Phonemes first, for every syllable in the phrase, because `dsdur` is
    # asked about the phrase as a whole rather than one syllable at a time.
    for word in words:
        text = word[0]["word"] or ""
        phon = lookup(voice, text) if text else None
        if text and phon is None:
            warn.add(text)
            phon = [voice.symbols and next(iter(voice.symbols)) or "a"]
        parts = syllabify(voice, phon or [], len(word)) if phon else [[] for _ in word]
        for group, phones in zip(word, parts):
            group["phones"] = list(phones)

    lengths = predicted_lengths(voice, groups, clock)

    timeline = []
    for gi, group in enumerate(groups):
        phones = group["phones"]
        # Either the singer's own model, or the constants it replaces.
        predicted = lengths[gi] if lengths else None
        length_of = ((lambda i, p: predicted[i]) if predicted
                     else (lambda i, p: voice.consonant_len(p)))
        start = seconds(group["notes"][0]["when"], clock)
        end = seconds(group["notes"][-1]["when"] + group["notes"][-1]["dur"], clock)
        vowel_at = next((i for i, p in enumerate(phones) if voice.is_vowel(p)), None)
        if vowel_at is None:
            onset, rest = list(enumerate(phones)), []
        else:
            onset = list(enumerate(phones))[:vowel_at]
            rest = list(enumerate(phones))[vowel_at:]

        # Onset consonants sit before the beat, taking time from whatever
        # precedes them -- the previous phoneme, or the head padding.
        lead = sum(length_of(i, p) for i, p in onset)
        room = start - (timeline[-1][1] if timeline else t0 - 0.5)
        scale = min(1.0, room / lead) if lead > 0 and room > 0 else (0 if lead else 1)
        cursor = start - lead * scale
        if timeline and cursor < timeline[-1][2]:
            timeline[-1] = (timeline[-1][0], timeline[-1][1], cursor)
        for i, p in onset:
            d = max(length_of(i, p) * scale, MIN_PHONEME_S)
            timeline.append((p, cursor, cursor + d))
            cursor += d

        # The vowel holds the note. A final consonant cluster is taken off
        # the end of the last note of the syllable.
        coda = [(i, p) for i, p in rest[1:] if not voice.is_vowel(p)]
        tail = sum(length_of(i, p) for i, p in coda)
        tail = min(tail, max(0.0, (end - start) * 0.4))
        vowel_end = end - tail
        if rest:
            timeline.append((rest[0][1], start, max(start + 0.02, vowel_end)))
            cursor = max(start + 0.02, vowel_end)
            share = tail / max(sum(length_of(i, p) for i, p in coda), 1e-9)
            for i, p in rest[1:]:
                d = length_of(i, p) * share if (i, p) in coda else 0.03
                d = max(d, MIN_PHONEME_S)
                timeline.append((p, cursor, cursor + d))
                cursor += d
        else:
            timeline.append((voice.silence(), start, end))

    return timeline, notes


def fill_silences(voice, timeline, head=0.25, tail=0.35):
    """Pad the phrase, plug gaps with silence, drop anything squeezed to nothing.

    Consonants are laid down backwards from the beat they precede, so a fast
    syllable can overrun the one before it and truncate its final consonant.
    A phoneme trimmed to zero length is worse than an absent one -- the model
    still allocates it a frame, and a one-frame stop reads as a click -- so a
    consonant with no room left is dropped rather than kept at zero.
    """
    sil = voice.silence()
    out = [(sil, timeline[0][1] - head, timeline[0][1])]
    for ph, a, b in timeline:
        a = max(a, out[-1][2])
        if b - a < MIN_PHONEME_S * 0.5:
            continue
        if a > out[-1][2] + 1e-6:
            out.append((sil, out[-1][2], a))
        out.append((ph, a, b))
    out.append((sil, out[-1][2], out[-1][2] + tail))
    return out
