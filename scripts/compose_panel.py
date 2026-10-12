#!/usr/bin/env python3
"""Compose a reference-style two-panel docking figure (main + zoom + leader lines).

Reproduces the academic layout: left overview with a thin-black rectangle marking
the binding site, two straight thin-black leader lines from the box's top-right and
bottom-right corners to the zoom frame's top-left and bottom-left corners (trapezoid),
the zoomed view enclosed by a thin black frame, a panel letter (A/B) top-left,
the system name "PDBID-Protein_Ligand" centered below, and a red parameter box
bottom-right.

Example:
  python compose_panel.py \
      --main overall.png --zoom detail.png \
      --box 930,400,1740,1200 --box-src 3200x2200 \
      --panel A --name "2Z64-TLR4/MD2_S100A8" \
      --red "Interface metrics|I73-MD-2  3.8 A@0x258CD9|K77-MD-2  4.1 A@0x666666" \
      --out panel.png
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFont

FONT_A = r"C:\Windows\Fonts\arial.ttf"
FONT_AB = r"C:\Windows\Fonts\arialbd.ttf"
FALLBACKS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(FALLBACKS[0 if "bd" not in path else 1], size)
        except Exception:
            return ImageFont.load_default()


def load_resized(path, width):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    nh = int(h * width / w)
    return im.resize((width, nh), Image.LANCZOS)


def scale_box(box, src, dst):
    sx, sy = src
    dx, dy = dst
    return (int(box[0] * dx / sx), int(box[1] * dy / sy),
            int(box[2] * dx / sx), int(box[3] * dy / sy))


def parse_red(spec):
    """Lines separated by |; each line 'text' or 'text@RRGGBB'."""
    lines = []
    for part in spec.split("|"):
        if "@" in part:
            text, color = part.rsplit("@", 1)
            lines.append((text, tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))))
        else:
            lines.append((part, (30, 30, 30)))
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--main", required=True, help="overview figure PNG")
    p.add_argument("--zoom", required=True, help="zoomed interface PNG")
    p.add_argument("--box", required=True,
                   help="binding-site box 'x0,y0,x1,y1' in --box-src coordinates")
    p.add_argument("--box-src", default="3200x2200",
                   help="native size of --main (WxH), e.g. 3200x2200")
    p.add_argument("--panel", required=True, help="panel letter, e.g. A or B")
    p.add_argument("--name", required=True,
                   help="system name under the figure, e.g. 2Z64-TLR4/MD2_S100A8")
    p.add_argument("--red", default=None,
                   help="red-box lines: 'title|line1@RRGGBB|line2' (first = small title)")
    p.add_argument("--img-width", type=int, default=1420)
    p.add_argument("--gap", type=int, default=260)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    img_w = args.img_width
    margin = 60
    top = 140
    src = tuple(int(v) for v in args.box_src.lower().split("x"))

    main = load_resized(args.main, img_w)
    zoom = load_resized(args.zoom, img_w)
    ih = main.size[1]
    W = margin * 2 + img_w * 2 + args.gap
    H = top + ih + 150

    canvas = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(canvas)

    x_main = margin
    x_zoom = margin + img_w + args.gap
    canvas.paste(main, (x_main, top))
    canvas.paste(zoom, (x_zoom, top))

    # thin black binding-site box on the main figure
    box = tuple(int(v) for v in args.box.split(","))
    ib = scale_box(box, src, (img_w, ih))
    bx = (ib[0] + x_main, ib[1] + top, ib[2] + x_main, ib[3] + top)
    d.rectangle(bx, outline=(0, 0, 0), width=4)

    # zoom outer frame (thin black)
    fz = (x_zoom - 8, top - 8, x_zoom + img_w + 8, top + ih + 8)
    d.rectangle(fz, outline=(0, 0, 0), width=4)

    # trapezoid leader lines
    d.line([(bx[2], bx[1]), (fz[0], fz[1])], fill=(0, 0, 0), width=3)
    d.line([(bx[2], bx[3]), (fz[0], fz[3])], fill=(0, 0, 0), width=3)

    # panel letter
    d.ellipse([margin + 10, 20, margin + 95, 105], fill=(0, 0, 0))
    d.text((margin + 38, 33), args.panel, font=font(FONT_AB, 48), fill=(255, 255, 255))

    # system name centered
    f_name = font(FONT_AB, 34)
    nw = d.textlength(args.name, font=f_name)
    d.text(((W - nw) // 2, top + ih + 35), args.name, font=f_name, fill=(30, 30, 30))

    # red parameter box
    if args.red:
        lines = parse_red(args.red)
        title, body = lines[0], lines[1:]
        red_w = 430
        red_h = 40 + 50 * len(body) + 30
        rx = W - red_w - margin
        ry = H - red_h - 30
        d.rounded_rectangle([rx, ry, rx + red_w, ry + red_h], radius=14,
                            outline=(0xD8, 0x28, 0x28), width=5)
        d.text((rx + 22, ry + 14), title[0], font=font(FONT_A, 19), fill=(0xD8, 0x28, 0x28))
        yy = ry + 52
        for text, color in body:
            d.text((rx + 22, yy), text, font=font(FONT_AB, 26), fill=color)
            yy += 48

    canvas.save(args.out, dpi=(300, 300))
    print(f"Saved {args.out} ({W}x{H})")


if __name__ == "__main__":
    main()
