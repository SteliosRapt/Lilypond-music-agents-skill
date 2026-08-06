"""Recover the printed geometry of an engraved score from its page images.

To animate a score you need to know, in pixels, where every bar of every system
sits on every page.  Inferring that from a normal black-and-white render means
guessing which vertical dark runs are barlines and which are note stems -- it is
fragile and it fails silently.

Instead, render the score twice at the same resolution: once normally (for
display) and once with `assets/analysis-colors.ily` included, which recolours
three grobs without touching spacing:

    green  system-start bar/brace/bracket -> one per system, spanning exactly
                                             that system's staves
    red    barlines and span bars         -> bar boundaries
    blue   staff lines                    -> left edge of each system

Order matters.  Resolve the green *bands* first, then look for red only inside a
band: systems are left- and right-aligned, so barlines from different systems
share x-coordinates and a whole-page column scan merges them into one.

    from lily_layout import analyze_pages
    pages = analyze_pages(["analysis-page1.png", "analysis-page2.png"])
"""

import numpy as np
from PIL import Image


def _clusters(indices, max_gap=6):
    """Group sorted indices into runs, splitting when the gap exceeds max_gap."""
    if len(indices) == 0:
        return []
    groups, cur = [], [int(indices[0])]
    for v in indices[1:]:
        v = int(v)
        if v - cur[-1] <= max_gap:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    return groups


def _masks(img):
    r, g, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    red = (r > 130) & (g < 110) & (b < 110)
    blue = (b > 130) & (r < 110) & (g < 110)
    green = (g > 90) & (r < 110) & (b < 110) & (g > r + 40) & (g > b + 40)
    return red, blue, green


def _system_bands(blue, green, min_height=20, system_gap=24):
    """Vertical (top, bottom) extent of each system on the page."""
    bands = []
    cols = np.where(green.sum(axis=0) > 0)[0]
    for group in _clusters(cols, max_gap=6):
        sub = green[:, group[0]:group[-1] + 1]
        rows = np.where(sub.sum(axis=1) > 0)[0]
        # One x-cluster can hold the start delimiter of *every* system on the
        # page, since systems share a left margin -- split it by vertical gaps.
        for run in _clusters(rows, max_gap=system_gap):
            if run[-1] - run[0] >= min_height:
                bands.append([run[0], run[-1]])

    if not bands:
        # Single-staff score: no system-start delimiter is drawn, so fall back
        # to the staff lines themselves, one staff per system.
        rows = np.where(blue.sum(axis=1) > 0)[0]
        for run in _clusters(rows, max_gap=system_gap):
            bands.append([run[0], run[-1]])

    bands.sort()
    merged = []
    for band in bands:
        if merged and band[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], band[1])
        else:
            merged.append(band)
    return merged


def analyze_page(png_path, min_barline_height=10):
    """Return {'size': (w, h), 'systems': [{'top','bot','left','bars':[x0..xn]}, ...]}.

    Each system's `bars` list holds n+1 x-coordinates for n bars: the left edge
    followed by every barline, so bar i spans bars[i] -> bars[i+1].
    """
    img = np.array(Image.open(png_path).convert("RGB")).astype(int)
    h, w, _ = img.shape
    red, blue, green = _masks(img)

    out = []
    for top, bot in _system_bands(blue, green):
        band_red = red[top:bot + 1, :]
        band_blue = blue[top:bot + 1, :]

        xs = []
        for group in _clusters(np.where(band_red.sum(axis=0) > 0)[0], max_gap=6):
            sub = band_red[:, group[0]:group[-1] + 1]
            if np.count_nonzero(sub.sum(axis=1)) < min_barline_height:
                continue
            xs.append((group[0] + group[-1]) / 2.0)
        xs.sort()

        bcols = np.where(band_blue.sum(axis=0) > 0)[0]
        left = float(bcols.min()) if len(bcols) else (xs[0] if xs else 0.0)
        # A barline drawn at the very start of a system marks the system start,
        # not a bar boundary; fold it into `left` instead of making a 0-width bar.
        while xs and xs[0] - left < 8:
            left = xs.pop(0)
        if not xs:
            continue
        out.append({"top": int(top), "bot": int(bot), "left": left, "bars": [left] + xs})

    return {"size": (w, h), "systems": out}


def analyze_pages(png_paths, **kw):
    """Analyse every page; each page dict gains a `bar_count`."""
    pages = [analyze_page(p, **kw) for p in png_paths]
    for pg in pages:
        pg["bar_count"] = sum(len(s["bars"]) - 1 for s in pg["systems"])
    return pages


def ink_bbox(png_paths, threshold=200):
    """Union bounding box of all non-white pixels across pages.

    Crop margins with one shared box for every page, so the score does not jump
    when the video turns the page.
    """
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for p in png_paths:
        a = np.array(Image.open(p).convert("L"))
        dark = a < threshold
        rows = np.where(dark.sum(axis=1) > 0)[0]
        cols = np.where(dark.sum(axis=0) > 0)[0]
        if not len(rows):
            continue
        y0, y1 = min(y0, int(rows.min())), max(y1, int(rows.max()))
        x0, x1 = min(x0, int(cols.min())), max(x1, int(cols.max()))
    return x0, y0, x1, y1


if __name__ == "__main__":
    import sys
    total = 0
    for path in sys.argv[1:]:
        info = analyze_page(path)
        print(f"{path}  {info['size'][0]}x{info['size'][1]}")
        for i, s in enumerate(info["systems"], 1):
            n = len(s["bars"]) - 1
            total += n
            print(f"  system {i}: y {s['top']}-{s['bot']}  {n} bars  "
                  f"x {s['bars'][0]:.0f}..{s['bars'][-1]:.0f}")
    print(f"total bars across pages: {total}")
