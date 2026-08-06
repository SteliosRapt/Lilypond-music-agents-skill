"""Perform hairpins that velocity cannot, by writing CC11 into the MIDI.

LilyPond performs a crescendo by raising the velocity of each successive note.
That works whenever the hairpin covers several notes, and fails completely
whenever it covers one long one: velocity is latched when a note begins and
cannot move while it sounds, so `<d a>1\\>` is printed and silent. Drones, pads,
sustained choir parts and tied notes are where written dynamics quietly vanish.

Expression (controller 11) is the missing dimension -- a continuous scalar on
the sounding note. This module reads the hairpin spans reported by
`assets/hairpins.ily` and writes an expression ramp across every long note
inside one.

The shape matters as much as the presence, and the obvious shape is wrong.
Resetting expression to unity at every note onset -- on the theory that velocity
carries the overall trend and expression only fills the gaps -- turns a
diminuendo over two whole notes into a sawtooth: fade down, jump back to full,
fade down again. It sounds like a note dropping out and returning, not like a
diminuendo.

So the ramp is continuous across the whole hairpin and ignores note boundaries:

    hairpin start  ->  unity at the loud end, `1 - depth` at the quiet end
    across it      ->  one uninterrupted ramp, whatever notes come and go
    hairpin end    ->  back to unity

That does compound with the velocity ramp LilyPond already applied, which is
exactly why it is not applied everywhere. A hairpin whose notes are short enough
for velocity to shape is left alone entirely; expression is spent only where
velocity cannot reach, on hairpins dominated by one long note.

    from midi_expression import parse_hairpins, add_expression
    staves, spans = parse_hairpins(lilypond_stderr)
    add_expression("score.midi", "expressive.midi", spans_by_track)
"""

import re
import struct
from fractions import Fraction

STAFF_RE = re.compile(r"@STAFF (\d+)")
HP_RE = re.compile(r"@HP (\d+) (-?\d+) (-?\d+(?:/\d+)?) (-?\d+(?:/\d+)?) (-?\d+)")

EXPRESSION_CC = 11
FULL = 127


def parse_hairpins(text):
    """Parse the ily's output into (staff count, hairpins).

    A hairpin's staff is already its index in context creation order, which is
    the order LilyPond writes MIDI tracks in, so nothing needs reordering here.
    The staff count is returned so the caller can refuse to guess when it does
    not match the number of sounding parts.

    Pieces of a hairpin broken across a line break share an id and are merged
    back into one span.
    """
    staves = {int(m.group(1)) for m in STAFF_RE.finditer(text)}

    merged = {}
    for m in HP_RE.finditer(text):
        hid, staff = int(m.group(1)), int(m.group(2))
        start, end = Fraction(m.group(3)), Fraction(m.group(4))
        direction = int(m.group(5))
        if staff < 0:
            continue
        if hid in merged:
            merged[hid]["start"] = min(merged[hid]["start"], start)
            merged[hid]["end"] = max(merged[hid]["end"], end)
        else:
            merged[hid] = {"staff": staff, "start": start, "end": end,
                           "direction": direction}
    return len(staves), [merged[k] for k in sorted(merged)]


def _read_varlen(data, i):
    val = 0
    while True:
        b = data[i]
        i += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, i


