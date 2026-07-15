"""Branded QR code image generation for the public subscription link.

Renders a 1:1 image with a SubIO-branded gradient background, a white
"quiet zone" card, and the subscription QR code centered on top — sized so
phone cameras can scan it reliably even after Telegram's photo compression.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import qrcode
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_H

_CANVAS_SIZE = 1024
_CARD_MARGIN = 72
_CARD_RADIUS = 40
_QR_MARGIN = 56

# SubIO brand palette: deep indigo -> electric violet gradient, matching the
# bot's dark, "premium VPN" visual identity used elsewhere in the product.
_GRADIENT_TOP = (20, 20, 46)
_GRADIENT_BOTTOM = (88, 28, 135)
_CARD_COLOR = (255, 255, 255)
_ACCENT_COLOR = (155, 92, 246)


@dataclass(frozen=True)
class BrandedQrResult:
    png_bytes: bytes


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (1, size), color=0)
    for y in range(size):
        ratio = y / max(1, size - 1)
        pixel = tuple(int(top[channel] + (bottom[channel] - top[channel]) * ratio) for channel in range(3))
        base.putpixel((0, y), pixel)
    return base.resize((size, size))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def generate_branded_qr(data: str) -> BrandedQrResult:
    """Builds the full branded PNG for a subscription URL/token payload."""
    canvas = _vertical_gradient(_CANVAS_SIZE, _GRADIENT_TOP, _GRADIENT_BOTTOM)
    draw = ImageDraw.Draw(canvas)

    # Subtle brand accent ring behind the card for depth, matching SubIO's
    # rounded, glassy admin/bot aesthetic without requiring bundled fonts.
    ring_bbox = (
        _CARD_MARGIN - 18,
        _CARD_MARGIN - 18,
        _CANVAS_SIZE - _CARD_MARGIN + 18,
        _CANVAS_SIZE - _CARD_MARGIN + 18,
    )
    draw.rounded_rectangle(ring_bbox, radius=_CARD_RADIUS + 18, outline=_ACCENT_COLOR, width=6)

    card_size = _CANVAS_SIZE - 2 * _CARD_MARGIN
    card = Image.new("RGB", (card_size, card_size), _CARD_COLOR)
    card_mask = _rounded_mask((card_size, card_size), _CARD_RADIUS)

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="#14142e", back_color="#ffffff").convert("RGB")

    qr_target = card_size - 2 * _QR_MARGIN
    qr_image = qr_image.resize((qr_target, qr_target), Image.NEAREST)
    card.paste(qr_image, (_QR_MARGIN, _QR_MARGIN))

    canvas.paste(card, (_CARD_MARGIN, _CARD_MARGIN), card_mask)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return BrandedQrResult(png_bytes=buffer.getvalue())
