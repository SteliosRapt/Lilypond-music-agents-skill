"""Turn LilyPond's paper columns into a note-level (time -> pixel) anchor table.

Interpolating the playhead linearly across a bar is wrong, because LilyPond does
not space notes proportionally to their duration: each doubling of a note's
length buys it one fixed increment more room, not twice the room.  In a bar of
half + quarter + quarter the second quarter is printed at about 43% of the bar's
width while it sounds at 50% of the bar's time, so a linearly-swept playhead
drifts off the notes by tens to a couple of hundred milliseconds in the middle
of every bar, snapping back into place at each barline.

The fix is more anchor points.  `assets/paper-columns.ily` makes LilyPond print
the moment and the horizontal position of every paper column -- every moment at
which anything is engraved -- so the playhead can be driven by a piecewise-linear
map through the printed onsets instead of one ramp per bar.

Those positions arrive in LilyPond units relative to each system's own origin,
which says nothing about page pixels.  They are anchored by fitting an affine
map per system against the barline positions already measured from the analysis
render (`lily_layout.py`): both describe the same barlines, one in LilyPond
units and one in pixels, so the transform between them is over-determined and
can be least-squares fitted and residual-checked.

    from column_map import parse_columns, note_anchors
    cols = parse_columns(stderr_text)
    anchors = note_anchors(cols, pages, bars, moment_to_sec)   # per system
"""

import re
from fractions import Fraction

COL_RE = re.compile(r"@COL (\d+) (\d) (-?\d+(?:/\d+)?) (-?[\d.eE+-]+)")


def parse_columns(text):
    """Parse '@COL system musical moment x' lines out of a LilyPond stderr log.

    Returns [{'system','musical','moment','x'}, ...] in the order printed.
    """
    out = []
    for line in text.splitlines():
        m = COL_RE.search(line)
        if m:
            out.append({
                "system": int(m.group(1)),
                "musical": m.group(2) == "1",
                "moment": Fraction(m.group(3)),
                "x": float(m.group(4)),
            })
    return out


def group_systems(cols):
    """Split columns by system, ordered by the earliest moment each contains.

    The ily hands out system indices by object identity in order of first
    appearance, which is not promised to be reading order; musical time is.
    """
    by_id = {}
    for c in cols:
        by_id.setdefault(c["system"], []).append(c)
    return [by_id[k] for k in sorted(by_id, key=lambda k: min(c["moment"] for c in by_id[k]))]


def _affine(src, dst):
    """Least-squares fit dst = a*src + b. Returns (a, b, max_abs_residual)."""
    n = len(src)
    if n < 2:
        return None
    mx, my = sum(src) / n, sum(dst) / n
    var = sum((u - mx) ** 2 for u in src)
    if var <= 0:
        return None
    a = sum((u - mx) * (v - my) for u, v in zip(src, dst)) / var
    b = my - a * mx
    resid = max(abs(v - (a * u + b)) for u, v in zip(src, dst))
    return a, b, resid


def system_transform(sys_cols, image_bars, tolerance=8.0):
    """Fit LilyPond x -> page pixel x for one system.

    `image_bars` is the system's measured [left, barline1, ... barlineN] in page
    pixels; the system's non-musical columns are the same boundaries in LilyPond
    units.  Returns (a, b, residual) or None if the two disagree.
    """
    bounds = sorted({(c["moment"], c["x"]) for c in sys_cols if not c["musical"]})
    if len(bounds) != len(image_bars):
        return None
    fit = _affine([x for _, x in bounds], list(image_bars))
    if fit is None or fit[2] > tolerance:
        return None
    return fit


def note_anchors(cols, pages, bars, moment_to_sec, tolerance=8.0):
    """Build per-system [(t_seconds, x_page_pixels), ...] anchor lists.

    One entry per printed onset, plus the system's closing barline, so a
    playhead interpolated linearly between consecutive anchors passes each
    printed note exactly when that note sounds.

    Returns (anchors, diagnostics).  `anchors` is a list parallel to the flat
    list of systems across `pages`, each entry either a list of (t, x) pairs or
    None, meaning 'no reliable map for this system -- fall back to bar-linear'.
    """
    flat_systems = [s for p in pages for s in p["systems"]]
    groups = group_systems(cols)
    diag = {"systems": len(flat_systems), "dumped": len(groups),
            "mapped": 0, "max_residual": 0.0}

    if len(groups) != len(flat_systems):
        diag["error"] = (f"paper-column pass found {len(groups)} systems but the "
                         f"page images show {len(flat_systems)}")
        return [None] * len(flat_systems), diag

    # A bar's start moment, so an anchor can be clipped to the music that is
    # actually on the page (the final barline's moment is the end of the piece).
    end_moment = max(c["moment"] for c in cols)

    out = []
    for sys_cols, image in zip(groups, flat_systems):
        fit = system_transform(sys_cols, image["bars"], tolerance)
        if fit is None:
            out.append(None)
            continue
        a, b, resid = fit
        diag["mapped"] += 1
        diag["max_residual"] = max(diag["max_residual"], resid)

        # One anchor per moment.  A grace note shares its main note's moment;
        # keeping the largest x puts the anchor on the main note and lets the
        # playhead sweep through the grace on its way in.
        best = {}
        for c in sys_cols:
            if c["musical"]:
                best[c["moment"]] = max(best.get(c["moment"], -1e9), c["x"])
        # the closing barline, so the last note of the system has somewhere to
        # sweep towards instead of stopping dead on its own note head
        closing = max((c for c in sys_cols if not c["musical"]),
                      key=lambda c: c["moment"])
        best[closing["moment"]] = max(best.get(closing["moment"], -1e9), closing["x"])

        pts = []
        for moment in sorted(best):
            if moment > end_moment:
                continue
            t = moment_to_sec(moment)
            x = a * best[moment] + b
            if pts and t <= pts[-1][0] + 1e-6:
                continue
            pts.append((t, x))
        out.append(pts if len(pts) >= 2 else None)

    return out, diag


if __name__ == "__main__":
    import sys
    text = open(sys.argv[1]).read()
    cols = parse_columns(text)
    groups = group_systems(cols)
    print(f"{len(cols)} paper columns in {len(groups)} systems")
    for i, g in enumerate(groups):
        musical = [c for c in g if c["musical"]]
        bounds = sorted({c["moment"] for c in g if not c["musical"]})
        print(f"  system {i + 1}: {len(musical)} onsets, "
              f"moments {bounds[0]}..{bounds[-1]}, "
              f"x {min(c['x'] for c in g):.1f}..{max(c['x'] for c in g):.1f}")
