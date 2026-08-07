"""Split a LilyPond MIDI file into one file per part, for independent mixing.

fluidsynth renders a whole MIDI file to one stereo wav, which leaves the balance
between parts entirely to two things you do not control: the note velocities
LilyPond derived from the written dynamics, and how loud the soundfont's samples
happen to be. A `\\mp` koto and a `\\mp` shakuhachi are the same velocity and
nowhere near the same loudness, so a score that is correctly notated can still
come out as a wall of koto.

Splitting first gives a real mixing desk: each part becomes its own wav, and
gain, pan and EQ then apply per part in ffmpeg, without touching the score.

Splitting is a byte-level operation. A format-1 file is a header plus a sequence
of `MTrk` chunks, of which the first carries the tempo map and the rest carry
one part each (LilyPond writes one track per staff). A single part is therefore
the original header with the track count set to two, followed by the tempo chunk
and that part's chunk, copied verbatim -- no event re-encoding, so running
status, channel assignments and the channel-10 drum mapping all survive intact.

    from midi_split import split_tracks
    parts = split_tracks("score.midi", "/tmp/parts")
    # [{'index': 1, 'name': 'Koto', 'program': 107, 'channel': 3,
    #   'notes': 201, 'path': '/tmp/parts/part01.midi'}, ...]
"""

import os
import re

import smf

HERE = os.path.dirname(os.path.abspath(__file__))
GM_REFERENCE = os.path.join(HERE, "..", "references", "gm-instruments.md")


