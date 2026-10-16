"""Read GGUF metadata without loading any tensor data (SPEC §4.3 step 1).

GGUF files are untrusted input. Every count and length is checked against the bytes that remain
in the file (and against `max_bytes`) before anything is read or allocated, nesting depth and
the number of materialized items are capped, and large arrays such as `tokenizer.ggml.tokens`
or `tokenizer.ggml.merges` are skipped without being materialized: only their element type and
length are kept, as a `SkippedValue`.

Layout of GGUF v2 and v3 (ggml `docs/gguf.md`; little-endian):

    magic "GGUF" | version u32 | tensor_count u64 | kv_count u64 | kv pairs | tensor infos
    kv pair:     key string | value type u32 | value
    string:      length u64 | UTF-8 bytes (not NUL-terminated)
    array:       element type u32 | element count u64 | elements (arrays may nest)
    tensor info: name string | n_dims u32 | dims u64[n_dims] | ggml type u32 | offset u64

Version 1 used 32-bit counts and is rejected, as llama.cpp does. Big-endian files (a byte-swapped
version field) are rejected as well; they are not used on the platforms Stepswitch supports.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from math import prod
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"GGUF"
SUPPORTED_VERSIONS = (2, 3)
DEFAULT_ALIGNMENT = 32

# Limits for untrusted files. Real models have a few dozen keys and at most a few thousand
# tensors per file; the format allows more, so these only stop pathological inputs.
MAX_KEY_BYTES = 65535  # the spec caps keys at 2^16 - 1 bytes
MAX_KV_COUNT = 1 << 16
MAX_TENSOR_COUNT = 1 << 20
MAX_TENSOR_NAME_BYTES = 64  # the spec caps tensor names at 64 bytes
MAX_DIMS = 4  # GGML_MAX_DIMS
MAX_ARRAY_DEPTH = 4
MAX_TOTAL_ITEMS = 1 << 18  # array elements materialized across the whole file

UINT8, INT8, UINT16, INT16, UINT32, INT32, FLOAT32, BOOL = range(8)
STRING, ARRAY, UINT64, INT64, FLOAT64 = range(8, 13)

_SCALARS: dict[int, tuple[str, str]] = {
    UINT8: ("uint8", "B"),
    INT8: ("int8", "b"),
    UINT16: ("uint16", "H"),
    INT16: ("int16", "h"),
    UINT32: ("uint32", "I"),
    INT32: ("int32", "i"),
    FLOAT32: ("float32", "f"),
    BOOL: ("bool", "B"),
    UINT64: ("uint64", "Q"),
    INT64: ("int64", "q"),
    FLOAT64: ("float64", "d"),
}
TYPE_NAMES = {**{code: name for code, (name, _) in _SCALARS.items()}, STRING: "string"}
TYPE_NAMES[ARRAY] = "array"

_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")

# `general.file_type` (llama.cpp `enum llama_ftype`, gguf-py `LlamaFileType`, llama.cpp v0.7.0),
# named the way GGUF file names spell the quantization.
FILE_TYPES: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    4: "Q4_1_SOME_F16",  # removed upstream; kept so old files still get a name
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    33: "Q4_0_4_4",  # removed upstream (runtime repack of Q4_0)
    34: "Q4_0_4_8",
    35: "Q4_0_8_8",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4_MOE",
    39: "NVFP4",
    40: "Q1_0",
    41: "Q2_0",
}
FILE_TYPE_GUESSED = 1024  # llama.cpp flag for a file type inferred at load time


class GGUFError(ValueError):
    """Not a readable GGUF file: bad magic, unsupported version, truncated or malformed."""


@dataclass(frozen=True)
class SkippedValue:
    """A value that was skipped instead of materialized: a large array or a very long string.

    `type` is `"string"` or `"array[<element type>]"`; `length` counts bytes for a string and
    elements for an array.
    """

    type: str
    length: int


@dataclass
class GGUFInfo:
    version: int
    tensor_count: int
    metadata: dict[str, Any]
    # Total elements over the tensor infos of this file (None when they were not read).
    parameters: int | None = None
    # File offset of the tensor data section (None when the tensor infos were not read).
    data_offset: int | None = None


class _Reader:
    """Sequential reads with bounds checks against the file size and the byte budget."""

    def __init__(self, handle: BinaryIO, file_size: int, max_bytes: int) -> None:
        self._handle = handle
        self._file_size = file_size
        self._max_bytes = max_bytes
        self._end = min(file_size, max_bytes)
        self.pos = 0

    def remaining(self) -> int:
        return self._end - self.pos

    def require(self, count: int, what: str) -> None:
        """Fail unless `count` more bytes can be read."""
        self._check(count, what)

    def _check(self, count: int, what: str) -> None:
        if count < 0 or count > self._end - self.pos:
            if self.pos + count > self._file_size:
                raise GGUFError(
                    f"truncated file: {what} needs {count} bytes at offset {self.pos}, "
                    f"but the file has {self._file_size}"
                )
            raise GGUFError(
                f"metadata exceeds the {self._max_bytes}-byte read limit (at {what}, "
                f"offset {self.pos})"
            )

    def read(self, count: int, what: str) -> bytes:
        self._check(count, what)
        data = self._handle.read(count)
        if len(data) != count:
            raise GGUFError(f"truncated file while reading {what}")
        self.pos += count
        return data

    def skip(self, count: int, what: str) -> None:
        self._check(count, what)
        self._handle.seek(count, os.SEEK_CUR)
        self.pos += count

    def u32(self, what: str) -> int:
        value: int = _U32.unpack(self.read(4, what))[0]
        return value

    def u64(self, what: str) -> int:
        value: int = _U64.unpack(self.read(8, what))[0]
        return value


class _Parser:
    def __init__(self, reader: _Reader, *, max_array_items: int, max_string_bytes: int) -> None:
        self.reader = reader
        self.max_array_items = max_array_items
        self.max_string_bytes = max_string_bytes
        self.items_left = MAX_TOTAL_ITEMS

    # -- values --------------------------------------------------------------------------------

    def value(self, vtype: int, what: str, depth: int = 0) -> Any:
        if vtype in _SCALARS:
            return self._scalars(vtype, 1, what)[0]
        if vtype == STRING:
            return self.string(what)
        if vtype == ARRAY:
            return self.array(what, depth + 1)
        raise GGUFError(f"unknown value type {vtype} in {what}")

    def string(self, what: str, *, limit: int | None = None) -> str | SkippedValue:
        length = self.reader.u64(f"{what} length")
        cap = self.max_string_bytes if limit is None else limit
        if length > cap:
            self.reader.skip(length, what)
            return SkippedValue("string", length)
        return self.reader.read(length, what).decode("utf-8", errors="replace")

    def _scalars(self, vtype: int, count: int, what: str) -> list[Any]:
        name, code = _SCALARS[vtype]
        size = struct.calcsize(code)
        data = self.reader.read(size * count, what)
        values = list(struct.unpack(f"<{count}{code}", data))
        if vtype == BOOL:
            if any(v not in (0, 1) for v in values):
                raise GGUFError(f"invalid bool value in {what}")
            return [bool(v) for v in values]
        return values

    def array(self, what: str, depth: int) -> list[Any] | SkippedValue:
        if depth > MAX_ARRAY_DEPTH:
            raise GGUFError(f"arrays nested deeper than {MAX_ARRAY_DEPTH} levels in {what}")
        etype = self.reader.u32(f"{what} element type")
        count = self.reader.u64(f"{what} length")
        # Even the smallest encoding of `count` elements must fit before anything is looped
        # over or allocated.
        self.reader.require(count * _min_size(etype, what), f"{what} ({count} elements)")
        type_name = f"array[{TYPE_NAMES[etype]}]"
        if count > self.max_array_items or count > self.items_left:
            self._skip_elements(etype, count, what, depth)
            return SkippedValue(type_name, count)
        self.items_left -= count
        if etype in _SCALARS:
            return self._scalars(etype, count, what)
        if etype == STRING:
            return [self.string(f"{what}[{index}]") for index in range(count)]
        return [self.array(f"{what}[{index}]", depth + 1) for index in range(count)]

    def _skip_elements(self, etype: int, count: int, what: str, depth: int) -> None:
        if etype in _SCALARS:
            self.reader.skip(count * struct.calcsize(_SCALARS[etype][1]), what)
            return
        for _ in range(count):
            if etype == STRING:
                self.reader.skip(self.reader.u64(f"{what} element length"), what)
            else:
                self._skip_array(what, depth + 1)

    def _skip_array(self, what: str, depth: int) -> None:
        if depth > MAX_ARRAY_DEPTH:
            raise GGUFError(f"arrays nested deeper than {MAX_ARRAY_DEPTH} levels in {what}")
        etype = self.reader.u32(f"{what} element type")
        count = self.reader.u64(f"{what} length")
        self.reader.require(count * _min_size(etype, what), f"{what} ({count} elements)")
        self._skip_elements(etype, count, what, depth)

    # -- sections ------------------------------------------------------------------------------

    def metadata(self, kv_count: int) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for index in range(kv_count):
            key = self.string(f"key {index}", limit=MAX_KEY_BYTES)
            if isinstance(key, SkippedValue) or not key:
                raise GGUFError(f"key {index} is empty or longer than {MAX_KEY_BYTES} bytes")
            if key in metadata:
                raise GGUFError(f"duplicate key {key!r}")
            label = repr(key)  # untrusted text: never print control characters raw
            vtype = self.reader.u32(f"type of {label}")
            metadata[key] = self.value(vtype, label)
        return metadata

    def tensor_infos(self, tensor_count: int, alignment: int) -> tuple[int, list[int]]:
        """Sum of element counts, plus the data offsets of tensors that hold data."""
        total = 0
        offsets: list[int] = []
        for index in range(tensor_count):
            what = f"tensor info {index}"
            name = self.string(f"{what} name", limit=MAX_TENSOR_NAME_BYTES)
            if isinstance(name, SkippedValue):
                raise GGUFError(f"{what}: name longer than {MAX_TENSOR_NAME_BYTES} bytes")
            n_dims = self.reader.u32(f"{what} n_dims")
            if n_dims > MAX_DIMS:
                raise GGUFError(f"{what} ({name!r}): {n_dims} dimensions, at most {MAX_DIMS}")
            dims = struct.unpack(f"<{n_dims}Q", self.reader.read(8 * n_dims, f"{what} dims"))
            self.reader.u32(f"{what} type")
            offset = self.reader.u64(f"{what} offset")
            if offset % alignment:
                raise GGUFError(f"{what} ({name!r}): offset {offset} is not aligned to {alignment}")
            elements = prod(dims)
            total += elements
            if elements:
                offsets.append(offset)
        return total, offsets


def _min_size(etype: int, what: str) -> int:
    """Smallest encoded size of one array element of type `etype`."""
    if etype in _SCALARS:
        return struct.calcsize(_SCALARS[etype][1])
    if etype == STRING:
        return 8
    if etype == ARRAY:
        return 12
    raise GGUFError(f"unknown array element type {etype} in {what}")


def read_metadata(
    path: Path,
    *,
    max_bytes: int = 64 << 20,
    tensors: bool = True,
    max_array_items: int = 1024,
    max_string_bytes: int = 4 << 20,
) -> GGUFInfo:
    """Read the header, metadata and (with `tensors`) tensor infos of a GGUF file.

    At most `max_bytes` bytes are read. Arrays longer than `max_array_items` and strings longer
    than `max_string_bytes` become `SkippedValue`s. With `tensors`, the tensor infos are read
    as well, to count parameters and to check that every tensor starts inside the file, which
    catches most truncated copies (tensor sizes are not computed, so a cut inside the last
    tensor goes unnoticed here; the registry's size and sha256 checks catch it).
    """
    file_size = path.stat().st_size
    with path.open("rb", buffering=1 << 20) as handle:
        reader = _Reader(handle, file_size, max_bytes)
        magic = reader.read(4, "magic")
        if magic != MAGIC:
            raise GGUFError(f"{path.name} is not a GGUF file (magic {magic!r})")
        version = reader.u32("version")
        if version not in SUPPORTED_VERSIONS:
            if version and not version & 0xFFFF:
                raise GGUFError(f"{path.name} is a big-endian GGUF file, which is not supported")
            raise GGUFError(f"{path.name}: unsupported GGUF version {version} (2 and 3 are)")
        tensor_count = reader.u64("tensor count")
        kv_count = reader.u64("metadata count")
        if kv_count > MAX_KV_COUNT:
            raise GGUFError(f"{path.name}: {kv_count} metadata keys, at most {MAX_KV_COUNT}")
        if tensor_count > MAX_TENSOR_COUNT:
            raise GGUFError(f"{path.name}: {tensor_count} tensors, at most {MAX_TENSOR_COUNT}")
        parser = _Parser(reader, max_array_items=max_array_items, max_string_bytes=max_string_bytes)
        metadata = parser.metadata(kv_count)
        info = GGUFInfo(version=version, tensor_count=tensor_count, metadata=metadata)
        if not tensors:
            return info
        alignment = _alignment(metadata)
        info.parameters, offsets = parser.tensor_infos(tensor_count, alignment)
        info.data_offset = reader.pos + (alignment - reader.pos % alignment) % alignment
        if offsets and info.data_offset + max(offsets) >= file_size:
            raise GGUFError(
                f"truncated file: {path.name} ends at byte {file_size}, before its tensor data "
                f"(the last tensor starts at byte {info.data_offset + max(offsets)})"
            )
        return info


def _alignment(metadata: dict[str, Any]) -> int:
    value = metadata.get("general.alignment", DEFAULT_ALIGNMENT)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value & (value - 1):
        raise GGUFError(f"general.alignment must be a power of two, got {value!r}")
    return value


# --------------------------------------------------------------------------------------------
# Helpers over the metadata
# --------------------------------------------------------------------------------------------


def _get(info: GGUFInfo, key: str, kind: type, check: Callable[[Any], bool] | None = None) -> Any:
    value = info.metadata.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        return None
    return value if check is None or check(value) else None


def architecture(info: GGUFInfo) -> str | None:
    """`general.architecture`, such as `qwen3` or `llama`."""
    value: str | None = _get(info, "general.architecture", str, bool)
    return value


def context_length(info: GGUFInfo) -> int | None:
    """`<architecture>.context_length`: the training context, in tokens."""
    arch = architecture(info)
    if arch is None:
        return None
    value: int | None = _get(info, f"{arch}.context_length", int, lambda v: v > 0)
    return value


def chat_template(info: GGUFInfo) -> str | None:
    """`tokenizer.chat_template`, the Jinja chat template, when the file carries one."""
    value: str | None = _get(info, "tokenizer.chat_template", str, bool)
    return value


def parameter_count(info: GGUFInfo) -> int | None:
    """Parameters counted over the tensor infos.

    None when the tensor infos were not read, or when the file is one part of a split model
    (`split.count` > 1), whose tensors are spread over several files.
    """
    splits = _get(info, "split.count", int)
    if splits is not None and splits > 1:
        return None
    return info.parameters


def quant_name(info: GGUFInfo) -> str | None:
    """The quantization named by `general.file_type` (for example `Q4_K_M`), if known."""
    value = _get(info, "general.file_type", int, lambda v: v >= 0)
    if value is None:
        return None
    return FILE_TYPES.get(value & ~FILE_TYPE_GUESSED)
