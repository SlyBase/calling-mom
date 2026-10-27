#!/usr/bin/env python3
"""Generate the calling-mom plugin assets in the Hermes printmaking language
on the SlyBase product palette.

Output: assets/calling-mom-icon.svg, assets/calling-mom-banner.svg, preview.html
Run:    python3 tools/make_assets.py
"""
import math
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

ORANGE, NAVY, AMBER, RUST, CREAM = "#ed8024", "#131a29", "#f6b853", "#c2611e", "#fff3e6"
MUTED = "#b1ada6"
FONT = "Inter, 'Helvetica Neue', Arial, sans-serif"  # SlyBase brand face: labels, body
FONT_DISPLAY = "'Playfair Display', Georgia, serif"  # Hermes headline face: high-contrast serif
FOX_VB = "145 41 224 232"  # true bbox of the SlyBase fox mark + 2px air
FOX_AR = 224 / 232


def fox_symbol() -> str:
    """SlyBase fox mark, recoloured to the product palette (never redrawn)."""
    raw = (OUT / "slybase-fox.svg").read_text()
    body = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", raw, flags=re.S)
    body = body.replace('fill="#ED943D"', f'fill="{AMBER}"')
    body = body.replace('fill="#D57930"', f'fill="{RUST}"')
    return f'<symbol id="fox" viewBox="{FOX_VB}">{body}</symbol>'


def dial(cx: float, cy: float, r: float, stroke: str, hole_fill: str, sw: float) -> str:
    """Engraved rotary dial: rings, ten finger holes, hatched shadow."""
    p = [
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r * 0.845:.1f}" fill="none" stroke="{stroke}" stroke-width="{sw * 0.45:.1f}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r * 0.845:.1f}" fill="none" stroke="{stroke}" stroke-width="{sw * 0.45:.1f}" transform="rotate(9 {cx} {cy})" stroke-dasharray="{r*0.06:.1f} {r*0.09:.1f}"/>',
    ]
    hole_r, hole_ring = r * 0.142, r * 0.645
    for i in range(10):
        a = math.radians(60 + i * 30)  # hole 1 at 5 o'clock, clockwise round to hole 0 at 2 o'clock
        p.append(
            f'<circle cx="{cx + hole_ring*math.cos(a):.1f}" cy="{cy + hole_ring*math.sin(a):.1f}"'
            f' r="{hole_r:.1f}" fill="{hole_fill}" stroke="{stroke}" stroke-width="{sw*0.5:.1f}"/>'
        )
    p.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.20:.1f}" fill="none" stroke="{stroke}" stroke-width="{sw*0.7:.1f}"/>')
    p.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.055:.1f}" fill="{stroke}"/>')
    return "".join(p)


def hatch(cx, cy, r, colour, opacity=0.16, step=9):
    """Diagonal line shading, clipped to a disc — the printing-style shadow."""
    lines = "".join(
        f'<line x1="{cx-r}" y1="{cy-r+i*step}" x2="{cx+r}" y2="{cy-r+i*step-r*1.6}" />'
        for i in range(int(2 * r / step) + 1)
    )
    return (
        f'<clipPath id="c{cx}"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'
        f'<g clip-path="url(#c{cx})" stroke="{colour}" stroke-width="2" opacity="{opacity}">{lines}</g>'
    )


def rays(cx, cy, r0, r1, stroke, count=64, sw=2.4, base=0.30):
    out = []
    for i in range(count):
        a = 2 * math.pi * i / count
        op = base if i % 4 else base + 0.22
        w = sw if i % 4 else sw * 1.6
        out.append(
            f'<line x1="{cx + r0*math.cos(a):.1f}" y1="{cy + r0*math.sin(a):.1f}"'
            f' x2="{cx + r1*math.cos(a):.1f}" y2="{cy + r1*math.sin(a):.1f}"'
            f' stroke="{stroke}" stroke-width="{w:.1f}" opacity="{op:.2f}"/>'
        )
    return "".join(out)


def icon() -> str:
    badge_x, badge_y, badge_s, pad = 366, 366, 122, 16
    fox_h = badge_s - 2 * pad
    fox_w = fox_h * FOX_AR
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" role="img" aria-label="Calling mom plugin icon">
<defs>{fox_symbol()}</defs>
<rect width="512" height="512" rx="96" fill="{ORANGE}"/>
<rect x="20" y="20" width="472" height="472" rx="78" fill="none" stroke="{NAVY}" stroke-width="2.5" opacity="0.22"/>
{rays(256, 256, 158, 244, NAVY, count=56, sw=2.6, base=0.26)}
{hatch(256, 256, 150, NAVY, 0.10, 11)}
{dial(256, 256, 148, NAVY, CREAM, 9)}
<rect x="{badge_x}" y="{badge_y}" width="{badge_s}" height="{badge_s}" rx="28" fill="{NAVY}"/>
<use href="#fox" x="{badge_x + pad + (badge_s - 2*pad - fox_w)/2:.1f}" y="{badge_y + pad}" width="{fox_w:.1f}" height="{fox_h}"/>
</svg>
"""


def banner() -> str:
    dx, dy, dr = 1232, 258, 168
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 520" width="1600" height="520" role="img" aria-label="Calling mom plugin banner">
<defs>{fox_symbol()}</defs>
<rect width="1600" height="520" fill="{NAVY}"/>
{rays(dx, dy, dr * 1.2, 980, ORANGE, count=72, sw=2.2, base=0.12)}
{hatch(dx, dy, dr, CREAM, 0.07, 13)}
{dial(dx, dy, dr, CREAM, ORANGE, 7)}
<rect x="28" y="28" width="1544" height="464" fill="none" stroke="{ORANGE}" stroke-width="1.6" opacity="0.65"/>
<g stroke="{ORANGE}" stroke-width="1.4" opacity="0.35">
{"".join(f'<line x1="1240" y1="{452 + i*4}" x2="1540" y2="{452 + i*4}" opacity="{0.5 - i*0.09:.2f}"/>' for i in range(5))}
</g>
<rect x="104" y="150" width="96" height="4" fill="{ORANGE}"/>
<text x="104" y="204" font-family="{FONT}" font-size="17" font-weight="700" letter-spacing="5.5" fill="{AMBER}">HERMES PLUGIN</text>
<text x="100" y="330" font-family="{FONT_DISPLAY}" font-size="118" font-weight="800" letter-spacing="0.5" fill="{CREAM}">CALLING MOM</text>
<text x="104" y="392" font-family="{FONT}" font-size="22" font-weight="500" fill="{CREAM}" opacity="0.86">Hard Kanban cards get a second opinion.</text>
<text x="104" y="424" font-family="{FONT}" font-size="22" font-weight="500" fill="{MUTED}">Routed to the strong model. The worker never judges itself.</text>
<use href="#fox" x="102" y="443" width="38" height="39.4"/>
<text x="160" y="474" font-family="{FONT}" font-size="26" font-weight="700" fill="{CREAM}">SlyBase</text>
<text x="288" y="473" font-family="{FONT}" font-size="13" font-weight="700" letter-spacing="3.2" fill="{AMBER}">TECH &amp; AI THE SLY WAY</text>
</svg>
"""


def main() -> None:
    (OUT / "calling-mom-icon.svg").write_text(icon())
    (OUT / "calling-mom-banner.svg").write_text(banner())
    print("wrote", OUT / "calling-mom-icon.svg", OUT / "calling-mom-banner.svg")


if __name__ == "__main__":
    main()
