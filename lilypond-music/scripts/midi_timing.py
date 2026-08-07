"""Derive a bar-by-bar timeline (in seconds) from a Standard MIDI File.

Why this exists: any video that follows a score needs to know when each bar
starts.  Hand-maintaining a tempo map is the single biggest source of drift --
you change `\tempo 4 = 66` in the score, forget to update the map, and the
playhead silently desynchronises.  LilyPond already writes tempo and time
signature meta events into its MIDI output, so read the timeline back from the
artefact instead of restating it.

Pure standard library -- no mido/pretty_midi dependency. The chunk list and the
variable-length quantities come from `smf.py`, shared with the other two modules
that read a MIDI file; the event walk below is this module's own, because a
tempo map is not what either of them is looking for.

    from midi_timing import bar_timeline
    bars = bar_timeline("score.midi")      # [(bar_number, start_sec, dur_sec), ...]
"""

from pathlib import Path

import smf


def parse_midi(path):
    """Return (division, tempo_events, timesig_events, end_tick).

    tempo_events   : [(tick, microseconds_per_quarter), ...] sorted
    timesig_events : [(tick, numerator, denominator), ...] sorted
    end_tick       : tick of the last event in the file
    """
    data = Path(path).read_bytes()
    _fmt, _ntrks, division = smf.header(data, str(path))

    tempos, timesigs, end_tick = [], [], 0
    for _kind, body_start, body_end, _blob in smf.tracks(data):
        i, tick, status = body_start, 0, None
        while i < body_end:
            delta, i = smf.read_varlen(data, i)
            tick += delta
            b = data[i]
            if b & 0x80:
                status = b
                i += 1
            # else: running status, reuse previous `status`

            if status == 0xFF:                      # meta event
                meta_type = data[i]
                i += 1
                length_, i = smf.read_varlen(data, i)
                payload = data[i:i + length_]
                i += length_
                if meta_type == 0x51 and length_ == 3:
                    tempos.append((tick, (payload[0] << 16) | (payload[1] << 8) | payload[2]))
                elif meta_type == 0x58 and length_ >= 2:
                    timesigs.append((tick, payload[0], 2 ** payload[1]))
            elif status in (0xF0, 0xF7):            # sysex
                length_, i = smf.read_varlen(data, i)
                i += length_
            else:                                   # channel voice message
                high = status & 0xF0
                i += 1 if high in (0xC0, 0xD0) else 2
            end_tick = max(end_tick, tick)

    tempos.sort()
    timesigs.sort()
    if not tempos:
        tempos = [(0, 500000)]                      # MIDI default = 120 bpm
    if not timesigs:
        timesigs = [(0, 4, 4)]
    if tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))
    if timesigs[0][0] > 0:
        timesigs.insert(0, (0, 4, 4))
    return division, tempos, timesigs, end_tick


def tick_to_seconds(division, tempos):
    """Return a function mapping absolute tick -> seconds, honouring tempo changes."""
    anchors = []                                    # (tick, seconds_at_tick, us_per_quarter)
    secs = 0.0
    for idx, (tick, upq) in enumerate(tempos):
        if idx:
            prev_tick, prev_secs, prev_upq = anchors[-1]
            secs = prev_secs + (tick - prev_tick) * prev_upq / division / 1e6
        anchors.append((tick, secs, upq))

    def convert(tick):
        lo = anchors[0]
        for a in anchors:
            if a[0] <= tick:
                lo = a
            else:
                break
        return lo[1] + (tick - lo[0]) * lo[2] / division / 1e6

    return convert


def moment_converter(midi_path):
    """Return a function mapping a LilyPond moment (in whole notes) to seconds.

    LilyPond's global moments and its MIDI output share an origin -- moment 0 is
    tick 0, including when the score opens with a `\\partial` pickup -- so a
    moment converts to a tick with nothing but the file's division, and to
    seconds through the same tempo map the bar timeline uses.  This is what lets
    note-level playhead anchors be placed without matching printed note heads
    against MIDI note-on events, which ties, rests, grace notes and unfolded
    repeats all make unreliable.
    """
    division, tempos, _timesigs, _end = parse_midi(midi_path)
    to_sec = tick_to_seconds(division, tempos)
    return lambda moment: to_sec(float(moment) * 4 * division)


def bar_timeline(midi_path, pickup_quarters=0.0):
    """Return [(bar_number, start_seconds, duration_seconds), ...].

    `pickup_quarters` handles an anacrusis written with \\partial: pass the
    length of the pickup in quarter notes (e.g. 1 for `\\partial 4`, 0.5 for
    `\\partial 8`) so bar 1 is short and everything after it lines up.
    """
    division, tempos, timesigs, end_tick = parse_midi(midi_path)
    to_sec = tick_to_seconds(division, tempos)

    def ts_at(tick):
        cur = timesigs[0]
        for t in timesigs:
            if t[0] <= tick:
                cur = t
            else:
                break
        return cur[1], cur[2]

    starts, tick = [], 0
    if pickup_quarters:
        starts.append(0)
        tick = int(round(pickup_quarters * division))
    guard = 0
    while tick < end_tick and guard < 100000:
        starts.append(tick)
        num, den = ts_at(tick)
        tick += int(round(division * 4 * num / den))
        guard += 1

    out = []
    for n, s in enumerate(starts, start=1):
        nxt = starts[n] if n < len(starts) else end_tick
        out.append((n, to_sec(s), to_sec(nxt) - to_sec(s)))
    return out


if __name__ == "__main__":
    import sys
    bars = bar_timeline(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.0)
    for n, start, dur in bars:
        print(f"bar {n:3d}  start {start:8.3f}s  dur {dur:6.3f}s")
    print(f"total {len(bars)} bars, music ends at {bars[-1][1] + bars[-1][2]:.3f}s")
