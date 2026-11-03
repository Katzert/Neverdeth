#!/usr/bin/env python3
"""Branding NEVERDETH a partir del logo ORIGINAL del autor.

Fuente (raiz del proyecto):
  neverdeth_logo_transparent.png      2066x550 RGBA (master)
  neverdeth_logo_transparent_4K.png   4200x1118 (master 4K, no se versiona)

Salidas:
  docs/assets/neverdeth_logo.png         1400 px de ancho, alfa (README)
  docs/assets/neverdeth_logo_banner.bmp  520x44, fondo carbon, para el menu F1
  config/neverdeth.ico                   16..256 px

El BMP es necesario porque LoadImage no carga PNG con alfa; lleva el carbon
#16161C exacto del menu, asi que el banner se funde con el fondo.
"""
from pathlib import Path

from PIL import Image, ImageDraw

PROJ = Path(__file__).resolve().parents[1]
SRC = PROJ / "neverdeth_logo_transparent.png"
OUT_LOGO = PROJ / "docs" / "assets" / "neverdeth_logo.png"
OUT_BANNER = PROJ / "docs" / "assets" / "neverdeth_logo_banner.bmp"
OUT_ICO = PROJ / "config" / "neverdeth.ico"

CARBON = (22, 22, 28)
BRONZE = (200, 138, 50)
CREAM = (242, 225, 195)


def fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    k = min(max_w / img.width, max_h / img.height)
    return img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)


def build_banner(logo: Image.Image) -> Image.Image:
    """520x44: logo a 40 px de alto, centrado, flanqueado por reglas bronce."""
    W, H = 520, 44
    bmp = Image.new("RGB", (W, H), CARBON)
    scaled = fit(logo, W - 80, H - 8)
    x = (W - scaled.width) // 2
    y = (H - scaled.height) // 2
    bmp.paste(scaled, (x, y), scaled)
    d = ImageDraw.Draw(bmp)
    mid = H // 2
    if x > 40:
        d.line([(18, mid), (x - 14, mid)], fill=BRONZE, width=1)
        d.line([(x + scaled.width + 14, mid), (W - 18, mid)], fill=BRONZE, width=1)
    return bmp


def build_icon(logo: Image.Image) -> Image.Image:
    """Cuadrado 256: monograma N en bronce. El wordmark completo no se lee a 16-32 px."""
    from PIL import ImageFont
    S = 256
    icon = Image.new("RGBA", (S, S), CARBON + (255,))
    d = ImageDraw.Draw(icon)
    d.rectangle([5, 5, S - 6, S - 6], outline=BRONZE + (255,), width=5)
    d.rectangle([16, 16, S - 17, S - 17], outline=(200, 138, 50, 70), width=1)
    font = None
    for path, variation in (("C:/Windows/Fonts/bahnschrift.ttf", "Bahnschrift SemiBold"),
                            ("C:/Windows/Fonts/arialbd.ttf", None)):
        try:
            font = ImageFont.truetype(path, 168)
            if variation:
                try:
                    font.set_variation_by_name(variation)
                except Exception:
                    pass
            break
        except Exception:
            continue
    if font is not None:
        box = d.textbbox((0, 0), "N", font=font)
        d.text(((S - (box[2] - box[0])) / 2 - box[0], 30 - box[1]), "N", font=font,
               fill=BRONZE + (255,))
    d.rectangle([78, 206, S - 78, 209], fill=(180, 70, 60, 255))
    return icon


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: falta {SRC.name} en la raiz del proyecto")
        return 1
    logo = Image.open(SRC).convert("RGBA")
    OUT_LOGO.parent.mkdir(parents=True, exist_ok=True)
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)

    # 1400 px de ancho: suficiente para el README (480) sin cargar 1 MB al repo.
    logo_web = logo.resize((1400, round(1400 * logo.height / logo.width)), Image.LANCZOS)
    logo_web.save(OUT_LOGO, optimize=True)
    build_banner(logo).save(OUT_BANNER)
    build_icon(logo).save(OUT_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                                          (128, 128), (256, 256)])

    print(f"fuente: {SRC.name} {logo.size}")
    for p in (OUT_LOGO, OUT_BANNER, OUT_ICO):
        print(f"  {p.relative_to(PROJ)}  {p.stat().st_size:,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
