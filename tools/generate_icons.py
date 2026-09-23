"""Generate the icon tiles for services `diagrams` does not ship.

Run this only when adding or changing an icon; the PNGs it writes are committed
to `archlens/render/assets/` so the package has no Pillow dependency at runtime:

    python tools/generate_icons.py

**These are role glyphs, not vendor logos.** Each tile pairs a generic symbol
for what the service *does* (a card for payments, an envelope for email, a
cylinder for a datastore) with that vendor's brand colour, so it reads at a
glance without reproducing anyone's trademark. Drawing the real logo would be a
trademark problem; borrowing a different company's logo - which this project
did for Stripe at one point, showing Facebook's mark - is simply wrong.

Style matches the AWS icon set so custom tiles sit beside official ones without
looking foreign: a rounded square, a flat brand colour, white line art.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Draw at 4x and downsample - PIL's drawing primitives are not antialiased.
SCALE = 4
OUT_SIZE = 256
SIZE = OUT_SIZE * SCALE
CORNER = int(SIZE * 0.18)
STROKE = int(SIZE * 0.045)
WHITE = (255, 255, 255, 255)

ASSETS = Path(__file__).resolve().parent.parent / "archlens" / "render" / "assets"


def _tile(colour: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=CORNER, fill=colour)
    return image, draw


def _save(image: Image.Image, name: str) -> Path:
    path = ASSETS / f"{name}.png"
    image.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS).save(path, "PNG", optimize=True)
    return path


def _box(fraction: float) -> tuple[int, int, int, int]:
    """A centred square occupying `fraction` of the tile."""
    inset = int(SIZE * (1 - fraction) / 2)
    return inset, inset, SIZE - inset, SIZE - inset


# --------------------------------------------------------------------------- #
# Glyphs
# --------------------------------------------------------------------------- #


def payments(colour: str) -> Image.Image:
    """A payment card: rounded rect, magnetic stripe, contact chip."""
    image, draw = _tile(colour)
    left, top, right, bottom = _box(0.62)
    top += int(SIZE * 0.06)
    bottom -= int(SIZE * 0.06)
    draw.rounded_rectangle([left, top, right, bottom], radius=int(SIZE * 0.05),
                           outline=WHITE, width=STROKE)
    band = top + int((bottom - top) * 0.28)
    draw.rectangle([left + STROKE, band, right - STROKE, band + int(SIZE * 0.07)], fill=WHITE)
    chip_y = band + int(SIZE * 0.15)
    draw.rounded_rectangle(
        [left + int(SIZE * 0.05), chip_y, left + int(SIZE * 0.16), chip_y + int(SIZE * 0.08)],
        radius=int(SIZE * 0.015), fill=WHITE,
    )
    return image


def email(colour: str) -> Image.Image:
    """An envelope."""
    image, draw = _tile(colour)
    left, top, right, bottom = _box(0.60)
    top += int(SIZE * 0.07)
    bottom -= int(SIZE * 0.07)
    draw.rounded_rectangle([left, top, right, bottom], radius=int(SIZE * 0.035),
                           outline=WHITE, width=STROKE)
    mid_x = (left + right) // 2
    flap_y = top + int((bottom - top) * 0.62)
    draw.line([left + STROKE // 2, top + STROKE // 2, mid_x, flap_y], fill=WHITE, width=STROKE)
    draw.line([right - STROKE // 2, top + STROKE // 2, mid_x, flap_y], fill=WHITE, width=STROKE)
    return image


def spark(colour: str) -> Image.Image:
    """A four-point spark - the common shorthand for a generative model."""
    image, draw = _tile(colour)
    cx = cy = SIZE // 2
    outer = int(SIZE * 0.30)
    waist = int(SIZE * 0.075)
    draw.polygon(
        [(cx, cy - outer), (cx + waist, cy - waist), (cx + outer, cy),
         (cx + waist, cy + waist), (cx, cy + outer), (cx - waist, cy + waist),
         (cx - outer, cy), (cx - waist, cy - waist)],
        fill=WHITE,
    )
    small = int(SIZE * 0.10)
    sx, sy = cx + int(SIZE * 0.24), cy - int(SIZE * 0.24)
    draw.polygon(
        [(sx, sy - small), (sx + small // 3, sy), (sx + small, sy),
         (sx + small // 3, sy), (sx, sy + small), (sx - small // 3, sy),
         (sx - small, sy), (sx - small // 3, sy)],
        fill=WHITE,
    )
    return image


def model(colour: str) -> Image.Image:
    """A small neural network: three inputs fanning into two outputs."""
    image, draw = _tile(colour)
    radius = int(SIZE * 0.045)
    left_x, right_x = int(SIZE * 0.30), int(SIZE * 0.70)
    lefts = [int(SIZE * y) for y in (0.28, 0.50, 0.72)]
    rights = [int(SIZE * y) for y in (0.39, 0.61)]

    for ly in lefts:
        for ry in rights:
            draw.line([left_x, ly, right_x, ry], fill=WHITE, width=int(STROKE * 0.55))
    for ly in lefts:
        draw.ellipse([left_x - radius, ly - radius, left_x + radius, ly + radius], fill=WHITE)
    for ry in rights:
        draw.ellipse([right_x - radius, ry - radius, right_x + radius, ry + radius], fill=WHITE)
    return image


def vector_store(colour: str) -> Image.Image:
    """A database cylinder with vector dots - a vector index."""
    image, draw = _tile(colour)
    left, right = int(SIZE * 0.28), int(SIZE * 0.72)
    top, bottom = int(SIZE * 0.28), int(SIZE * 0.72)
    ellipse_h = int(SIZE * 0.12)

    draw.ellipse([left, top, right, top + ellipse_h], outline=WHITE, width=STROKE)
    draw.line([left, top + ellipse_h // 2, left, bottom - ellipse_h // 2], fill=WHITE, width=STROKE)
    draw.line([right, top + ellipse_h // 2, right, bottom - ellipse_h // 2], fill=WHITE, width=STROKE)
    draw.arc([left, bottom - ellipse_h, right, bottom], start=0, end=180, fill=WHITE, width=STROKE)

    dot = int(SIZE * 0.022)
    for dx, dy in ((0.40, 0.47), (0.50, 0.53), (0.60, 0.46)):
        cx, cy = int(SIZE * dx), int(SIZE * dy)
        draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=WHITE)
    return image


def chain(colour: str) -> Image.Image:
    """Two interlocking links - an orchestration framework."""
    image, draw = _tile(colour)
    width = int(SIZE * 0.26)
    height = int(SIZE * 0.17)
    radius = height // 2
    offset = int(SIZE * 0.085)
    cx = cy = SIZE // 2
    draw.rounded_rectangle(
        [cx - width, cy - height - offset // 2, cx + offset, cy + height - offset // 2],
        radius=radius, outline=WHITE, width=STROKE,
    )
    draw.rounded_rectangle(
        [cx - offset, cy - height + offset // 2, cx + width, cy + height + offset // 2],
        radius=radius, outline=WHITE, width=STROKE,
    )
    return image


def token(colour: str) -> Image.Image:
    """A shield with a keyhole - an auth token or credential."""
    image, draw = _tile(colour)
    top, bottom = int(SIZE * 0.26), int(SIZE * 0.76)
    left, right = int(SIZE * 0.30), int(SIZE * 0.70)
    cx = SIZE // 2
    draw.polygon(
        [(cx, top), (right, top + int(SIZE * 0.07)), (right, int(SIZE * 0.53)),
         (cx, bottom), (left, int(SIZE * 0.53)), (left, top + int(SIZE * 0.07))],
        outline=WHITE, width=STROKE,
    )
    hole = int(SIZE * 0.045)
    hy = int(SIZE * 0.46)
    draw.ellipse([cx - hole, hy - hole, cx + hole, hy + hole], fill=WHITE)
    draw.rectangle([cx - hole // 2, hy, cx + hole // 2, hy + int(SIZE * 0.09)], fill=WHITE)
    return image


def search(colour: str) -> Image.Image:
    """A magnifier - a hosted search service."""
    image, draw = _tile(colour)
    cx, cy = int(SIZE * 0.45), int(SIZE * 0.44)
    radius = int(SIZE * 0.16)
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                 outline=WHITE, width=STROKE)
    draw.line([cx + int(radius * 0.72), cy + int(radius * 0.72),
               int(SIZE * 0.70), int(SIZE * 0.70)], fill=WHITE, width=int(STROKE * 1.2))
    return image


def dataframe(colour: str) -> Image.Image:
    """A table - tabular data processing."""
    image, draw = _tile(colour)
    left, top, right, bottom = _box(0.52)
    draw.rounded_rectangle([left, top, right, bottom], radius=int(SIZE * 0.03),
                           outline=WHITE, width=STROKE)
    header = top + int((bottom - top) * 0.30)
    draw.rectangle([left, top, right, header], fill=WHITE)
    draw.line([left, header + (bottom - header) // 2, right, header + (bottom - header) // 2],
              fill=WHITE, width=int(STROKE * 0.7))
    mid_x = (left + right) // 2
    draw.line([mid_x, header, mid_x, bottom], fill=WHITE, width=int(STROKE * 0.7))
    return image


# name -> (glyph, brand-ish colour)
ICONS: dict[str, tuple] = {
    "stripe":       (payments,     "#635BFF"),
    "sendgrid":     (email,        "#1A82E2"),
    "anthropic":    (spark,        "#D97757"),
    "openai":       (model,        "#10A37F"),
    "vectordb":     (vector_store, "#7C3AED"),
    "llmframework": (chain,        "#0EA5E9"),
    "ml":           (model,        "#EC4899"),
    "jwt":          (token,        "#475569"),
    "algolia":      (search,       "#003DFF"),
    "dataframe":    (dataframe,    "#0F766E"),
}


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, (glyph, colour) in ICONS.items():
        path = _save(glyph(colour), name)
        print(f"  wrote {path.relative_to(ASSETS.parent.parent.parent)} ({path.stat().st_size:,} bytes)")
    print(f"\n{len(ICONS)} icons written to {ASSETS}")


if __name__ == "__main__":
    main()
