"""render.py — turn the REAL Forge board snapshots (ForgeVsBot -Ddump JSONL) into a tabletop-style mp4.

    python3 forge_integration/render.py /tmp/game.jsonl /tmp/game.mp4 [fps]

Each JSONL line is one phase: turn, phase, active, and both players' life / hand / library / graveyard
counts + battlefield (cards with kind/tapped/power/toughness). We draw a simple two-sided tabletop (opponent
on top, our engine on the bottom), one frame per snapshot, and ffmpeg-encode to mp4. Needs Pillow + ffmpeg.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
CARD_W, CARD_H = 78, 104
BG = (24, 88, 52)               # felt green
LAND_COLOR = {"forest": (60, 120, 60), "mountain": (150, 70, 55), "island": (60, 95, 150),
              "swamp": (60, 60, 70), "plains": (200, 195, 160)}
CREATURE = (205, 180, 130)
OTHER = (140, 140, 150)


def _font(sz):
    for p in ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


F_SM, F_MD, F_LG, F_XL = _font(12), _font(16), _font(22), _font(40)


def _card_color(c):
    if c["kind"] == "land":
        return LAND_COLOR.get(c["name"].split()[-1].lower(), (90, 110, 90))
    if c["kind"] == "creature":
        return CREATURE
    return OTHER


def _rounded(d, xy, r, fill, outline=(20, 20, 20), width=2):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def _draw_card(img, d, x, y, c):
    tapped = c.get("tapped")
    w, h = (CARD_H, CARD_W) if tapped else (CARD_W, CARD_H)     # tapped = sideways
    col = _card_color(c)
    if tapped:
        col = tuple(int(v * 0.7) for v in col)
    _rounded(d, [x, y, x + w, y + h], 8, col)
    name = c["name"]
    short = name if len(name) <= 11 else name[:10] + "…"
    d.text((x + 5, y + 5), short, font=F_SM, fill=(15, 15, 15))
    if c["kind"] == "creature" and "pow" in c:
        d.text((x + w - 30, y + h - 18), f'{c["pow"]}/{c["tou"]}', font=F_SM, fill=(15, 15, 15))


def _draw_side(img, d, player, top, mine):
    label = ("▶ " if mine else "") + player["name"]
    accent = (235, 225, 120) if mine else (235, 235, 245)
    d.text((20, top + 6), label, font=F_MD, fill=accent)
    # life + zone counts
    d.text((20, top + 30), f'♥ {player["life"]}', font=F_LG, fill=(255, 120, 120))
    info = f'hand {player["hand"]}   lib {player["library"]}   gy {player["graveyard"]}'
    d.text((120, top + 36), info, font=F_SM, fill=(220, 220, 220))
    # facedown hand (small stack on the right)
    for i in range(min(player["hand"], 10)):
        hx = W - 180 + i * 14
        _rounded(d, [hx, top + 18, hx + 26, top + 56], 4, (40, 40, 90), outline=(15, 15, 15), width=1)
    # battlefield: lands row then creatures row (so the layout reads like a real board)
    bf = player["battlefield"]
    lands = [c for c in bf if c["kind"] == "land"]
    rest = [c for c in bf if c["kind"] != "land"]
    row_creatures = top + 66
    row_lands = top + 66 + CARD_H + 8
    for i, c in enumerate(rest[:14]):
        _draw_card(img, d, 24 + i * (CARD_W + 8), row_creatures, c)
    for i, c in enumerate(lands[:16]):
        _draw_card(img, d, 24 + i * (CARD_H + 6), row_lands + (CARD_W if False else 0), {**c})


def render_frame(snap, out_path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    players = snap["players"]
    # our engine seat is "Witchcraft-Engine"; show it on the bottom.
    mine_name = "Witchcraft-Engine"
    opp = next((p for p in players if p["name"] != mine_name), players[0])
    me = next((p for p in players if p["name"] == mine_name), players[-1])
    _draw_side(img, d, opp, top=10, mine=False)
    d.line([(0, H // 2), (W, H // 2)], fill=(15, 50, 30), width=3)
    # center banner
    banner = f'Turn {snap["turn"]} — {snap["phase"]}   (active: {snap["active"]})'
    d.rectangle([0, H // 2 - 16, W, H // 2 + 16], fill=(15, 50, 30))
    d.text((20, H // 2 - 12), banner, font=F_MD, fill=(245, 245, 200))
    _draw_side(img, d, me, top=H // 2 + 24, mine=True)
    img.save(out_path)


def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else "/tmp/game.jsonl"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/game.mp4"
    fps = sys.argv[3] if len(sys.argv) > 3 else "2"
    snaps = [json.loads(l) for l in open(inp) if l.strip()]
    if not snaps:
        print("no snapshots in", inp); return
    with tempfile.TemporaryDirectory() as td:
        for i, s in enumerate(snaps):
            render_frame(s, os.path.join(td, f"f{i:05d}.png"))
        # this ffmpeg build has libopenh264 (not libx264) — both produce mp4-compatible H.264.
        subprocess.run(["ffmpeg", "-y", "-framerate", fps, "-i", os.path.join(td, "f%05d.png"),
                        "-c:v", "libopenh264", "-pix_fmt", "yuv420p", out],
                       check=True, capture_output=True)
    print(f"wrote {out} ({len(snaps)} frames @ {fps}fps)")


if __name__ == "__main__":
    main()
