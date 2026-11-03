#!/usr/bin/env python3
"""Genera el branding NEVERDETH: logo transparente, icono .ico y banner para el menu.

Paleta:
  bronce  #C88A32   texto principal
  crema   #F2E1C3   subtitulo
  rojo    #B4463C   acento
  carbon  #16161C   fondo del menu (para el banner BMP, que no lleva alfa)

Salidas:
  docs/assets/neverdeth_logo.png          (transparente, README)
  docs/assets/neverdeth_logo_banner.bmp   (fondo carbon, banner del menu F1)
  config/neverdeth.ico                    (16..256 px)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJ = Path(__file__).resolve().parents[1]
OUT_LOGO = PROJ / "docs" / "assets" / "neverdeth_logo.png"
OUT_BANNER = PROJ / "docs" / "assets" / "neverdeth_logo_banner.bmp"
OUT_ICO = PROJ / "config" / "neverdeth.ico"

BRONZE = (200, 138, 50, 255)
CREAM = (242, 225, 195, 255)
RED = (180, 70, 60, 255)
CARBON = (22, 22, 28, 255)

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/bahnschrift.ttf", "Bahnschrift SemiBold"),
    ("C:/Windows/Fonts/bahnschrift.ttf", "Bahnschrift"),
    ("C:/Windows/Fonts/arialbd.ttf", None),
]


def load_font(size: int):
    for path, variation in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            f = ImageFont.truetype(path, size)
            if variation:
                try:
                    f.set_variation_by_name(variation)
                except Exception:
                    pass
            return f
        except Exception:
            continue
    return ImageFont.load_default()


def tracked(draw, xy, text, font, fill, tracking):
    """Dibuja texto con interletraje manual; devuelve el ancho total."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x - xy[0]


def build_logo() -> Image.Image:
    W, H = 1400, 380
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    word = load_font(190)
    sub = load_font(40)

    # Barra de acento a la izquierda
    d.rectangle([70, 96, 82, 284], fill=RED)

    # Wordmark con un leve "shadow" para dar volumen
    x0 = 118
    tracked(d, (x0 + 3, 91), "NEVERDETH", word, (0, 0, 0, 90), 6)
    width = tracked(d, (x0, 88), "NEVERDETH", word, BRONZE, 6)

    # Regla bajo el wordmark + subtitulo
    d.rectangle([x0 + 4, 300, x0 + 4 + width - 12, 303], fill=(200, 138, 50, 120))
    tracked(d, (x0 + 6, 314), "PC RECOMPILATION", sub, CREAM, 11)

    return img


def build_banner(logo: Image.Image) -> Image.Image:
    """Banner 520x44 sobre carbon solido (el menu lo pinta con LoadImage, sin alfa)."""
    W, H = 520, 44
    bmp = Image.new("RGB", (W, H), CARBON[:3])
    scaled = logo.resize((int(logo.width * (H - 4) / logo.height), H - 4), Image.LANCZOS)
    bmp.paste(scaled, ((W - scaled.width) // 2, 2), scaled)
    return bmp


def build_icon(logo: Image.Image) -> Image.Image:
    """Monograma N sobre carbon con marco bronce, cuadrado."""
    S = 256
    icon = Image.new("RGBA", (S, S), CARBON)
    d = ImageDraw.Draw(icon)
    d.rectangle([6, 6, S - 7, S - 7], outline=BRONZE, width=6)
    d.rectangle([18, 18, S - 19, S - 19], outline=(200, 138, 50, 90), width=2)
    n = load_font(150)
    tw = d.textlength("N", font=n)
    d.text(((S - tw) / 2, 40), "N", font=n, fill=BRONZE)
    d.rectangle([70, 206, S - 70, 209], fill=RED)
    return icon


def main() -> int:
    OUT_LOGO.parent.mkdir(parents=True, exist_ok=True)
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)

    logo = build_logo()
    logo.save(OUT_LOGO)
    build_banner(logo).save(OUT_BANNER)
    icon = build_icon(logo)
    icon.save(OUT_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    for p in (OUT_LOGO, OUT_BANNER, OUT_ICO):
        print(f"  {p.relative_to(PROJ)}  {p.stat().st_size:,} B")
    print(f"logo {logo.size}, icono 256x256 (6 tamanos), banner 520x44")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
