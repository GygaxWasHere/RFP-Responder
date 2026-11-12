"""Generate the RFP Responder background art (Svensk Tenn x radar/transponder) into responder/web/img/.

  wallpaper.svg - dark botanical wallpaper tile (Apollo N6 base, P13/P23 flowers, A36 berries)
  radar.svg     - a radar scope overgrown with Josef Frank-style vines. Questions go out as yellow
                  "Q" blips, the transponder flower answers with "A" rings. The sweep turns slowly.

Run:  python tools/make_background.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svensk_tenn_pattern import _bird, _flower, _stem, botanical  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "responder", "web", "img")

N6, N13, N17 = "#0D1012", "#1C2326", "#252E32"
P13, P23, P28, P33, P43, P53 = "#002F42", "#005275", "#00648F", "#0076A8", "#0099DA", "#0FB8FF"
A36, A50 = "#B8A600", "#FFE700"


def wallpaper():
    pat = botanical("w", N13, N13, P13, "#00405C", "#6E6400", 1.2, 1, leaf2="#151C1F", bird=True)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="384" height="384">{pat[pat.index("<defs>"):pat.rindex("</svg>")]}'
           f'<rect width="384" height="384" fill="{N6}"/><rect width="384" height="384" fill="url(#w)"/></svg>')
    open(os.path.join(OUT, "wallpaper.svg"), "w", encoding="utf-8").write(svg)


def bubble(x, y, letter, fill, txt, s=1.0, tail=1):
    return (f'<g transform="translate({x} {y}) scale({s})"><path d="M-17 -15 h34 a6 6 0 0 1 6 6 v18 a6 6 0 0 1 -6 6 '
            f'h-{12 if tail > 0 else 22} l{-8 * tail} 9 l2 -9 h-{14 if tail > 0 else 4} a6 6 0 0 1 -6 -6 v-18 a6 6 0 0 1 6 -6z" '
            f'fill="{fill}"/><text x="0" y="6" text-anchor="middle" font-family="Segoe UI, Open Sans, sans-serif" '
            f'font-size="18" font-weight="700" fill="{txt}">{letter}</text></g>')


def radar():
    W = H = 900
    cx = cy = 450
    parts = []
    parts.append('<defs><radialGradient id="glow" cx="50%" cy="50%" r="50%">'
                 f'<stop offset="0" stop-color="{P23}" stop-opacity=".35"/><stop offset="1" stop-color="{P13}" stop-opacity="0"/>'
                 '</radialGradient><linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">'
                 f'<stop offset="0" stop-color="{P43}" stop-opacity="0"/><stop offset="1" stop-color="{P53}" stop-opacity=".45"/>'
                 '</linearGradient></defs>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="340" fill="url(#glow)"/>')
    for r, op in ((330, .55), (250, .45), (170, .4), (90, .35)):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{P28}" stroke-opacity="{op}" stroke-width="1.5"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="345" fill="none" stroke="{P33}" stroke-opacity=".5" stroke-width="1" stroke-dasharray="2 9"/>')
    for a in range(0, 360, 30):
        x2, y2 = cx + 330 * math.cos(math.radians(a)), cy + 330 * math.sin(math.radians(a))
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{P28}" stroke-opacity=".22"/>')
    # sweep wedge (rotates)
    a0, a1 = math.radians(-38), 0
    parts.append(f'<g><path d="M{cx} {cy} L{cx + 330 * math.cos(a0):.1f} {cy + 330 * math.sin(a0):.1f} '
                 f'A330 330 0 0 1 {cx + 330:.1f} {cy} z" fill="url(#sweep)"/>'
                 f'<line x1="{cx}" y1="{cy}" x2="{cx + 330}" y2="{cy}" stroke="{P53}" stroke-opacity=".7" stroke-width="2"/>'
                 f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" '
                 'dur="14s" repeatCount="indefinite"/></g>')
    # vines growing around the rim of the scope (Josef Frank style)
    vines = [
        ([(120, 760), (60, 600), (90, 430), (150, 300)], 9, 15),
        ([(150, 300), (200, 190), (300, 120), (420, 95)], 7, 13),
        ([(780, 800), (860, 650), (850, 480), (790, 330)], 8, 14),
        ([(560, 880), (650, 860), (720, 830), (780, 800)], 5, 11),
    ]
    stems, leaves = "", ""
    for i, (p, n, sz) in enumerate(vines):
        d, lv = _stem(p, n, .06, .94, P23 if i % 2 else "#00405C", sz)
        stems += f'<path d="{d}" fill="none" stroke="{P23}" stroke-width="2.4"/>'
        leaves += lv
    parts.append(stems + leaves)
    parts.append(_flower(150, 300, 34, 8, P33, P23, A50))
    parts.append(_flower(420, 95, 22, 6, P23, P33, A36))
    parts.append(_flower(790, 330, 28, 7, P33, P23, A50))
    parts.append(_flower(120, 760, 20, 6, P23, P33, A36))
    # blips = small flowers on the scope (questions found by the sweep)
    for (r, a, s) in ((250, -60, 9), (170, 200, 8), (300, 110, 7), (90, 20, 6), (320, -150, 6)):
        x, y = cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
        parts.append(_flower(round(x, 1), round(y, 1), s * 2, 6, A36, A50, "#FFFFFF"))
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{s * 2.4}" fill="none" stroke="{A50}" stroke-opacity=".6">'
                     f'<animate attributeName="r" values="{s * 1.2};{s * 4}" dur="3.5s" begin="{(a % 7) * .4:.1f}s" repeatCount="indefinite"/>'
                     f'<animate attributeName="stroke-opacity" values=".7;0" dur="3.5s" begin="{(a % 7) * .4:.1f}s" repeatCount="indefinite"/></circle>')
    # question goes out, answer comes back
    parts.append(bubble(cx + 250 * math.cos(math.radians(-60)) + 34, cy + 250 * math.sin(math.radians(-60)) - 30, "Q", A50, "#070809", 1.1, 1))
    # transponder: a tulip-like flower at the centre that answers with rings
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="16" fill="{P43}"/>')
    parts.append(_flower(cx, cy, 26, 8, P43, P33, A50))
    for k in range(3):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="30" fill="none" stroke="{P53}" stroke-width="2">'
                     f'<animate attributeName="r" values="30;150" dur="4.5s" begin="{k * 1.5}s" repeatCount="indefinite"/>'
                     f'<animate attributeName="stroke-opacity" values=".8;0" dur="4.5s" begin="{k * 1.5}s" repeatCount="indefinite"/></circle>')
    parts.append(bubble(cx - 70, cy - 70, "A", P43, "#070809", 1.1, -1))
    # birds: one brings a question, one leaves with the answer
    parts.append(_bird(250, 610, 1.2, P33, P23, 1))
    parts.append(bubble(250 + 52, 610 - 34, "?", A50, "#070809", .75, 1))
    parts.append(_bird(700, 210, 1.0, P33, P23, -1))
    parts.append(bubble(700 - 48, 210 - 32, "✓", P43, "#070809", .7, -1))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
           + "".join(parts) + "</svg>")
    open(os.path.join(OUT, "radar.svg"), "w", encoding="utf-8").write(svg)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    wallpaper()
    radar()
    print("written to", os.path.abspath(OUT))
