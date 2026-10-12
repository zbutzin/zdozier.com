"""Static SVG charts and diagrams for zdozier.com, drawn at build time.

No JavaScript charting. Every chart is plain inline SVG whose colors come from
CSS custom properties (so light and dark mode both work), with a text
alternative and a data table rendered next to it by the templates.

Chart conventions (from the dataviz method used for this site):
- emphasis form: first or senior author work in the accent, co-author work in
  a neutral gray (one hue plus gray, validated for both color schemes);
- columns at most 24px wide, 4px rounded data end, square at the baseline;
- a 2px surface-colored gap between stacked segments;
- hairline solid gridlines, recessive axis text, selective direct labels;
- each column has a hit target the full height of the plot with a native
  <title> (no-JS tooltip) and a data-tip payload for the small JS tooltip.

Runs on Python 3.8 and 3.12.
"""
from __future__ import annotations

import json
import math

from markupsafe import Markup, escape


def _f(x):
    """Compact number formatting for SVG coordinates."""
    s = ("%.2f" % x).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def nice_scale(vmax, max_intervals=4):
    """Return (top, step) for a zero-based axis with clean tick values."""
    if vmax <= 0:
        return 1, 1
    k = 0
    while True:
        for base in (1, 2, 5):
            step = base * (10 ** k)
            if math.ceil(vmax / float(step)) <= max_intervals:
                return int(math.ceil(vmax / float(step)) * step), step
        k += 1


def _column_path(x, y, w, h, r):
    """Column with rounded top corners and a square base. (x, y) is top-left."""
    if h <= 0 or w <= 0:
        return ""
    r = max(0.0, min(r, h, w / 2.0))
    if r == 0:
        return "M%s %sh%sv%sh%sz" % (_f(x), _f(y), _f(w), _f(h), _f(-w))
    return (
        "M%s %s" % (_f(x), _f(y + h))
        + "V%s" % _f(y + r)
        + "A%s %s 0 0 1 %s %s" % (_f(r), _f(r), _f(x + r), _f(y))
        + "H%s" % _f(x + w - r)
        + "A%s %s 0 0 1 %s %s" % (_f(r), _f(r), _f(x + w), _f(y + r))
        + "V%s" % _f(y + h)
        + "Z"
    )


def _tip(payload):
    return escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _plural(n, one, many):
    return one if n == 1 else many


