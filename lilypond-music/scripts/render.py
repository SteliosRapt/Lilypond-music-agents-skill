#!/usr/bin/env python3
"""Render a LilyPond score to PDF + MIDI + audio + a bar-synced score video.

    python3 render.py score.ly -o out/                  # everything
    python3 render.py score.ly -o out/ --no-video       # PDF, MIDI, mp3 only
    python3 render.py score.ly -o out/ --size 1920x1080 # landscape

The video shows each page of the engraved score with a moving playhead that
tracks the music bar by bar and a soft band highlighting the system currently
sounding.  Timing comes from the score's own MIDI output and geometry from a
colour-coded second render, so nothing is hand-maintained: edit the score, re-run,
and the animation follows.

Requires: lilypond, fluidsynth (+ a GM soundfont), ffmpeg, numpy, pillow.
Run scripts/setup.sh once on a fresh machine.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from column_map import note_anchors, parse_columns       # noqa: E402
from lily_layout import analyze_pages, ink_bbox          # noqa: E402
from midi_expression import add_expression, parse_hairpins  # noqa: E402
from midi_split import describe, split_tracks             # noqa: E402
from midi_timing import bar_timeline, moment_converter   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_ILY = os.path.join(HERE, "..", "assets", "analysis-colors.ily")
COLUMNS_ILY = os.path.join(HERE, "..", "assets", "paper-columns.ily")
HAIRPINS_ILY = os.path.join(HERE, "..", "assets", "hairpins.ily")
FADE_IN, FADE_OUT = 1.2, 2.5      # opening/closing dips, shared with verify()
SOUNDFONTS = [
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
]


def run(cmd, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        sys.stderr.write(f"\n$ {' '.join(cmd)}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}\n")
        raise SystemExit(f"command failed: {cmd[0]}")
    return proc


def need(binary, hint):
    if shutil.which(binary) is None:
        raise SystemExit(f"missing dependency: {binary}\n  install with: {hint}")


def pages_of(stem):
    """LilyPond writes stem.png for one page and stem-page1.png for several."""
    multi = sorted(glob.glob(f"{stem}-page*.png"),
                   key=lambda p: int(re.search(r"page(\d+)", p).group(1)))
    if multi:
        return multi
    single = f"{stem}.png"
    return [single] if os.path.exists(single) else []


# --------------------------------------------------------------------------
# engraving
# --------------------------------------------------------------------------

def engrave(score, work, resolution):
    """Compile once for display (pdf+png+midi) and once colour-coded for analysis."""
    base = os.path.join(work, "base")
    run(["lilypond", "-dno-point-and-click", "--formats=pdf,png",
         f"-dresolution={resolution}", "-o", base, score])
    # -dinclude-settings takes a single filename, so chain both instrumentation
    # blocks through one wrapper: the colour recoding that makes barlines and
    # systems findable in the page image, and the paper-column hook that reports
    # where every musical moment was printed.  Neither touches spacing.
    settings = os.path.join(work, "analysis-settings.ily")
    with open(settings, "w") as fh:
        for ily in (ANALYSIS_ILY, COLUMNS_ILY, HAIRPINS_ILY):
            fh.write(f'\\include "{os.path.abspath(ily)}"\n')
    analysis = os.path.join(work, "analysis")
    # --loglevel=WARN for the same reason vocal_score.py uses it: the progress
    # markers LilyPond prints while drawing ("[16]") share this stream with the
    # column report, and one landing inside a line costs an anchor silently.
    proc = run(["lilypond", "-dno-point-and-click",
                f"-dinclude-settings={settings}", "--loglevel=WARN",
                "--formats=png", f"-dresolution={resolution}", "-o", analysis, score])

    display_pngs = pages_of(base)
    analysis_pngs = pages_of(analysis)
    if len(display_pngs) != len(analysis_pngs):
        raise SystemExit("display and analysis renders disagree on page count")
    midis = sorted(glob.glob(f"{base}*.mid*"))
    return {
        "pdf": f"{base}.pdf",
        "midi": midis[0] if midis else None,
        "display": display_pngs,
        "analysis": analysis_pngs,
        "columns": proc.stderr,
        "log": proc.stderr,
    }


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

def find_soundfont(explicit=None):
    sf = explicit or next((p for p in SOUNDFONTS if os.path.exists(p)), None)
    if not sf:
        raise SystemExit("no GM soundfont found; pass --soundfont or run setup.sh")
    return sf


def select_parts(key, parts, flag):
    """Resolve one `--mix`/`--eq` key to the parts it names."""
    from midi_split import gm_names
    names = gm_names()
    hits = [p for p in parts
            if key == str(p["index"])
            or key.lower() in p["name"].lower()
            or key.lower() in names.get(p["program"], "").lower()]
    if not hits:
        raise SystemExit(f"{flag}: nothing matches {key!r}. Parts are:\n"
                         + describe(parts))
    return hits


def parse_mix(spec, parts):
    """Parse `--mix "koto=-7,voice=+4/-0.3"` into {part index: (gain_dB, pan)}.

    A key is a part number, or any case-insensitive substring of the part's name
    or its GM sound -- `koto`, `pad`, `drums`, `3` all select. Gain is in
    decibels; `mute` silences the part. The optional value after `/` is stereo
    balance, -1 hard left to 1 hard right.
    """
    settings = {}
    for item in (i.strip() for i in spec.split(",") if i.strip()):
        if "=" not in item:
            raise SystemExit(f"--mix: expected key=value, got {item!r}")
        key, value = (v.strip() for v in item.split("=", 1))
        pan = 0.0
        if "/" in value:
            value, pan_s = value.split("/", 1)
            try:
                pan = max(-1.0, min(1.0, float(pan_s)))
            except ValueError:
                raise SystemExit(f"--mix: {pan_s!r} is not a stereo balance "
                                 "(a number from -1 to 1)")
        if value.lower() in ("mute", "off"):
            gain = -120.0
        else:
            try:
                gain = float(value)
            except ValueError:
                raise SystemExit(f"--mix: {value!r} is not a gain in dB "
                                 "(or 'mute')")
        for p in select_parts(key, parts, "--mix"):
            settings[p["index"]] = (gain, pan)
    return settings


# The named curves --eq accepts. Each is a list of ffmpeg filter fragments, and
# each exists because it is a fix for something a General MIDI rendering does
# to a specific family of instruments -- see references/audio-and-midi.md
# section 10, which is where the frequencies are justified.
# `equalizer` is always a bell, whatever `t` is set to: `t` names the *unit* of
# the width, not the shape (h = hertz, q = Q factor, o = octaves). A shelf is a
# different filter -- `highshelf`/`lowshelf` -- and writing `equalizer=t=h:w=0.7`
# in the hope of one gives a bell 0.7 Hz wide, which is silence dressed as a
# setting: measured, it moved its band by 0.02 dB.
EQ_PRESETS = {
    "warm":    ["equalizer=f=250:t=q:w=1.0:g=2.5",
                "equalizer=f=3200:t=q:w=1.2:g=-3"],
    "bright":  ["highshelf=f=5000:t=q:w=0.7:g=3",
                "equalizer=f=300:t=q:w=1.0:g=-1.5"],
    "clear":   ["equalizer=f=400:t=q:w=1.4:g=-3.5",
                "equalizer=f=2500:t=q:w=1.0:g=2"],
    "thin":    ["highpass=f=180", "equalizer=f=800:t=q:w=1.2:g=-2"],
    "body":    ["equalizer=f=120:t=q:w=1.0:g=3",
                "equalizer=f=500:t=q:w=1.2:g=-2"],
    "air":     ["highshelf=f=7000:t=q:w=0.7:g=3.5"],
    "distant": ["lowpass=f=5000", "equalizer=f=200:t=q:w=1.0:g=-2"],
    "vocal":   ["highpass=f=90", "equalizer=f=250:t=q:w=1.2:g=-2.5",
                "equalizer=f=2800:t=q:w=1.0:g=2.5",
                "highshelf=f=7000:t=q:w=0.7:g=2"],
}


def parse_eq(spec, parts):
    """Parse `--eq "koto=warm,drums=hp:120,voice=2500+3/1.2"` per part.

    Three forms, and they compose left to right: parts are separated by commas
    and a part's stages by `|`, which is not `+` because `+` is already the
    sign of a bell's gain and `2500+3` has to stay readable.

        warm            a named curve from EQ_PRESETS
        hp:120 lp:9000  a high-pass or low-pass at that frequency
        2500+3/1.4      a peaking bell: frequency, gain in dB, optional /Q
        400-3           the same, cutting

    Bells are what actually fix a rendering -- the presets are bells with names
    -- and the Q defaults to 1.0, about an octave wide, which is broad enough
    to sound like a tone change rather than a filter.
    """
    settings = {}
    for item in (i.strip() for i in spec.split(",") if i.strip()):
        if "=" not in item:
            raise SystemExit(f"--eq: expected key=value, got {item!r}")
        key, value = (v.strip() for v in item.split("=", 1))
        chain = []
        for stage in (s.strip() for s in value.split("|") if s.strip()):
            chain += eq_stage(stage)
        if not chain:
            raise SystemExit(f"--eq: {value!r} describes no filter")
        for p in select_parts(key, parts, "--eq"):
            settings.setdefault(p["index"], []).extend(chain)
    return settings


BELL_RE = re.compile(r"^(\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?$")


def eq_stage(stage):
    """One `--eq` stage to ffmpeg filter fragments."""
    if stage in EQ_PRESETS:
        return list(EQ_PRESETS[stage])
    for prefix, filt in (("hp:", "highpass"), ("lp:", "lowpass")):
        if stage.startswith(prefix):
            try:
                return [f"{filt}=f={float(stage[len(prefix):]):.0f}"]
            except ValueError:
                raise SystemExit(f"--eq: {stage!r} needs a frequency in Hz")
    # A bell written the way it is spoken: "2500 plus 3 dB, Q of 1.4".
    m = BELL_RE.match(stage)
    if m:
        freq, gain, q = m.group(1), m.group(2), m.group(3) or "1.0"
        return [f"equalizer=f={float(freq):.0f}:t=q:w={float(q):.2f}:"
                f"g={float(gain):.2f}"]
    raise SystemExit(
        f"--eq: cannot read {stage!r}. Expected a preset "
        f"({', '.join(sorted(EQ_PRESETS))}), hp:HZ, lp:HZ, or a bell like "
        "2500+3 or 400-3/1.4")


def master_chain(dur, reverb, tail, limit=False, eq=(), band=(35.0, 9500.0)):
    """Shared post-processing: room, tone, band limits, levelling, tail fade.

    Order is not cosmetic. Reverb first, so the room is coloured with
    everything else rather than added on top of a shaped signal; then any
    master EQ, while the level is still whatever the mix made it; then the
    limiter, which must see the final peaks; then the band limits and
    levelling; and the fade last, over the top of all of it.

    The band limits sit *after* every EQ in the chain, per-part ones included,
    and that is a trap worth knowing rather than a detail: with the default
    9.5 kHz ceiling, boosting 11 kHz anywhere upstream is inaudible, because
    this filter removes it again. `--band` is how you open the top up.
    """
    chain = []
    if reverb:
        # a cheap hall tail; afir with a real impulse response is better if you have one
        chain.append("aecho=0.85:0.9:70|130|220|400:0.35|0.25|0.18|0.1")
    chain += list(eq)
    if limit:
        # summing several parts can peak above unity where one part never did
        chain.append("alimiter=limit=0.95")
    low, high = band
    chain += [f"highpass=f={low:.0f}", f"lowpass=f={high:.0f}",
              "dynaudnorm=p=0.65:m=5",
              f"afade=t=out:st={max(dur - tail, 0):.2f}:d={tail:.2f}"]
    return chain


def duration_of(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path]).stdout.strip())


def synthesize(midi, work, out_mp3, soundfont=None, gain=1.0, reverb=True, tail=3.0,
               mix=None, parts=None, vocal=None, vocal_gain=0.0, eq=None,
               master_eq=(), vocal_eq=(), band=(35.0, 9500.0)):
    """MIDI -> mastered mp3, in one pass or as a per-part mix.

    Without `mix` or `eq` the whole file goes through fluidsynth once, which is
    fast and is what the balance in the score gives you. With either, each part
    is synthesised separately and summed with its own gain, stereo balance and
    tone, so how the instruments sit against each other is a decision rather
    than an accident of how loud and how bright each soundfont sample happens
    to be.
    """
    sf = find_soundfont(soundfont)

    def render_midi(src, dst):
        run(["fluidsynth", "-ni", "-F", dst, "-r", "44100", "-g", str(gain), "-R", "1", sf, src])

    per_part = bool(mix or eq)
    if not per_part and not vocal:
        wav = os.path.join(work, "raw.wav")
        render_midi(midi, wav)
        chain = master_chain(duration_of(wav), reverb, tail, eq=master_eq,
                             band=band)
        run(["ffmpeg", "-y", "-i", wav, "-af", ",".join(chain),
             "-c:a", "libmp3lame", "-b:a", "192k", out_mp3])
        return duration_of(out_mp3)

    if not per_part:
        # A sung line to lay over the whole instrumental: one stem each.
        wav = os.path.join(work, "raw.wav")
        render_midi(midi, wav)
        longest = max(duration_of(wav), duration_of(vocal))
        graph = [f"[1:a]{','.join(list(vocal_eq) + [f'volume={vocal_gain:.2f}dB'])}[v]",
                 "[0:a][v]amix=inputs=2:normalize=0[sum]",
                 f"[sum]{','.join(master_chain(longest, reverb, tail, True, master_eq, band))}[out]"]
        run(["ffmpeg", "-y", "-i", wav, "-i", vocal,
             "-filter_complex", ";".join(graph), "-map", "[out]",
             "-c:a", "libmp3lame", "-b:a", "192k", out_mp3])
        return duration_of(out_mp3)

    stems, longest = [], 0.0
    if vocal:
        longest = duration_of(vocal)
    for part in parts:
        wav = os.path.join(work, f"stem{part['index']:02d}.wav")
        render_midi(part["path"], wav)
        longest = max(longest, duration_of(wav))
        stems.append((part, wav))

    cmd = ["ffmpeg", "-y"]
    for _part, wav in stems:
        cmd += ["-i", wav]
    if vocal:
        cmd += ["-i", vocal]
    graph, labels = [], []
    for k, (part, _wav) in enumerate(stems):
        g, pan = (mix or {}).get(part["index"], (0.0, 0.0))
        # Tone before level, so a part's gain still means what --list-tracks
        # and an ebur128 reading said it meant.
        steps = list((eq or {}).get(part["index"], []))
        steps.append(f"volume={g:.2f}dB")
        if pan:
            left, right = min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)
            steps.append(f"pan=stereo|c0={left:.3f}*c0|c1={right:.3f}*c1")
        graph.append(f"[{k}:a]{','.join(steps)}[s{k}]")
        labels.append(f"[s{k}]")
    if vocal:
        sung = list(vocal_eq) + [f"volume={vocal_gain:.2f}dB"]
        graph.append(f"[{len(stems)}:a]{','.join(sung)}[sung]")
        labels.append("[sung]")
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[sum]")
    graph.append(f"[sum]{','.join(master_chain(longest, reverb, tail, True, master_eq, band))}[out]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "192k", out_mp3]
    run(cmd)
    return duration_of(out_mp3)


# --------------------------------------------------------------------------
# video
# --------------------------------------------------------------------------

class Frame:
    """Maps page-image pixels into video-frame pixels (one shared crop for all pages)."""

    def __init__(self, pngs, width, height, margin=24):
        x0, y0, x1, y1 = ink_bbox(pngs)
        pw, ph = Image.open(pngs[0]).size
        self.crop = (max(x0 - margin, 0), max(y0 - margin, 0),
                     min(x1 + margin, pw), min(y1 + margin, ph))
        cw, ch = self.crop[2] - self.crop[0], self.crop[3] - self.crop[1]
        self.scale = min(width / cw, height / ch)
        self.width, self.height = width, height
        self.xoff = (width - cw * self.scale) / 2
        self.yoff = (height - ch * self.scale) / 2

    def x(self, px):
        return (px - self.crop[0]) * self.scale + self.xoff

    def y(self, py):
        return (py - self.crop[1]) * self.scale + self.yoff

    def background(self, png, path, bg=(16, 15, 18)):
        im = Image.open(png).convert("RGB").crop(self.crop)
        im = im.resize((max(int(round((self.crop[2] - self.crop[0]) * self.scale)), 1),
                        max(int(round((self.crop[3] - self.crop[1]) * self.scale)), 1)),
                       Image.LANCZOS)
        canvas = Image.new("RGB", (self.width, self.height), bg)
        canvas.paste(im, (int(round(self.xoff)), int(round(self.yoff))))
        canvas.save(path)


def system_bands(systems):
    """Pad each system towards its neighbours so lyrics and dynamics stay inside."""
    bands = []
    for i, s in enumerate(systems):
        prev_bot = systems[i - 1]["bot"] if i else None
        next_top = systems[i + 1]["top"] if i + 1 < len(systems) else None
        pad_up = min((s["top"] - prev_bot) / 2, 90) if prev_bot is not None else 45
        pad_dn = min((next_top - s["bot"]) / 2, 90) if next_top is not None else 45
        bands.append((s["top"] - pad_up, s["bot"] + pad_dn))
    return bands


def piecewise(cases, default):
    """Nested if() expression for ffmpeg: cases = [(t_end, value_expr), ...]."""
    expr = default
    for t_end, value in reversed(cases):
        expr = f"if(lt(t,{t_end:.3f}),{value},{expr})"
    return expr


def playhead_track(pages, bars, frame, anchors):
    """Per system, the (time, frame-x) points the playhead interpolates between.

    With note-level anchors that is one point per printed onset, so the playhead
    is on the note at the moment it sounds.  Without them -- no paper-column
    dump, or a system whose dump and page image disagree -- it falls back to the
    old behaviour for that system: one point per barline, linear in between,
    which is right at the barlines and approximate elsewhere.
    """
    track, index, si = [], 0, 0
    for page in pages:
        for sysd in page["systems"]:
            n = len(sysd["bars"]) - 1
            sys_bars = bars[index:index + n]
            index += n
            pts = anchors[si] if anchors and si < len(anchors) else None
            si += 1
            if pts:
                track.append([(t, frame.x(x)) for t, x in pts])
            elif sys_bars:
                fallback = [(start, frame.x(sysd["bars"][j]))
                            for j, (_, start, _) in enumerate(sys_bars)]
                last_start, last_dur = sys_bars[-1][1], sys_bars[-1][2]
                fallback.append((last_start + last_dur, frame.x(sysd["bars"][n])))
                track.append(fallback)
            else:
                track.append([])
    return track


def build_video(pages, bars, frame, work, out_mp4, audio, audio_dur,
                fps, playhead_rgb, highlight_hex, bg_rgb, track):
    """One video segment per page, then concatenate and mux the audio.

    Each system gets its own playhead image sized to that system's band, so the
    marker never spills into the staves above or below.
    """
    bar_w = max(int(frame.width / 180), 4)
    segments, index, sys_index = [], 0, 0

    for p, page in enumerate(pages):
        n_bars = page["bar_count"]
        page_bars = bars[index:index + n_bars]
        index += n_bars
        page_sys0 = sys_index
        sys_index += len(page["systems"])
        if not page_bars:
            continue
        t_start = page_bars[0][1]
        t_end = bars[index][1] if index < len(bars) else audio_dur
        if p == len(pages) - 1:
            t_end = max(t_end, audio_dur)
        duration = t_end - t_start

        bg_path = os.path.join(work, f"bg{p}.png")
        frame.background(page["display"], bg_path, bg=tuple(bg_rgb))

        boxes, overlays, inputs = [], [], []
        b = 0
        for si, (sysd, (band_top, band_bot)) in enumerate(
                zip(page["systems"], system_bands(page["systems"]))):
            pts = track[page_sys0 + si]
            n = len(sysd["bars"]) - 1
            sys_bars = page_bars[b:b + n]
            b += n
            if not sys_bars:
                break
            y0 = frame.y(band_top)
            h = max(int(round((band_bot - band_top) * frame.scale)), 8)
            s0 = sys_bars[0][1] - t_start
            s1 = sys_bars[-1][1] + sys_bars[-1][2] - t_start
            boxes.append(
                f"drawbox=x=0:y={y0:.0f}:w={frame.width}:h={h}:"
                f"color=0x{highlight_hex}@0.10:thickness=fill:"
                f"enable='between(t,{s0:.3f},{s1:.3f})'")

            # One case per gap between anchors.  The overlay places the image's
            # left edge, so shift by half its width to sit the marker *on* the
            # note rather than to the right of it.
            x_cases = []
            for (ta, xa), (tb, xb) in zip(pts, pts[1:]):
                ra, rb = ta - t_start, tb - t_start
                span = max(rb - ra, 1e-6)
                x_cases.append(
                    (rb, f"({xa - bar_w / 2:.1f}+{xb - xa:.1f}*(t-{ra:.3f})/{span:.4f})"))
            if not x_cases:
                continue
            ph = os.path.join(work, f"playhead{p}_{si}.png")
            Image.new("RGBA", (bar_w, h), tuple(playhead_rgb) + (195,)).save(ph)
            inputs.append(ph)
            overlays.append((piecewise(x_cases, x_cases[-1][1]), y0, s0, s1))

        chain = [f"[0:v]{','.join(boxes)}[v0]"]
        for k, (x_expr, y0, s0, s1) in enumerate(overlays):
            src, dst = f"[v{k}]", f"[v{k + 1}]"
            chain.append(f"{src}[{k + 1}:v]overlay=x='{x_expr}':y={y0:.0f}:"
                         f"enable='between(t,{s0:.3f},{s1:.3f})'{dst}")
        tail = []
        if p == 0:
            tail.append(f"fade=t=in:st=0:d={FADE_IN}")
        if p == len(pages) - 1:
            tail.append(f"fade=t=out:st={max(duration - FADE_OUT, 0):.2f}:d={FADE_OUT}")
        tail.append("format=yuv420p")
        chain.append(f"[v{len(overlays)}]{','.join(tail)}[v]")

        graph_path = os.path.join(work, f"graph{p}.txt")
        with open(graph_path, "w") as fh:
            fh.write(";".join(chain))

        cmd = ["ffmpeg", "-y",
               "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", bg_path]
        # Every looping image input needs its own -t: an image input never ends
        # on its own, and overlay keeps pulling frames from it long after the
        # background has run out, producing a video hours long.
        for ph in inputs:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", ph]
        seg = os.path.join(work, f"seg{p}.mp4")
        cmd += ["-filter_complex_script", graph_path, "-map", "[v]", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-r", str(fps), "-shortest", seg]
        run(cmd)
        segments.append(seg)

    concat = os.path.join(work, "concat.txt")
    with open(concat, "w") as fh:
        fh.write("".join(f"file '{os.path.abspath(s)}'\n" for s in segments))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-i", audio,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", out_mp4])
    return bar_w


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def verify(out_mp4, track, bars, samples, bar_w=6, fps=24, slack=4.0,
           work="."):
    """Sample frames and check the playhead really is where the music is.

    Worth doing every time.  A silently-missing or mis-scaled playhead looks
    plausible in a thumbnail; this catches it in a few seconds.

    Samples land *between* anchors, not on them, because that is where an error
    hides: a bar-linear playhead is exactly right at every barline and wrong in
    between, so a check that only probed bar boundaries would pass either way.

    The tolerance is one frame of travel plus a few pixels: a probe at time t
    returns the frame at or just before t, so the playhead legitimately lags the
    exact position by up to one frame of its current speed.
    """
    import numpy as np
    flat = [pt for pts in track for pt in pts]
    spans = [(a, b) for a, b in zip(flat, flat[1:]) if b[0] > a[0] + 1e-6]
    if not spans:
        return True

    # The playhead is faded to black at both ends, so it cannot be measured
    # there.  Probing inside a fade reports MISSING and looks like a sync
    # failure; sample the body of the piece instead.
    t_hi = max(t for t, _ in flat) - FADE_OUT - 0.3
    spans = [sp for sp in spans if FADE_IN + 0.3 < sp[0][0] + (sp[1][0] - sp[0][0]) / 2 < t_hi]
    if not spans:
        return True

    def bar_of(t):
        return max((n for n, start, _ in bars if start <= t + 1e-6), default=1)

    step = max(len(spans) // samples, 1)
    rows, ok = [], True
    for (ta, xa), (tb, xb) in spans[::step]:
        t = ta + (tb - ta) * 0.5
        expected = xa + (xb - xa) * 0.5
        # In the run's own working directory, not /tmp: two renders of two
        # scores at once would otherwise read each other's probe frames.
        probe = os.path.join(work, "verify-frame.png")
        run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", out_mp4, "-frames:v", "1", probe])
        a = np.array(Image.open(probe).convert("RGB")).astype(int)
        mask = (a[:, :, 0] - np.maximum(a[:, :, 1], a[:, :, 2])) > 40
        cols = np.where(mask.sum(axis=0) > 10)[0]
        measured = float(cols.mean()) if len(cols) else None
        tolerance = slack + abs(xb - xa) / max((tb - ta) * fps, 1e-6)
        good = measured is not None and abs(measured - expected) <= tolerance
        ok &= good
        rows.append((bar_of(t), t, expected, measured, good))

    print("\n  bar     time    expected x   measured x")
    for n, t, exp, meas, good in rows:
        got = f"{meas:10.1f}" if meas is not None else "   MISSING"
        print(f"  {n:3d}  {t:7.2f}s   {exp:9.1f}   {got}   {'ok' if good else 'MISMATCH'}")
    print("  playhead sync:", "verified" if ok else "FAILED -- do not ship this video")
    return ok


def perform_hairpins(art, work, depth):
    """Rewrite the MIDI so hairpins across held notes are actually audible.

    LilyPond performs a hairpin as rising or falling note velocities, which does
    nothing for a hairpin drawn over one long note -- velocity is fixed at
    note-on.  `hairpins.ily` reports where the hairpins are; this maps them onto
    MIDI tracks and writes expression ramps under the held notes.

    Staff order is the join: the ily numbers staves in context creation order,
    which is the order LilyPond writes MIDI tracks in.  If the two counts
    disagree the mapping is unsafe, so the score is left exactly as LilyPond
    performed it rather than shaped on a guess.
    """
    staff_count, hairpins = parse_hairpins(art["log"])
    if not hairpins:
        return art["midi"]

    parts = split_tracks(art["midi"], os.path.join(work, "scan"))
    if staff_count != len(parts):
        print(f"  {len(hairpins)} hairpins found but {staff_count} staves map to "
              f"{len(parts)} sounding parts; leaving dynamics as engraved")
        return art["midi"]

    spans = {}
    for position, part in enumerate(parts):
        mine = [h for h in hairpins if h["staff"] == position]
        if mine:
            spans[part["track"]] = mine

    out = os.path.join(work, "expressive.midi")
    shaped = add_expression(art["midi"], out, spans, depth=depth)
    if not shaped:
        return art["midi"]

    names = {p["track"]: p["name"] for p in parts}
    print(f"  {len(shaped)} hairpin(s) over held notes performed as CC11 expression:")
    for item in shaped[:8]:
        arrow = "cresc from" if item["direction"] > 0 else "dim to"
        print(f"    {names.get(item['track'], '?'):<18} {item['quarters']:.0f}q "
              f"{arrow} {item['to']:.0%}")
    if len(shaped) > 8:
        print(f"    ... and {len(shaped) - 8} more")
    return out


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--stem", help="basename for outputs (default: score filename)")
    ap.add_argument("--resolution", type=int, default=200, help="page render DPI")
    ap.add_argument("--size", default="1080x1920", help="video size, e.g. 1920x1080")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--soundfont")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--mix", metavar="SPEC",
                    help="per-part balance, e.g. 'koto=-7,voice=+4/-0.3': gain in dB "
                         "(or 'mute'), optional /stereo-balance from -1 to 1. Keys "
                         "match a part number or any substring of its name or sound.")
    ap.add_argument("--eq", metavar="SPEC",
                    help="per-part tone, e.g. 'koto=warm,drums=hp:120,"
                         "shakuhachi=2500+3/1.4'. Keys select parts the same "
                         "way --mix does; stages combine with '|'. A stage is "
                         f"a preset ({', '.join(sorted(EQ_PRESETS))}), hp:HZ, "
                         "lp:HZ, or a bell FREQ+GAIN[/Q] such as 400-3/1.2.")
    ap.add_argument("--master-eq", metavar="SPEC",
                    help="the same stage syntax, applied to the finished mix "
                         "(no part key), e.g. 'clear|air'")
    ap.add_argument("--vocal-eq", metavar="SPEC",
                    help="the same, applied to the --vocal line alone")
    ap.add_argument("--band", default="35:9500", metavar="LOW:HIGH",
                    help="master high-pass and low-pass in Hz (default "
                         "35:9500). These run after every EQ, so raise HIGH "
                         "before boosting anything above it.")
    ap.add_argument("--list-tracks", action="store_true",
                    help="print the parts available to --mix and --eq, then stop")
    ap.add_argument("--no-reverb", action="store_true")
    ap.add_argument("--no-swell", action="store_true",
                    help="skip CC11 expression: hairpins across held notes stay silent")
    ap.add_argument("--swell-depth", type=float, default=0.4, metavar="D",
                    help="how far expression travels from end to end of a shaped "
                         "hairpin, 0-1 (default 0.4, about 9 dB)")
    ap.add_argument("--vocal", metavar="WAV",
                    help="a sung line from sing.py, mixed over the instruments")
    ap.add_argument("--vocal-gain", type=float, default=0.0, metavar="DB")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--pickup", type=float, default=0.0,
                    help="anacrusis length in quarter notes (\\partial 4 -> 1)")
    ap.add_argument("--playhead", default="d64a3a", help="hex colour")
    ap.add_argument("--highlight", default="ffc36a", help="hex colour")
    ap.add_argument("--bg", default="100f12", help="hex colour")
    ap.add_argument("--playhead-mode", choices=("notes", "bars"), default="notes",
                    help="notes: anchor the playhead on every printed onset "
                         "(default); bars: one linear sweep per bar")
    ap.add_argument("--verify", type=int, default=4, metavar="N",
                    help="sample N frames to confirm playhead sync (0 disables)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    need("lilypond", "apt-get install -y lilypond")
    need("ffmpeg", "apt-get install -y ffmpeg")
    if not args.no_audio:
        need("fluidsynth", "apt-get install -y fluidsynth fluid-soundfont-gm")
        # fail here rather than after a full engraving run
        find_soundfont(args.soundfont)

    stem = args.stem or os.path.splitext(os.path.basename(args.score))[0]
    outdir = os.path.abspath(args.outdir)
    work = os.path.join(outdir, f".{stem}-work")
    os.makedirs(work, exist_ok=True)

    print("engraving ...")
    art = engrave(os.path.abspath(args.score), work, args.resolution)
    shutil.copy(art["pdf"], os.path.join(outdir, f"{stem}.pdf"))
    if art["midi"]:
        shutil.copy(art["midi"], os.path.join(outdir, f"{stem}.midi"))
    print(f"  {len(art['display'])} page(s)")

    if art["midi"] and not args.no_swell:
        art["midi"] = perform_hairpins(art, work, args.swell_depth)
        shutil.copy(art["midi"], os.path.join(outdir, f"{stem}.midi"))

    parts = split_tracks(art["midi"], os.path.join(work, "parts")) if art["midi"] else []
    if args.list_tracks:
        print(describe(parts))
        return

    audio_path, audio_dur = None, None
    if not args.no_audio and art["midi"]:
        mix = parse_mix(args.mix, parts) if args.mix else None
        eq = parse_eq(args.eq, parts) if args.eq else None
        master_eq = [f for s in (args.master_eq or "").split("|") if s.strip()
                     for f in eq_stage(s.strip())]
        vocal_eq = [f for s in (args.vocal_eq or "").split("|") if s.strip()
                    for f in eq_stage(s.strip())]
        try:
            low, high = (float(v) for v in args.band.split(":"))
        except ValueError:
            sys.exit(f"--band wants LOW:HIGH in Hz, got {args.band!r}")
        if not 0 < low < high:
            sys.exit(f"--band: {low:.0f} Hz is not below {high:.0f} Hz")
        print(f"synthesising audio ({len(parts)} parts, mixed)" if mix or eq
              else "synthesising audio ...")
        audio_path = os.path.join(outdir, f"{stem}.mp3")
        if args.vocal and not os.path.exists(args.vocal):
            sys.exit(f"--vocal file not found: {args.vocal}")
        audio_dur = synthesize(art["midi"], work, audio_path, args.soundfont,
                               args.gain, not args.no_reverb, mix=mix, parts=parts,
                               vocal=args.vocal, vocal_gain=args.vocal_gain,
                               eq=eq, master_eq=master_eq, vocal_eq=vocal_eq,
                               band=(low, high))
        if args.vocal:
            print(f"  sung line: {os.path.basename(args.vocal)} "
                  f"({args.vocal_gain:+.1f} dB)"
                  + (f" eq {args.vocal_eq}" if vocal_eq else ""))
        for p in parts:
            g, pan = (mix or {}).get(p["index"], (0.0, 0.0))
            tone = (eq or {}).get(p["index"])
            if g or pan or tone:
                where = f" pan {pan:+.1f}" if pan else ""
                level = "muted" if g < -100 else f"{g:+.1f} dB"
                shaped = f"  [{', '.join(tone)}]" if tone else ""
                print(f"  {p['name']:<18} {level}{where}{shaped}")
        if master_eq:
            print(f"  master eq          [{', '.join(master_eq)}]")
        print(f"  {audio_dur:.1f}s")

    if args.no_video or not audio_path:
        print("done (no video requested)")
        return

    print("reading geometry and timeline ...")
    pages = analyze_pages(art["analysis"])
    for pg, disp in zip(pages, art["display"]):
        pg["display"] = disp
    bars = bar_timeline(art["midi"], args.pickup)
    printed = sum(p["bar_count"] for p in pages)
    print(f"  {printed} bars printed, {len(bars)} bars in MIDI")
    if printed != len(bars):
        print("  WARNING: counts disagree -- a pickup bar (--pickup), a repeat, or a\n"
              "  mid-score time signature change is the usual cause. Animation may drift.")

    anchors = None
    if args.playhead_mode == "notes" and art.get("columns"):
        cols = parse_columns(art["columns"])
        anchors, diag = note_anchors(cols, pages, bars, moment_converter(art["midi"]))
        if diag.get("error"):
            print(f"  note anchors unavailable ({diag['error']}); "
                  "playhead falls back to one sweep per bar")
        else:
            onsets = sum(len(a) - 1 for a in anchors if a)
            print(f"  {onsets} printed onsets anchored across "
                  f"{diag['mapped']}/{diag['systems']} systems "
                  f"(worst fit residual {diag['max_residual']:.1f}px)")

    width, height = (int(v) for v in args.size.lower().split("x"))
    frame = Frame(art["display"], width, height)
    hex2rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # noqa: E731

    print("rendering video ...")
    out_mp4 = os.path.join(outdir, f"{stem}.mp4")
    track = playhead_track(pages, bars, frame, anchors)
    bar_w = build_video(pages, bars, frame, work, out_mp4, audio_path, audio_dur,
                        args.fps, hex2rgb(args.playhead), args.highlight,
                        hex2rgb(args.bg), track)

    with open(os.path.join(work, "layout.json"), "w") as fh:
        json.dump({"bars": bars,
                   "pages": [{k: v for k, v in p.items() if k != "display"}
                             for p in pages]}, fh, indent=1)

    if args.verify:
        verify(out_mp4, track, bars, args.verify, bar_w, args.fps, work=work)

    if not args.keep_temp:
        shutil.rmtree(work, ignore_errors=True)
    print(f"\nwrote {outdir}/{stem}.pdf .midi .mp3 .mp4")


if __name__ == "__main__":
    main()
