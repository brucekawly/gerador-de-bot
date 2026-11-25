"""Gera o ícone do app (gui/assets/app_icon.png e .ico): robô branco sobre o laranja do OLT Cloud.

Rodar só quando quiser mudar o desenho:  python tools/make_icon.py   (precisa do Pillow)
"""
import os

from PIL import Image, ImageDraw

ORANGE = (242, 113, 28, 255)        # #F2711C (OLT Cloud)
ORANGE_DARK = (196, 84, 10, 255)
WHITE = (255, 255, 255, 255)
INK = (40, 30, 24, 255)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui", "assets")


def draw(size: int = 1024) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = s / 64                                         # grade de 64 unidades

    # fundo: quadrado arredondado laranja com leve sombra embaixo
    d.rounded_rectangle([2 * u, 4 * u, 62 * u, 62 * u], radius=14 * u, fill=ORANGE_DARK)
    d.rounded_rectangle([2 * u, 2 * u, 62 * u, 60 * u], radius=14 * u, fill=ORANGE)

    # antena
    d.line([32 * u, 10 * u, 32 * u, 17 * u], fill=WHITE, width=round(3 * u))
    d.ellipse([28.5 * u, 6.5 * u, 35.5 * u, 13.5 * u], fill=WHITE)

    # orelhas
    d.rounded_rectangle([8 * u, 28 * u, 13 * u, 40 * u], radius=2.5 * u, fill=WHITE)
    d.rounded_rectangle([51 * u, 28 * u, 56 * u, 40 * u], radius=2.5 * u, fill=WHITE)

    # cabeça
    d.rounded_rectangle([12 * u, 17 * u, 52 * u, 51 * u], radius=10 * u, fill=WHITE)

    # visor + olhos
    d.rounded_rectangle([17 * u, 24 * u, 47 * u, 39 * u], radius=7 * u, fill=INK)
    d.ellipse([22 * u, 27.5 * u, 30 * u, 35.5 * u], fill=ORANGE)
    d.ellipse([34 * u, 27.5 * u, 42 * u, 35.5 * u], fill=ORANGE)

    # boca (grade de "porta de roteador")
    for x in (24, 30, 36):
        d.rounded_rectangle([x * u, 43 * u, (x + 4) * u, 46 * u], radius=1.2 * u, fill=INK)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw(1024)
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "app_icon.png"))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, "app_icon_64.png"))
    big.save(os.path.join(OUT, "app_icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ícones gerados em", OUT)


if __name__ == "__main__":
    main()
