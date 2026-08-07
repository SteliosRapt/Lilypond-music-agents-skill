#!/usr/bin/env python3
"""A built-in voice for auditioning a sung line without a voicebank.

    python3 scripts/sing.py score.ly --preview -o out/

This is a formant synthesiser -- a source-filter model of a voice, of the kind
that predates neural synthesis by fifty years. It sounds like a choir of robots
and it is not trying to sound like anything else.

What it is for: everything about a sung line that is decided *before* the
neural model sees it. Whether the syllables land on the beats you meant, where
the melismata hold, whether the phrasing breathes in the right places, how the
portamento and vibrato read at this tempo. All of that is computed by the same
code that feeds the voicebank -- `phonemize()` and `f0_curve()` in sing.py --
so a mistake audible here is a mistake that would survive into the real render,
and it is audible in three seconds instead of three minutes.

What it is not for: judging the voice. Timbre, diction, and the whole question
of whether the words are intelligible belong to the voicebank.

Words are phonemised by letter-to-sound rules rather than a dictionary, since
there is no bank to supply one. English spelling being what it is, expect the
preview to mispronounce roughly the words you would expect it to mispronounce.
"""

import numpy as np

SR = 44100

# Formant frequencies (F1, F2, F3) in Hz. Diphthongs carry a second target and
# glide towards it across the note, which is most of what makes them audible as
# diphthongs rather than as two vowels.
VOWELS = {
    "iy": (270, 2290, 3010), "ih": (390, 1990, 2550), "eh": (530, 1840, 2480),
    "ae": (660, 1720, 2410), "aa": (730, 1090, 2440), "ao": (570, 840, 2410),
    "uh": (440, 1020, 2240), "uw": (300, 870, 2240), "ah": (640, 1190, 2390),
    "er": (490, 1350, 1690),
}
DIPHTHONGS = {
    "ey": ("eh", "iy"), "ay": ("aa", "ih"), "oy": ("ao", "ih"),
    "ow": ("ao", "uw"), "aw": ("aa", "uh"),
}
# Voiced consonants that behave like vowels with damped, extreme formants.
SONORANTS = {
    "l": (360, 1300, 2700), "r": (490, 1350, 1690), "w": (300, 610, 2200),
    "y": (270, 2290, 3010), "m": (250, 1100, 2200), "n": (250, 1700, 2600),
    "ng": (250, 2000, 2800),
}
# Noise band (centre Hz, bandwidth Hz, loudness, voiced?) for the rest.
FRICATIVES = {
    "s": (6200, 2500, 0.30, False), "z": (5200, 2500, 0.18, True),
    "sh": (3000, 1800, 0.34, False), "zh": (2900, 1800, 0.20, True),
    "f": (4200, 3000, 0.16, False), "v": (3600, 2500, 0.12, True),
    "th": (5200, 3000, 0.13, False), "dh": (3200, 2500, 0.11, True),
    "hh": (1500, 2500, 0.14, False),
    "ch": (3000, 1800, 0.36, False), "jh": (2800, 1800, 0.24, True),
}
STOPS = {"p": (1200, 0.22, False), "t": (3800, 0.26, False), "k": (2200, 0.24, False),
         "b": (700, 0.16, True), "d": (2600, 0.18, True), "g": (1600, 0.16, True)}

SYMBOL_TYPES = (
    {v: "vowel" for v in list(VOWELS) + list(DIPHTHONGS)}
    | {"l": "liquid", "r": "liquid", "w": "semivowel", "y": "semivowel"}
    | {"m": "nasal", "n": "nasal", "ng": "nasal"}
    | {k: ("affricate" if k in ("ch", "jh") else "aspirate" if k == "hh" else "fricative")
       for k in FRICATIVES}
    | {k: "stop" for k in STOPS}
    | {"SP": "vowel", "AP": "vowel"}
)

# ----------------------------------------------------------------- spelling