def pubs_per_year_svg(rows, compact=False, title_id=None, desc_id=None):
    """Stacked columns of works per year: first/senior (accent) under co-author (gray).

    rows: list of {"year": int, "lead": int, "co": int}, consecutive years.
    compact: narrow variant for phones (fewer year labels, thinner columns).
    """
    rows = list(rows or [])
    if not rows:
        return Markup("")
    width = 360 if compact else 720
    height = 190 if compact else 210
    ml, mr, mt, mb = (26, 6, 22, 24) if compact else (30, 8, 22, 26)
    pw = width - ml - mr
    ph = height - mt - mb
    n = len(rows)
    band = pw / float(n)
    bar_w = min(14.0 if compact else 24.0, band * (0.72 if compact else 0.62))
    totals = [int(r.get("lead", 0)) + int(r.get("co", 0)) for r in rows]
    top, step = nice_scale(max(totals) if totals else 0)
    base_y = mt + ph

    def y_of(v):
        return mt + ph * (1.0 - v / float(top))

    out = []
    attrs = 'class="viz viz-columns%s" viewBox="0 0 %d %d" role="group"' % (
        " viz-compact" if compact else "", width, height)
    if title_id:
        attrs += ' aria-labelledby="%s"' % title_id
    if desc_id:
        attrs += ' aria-describedby="%s"' % desc_id
    out.append("<svg %s>" % attrs)

    # Gridlines and y-axis labels (recessive, hairline, solid).
    v = step
    while v <= top:
        y = y_of(v)
        out.append('<line class="viz-grid" x1="%s" x2="%s" y1="%s" y2="%s"/>'
                   % (_f(ml), _f(width - mr), _f(y), _f(y)))
        out.append('<text class="viz-tick" x="%s" y="%s" text-anchor="end" dy="0.32em">%d</text>'
                   % (_f(ml - 6), _f(y), v))
        v += step
    out.append('<text class="viz-tick" x="%s" y="%s" text-anchor="end" dy="0.32em">0</text>'
               % (_f(ml - 6), _f(base_y)))

    # Which years get an x label.
    label_idx = set()
    if compact and band >= 34:
        label_idx = set(range(n))  # room for every year (the usual 8-year window)
    elif compact:
        last_x = -1e9
        for i, r in enumerate(rows):
            yr = int(r["year"])
            want = i == 0 or i == n - 1 or yr % 5 == 0
            cx = ml + band * (i + 0.5)
            if want and cx - last_x >= 30:
                label_idx.add(i)
                last_x = cx
        # Always keep the final year even if it crowds the previous label.
        if n - 1 not in label_idx:
            label_idx.add(n - 1)
            prev = sorted(label_idx)[-2] if len(label_idx) > 1 else None
            if prev is not None and (n - 1 - prev) * band < 30:
                label_idx.discard(prev)
    else:
        every = 1 if band >= 30 else 2
        for i in range(n):
            if i % every == 0 or i == n - 1:
                label_idx.add(i)

    peak = max(totals) if totals else 0
    peak_i = max(range(n), key=lambda i: (totals[i], rows[i]["year"])) if peak > 0 else None

    for i, r in enumerate(rows):
        yr = int(r["year"])
        lead = int(r.get("lead", 0))
        co = int(r.get("co", 0))
        total = lead + co
        x = ml + band * i + (band - bar_w) / 2.0
        cx = ml + band * (i + 0.5)
        tip = [str(yr), [str(total), _plural(total, "work", "works")],
               [str(lead), "first or senior author", "lead"], [str(co), "co-author", "co"]]
        label = "%d: %d %s, %d first or senior author, %d co-author" % (
            yr, total, _plural(total, "work", "works"), lead, co)
        focus = ' tabindex="0" role="img" aria-label="%s"' % escape(label) if total else ' aria-hidden="true"'
        out.append('<g class="viz-col" data-tip="%s"%s>' % (_tip(tip), focus))
        out.append("<title>%s</title>" % escape(label))
        out.append('<rect class="viz-hit" x="%s" y="%s" width="%s" height="%s"/>'
                   % (_f(ml + band * i), _f(mt), _f(band), _f(ph)))
        lead_h = ph * lead / float(top)
        co_h = ph * co / float(top)
        if lead_h > 0:
            y_lead = base_y - lead_h
            r_top = 4.0 if co == 0 else 0.0
            out.append('<path class="viz-lead" d="%s"/>' % _column_path(x, y_lead, bar_w, lead_h, r_top))
        if co_h > 0:
            gap = 2.0 if lead_h > 0 else 0.0
            y_co_bottom = base_y - lead_h - gap
            h = max(0.0, co_h - gap) if lead_h > 0 else co_h
            if h > 0:
                out.append('<path class="viz-co" d="%s"/>' % _column_path(x, y_co_bottom - h, bar_w, h, 4.0))
        if i == peak_i:
            out.append('<text class="viz-value" x="%s" y="%s" text-anchor="middle">%d</text>'
                       % (_f(cx), _f(y_of(total) - 6), total))
        out.append("</g>")
        if i in label_idx:
            out.append('<text class="viz-tick" x="%s" y="%s" text-anchor="middle">%d</text>'
                       % (_f(cx), _f(base_y + 16), yr))

    # Baseline last so it sits over the column bases.
    out.append('<line class="viz-axis" x1="%s" x2="%s" y1="%s" y2="%s"/>'
               % (_f(ml), _f(width - mr), _f(base_y + 0.5), _f(base_y + 0.5)))
    out.append("</svg>")
    return Markup("".join(out))


