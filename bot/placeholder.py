from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from bot.config import ROOT

PLACEHOLDER_PATH = ROOT / "assets" / "removed.webp"


def ensure_placeholder_file() -> Path:
    """Return the default removed-sticker WEBP, creating a fallback icon if needed."""
    PLACEHOLDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PLACEHOLDER_PATH.exists() and PLACEHOLDER_PATH.stat().st_size > 0:
        return PLACEHOLDER_PATH

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    red = (227, 30, 36, 255)
    margin = 28
    draw.ellipse([margin, margin, size - margin - 1, size - margin - 1], outline=red, width=42)

    bar_w, bar_h = 390, 44
    bar = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle([0, 0, bar_w - 1, bar_h - 1], radius=22, fill=red)
    bar = bar.rotate(-45, expand=True, resample=Image.Resampling.BICUBIC)
    bx = (size - bar.width) // 2
    by = (size - bar.height) // 2
    img.alpha_composite(ring)
    img.alpha_composite(bar, (bx, by))
    img.save(PLACEHOLDER_PATH, "WEBP", lossless=True)
    return PLACEHOLDER_PATH
