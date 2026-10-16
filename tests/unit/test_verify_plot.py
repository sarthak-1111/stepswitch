"""The dependency-free PNG writer behind the numerical diff report's overlay plots (SPEC §6.2).

Files are decoded independently here (signature, chunk layout, CRC-32 over type and data, IHDR
fields, zlib-decodable IDAT with one filter byte per scanline) rather than trusted.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import pytest

from stepswitch.verify import plot

Rows = list[bytes]


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """(type, data) for every chunk, checking each CRC and that nothing trails IEND."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    found: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, kind
        found.append((kind, body))
        offset += 12 + length
    assert offset == len(data)
    return found


def decode(data: bytes) -> tuple[int, int, Rows]:
    """Width, height and RGB rows of an 8-bit truecolour, non-interlaced PNG."""
    parsed = chunks(data)
    kinds = [kind for kind, _ in parsed]
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND" and parsed[-1][1] == b""
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", parsed[0][1]
    )
    assert (depth, colour, compression, filtering, interlace) == (8, 2, 0, 0, 0)
    raw = zlib.decompress(b"".join(body for kind, body in parsed if kind == b"IDAT"))
    stride = 1 + 3 * width
    assert len(raw) == stride * height
    rows = []
    for y in range(height):
        line = raw[y * stride : (y + 1) * stride]
        assert line[0] == 0  # filter type None
        rows.append(line[1:])
    return width, height, rows


def pixel(rows: Rows, x: int, y: int) -> tuple[int, int, int]:
    r, g, b = rows[y][3 * x : 3 * x + 3]
    return (r, g, b)


def colours(rows: Rows) -> set[tuple[int, int, int]]:
    return {pixel(rows, x, y) for y in range(len(rows)) for x in range(len(rows[0]) // 3)}


def test_encode_png_round_trips_a_known_image() -> None:
    red, green, blue, white = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)
    data = plot.encode_png(2, 2, bytes([*red, *green, *blue, *white]))
    width, height, rows = decode(data)
    assert (width, height) == (2, 2)
    assert [pixel(rows, 0, 0), pixel(rows, 1, 0), pixel(rows, 0, 1), pixel(rows, 1, 1)] == [
        red,
        green,
        blue,
        white,
    ]
    # IEND is always the same 12 bytes: empty data and CRC-32 of b"IEND".
    assert data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.mark.parametrize(("width", "height", "size"), [(0, 1, 0), (1, 0, 0), (2, 2, 11)])
def test_encode_png_rejects_bad_sizes(width: int, height: int, size: int) -> None:
    with pytest.raises(ValueError):
        plot.encode_png(width, height, bytes(size))


def test_canvas_lines_rectangles_and_text() -> None:
    canvas = plot.Canvas(40, 20)
    canvas.line(0, 0, 9, 9, plot.BLUE)
    assert canvas.get(0, 0) == canvas.get(9, 9) == canvas.get(5, 5) == plot.BLUE
    canvas.line(39, 19, 30, 19, plot.GREEN, thickness=1)
    assert all(canvas.get(x, 19) == plot.GREEN for x in range(30, 40))
    canvas.fill(-5, -5, 1, 1, plot.VERMILLION)  # clipped, no error
    assert canvas.get(0, 0) == plot.VERMILLION
    width = canvas.text(12, 2, "1", plot.TEXT)
    assert width == plot.GLYPH_W
    # The "1" glyph: a vertical stroke in the middle column on rows 1-5.
    assert all(canvas.get(14, 2 + row) == plot.TEXT for row in range(1, 6))
    assert canvas.get(12, 4) == plot.WHITE
    # Lowercase draws as uppercase; unknown characters draw the "?" glyph.
    a, b = plot.Canvas(6, 7), plot.Canvas(6, 7)
    a.text(0, 0, "e")
    b.text(0, 0, "E")
    assert a.pixels == b.pixels
    unknown, question = plot.Canvas(6, 7), plot.Canvas(6, 7)
    unknown.text(0, 0, "µ")
    question.text(0, 0, "?")
    assert unknown.pixels == question.pixels


def test_nice_ticks_and_labels() -> None:
    assert plot.nice_ticks(0.0, 10.0, 5) == ([0.0, 2.0, 4.0, 6.0, 8.0, 10.0], 2.0)
    ticks, step = plot.nice_ticks(-1e-6, 1e-6, 4)
    assert step == pytest.approx(5e-7) and ticks[0] == pytest.approx(-1e-6)
    assert plot.nice_ticks(3.0, 3.0) == ([3.0], 0.0)
    assert plot.format_tick(0.5, 0.1) == "0.5"
    assert plot.format_tick(12.0, 2.0) == "12"
    assert plot.format_tick(-3e-17, 0.5) == "0"
    assert plot.format_tick(1.5e-6, 5e-7) == "1.5e-6"
    assert plot.format_tick(2.5e7, 5e6) == "2.5e7"


def test_overlay_plot_is_a_valid_deterministic_png(tmp_path: Path) -> None:
    x = [i / 50 for i in range(201)]
    expected = [math.exp(-0.5 * ((v - 2.0) / 0.3) ** 2) for v in x]
    actual = [math.exp(-0.5 * ((v - 2.1) / 0.3) ** 2) for v in x]
    first = plot.overlay_png(
        tmp_path / "a" / "plot.png", expected, actual, x_expected=x, x_actual=x, title="Peak"
    )
    second = plot.overlay_png(
        tmp_path / "b.png", expected, actual, x_expected=x, x_actual=x, title="Peak"
    )
    assert first.read_bytes() == second.read_bytes()
    width, height, rows = decode(first.read_bytes())
    assert width == 900 and height > 400
    seen = colours(rows)
    # Expected, actual and the difference panel are all drawn.
    assert {plot.BLUE, plot.VERMILLION, plot.GREEN} <= seen


def test_overlay_without_difference_when_grids_differ(tmp_path: Path) -> None:
    same = plot.overlay_png(tmp_path / "same.png", [0.0, 1.0, 0.0], [0.0, 1.0, 0.0])
    other = plot.overlay_png(
        tmp_path / "other.png",
        [0.0, 1.0, 0.0],
        [0.0, 0.5, 1.0, 0.5],
        x_expected=[0.0, 1.0, 2.0],
        x_actual=[0.0, 0.5, 1.0, 1.5],
    )
    _, same_height, _ = decode(same.read_bytes())
    _, other_height, rows = decode(other.read_bytes())
    assert other_height < same_height
    assert plot.GREEN not in colours(rows)


def test_non_finite_and_constant_values_do_not_break_plots(tmp_path: Path) -> None:
    values = [0.0, math.nan, 1.0, math.inf, -math.inf, 2.0]
    path = plot.overlay_png(tmp_path / "nan.png", values, [5.0] * 6)
    decode(path.read_bytes())
    path = plot.overlay_png(tmp_path / "empty.png", [], [])
    decode(path.read_bytes())
