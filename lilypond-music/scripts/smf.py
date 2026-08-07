"""Standard MIDI File primitives, shared by the three modules that read one.

`midi_timing`, `midi_split` and `midi_expression` all open the same file format
and none of them wants the same thing out of it: one builds a tempo map, one
copies tracks apart byte for byte, one rewrites events. They share a *format*,
not a purpose, so what lives here is only the layer below all three -- variable
length quantities and the chunk list -- and each module keeps its own event
walk.

That division is the point. `_read_varlen` used to be copied byte-identically
into all three, and three copies is where a fix lands twice and misses once.
Merging the event walks as well would be the opposite mistake: `_scan_track`
summarises a part, `_parse_track` explodes one for rewriting, and `parse_midi`
collects meta events, and a single function doing all three would be worse than
any of them.

Pure standard library, deliberately -- no mido, no pretty_midi. Those re-encode
events on the way out, and `midi_split` depends on copying a track's bytes
verbatim so that running status, channel assignments and the channel-10 drum
mapping survive untouched.

    import smf
    fmt, ntracks, division = smf.header(data)
    for kind, start, end, blob in smf.chunks(data):
        ...
"""

import struct

MTHD = b"MThd"
MTRK = b"MTrk"
HEADER_BYTES = 14          # "MThd" + a 4-byte length of 6 + the 6-byte body
CHUNK_HEADER_BYTES = 8     # a 4-byte kind + a 4-byte body length


def read_varlen(data, i):
    """Read a variable-length quantity at `i`. Returns (value, index after it).

    Seven bits per byte, high bit set on every byte but the last.
    """
    val = 0
    while True:
        b = data[i]
        i += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, i


def write_varlen(value):
    """The inverse of `read_varlen`: an integer to its variable-length bytes."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def header(data, source="this file"):
    """Validate the MThd chunk. Returns (format, track count, division).

    SMPTE time division is rejected here rather than in one caller, because
    every reader in the skill assumes `division` is ticks per quarter note: a
    tempo map derived from an SMPTE file is wrong, and a split of one is wrong
    in a way that only shows up as audio at the wrong speed. LilyPond does not
    write SMPTE, so this is a guard against a hand-made file, not a limitation
    anyone here meets.
    """
    if data[:4] != MTHD:
        raise ValueError(f"{source} is not a Standard MIDI File")
    fmt, ntracks, division = struct.unpack(">HHH", data[8:HEADER_BYTES])
    if division & 0x8000:
        raise ValueError(f"{source} uses SMPTE time division, "
                         "which is not supported")
    return fmt, ntracks, division


def write_header(fmt, ntracks, division):
    """An MThd chunk, byte for byte as `header` would read it back."""
    return MTHD + struct.pack(">IHHH", 6, fmt, ntracks, division)


def chunks(data):
    """Every chunk after the header, as (kind, body start, body end, blob).

    `blob` is the chunk complete with its own 8-byte header, which is what
    `midi_split` writes out verbatim; `start` and `end` bracket the body alone,
    which is what an event walk wants. A chunk whose stated length runs past
    the end of the buffer ends the walk rather than yielding a truncated body.
    """
    out, pos = [], HEADER_BYTES
    while pos + CHUNK_HEADER_BYTES <= len(data):
        kind = data[pos:pos + 4]
        length = struct.unpack(">I", data[pos + 4:pos + CHUNK_HEADER_BYTES])[0]
        start = pos + CHUNK_HEADER_BYTES
        end = start + length
        if end > len(data):
            break
        out.append((kind, start, end, data[pos:end]))
        pos = end
    return out


def tracks(data):
    """Just the MTrk chunks, in file order."""
    return [c for c in chunks(data) if c[0] == MTRK]
