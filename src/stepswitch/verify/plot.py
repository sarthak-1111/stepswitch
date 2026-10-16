"""Dependency-free PNG line plots for the numerical diff report (SPEC §6.2).

A small rasterizer (Bresenham lines on an RGB canvas, a 5x7 bitmap font) and a PNG encoder on
top of zlib, so the science gate can attach overlay plots without a plotting library. The
output is deterministic: the same data always gives the same bytes.

PNG layout written here: the 8-byte signature, then chunks of (4-byte big-endian length, 4-byte
type, data, CRC-32 over type and data): IHDR (8-bit truecolour, no interlace), one IDAT holding
the zlib stream of the scanlines (each prefixed with filter type 0), and IEND.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

Color = tuple[int, int, int]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WHITE: Color = (255, 255, 255)
TEXT: Color = (40, 40, 40)
AXIS: Color = (110, 110, 110)
GRID: Color = (228, 228, 228)
ZERO: Color = (170, 170, 170)
# Okabe-Ito colours: distinguishable with the common forms of colour blindness.
BLUE: Color = (0, 114, 178)
VERMILLION: Color = (213, 94, 0)
GREEN: Color = (0, 158, 115)

GLYPH_W, GLYPH_H = 5, 7
# 5x7 glyphs, one int per row from the top, bit 4 = leftmost column. Lowercase draws as
# uppercase and anything else as "?".
FONT: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "A": (0x0E, 0x11, 0x11, 0x11, 0x1F, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x11, 0x0A, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "-": (0, 0, 0, 0x1F, 0, 0, 0),
    "+": (0, 0x04, 0x04, 0x1F, 0x04, 0x04, 0),
    ".": (0, 0, 0, 0, 0, 0x0C, 0x0C),
    ",": (0, 0, 0, 0, 0x0C, 0x04, 0x08),
    ":": (0, 0x0C, 0x0C, 0, 0x0C, 0x0C, 0),
    "=": (0, 0, 0x1F, 0, 0x1F, 0, 0),
    "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08),
    "[": (0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E),
    "]": (0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E),
    "/": (0, 0x01, 0x02, 0x04, 0x08, 0x10, 0),
    "_": (0, 0, 0, 0, 0, 0, 0x1F),
    "%": (0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03),
    "<": (0x02, 0x04, 0x08, 0x10, 0x08, 0x04, 0x02),
    ">": (0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08),
    "|": (0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "*": (0, 0x04, 0x15, 0x0E, 0x15, 0x04, 0),
    ";": (0, 0x0C, 0x0C, 0, 0x0C, 0x04, 0x08),
    "!": (0x04, 0x04, 0x04, 0x04, 0x04, 0, 0x04),
    "'": (0x0C, 0x04, 0x08, 0, 0, 0, 0),
    '"': (0x0A, 0x0A, 0x0A, 0, 0, 0, 0),
    "#": (0x0A, 0x0A, 0x1F, 0x0A, 0x1F, 0x0A, 0x0A),
    "{": (0x02, 0x04, 0x04, 0x08, 0x04, 0x04, 0x02),
    "}": (0x08, 0x04, 0x04, 0x02, 0x04, 0x04, 0x08),
    "^": (0x04, 0x0A, 0x11, 0, 0, 0, 0),
    "&": (0x0C, 0x12, 0x14, 0x08, 0x15, 0x12, 0x0D),
    "?": (0x0E, 0x11, 0x01, 0x02, 0x04, 0, 0x04),
}


# --------------------------------------------------------------------------------------------
# PNG encoding
# --------------------------------------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def encode_png(width: int, height: int, rgb: bytes | bytearray) -> bytes:
    """PNG bytes for `height` rows of `width` RGB pixels (8 bits per channel)."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image size must be positive, got {width}x{height}")
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGB data, got {len(rgb)}")
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgb[row * stride : (row + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------------------------
# Canvas
# --------------------------------------------------------------------------------------------


class Canvas:
    """An RGB pixel buffer with clipped drawing primitives."""

    def __init__(self, width: int, height: int, background: Color = WHITE) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"canvas size must be positive, got {width}x{height}")
        self.width = width
        self.height = height
        self.pixels = bytearray(bytes(background) * (width * height))

    def get(self, x: int, y: int) -> Color:
        index = (y * self.width + x) * 3
        r, g, b = self.pixels[index : index + 3]
        return (r, g, b)

    def fill(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Fill the rectangle with corners (x0, y0) and (x1, y1), both inclusive."""
        left, right = max(min(x0, x1), 0), min(max(x0, x1), self.width - 1)
        top, bottom = max(min(y0, y1), 0), min(max(y0, y1), self.height - 1)
        if left > right or top > bottom:
            return
        span = bytes(color) * (right - left + 1)
        for row in range(top, bottom + 1):
            start = (row * self.width + left) * 3
            self.pixels[start : start + len(span)] = span

    def outline(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        self.fill(x0, y0, x1, y0, color)
        self.fill(x0, y1, x1, y1, color)
        self.fill(x0, y0, x0, y1, color)
        self.fill(x1, y0, x1, y1, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: Color, thickness: int = 1) -> None:
        """Bresenham line; each point is a thickness x thickness square."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.fill(x0, y0, x0 + thickness - 1, y0 + thickness - 1, color)
            if x0 == x1 and y0 == y1:
                return
            twice = 2 * err
            if twice >= dy:
                err += dy
                x0 += sx
            if twice <= dx:
                err += dx
                y0 += sy

    def text(self, x: int, y: int, text: str, color: Color = TEXT, scale: int = 1) -> int:
        """Draw `text` with its top-left corner at (x, y); returns the width drawn."""
        cursor = x
        for char in text:
            glyph = FONT.get(char) or FONT.get(char.upper()) or FONT["?"]
            for row, bits in enumerate(glyph):
                for col in range(GLYPH_W):
                    if bits & (1 << (GLYPH_W - 1 - col)):
                        px, py = cursor + col * scale, y + row * scale
                        self.fill(px, py, px + scale - 1, py + scale - 1, color)
            cursor += (GLYPH_W + 1) * scale
        return max(cursor - x - scale, 0)

    def png(self) -> bytes:
        return encode_png(self.width, self.height, self.pixels)


def text_width(text: str, scale: int = 1) -> int:
    return max(len(text) * (GLYPH_W + 1) * scale - scale, 0)


# --------------------------------------------------------------------------------------------
# Axes
# --------------------------------------------------------------------------------------------


def nice_ticks(lo: float, hi: float, count: int = 5) -> tuple[list[float], float]:
    """About `count` round tick values (1, 2 or 5 times a power of ten) covering [lo, hi]."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo], 0.0
    raw = (hi - lo) / max(count, 1)
    base = 10.0 ** math.floor(math.log10(raw))
    step = next(m * base for m in (1, 2, 5, 10) if m * base >= raw * (1 - 1e-12))
    first = math.ceil(lo / step - 1e-9)
    ticks: list[float] = []
    index = first
    while index * step <= hi + step * 1e-9 and len(ticks) < 50:
        ticks.append(index * step)
        index += 1
    return ticks, step


def format_tick(value: float, step: float) -> str:
    """A short label for a tick value on an axis with tick spacing `step`."""
    if step > 0 and abs(value) < step * 1e-6:
        return "0"
    magnitude = max(abs(value), step)
    if 1e-3 <= magnitude < 1e5:
        decimals = max(0, -math.floor(math.log10(step))) if step > 0 else 3
        return f"{value:.{min(decimals, 8)}f}"
    digits = 0
    if step > 0 and value != 0:
        digits = min(max(math.floor(math.log10(abs(value))) - math.floor(math.log10(step)), 0), 3)
    mantissa, exponent = f"{value:.{digits}e}".split("e")
    return f"{mantissa}e{int(exponent)}"


@dataclass
class Series:
    """One line: `y` against `x` (sample index when x is None); non-finite values break it."""

    y: Sequence[float]
    x: Sequence[float] | None = None
    label: str = ""
    color: Color = BLUE

    def points(self) -> list[tuple[float, float] | None]:
        xs = self.x if self.x is not None else range(len(self.y))
        out: list[tuple[float, float] | None] = []
        for xv, yv in zip(xs, self.y, strict=False):
            fx, fy = float(xv), float(yv)
            out.append((fx, fy) if math.isfinite(fx) and math.isfinite(fy) else None)
        return out


@dataclass
class Panel:
    """A plot area with its own y axis; panels in one figure share the x axis."""

    series: list[Series]
    title: str = ""
    zero_line: bool = False
    height: int = 260


@dataclass
class _Frame:
    left: int
    top: int
    right: int
    bottom: int
    x_range: tuple[float, float]
    y_range: tuple[float, float]

    def map(self, x: float, y: float) -> tuple[int, int]:
        (x0, x1), (y0, y1) = self.x_range, self.y_range
        px = self.left + (x - x0) / (x1 - x0) * (self.right - self.left)
        py = self.bottom - (y - y0) / (y1 - y0) * (self.bottom - self.top)
        return round(px), round(py)


def _range(values: list[float], pad: float) -> tuple[float, float]:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return 0.0, 1.0
    lo, hi = min(finite), max(finite)
    if hi == lo:
        delta = abs(lo) * 0.1 or 1.0
        return lo - delta, hi + delta
    margin = (hi - lo) * pad
    return lo - margin, hi + margin


def _draw_series(canvas: Canvas, frame: _Frame, series: Series, thickness: int) -> None:
    previous: tuple[int, int] | None = None
    for point in series.points():
        if point is None:
            previous = None
            continue
        current = frame.map(*point)
        if previous is None:
            canvas.fill(
                *current, current[0] + thickness - 1, current[1] + thickness - 1, series.color
            )
        elif current != previous:
            canvas.line(*previous, *current, series.color, thickness)
        previous = current


@dataclass
class Figure:
    """Stacked panels with a title and a shared x axis."""

    panels: list[Panel]
    title: str = ""
    x_label: str = ""
    width: int = 900
    margin_left: int = 84
    margin_right: int = 24
    gap: int = 46
    thickness: int = 2
    notes: list[str] = field(default_factory=list)

    def render(self) -> Canvas:
        top = 40 if self.title else 16
        height = top + sum(p.height for p in self.panels) + self.gap * len(self.panels) + 18
        height += 12 * len(self.notes)
        canvas = Canvas(self.width, height)
        if self.title:
            canvas.text(self.margin_left, 12, self.title, TEXT, scale=2)
        xs = [p[0] for panel in self.panels for s in panel.series for p in s.points() if p]
        x_range = _range(xs, 0.0)
        x_ticks, x_step = nice_ticks(*x_range)
        y = top + 14
        for panel in self.panels:
            frame = self._panel(canvas, panel, y, x_range, x_ticks, x_step)
            y = frame.bottom + self.gap
        if self.x_label:
            label_x = (self.margin_left + self.width - self.margin_right) // 2
            canvas.text(label_x - text_width(self.x_label) // 2, y - self.gap + 22, self.x_label)
        for index, note in enumerate(self.notes):
            canvas.text(self.margin_left, y - self.gap + 40 + 12 * index, note)
        return canvas

    def _panel(
        self,
        canvas: Canvas,
        panel: Panel,
        top: int,
        x_range: tuple[float, float],
        x_ticks: list[float],
        x_step: float,
    ) -> _Frame:
        ys = [p[1] for s in panel.series for p in s.points() if p]
        if panel.zero_line:
            ys.append(0.0)
        frame = _Frame(
            self.margin_left,
            top,
            self.width - self.margin_right,
            top + panel.height,
            x_range,
            _range(ys, 0.06),
        )
        if panel.title:
            canvas.text(frame.left, top - 12, panel.title)
        y_ticks, y_step = nice_ticks(*frame.y_range)
        for value in y_ticks:
            _, py = frame.map(x_range[0], value)
            canvas.fill(frame.left, py, frame.right, py, GRID)
            label = format_tick(value, y_step)
            canvas.text(frame.left - 6 - text_width(label), py - GLYPH_H // 2, label)
        for value in x_ticks:
            px, _ = frame.map(value, frame.y_range[0])
            canvas.fill(px, frame.top, px, frame.bottom, GRID)
            label = format_tick(value, x_step)
            canvas.text(px - text_width(label) // 2, frame.bottom + 6, label)
        if panel.zero_line and frame.y_range[0] < 0 < frame.y_range[1]:
            _, py = frame.map(x_range[0], 0.0)
            canvas.fill(frame.left, py, frame.right, py, ZERO)
        for series in panel.series:
            _draw_series(canvas, frame, series, self.thickness)
        canvas.outline(frame.left, frame.top, frame.right, frame.bottom, AXIS)
        self._legend(canvas, frame, panel)
        return frame

    def _legend(self, canvas: Canvas, frame: _Frame, panel: Panel) -> None:
        labeled = [s for s in panel.series if s.label]
        if not labeled:
            return
        width = max(text_width(s.label) for s in labeled) + 34
        left, top = frame.right - width - 8, frame.top + 8
        canvas.fill(left, top, left + width, top + 12 * len(labeled) + 6, WHITE)
        canvas.outline(left, top, left + width, top + 12 * len(labeled) + 6, GRID)
        for index, series in enumerate(labeled):
            row = top + 6 + 12 * index
            canvas.fill(left + 6, row + 3, left + 22, row + 4, series.color)
            canvas.text(left + 28, row, series.label)

    def png(self) -> bytes:
        return self.render().png()

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png())
        return path


def overlay_png(
    path: Path,
    expected: Sequence[float],
    actual: Sequence[float],
    *,
    x_expected: Sequence[float] | None = None,
    x_actual: Sequence[float] | None = None,
    title: str = "",
    x_label: str = "",
    notes: Sequence[str] = (),
) -> Path:
    """Expected and actual curves overlaid, plus their difference when the samples align."""
    panels = [
        Panel(
            [
                Series(expected, x_expected, "expected", BLUE),
                Series(actual, x_actual, "actual", VERMILLION),
            ],
            title="expected vs actual",
            height=280,
        )
    ]
    same_grid = len(expected) == len(actual) and (
        x_actual is None or x_expected is None or list(x_actual) == list(x_expected)
    )
    if same_grid:
        diff = [float(a) - float(e) for a, e in zip(actual, expected, strict=True)]
        panels.append(
            Panel(
                [Series(diff, x_expected, "actual - expected", GREEN)],
                title="difference",
                zero_line=True,
                height=150,
            )
        )
    figure = Figure(panels, title=title, x_label=x_label, notes=list(notes))
    return figure.save(path)
