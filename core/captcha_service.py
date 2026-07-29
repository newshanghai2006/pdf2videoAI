# -*- coding: utf-8 -*-
"""Bitmap captcha renderer ported from Security_center/src/captcha.js."""
import base64
import io
import secrets

from PIL import Image, ImageDraw


CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "01010", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}


def build_captcha_text(length=5):
    return "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(length))


def _fill_rect(draw, x, y, width, height, color):
    if width <= 0 or height <= 0:
        return
    draw.rectangle((round(x), round(y), round(x + width - 1), round(y + height - 1)),
                   fill=color)


def _draw_glyph(draw, character, offset_x, offset_y, scale, color, slant=0):
    glyph = GLYPHS.get(character, GLYPHS["A"])
    for row_index, row in enumerate(glyph):
        row_shift = round((row_index - 3) * slant)
        for column_index, cell in enumerate(row):
            if cell == "1":
                _fill_rect(
                    draw,
                    offset_x + column_index * scale + row_shift,
                    offset_y + row_index * scale,
                    scale - 1,
                    scale - 1,
                    color,
                )


def render_captcha_data_url(text, width=180, height=56):
    """Render the same pixel-glyph captcha style used by Security_center."""
    background = (247, 239, 225)
    border = (208, 179, 143)
    ink = (26, 53, 52)
    accent = (184, 115, 51)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    _fill_rect(draw, 0, 0, width, 2, border)
    _fill_rect(draw, 0, height - 2, width, 2, border)
    _fill_rect(draw, 0, 0, 2, height, border)
    _fill_rect(draw, width - 2, 0, 2, height, border)

    for index in range(7):
        draw.line(
            (
                8 + index * 24,
                8 + (index % 3) * 6,
                28 + index * 22,
                46 - (index % 2) * 7,
            ),
            fill=accent,
            width=1,
        )

    for index in range(22):
        _fill_rect(draw, 6 + index * 7, 7 + (index * 11) % 32, 2, 2, (29, 95, 90))

    for index, character in enumerate(str(text).upper()):
        x = 16 + index * 30 + (0 if index % 2 == 0 else 2)
        y = 12 + (2 if index % 2 == 0 else -1)
        slant = -0.18 if index % 2 == 0 else 0.16
        _draw_glyph(draw, character, x, y, 4, ink, slant)
        draw.line((x - 2, y + 22, x + 24, y + 4), fill=accent, width=1)

    output = io.BytesIO()
    image.save(output, format="BMP")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/bmp;base64,{encoded}"
