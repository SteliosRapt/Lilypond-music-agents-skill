"""Where a score moment falls in seconds, and where a phrase ends.

Everything downstream of the extractor works in *moments* -- whole notes from
the start of the score, the unit `assets/lyrics.ily` reports and the unit
LilyPond's own MIDI is written in. Turning one into a time means walking a
tempo map, which is why this is a small module rather than a multiplication.

    from score_time import Clock, seconds, phrase_split
    clock = Clock(doc["tempo_map"], doc["tempo"])
    at = seconds(note["when"], clock)
"""


class Clock:
    """Score moments (in whole notes) to seconds, through a tempo map.

    A single tempo cannot describe a score that changes speed, and getting this
    wrong is invisible until it is glaring: with one number taken from the
    piece's *last* `\\tempo`, every note before the change is placed at the
    wrong time and the sung line drifts steadily away from the instruments.
    The map is the same one LilyPond writes into its MIDI, so the two agree by
    construction.

    Callable, so it can stand where a bare tempo used to: `clock(moment)`.
    """

    def __init__(self, tempo_map=None, tempo=None):
        pairs = [(float(w), float(q)) for w, q in (tempo_map or [])
                 if float(q) > 0]
        if not pairs:
            pairs = [(0.0, float(tempo or 60.0))]
        pairs.sort()
        if pairs[0][0] > 0:
            pairs.insert(0, (0.0, pairs[0][1]))
        # Precompute the elapsed seconds at each change, so a lookup is local.
        self.points, elapsed = [], 0.0
        for i, (when, qpm) in enumerate(pairs):
            if i:
                prev_when, prev_qpm, prev_secs = self.points[-1]
                elapsed = prev_secs + (when - prev_when) * 4.0 * 60.0 / prev_qpm
            self.points.append((when, qpm, elapsed))
        self.tempo = pairs[0][1]

    def __call__(self, moment):
        when, qpm, secs = self.points[0]
        for point in self.points:
            if point[0] <= moment:
                when, qpm, secs = point
            else:
                break
        return secs + (float(moment) - when) * 4.0 * 60.0 / qpm

    def __repr__(self):
        return ("Clock(" + ", ".join(f"{w:g}@{q:g}" for w, q, _s in self.points)
                + ")")


def seconds(whole, clock):
    """Whole notes to seconds, through a Clock (or a bare tempo in bpm)."""
    if callable(clock):
        return clock(whole)
    return whole * 4.0 * 60.0 / clock


def phrase_split(notes, clock, gap=0.6):
    """Break the line at rests long enough to breathe in."""
    phrases, current = [], []
    for n in notes:
        if current:
            prev = current[-1]
            rest = seconds(n["when"] - (prev["when"] + prev["dur"]), clock)
            if rest > gap:
                phrases.append(current)
                current = []
        current.append(n)
    if current:
        phrases.append(current)
    return phrases