def sparkline_svg(rows, highlight_year=None, partial_year=None, label="citations"):
    """Small bar sparkline: gray bars, the current period in the accent.

    rows: list of {"year": int, "value": int}.
    """
    rows = list(rows or [])
    if not rows:
        return Markup("")
    width, height = 168, 40
    mt, mb = 3, 1
    ph = height - mt - mb
    n = len(rows)
    band = width / float(n)
    bar_w = max(3.0, min(9.0, band - 4.0))
    vmax = max([int(r.get("value") or 0) for r in rows] + [1])
    out = ['<svg class="viz viz-spark" viewBox="0 0 %d %d" aria-hidden="true" focusable="false">' % (width, height)]
    for i, r in enumerate(rows):
        val = int(r.get("value") or 0)
        yr = int(r["year"])
        h = ph * val / float(vmax)
        x = band * i + (band - bar_w) / 2.0
        cls = "viz-lead" if yr == highlight_year else "viz-co"
        when = "%d%s" % (yr, " (to date)" if yr == partial_year else "")
        tip = [when, [str(val), label]]
        out.append('<g class="viz-col" data-tip="%s"><title>%s: %d %s</title>' % (_tip(tip), escape(when), val, escape(label)))
        out.append('<rect class="viz-hit" x="%s" y="0" width="%s" height="%d"/>' % (_f(band * i), _f(band), height))
        if h > 0:
            out.append('<path class="%s" d="%s"/>' % (cls, _column_path(x, mt + ph - h, bar_w, h, 2.0)))
        out.append("</g>")
    out.append('<line class="viz-axis" x1="0" x2="%d" y1="%s" y2="%s"/>' % (width, _f(height - 0.5), _f(height - 0.5)))
    out.append("</svg>")
    return Markup("".join(out))


# --------------------------------------------------------------------------
# Colophon pipeline diagram
# --------------------------------------------------------------------------

PIPELINE_STEPS = [
    ("fetch.py", ["calls each API,", "keeps last copy"]),
    ("data/", ["JSON snapshots,", "committed weekly"]),
    ("build.py", ["pages, charts,", "BibTeX, feed"]),
    ("_site/", ["static HTML", "+ cv.pdf"]),
    ("GitHub Pages", ["zdozier.com"]),
]


def _arrow(x1, y1, x2, y2):
    """Hairline arrow with a small open head."""
    out = ['<line class="dg-line" x1="%s" y1="%s" x2="%s" y2="%s"/>' % (_f(x1), _f(y1), _f(x2), _f(y2))]
    if abs(x2 - x1) >= abs(y2 - y1):
        d = 1 if x2 > x1 else -1
        out.append('<path class="dg-head" d="M%s %sl%s -4M%s %sl%s 4"/>'
                   % (_f(x2), _f(y2), _f(-5 * d), _f(x2), _f(y2), _f(-5 * d)))
    else:
        d = 1 if y2 > y1 else -1
        out.append('<path class="dg-head" d="M%s %sl-4 %sM%s %sl4 %s"/>'
                   % (_f(x2), _f(y2), _f(-5 * d), _f(x2), _f(y2), _f(-5 * d)))
    return "".join(out)


def _status_dot(cx, cy, ok):
    return '<circle class="%s" cx="%s" cy="%s" r="3.5"/>' % ("dg-ok" if ok else "dg-warn", _f(cx), _f(cy))


def pipeline_svg(sources, compact=False, title_id=None):
    """Data flow from the public APIs to the published site.

    sources: list of (label, ok, used_cache) in display order.
    """
    sources = list(sources or [])
    if compact:
        return _pipeline_tall(sources, title_id)
    return _pipeline_wide(sources, title_id)