DIGRAPH_VOWELS = {
    "ee": "iy", "ea": "iy", "ie": "iy", "ei": "ey", "ai": "ey", "ay": "ey",
    "oo": "uw", "ou": "aw", "ow": "aw", "oi": "oy", "oy": "oy", "oa": "ow",
    "au": "ao", "aw": "ao", "ew": "uw", "ue": "uw", "er": "er", "ir": "er",
    "ur": "er", "ar": "aa", "or": "ao",
}
SINGLE_VOWELS = {"a": "ae", "e": "eh", "i": "ih", "o": "aa", "u": "ah", "y": "ih"}
# Keyed by the short phoneme rather than by the letter it came from. Keying it
# by letter meant inverting SINGLE_VOWELS to get back there, and that map is not
# injective -- "y" also spells "ih" and overwrote "i" -- so "time" and "shine"
# came out with a short i and the rule never fired for that vowel at all.
MAGIC_E = {"ae": "ey", "eh": "iy", "ih": "ay", "aa": "ow", "ah": "uw"}
DIGRAPH_CONSONANTS = {"ch": "ch", "sh": "sh", "th": "th", "ph": "f", "wh": "w",
                      "ck": "k", "ng": "ng", "qu": "k", "gh": "g"}
SINGLE_CONSONANTS = {"b": "b", "c": "k", "d": "d", "f": "f", "g": "g", "h": "hh",
                     "j": "jh", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p",
                     "q": "k", "r": "r", "s": "s", "t": "t", "v": "v", "w": "w",
                     "x": "k", "y": "y", "z": "z"}
VOWEL_LETTERS = set("aeiouy")


def letters_to_phonemes(word):
    """Rough English letter-to-sound. Good enough to hear the shape of a line."""
    w = "".join(c for c in word.lower() if c.isalpha())
    if not w:
        return []
    silent_e = len(w) > 2 and w.endswith("e") and w[-2] not in VOWEL_LETTERS
    body = w[:-1] if silent_e else w

    out, i = [], 0
    while i < len(body):
        pair = body[i:i + 2]
        if pair in DIGRAPH_VOWELS:
            out.append(DIGRAPH_VOWELS[pair])
            i += 2
            continue
        if pair in DIGRAPH_CONSONANTS:
            out.append(DIGRAPH_CONSONANTS[pair])
            if pair == "qu":
                out.append("w")
            i += 2
            continue
        c = body[i]
        if c in VOWEL_LETTERS:
            # y is a consonant only at the head of a word
            if c == "y" and i == 0 and len(body) > 1:
                out.append("y")
            else:
                out.append(SINGLE_VOWELS[c])
        elif c in SINGLE_CONSONANTS:
            # c and g soften before e, i, y; s between vowels voices
            nxt = body[i + 1] if i + 1 < len(body) else ""
            if c == "c" and nxt in "eiy":
                out.append("s")
            elif c == "g" and nxt in "eiy":
                out.append("jh")
            elif c == "x":
                out += ["k", "s"]
            else:
                out.append(SINGLE_CONSONANTS[c])
        i += 1

    if silent_e:
        for j in range(len(out) - 1, -1, -1):
            if out[j] in MAGIC_E:
                out[j] = MAGIC_E[out[j]]
                break
    # collapse doubled consonants: "blossom" is not [s][s]
    dedup = [p for k, p in enumerate(out) if k == 0 or p != out[k - 1] or p in VOWELS]
    return dedup or ["ah"]


# ------------------------------------------------------------------ synthesis

def _resonance(freq, centre, bandwidth):
    """Magnitude of a two-pole resonator, evaluated at arbitrary frequencies."""
    x = (freq * freq - centre * centre) / np.maximum(freq * bandwidth, 1.0)
    return 1.0 / np.sqrt(1.0 + x * x)


def _band_noise(n, centre, bandwidth, rng):
    """Band-limited noise: smooth white noise, then shift the band up to centre."""
    width = max(2, int(SR / max(bandwidth, 50.0)))
    white = rng.standard_normal(n + width)
    box = np.convolve(white, np.ones(width) / width, mode="same")[:n]
    t = np.arange(n) / SR
    return box * np.cos(2 * np.pi * centre * t + rng.uniform(0, 6.28)) * width ** 0.5


