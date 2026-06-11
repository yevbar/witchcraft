"""render.py — turn the REAL Forge board snapshots (ForgeVsBot / ForgeComboKill -Ddump JSONL) into a
tabletop-style mp4.

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

W, H = 1280, 820
CARD_W, CARD_H = 74, 98
HAND_W, HAND_H = 58, 80         # hand cards are a touch smaller than the battlefield
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


def _draw_card(img, d, x, y, c, w=CARD_W, h=CARD_H):
    tapped = c.get("tapped")
    if tapped:
        w, h = h, w                                            # tapped = sideways
    col = _card_color(c)
    if tapped:
        col = tuple(int(v * 0.7) for v in col)
    _rounded(d, [x, y, x + w, y + h], 7, col)
    name = c["name"]
    cap = max(6, int(w / 6.5))
    short = name if len(name) <= cap else name[:cap - 1] + "…"
    d.text((x + 4, y + 4), short, font=F_SM, fill=(15, 15, 15))
    if c["kind"] == "creature" and "pow" in c:
        d.text((x + w - 28, y + h - 16), f'{c["pow"]}/{c["tou"]}', font=F_SM, fill=(15, 15, 15))


def _row(img, d, cards, x0, y, w, h, gap, cap):
    """Draw a left-to-right row of cards, with a '+N' marker if it overflows the cap."""
    for i, c in enumerate(cards[:cap]):
        _draw_card(img, d, x0 + i * (w + gap), y, c, w, h)
    if len(cards) > cap:
        d.text((x0 + cap * (w + gap) + 4, y + h // 2), f'+{len(cards) - cap}', font=F_MD, fill=(230, 230, 230))


def _draw_side(img, d, player, top, mine):
    label = ("▶ " if mine else "") + player["name"]
    accent = (235, 225, 120) if mine else (235, 235, 245)
    d.text((20, top + 4), label, font=F_MD, fill=accent)
    d.text((230, top + 2), f'♥ {player["life"]}', font=F_LG, fill=(255, 120, 120))
    d.text((320, top + 8), f'lib {player["library"]}   gy {player["graveyard"]}', font=F_SM, fill=(210, 210, 210))
    # HAND — face-up cards (the Dumper is a spectator, so both hands are visible)
    d.text((20, top + 30), f'hand ({player["hand"]})', font=F_SM, fill=(200, 220, 200))
    hand = player.get("handcards", [])
    _row(img, d, hand, 100, top + 28, HAND_W, HAND_H, 6, cap=12)
    # BATTLEFIELD — creatures row then lands row
    bf = player["battlefield"]
    creatures = [c for c in bf if c["kind"] != "land"]
    lands = [c for c in bf if c["kind"] == "land"]
    row_creatures = top + 28 + HAND_H + 8
    row_lands = row_creatures + CARD_H + 6
    _row(img, d, creatures, 20, row_creatures, CARD_W, CARD_H, 8, cap=14)
    _row(img, d, lands, 20, row_lands, CARD_W, CARD_H, 6, cap=16)


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
