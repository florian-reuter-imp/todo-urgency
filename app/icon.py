"""Erzeugt todo.ico in reinem Python - ohne Pillow.

Gezeichnet wird dasselbe Motiv wie in icon.svg: ein abgerundetes Quadrat
in der Akzentfarbe, darauf die beiden Achsen der Matrix und ein paar
Punkte. Windows-Verknuepfungen brauchen ein .ico; ein SVG nimmt der
Explorer nicht.

Das ICO enthaelt mehrere Groessen als 32-Bit-BGRA-Bitmaps, weil das von
jeder Windows-Version gelesen wird (PNG im ICO erst ab Vista).
"""

from __future__ import annotations

import struct
from pathlib import Path

from .config import ROOT

SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_PATH = ROOT / "todo.ico"

#: Akzentfarbe wie in style.css (--accent).
BG = (0x2F, 0x6F, 0x4E)
FG = (0xFF, 0xFF, 0xFF)

#: Punkte als (x, y, Radius, Deckkraft) in Anteilen der Kantenlaenge.
DOTS = (
    (0.690, 0.330, 0.082, 1.00),
    (0.395, 0.410, 0.052, 0.75),
    (0.625, 0.610, 0.042, 0.55),
    (0.345, 0.655, 0.034, 0.40),
)


def _rounded_square(x: float, y: float, radius: float = 0.22) -> bool:
    """Punkt innerhalb eines abgerundeten Quadrats (0..1)?"""
    dx = max(radius - x, x - (1.0 - radius), 0.0)
    dy = max(radius - y, y - (1.0 - radius), 0.0)
    return dx * dx + dy * dy <= radius * radius


def _sample(x: float, y: float, detail: bool) -> tuple[int, int, int, int]:
    """Farbe an einer Stelle (0..1). Rueckgabe als (B, G, R, A)."""
    if not _rounded_square(x, y):
        return (0, 0, 0, 0)

    b, g, r = BG[2], BG[1], BG[0]
    ink = 0.0   # Deckkraft des weissen Vordergrunds

    if detail:
        # Achsenkreuz unten links, wie in der Matrix.
        if 0.205 <= x <= 0.245 and 0.17 <= y <= 0.80:
            ink = max(ink, 0.45)
        if 0.205 <= x <= 0.83 and 0.765 <= y <= 0.805:
            ink = max(ink, 0.45)

    for cx, cy, rad, alpha in DOTS:
        if not detail and alpha < 0.7:
            continue        # kleine Groessen: nur der Hauptpunkt
        if (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad:
            ink = max(ink, alpha)

    if ink:
        b = round(b + (FG[2] - b) * ink)
        g = round(g + (FG[1] - g) * ink)
        r = round(r + (FG[0] - r) * ink)
    return (b, g, r, 255)


def _render(size: int, supersample: int = 4) -> bytes:
    """Eine Groesse als BGRA-Pixel, zeilenweise von unten nach oben (BMP)."""
    detail = size >= 32
    step = 1.0 / (size * supersample)
    rows: list[bytes] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(supersample):
                for sx in range(supersample):
                    x = (px * supersample + sx + 0.5) * step
                    y = (py * supersample + sy + 0.5) * step
                    pixel = _sample(x, y, detail)
                    for i in range(4):
                        acc[i] += pixel[i]
            n = supersample * supersample
            row += bytes(value // n for value in acc)
        rows.append(bytes(row))
    return b"".join(reversed(rows))        # BMP steht auf dem Kopf


def build() -> bytes:
    """Das vollstaendige ICO als Bytes."""
    images = []
    for size in SIZES:
        pixels = _render(size)
        header = struct.pack(
            "<IiiHHIIiiII",
            40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0,
        )
        # Undurchsichtigkeitsmaske: wird bei 32 Bit ignoriert, muss aber da sein.
        mask_row = ((size + 31) // 32) * 4
        images.append(header + pixels + b"\x00" * (mask_row * size))

    out = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    for size, data in zip(SIZES, images):
        out += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(data), offset,
        )
        offset += len(data)
    return out + b"".join(images)


def ensure(path: Path | None = None, force: bool = False) -> Path:
    """Schreibt die Datei, falls sie fehlt. Gibt den Pfad zurueck."""
    path = path or ICON_PATH
    if force or not path.is_file():
        path.write_bytes(build())
    return path


if __name__ == "__main__":
    print(ensure(force=True))