def _write_varlen(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _parse_track(data, start, end):
    """Explode one MTrk body into absolute-time events plus its notes.

    Running status is expanded on the way in, so every event carries a status
    byte and can be reordered and re-emitted freely.
    """
    events, notes, sounding = [], [], {}
    i, tick, status = start, 0, None
    channel = None
    while i < end:
        delta, i = _read_varlen(data, i)
        tick += delta
        b = data[i]
        if b & 0x80:
            status = b
            i += 1
        begin = i
        if status == 0xFF:
            meta = data[i]
            i += 1
            length, i = _read_varlen(data, i)
            i += length
            if meta == 0x2F:                     # end of track, re-added on write
                continue
            events.append((tick, 0, bytes([status]) + data[begin:i]))
        elif status in (0xF0, 0xF7):
            length, i = _read_varlen(data, i)
            i += length
            events.append((tick, 0, bytes([status]) + data[begin:i]))
        else:
            high = status & 0xF0
            if channel is None:
                channel = status & 0x0F
            i += 1 if high in (0xC0, 0xD0) else 2
            events.append((tick, 0, bytes([status]) + data[begin:i]))
            pitch, velocity = data[begin], data[begin + 1] if i - begin > 1 else 0
            if high == 0x90 and velocity > 0:
                sounding[pitch] = tick
            elif high == 0x80 or (high == 0x90 and velocity == 0):
                began = sounding.pop(pitch, None)
                if began is not None:
                    notes.append((began, tick))
    return events, notes, channel


def _write_track(events):
    body, last = bytearray(), 0
    for tick, _order, raw in events:
        body += _write_varlen(tick - last)
        body += raw
        last = tick
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _ramp(channel, t0, t1, v0, v1, step):
    """CC11 events interpolating v0 -> v1 across [t0, t1], endpoint included."""
    out = []
    span = max(t1 - t0, 1)
    tick = t0
    while tick < t1:
        frac = (tick - t0) / span
        out.append((tick, -1, _cc(channel, v0 + (v1 - v0) * frac)))
        tick += step
    out.append((t1, -1, _cc(channel, v1)))
    return out


def _cc(channel, value):
    return bytes([0xB0 | channel, EXPRESSION_CC,
                  max(0, min(FULL, int(round(value))))])


def add_expression(midi_path, out_path, spans_by_track, depth=0.4,
                   min_note_quarters=2.0, coverage=0.4, glide_quarters=1.0,
                   steps_per_quarter=8):
    """Write a copy of the MIDI with expression ramps under held hairpin notes.

    `spans_by_track` maps a track's chunk index to its hairpins, each a dict of
    start/end (in whole notes) and direction (1 crescendo, -1 diminuendo).
    `depth` is how far expression travels from end to end of a hairpin, and it
    is not linear in loudness: synthesisers apply CC11 on roughly a
    `40*log10(cc/127)` curve, so depth 0.4 is about 9 dB and depth 0.6 is about
    16 dB. 16 dB is too much -- the quiet end of a crescendo becomes inaudible,
    which sounds like a missing note rather than a swell. A hairpin is shaped only when
    one note covers at least `coverage` of its span and lasts at least
    `min_note_quarters` -- otherwise there are enough note onsets in it for
    LilyPond's velocity ramp to do the job, and adding expression on top would
    simply exaggerate a swell that already works.

    Returns a list of the notes actually shaped, for reporting.
    """
    data = open(midi_path, "rb").read()
    fmt, ntrks, division = struct.unpack(">HHH", data[8:14])

    chunks, pos = [], 14
    while pos < len(data) - 8:
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        chunks.append((data[pos:pos + 4], pos + 8, pos + 8 + length, data[pos:pos + 8 + length]))
        pos += 8 + length

    step = max(int(division / steps_per_quarter), 1)
    min_note = min_note_quarters * division
    glide = max(int(round(glide_quarters * division)), step)
    out, shaped = bytearray(data[:14]), []

    for index, (kind, start, end, blob) in enumerate(chunks):
        spans = spans_by_track.get(index)
        if kind != b"MTrk" or not spans:
            out += blob
            continue

        events, notes, channel = _parse_track(data, start, end)
        if channel is None:
            out += blob
            continue

        added = []
        for span in spans:
            t0 = int(round(float(span["start"]) * 4 * division))
            t1 = int(round(float(span["end"]) * 4 * division))
            if t1 <= t0:
                continue
            # Overlap, not containment: a hairpin frequently begins part-way
            # through a note tied over from an earlier bar, which is precisely
            # the case velocity handles worst.
            inside = [(max(ns, t0), min(ne, t1)) for ns, ne in set(notes)
                      if ne > t0 and ns < t1]
            longest = max((b - a for a, b in inside), default=0)
            if longest < min_note or longest / (t1 - t0) < coverage:
                continue          # velocity already shapes this one; leave it
            # A ramp that simply *begins* at its quiet end puts the whole depth
            # into one tick: the channel drops ~9 dB instantly, chopping the
            # release of whatever was still ringing.  It is plainly audible as a
            # bump, and it is the same defect at both ends -- a crescendo jumps
            # down as it starts, a diminuendo jumps back up as it finishes.  So
            # each hairpin is eased into or out of unity over `glide`, on
            # whichever side is not already at unity.
            quiet = FULL * (1.0 - depth)
            if span["direction"] > 0:
                added += _ramp(channel, max(t0 - glide, 0), t0, FULL, quiet, step)
                added += _ramp(channel, t0, t1, quiet, FULL, step)
            else:
                added += _ramp(channel, t0, t1, FULL, quiet, step)
                added += _ramp(channel, t1, t1 + glide, quiet, FULL, step)
            shaped.append({"track": index, "start": t0 / division,
                           "quarters": (t1 - t0) / division,
                           "direction": span["direction"],
                           "to": quiet / FULL})

        if not added:
            out += blob
            continue
        events = sorted(events + added, key=lambda e: (e[0], e[1]))
        out += _write_track(events)

    open(out_path, "wb").write(bytes(out))
    return shaped