def _pipeline_wide(sources, title_id):
    W = 800
    src_w, node_w, gap = 120, 114, 20
    row_h = 19
    src_h = 34 + row_h * max(1, len(sources))
    cy = 40 + src_h / 2.0
    out = []
    # Sources box.
    y0 = cy - src_h / 2.0
    out.append('<rect class="dg-box" x="0.5" y="%s" width="%d" height="%s" rx="6"/>' % (_f(y0), src_w, _f(src_h)))
    out.append('<text class="dg-kicker" x="12" y="%s">Public APIs</text>' % _f(y0 + 20))
    for i, (label, ok, cached) in enumerate(sources):
        ty = y0 + 40 + i * row_h
        out.append(_status_dot(17, ty - 4, ok and not cached))
        out.append('<text class="dg-src" x="27" y="%s">%s%s</text>'
                   % (_f(ty), escape(label), " (cached)" if cached else ""))
    x = src_w
    prev_right = src_w
    node_h = 64
    centers = []
    for title, lines in PIPELINE_STEPS:
        x = prev_right + gap
        out.append(_arrow(prev_right + 2, cy, x - 2, cy))
        cls = "dg-box dg-accent" if title == "GitHub Pages" else "dg-box"
        out.append('<rect class="%s" x="%s" y="%s" width="%d" height="%d" rx="6"/>'
                   % (cls, _f(x + 0.5), _f(cy - node_h / 2.0), node_w, node_h))
        out.append('<text class="dg-title" x="%s" y="%s">%s</text>' % (_f(x + 10), _f(cy - 12), escape(title)))
        for j, line in enumerate(lines):
            out.append('<text class="dg-sub" x="%s" y="%s">%s</text>' % (_f(x + 10), _f(cy + 6 + j * 14), escape(line)))
        centers.append(x + node_w / 2.0)
        prev_right = x + node_w
    # content/*.yml feeds the data step from above.
    dx = centers[1]
    out.append('<rect class="dg-box dg-soft" x="%s" y="0.5" width="%d" height="26" rx="5"/>' % (_f(dx - 54), 108))
    out.append('<text class="dg-src" x="%s" y="18" text-anchor="middle">content/*.yml</text>' % _f(dx))
    out.append(_arrow(dx, 28, dx, cy - node_h / 2.0 - 2))
    out.append('<text class="dg-sub" x="%s" y="%s">merge.py</text>' % (_f(dx + 6), _f((28 + cy - node_h / 2.0) / 2.0 + 4)))
    # Schedule bracket under the automated steps.
    bx1 = src_w + gap
    bx2 = prev_right
    by = cy + node_h / 2.0 + 30
    out.append('<path class="dg-line" d="M%s %sv8H%sv-8"/>' % (_f(bx1), _f(by - 8), _f(bx2)))
    out.append('<text class="dg-sched" x="%s" y="%s" text-anchor="middle">GitHub Actions: every Monday 14:00 UTC, and when code or content changes</text>'
               % (_f((bx1 + bx2) / 2.0), _f(by + 18)))
    H = int(math.ceil(max(by + 26, cy + src_h / 2.0 + 4)))
    head = '<svg class="diagram" viewBox="0 0 %d %d" role="img"%s>' % (
        W, H, ' aria-labelledby="%s"' % title_id if title_id else "")
    return Markup(head + "".join(out) + "</svg>")


def _pipeline_tall(sources, title_id):
    W = 340
    out = []
    y = 0.5
    cols = 2
    row_h = 20
    nrows = int(math.ceil(len(sources) / float(cols))) if sources else 1
    src_h = 34 + nrows * row_h
    parts = []
    parts.append('<rect class="dg-box" x="0.5" y="%s" width="%d" height="%s" rx="6"/>' % (_f(y), W - 1, _f(src_h)))
    parts.append('<text class="dg-kicker" x="12" y="%s">Public APIs</text>' % _f(y + 20))
    for i, (label, ok, cached) in enumerate(sources):
        c = i % cols
        r = i // cols
        tx = 12 + c * (W / 2.0)
        ty = y + 42 + r * row_h
        parts.append(_status_dot(tx + 5, ty - 4, ok and not cached))
        parts.append('<text class="dg-src" x="%s" y="%s">%s%s</text>'
                     % (_f(tx + 15), _f(ty), escape(label), " (cached)" if cached else ""))
    y += src_h
    node_h = 48
    gap = 26
    for title, lines in PIPELINE_STEPS:
        parts.append(_arrow(W / 2.0, y + 2, W / 2.0, y + gap - 2))
        y += gap
        cls = "dg-box dg-accent" if title == "GitHub Pages" else "dg-box"
        parts.append('<rect class="%s" x="40.5" y="%s" width="%d" height="%d" rx="6"/>' % (cls, _f(y), W - 80, node_h))
        parts.append('<text class="dg-title" x="54" y="%s">%s</text>' % (_f(y + 20), escape(title)))
        parts.append('<text class="dg-sub" x="54" y="%s">%s</text>' % (_f(y + 36), escape(" ".join(lines))))
        if title == "data/":
            parts.append('<text class="dg-sub" x="%d" y="%s" text-anchor="end">+ content/*.yml</text>' % (W - 50, _f(y + 20)))
        y += node_h
    y += 22
    parts.append('<text class="dg-sched" x="%s" y="%s" text-anchor="middle">Mondays 14:00 UTC, and when code or content changes</text>'
                 % (_f(W / 2.0), _f(y)))
    H = int(math.ceil(y + 8))
    out.append('<svg class="diagram diagram-tall" viewBox="0 0 %d %d" role="img"%s>'
               % (W, H, ' aria-labelledby="%s"' % title_id if title_id else ""))
    out.extend(parts)
    out.append("</svg>")
    return Markup("".join(out))