def _envelope(n, attack, release):
    e = np.ones(n)
    a, r = min(int(attack * SR), n // 2), min(int(release * SR), n // 2)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] = np.linspace(1, 0, r)
    return e


def _voiced(phase, f0, formants, harmonics=48):
    """Additive glottal source through a three-formant filter, sample by sample.

    Additive rather than an actual IIR filter because the formants move
    continuously and numpy has no cheap time-varying filter; evaluating the
    resonator's magnitude at each harmonic is the same thing, computed forwards.
    """
    out = np.zeros(len(phase))
    f1, f2, f3 = formants
    for k in range(1, harmonics + 1):
        freq = k * f0
        live = freq < SR / 2 - 500
        if not live.any():
            break
        gain = (_resonance(freq, f1, 80) +
                0.55 * _resonance(freq, f2, 110) +
                0.25 * _resonance(freq, f3, 160))
        out += np.where(live, gain / k ** 1.1 * np.sin(k * phase), 0.0)
    return out


def render(timeline, f0_frames, frame_s, seed=0):
    """Render one phrase: [(phoneme, start_s, end_s)] plus its f0, to samples."""
    rng = np.random.default_rng(seed)
    t0 = timeline[0][1]
    total = int(round((timeline[-1][2] - t0) * SR)) + 1
    audio = np.zeros(total)

    # f0 is one value per acoustic frame; the synthesiser wants one per sample.
    src = np.arange(len(f0_frames)) * frame_s
    f0 = np.interp(np.arange(total) / SR, src, f0_frames)
    phase = np.cumsum(2 * np.pi * f0 / SR)

    for ph, a, b in timeline:
        i, j = int(round((a - t0) * SR)), int(round((b - t0) * SR))
        j = min(max(j, i + 64), total)
        n = j - i
        if n < 32:
            continue
        seg, ph = np.zeros(n), ph.lower()

        if ph in ("sp", "ap", "sil", "pau"):
            if ph == "ap":                        # a breath, not just silence
                seg = _band_noise(n, 900, 1600, rng) * 0.05 * _envelope(n, 0.04, 0.06)
        elif ph in VOWELS or ph in DIPHTHONGS or ph in SONORANTS:
            if ph in DIPHTHONGS:
                start, end = (np.array(VOWELS[x], float) for x in DIPHTHONGS[ph])
                ramp = np.clip((np.linspace(0, 1, n) - 0.45) / 0.45, 0, 1)
                formants = [s + (e - s) * ramp for s, e in zip(start, end)]
            else:
                formants = [np.full(n, f, float)
                            for f in (VOWELS.get(ph) or SONORANTS[ph])]
            level = 0.30 if ph in SONORANTS else 0.42
            seg = _voiced(phase[i:j], f0[i:j], formants) * level
            seg *= _envelope(n, 0.012, 0.015)
        elif ph in FRICATIVES:
            centre, bw, level, voiced = FRICATIVES[ph]
            seg = _band_noise(n, centre, bw, rng) * level
            if voiced:
                seg += _voiced(phase[i:j], f0[i:j],
                               [np.full(n, f, float) for f in (300, 1100, 2400)]) * 0.10
            seg *= _envelope(n, 0.008, 0.012)
        elif ph in STOPS:
            centre, level, voiced = STOPS[ph]
            burst = max(8, int(0.012 * SR))
            hold = max(0, n - burst)
            seg[hold:] = (_band_noise(n - hold, centre, 2500, rng) * level *
                          np.exp(-np.linspace(0, 6, n - hold)))
            if voiced and hold:                    # voice bar during the closure
                seg[:hold] += _voiced(phase[i:i + hold], f0[i:i + hold],
                                      [np.full(hold, f, float)
                                       for f in (220, 900, 2200)]) * 0.06
        audio[i:j] += seg

    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak * 0.85).astype(np.float32)