def gm_names():
    """program number -> GM instrument name, read from the skill's own reference.

    Kept as a lookup for display only; an empty dict just means parts are listed
    by program number.
    """
    try:
        with open(GM_REFERENCE, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    return {int(num) - 1: name for name, num in re.findall(r"`([^`]+)` \((\d+)\)", text)}


def _scan_track(data, start, end):
    """Summarise one MTrk body.

    Returns (name, program, channel, note count, velocities, longest note in
    ticks, every note's length in ticks).  The last three are what tell you
    whether the written dynamics survived the trip: a part with one distinct
    velocity got no shaping at all, and a part that spends most of its time
    inside long held notes cannot be shaped by velocity, because velocity is
    fixed at note-on.
    """
    i, status = start, None
    name, program, channel, notes = None, None, None, 0
    velocities, longest, sounding, tick = [], 0, {}, 0
    durations = []
    while i < end:
        delta, i = smf.read_varlen(data, i)
        tick += delta
        b = data[i]
        if b & 0x80:
            status = b
            i += 1
        if status == 0xFF:
            meta = data[i]
            i += 1
            length, i = smf.read_varlen(data, i)
            if meta == 0x03 and name is None:
                name = data[i:i + length].decode("latin1")
            i += length
        elif status in (0xF0, 0xF7):
            length, i = smf.read_varlen(data, i)
            i += length
        else:
            high = status & 0xF0
            if channel is None:
                channel = status & 0x0F
            if high == 0xC0:
                if program is None:
                    program = data[i]
                i += 1
            elif high == 0xD0:
                i += 1
            else:
                pitch, vel = data[i], data[i + 1]
                if high == 0x90 and vel > 0:
                    notes += 1
                    velocities.append(vel)
                    sounding[pitch] = tick
                elif high == 0x80 or (high == 0x90 and vel == 0):
                    began = sounding.pop(pitch, None)
                    if began is not None:
                        durations.append(tick - began)
                        longest = max(longest, tick - began)
                i += 2
    return name, program, channel, notes, velocities, longest, durations


def split_tracks(midi_path, outdir):
    """Write one single-part MIDI per sounding track. Returns a list of parts.

    Tracks with no notes -- the conductor track, and any `Devnull` context used
    to carry layout-only material such as `\\break`s -- are skipped rather than
    rendered to silence.
    """
    with open(midi_path, "rb") as fh:
        data = fh.read()
    _fmt, _ntracks, division = smf.header(data, str(midi_path))

    chunks = smf.tracks(data)
    if not chunks:
        raise ValueError(f"{midi_path} contains no tracks")

    os.makedirs(outdir, exist_ok=True)
    conductor = chunks[0][3]
    names = gm_names()
    parts = []
    for n, (_kind, start, end, blob) in enumerate(chunks):
        name, program, channel, notes, velocities, longest, durations = _scan_track(
            data, start, end)
        if not notes:
            continue
        path = os.path.join(outdir, f"part{n:02d}.midi")
        with open(path, "wb") as fh:
            # Format 1 with two tracks: the tempo map, then this part. Anything
            # else here changes what fluidsynth renders.
            fh.write(smf.write_header(1, 2, division))
            fh.write(conductor)
            fh.write(blob)
        label = (name or "").strip(": ") or None
        if label is None and channel == 9:
            label = "drums"
        if label is None:
            label = names.get(program, f"program {program}")
        parts.append({"index": len(parts) + 1, "track": n, "name": label,
                      "program": program, "channel": channel,
                      "notes": notes, "path": path,
                      "velocities": velocities,
                      "longest_quarters": longest / division,
                      "durations_quarters": [d / division for d in durations]})
    return parts


def describe(parts, held_threshold=4.0):
    """A table of the parts, and what the written dynamics turned into.

    `--list-tracks` is the command that answers "why does this sound wrong",
    so it reports more than names: the velocity spread each part received, and
    the longest note in it.  Those two numbers between them explain most bad
    renderings.  A part with a single velocity was written without dynamics, or
    with dynamics LilyPond could not perform.  A part whose longest note runs a
    whole bar or more cannot be shaped by velocity across that note at all --
    velocity is set once, at note-on -- so a hairpin drawn over it is printed
    and silent.  See references/audio-and-midi.md section 4 for the fix.
    """
    names = gm_names()
    lines = ["  #  part                    notes  vels  velocity  longest  held  sound",
             "  -- ----------------------  -----  ----  --------  -------  ----  -----"]
    flat, held = [], []
    for p in parts:
        sound = ("drum kit" if p["channel"] == 9 else
                 names.get(p["program"], f"program {p['program']}"))
        vels = p.get("velocities") or []
        distinct = len(set(vels))
        span = f"{min(vels)}-{max(vels)}" if vels else "-"
        longest = p.get("longest_quarters", 0.0)
        # What fraction of this part's sounding time is inside notes too long to
        # be shaped by velocity.  The bare longest note is a poor signal -- one
        # closing whole note flags a part that is otherwise all semiquavers.
        durs = p.get("durations_quarters") or []
        total = sum(durs)
        share = sum(d for d in durs if d >= held_threshold) / total if total else 0.0
        lines.append(f"  {p['index']:<2} {p['name'][:22]:<22} {p['notes']:>6} "
                     f"{distinct:>5}  {span:>8}  {longest:>5.1f}q  {share:>4.0%}  {sound}")
        if distinct <= 1 and p["notes"] > 1:
            flat.append(p["name"])
        if share >= 0.5:
            held.append(p["name"])

    if flat:
        lines.append("")
        lines.append(f"  one velocity throughout: {', '.join(flat)}")
        lines.append("  -> no dynamics reached these parts; they will render flat.")
    if held:
        lines.append("")
        lines.append(f"  mostly sustained: {', '.join(held)}")
        lines.append("  -> over half of these parts' sounding time is inside notes of")
        lines.append(f"     {held_threshold:.0f} quarters or more. Velocity cannot change")
        lines.append("     while a note sounds, so hairpins across them cannot be")
        lines.append("     performed as velocity.")
        lines.append("     render.py rewrites those as CC11 expression ramps automatically;")
        lines.append("     --no-swell disables it (audio-and-midi.md section 4).")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import tempfile
    print(describe(split_tracks(sys.argv[1], tempfile.mkdtemp())))
