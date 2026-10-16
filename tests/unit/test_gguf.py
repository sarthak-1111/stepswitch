"""GGUF metadata reader on small synthetic files (stepswitch.gguf)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from stepswitch import gguf
from stepswitch.gguf import (
    ARRAY,
    BOOL,
    FLOAT32,
    FLOAT64,
    INT8,
    INT16,
    INT32,
    INT64,
    STRING,
    UINT8,
    UINT16,
    UINT32,
    UINT64,
    GGUFError,
    SkippedValue,
)

# --------------------------------------------------------------------------------------------
# A minimal GGUF writer (little-endian, the layout of ggml docs/gguf.md)
# --------------------------------------------------------------------------------------------

_FORMATS = {
    UINT8: "B",
    INT8: "b",
    UINT16: "H",
    INT16: "h",
    UINT32: "I",
    INT32: "i",
    FLOAT32: "f",
    BOOL: "B",
    UINT64: "Q",
    INT64: "q",
    FLOAT64: "d",
}

KV = tuple[str, int, Any]
Tensor = tuple[str, list[int], int, int]  # name, dims, ggml type, offset


def gguf_string(text: str | bytes) -> bytes:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return struct.pack("<Q", len(data)) + data


def gguf_value(vtype: int, value: Any) -> bytes:
    if vtype == STRING:
        return gguf_string(value)
    if vtype == ARRAY:
        etype, items = value
        return struct.pack("<IQ", etype, len(items)) + b"".join(
            gguf_value(etype, item) for item in items
        )
    return struct.pack("<" + _FORMATS[vtype], value)


def build_gguf(
    kvs: list[KV],
    *,
    tensors: list[Tensor] | None = None,
    data_bytes: int = 0,
    version: int = 3,
    alignment: int = 32,
) -> bytes:
    tensors = tensors or []
    out = b"GGUF" + struct.pack("<IQQ", version, len(tensors), len(kvs))
    for key, vtype, value in kvs:
        out += gguf_string(key) + struct.pack("<I", vtype) + gguf_value(vtype, value)
    for name, dims, ggml_type, offset in tensors:
        out += gguf_string(name) + struct.pack("<I", len(dims))
        out += struct.pack(f"<{len(dims)}Q", *dims) + struct.pack("<IQ", ggml_type, offset)
    out += b"\0" * ((-len(out)) % alignment)
    return out + b"\0" * data_bytes


TEMPLATE = (
    "{%- for message in messages %}<|im_start|>{{ message.role }}\n{{ message.content }}"
    "<|im_end|>\n{%- endfor %}{%- if tools %}<tool_call>{{ tools | tojson }}</tool_call>"
    "{%- endif %}"
)

BASIC: list[KV] = [
    ("general.architecture", STRING, "qwen3"),
    ("general.name", STRING, "Tiny Test Model"),
    ("general.file_type", UINT32, 15),
    ("general.license", STRING, "apache-2.0"),
    ("qwen3.context_length", UINT32, 32768),
    ("qwen3.rope.freq_base", FLOAT32, 1000000.0),
    ("tokenizer.chat_template", STRING, TEMPLATE),
]


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------------------------


def test_reads_scalars_strings_and_helpers(tmp_path: Path) -> None:
    kvs = [
        *BASIC,
        ("test.u8", UINT8, 255),
        ("test.i8", INT8, -128),
        ("test.u16", UINT16, 65535),
        ("test.i16", INT16, -32768),
        ("test.i32", INT32, -5),
        ("test.u64", UINT64, 2**64 - 1),
        ("test.i64", INT64, -(2**63)),
        ("test.f64", FLOAT64, 0.25),
        ("test.flag", BOOL, 1),
        ("test.unicode", STRING, "größe ✓"),
    ]
    info = gguf.read_metadata(write(tmp_path / "m.gguf", build_gguf(kvs)))
    assert info.version == 3
    assert info.tensor_count == 0
    meta = info.metadata
    assert meta["test.u8"] == 255 and meta["test.i8"] == -128
    assert meta["test.u16"] == 65535 and meta["test.i16"] == -32768
    assert meta["test.u64"] == 2**64 - 1 and meta["test.i64"] == -(2**63)
    assert meta["test.f64"] == 0.25 and meta["qwen3.rope.freq_base"] == 1000000.0
    assert meta["test.flag"] is True
    assert meta["test.unicode"] == "größe ✓"
    assert gguf.architecture(info) == "qwen3"
    assert gguf.context_length(info) == 32768
    assert gguf.chat_template(info) == TEMPLATE
    assert gguf.quant_name(info) == "Q4_K_M"
    assert info.parameters == 0
    assert gguf.parameter_count(info) == 0


def test_version_2_is_read(tmp_path: Path) -> None:
    info = gguf.read_metadata(write(tmp_path / "v2.gguf", build_gguf(BASIC, version=2)))
    assert info.version == 2
    assert gguf.architecture(info) == "qwen3"


def test_small_arrays_and_nested_arrays(tmp_path: Path) -> None:
    kvs = [
        *BASIC,
        ("general.tags", ARRAY, (STRING, ["text-generation", "gguf"])),
        ("test.ints", ARRAY, (INT32, [1, -2, 3])),
        ("test.flags", ARRAY, (BOOL, [0, 1])),
        ("test.nested", ARRAY, (ARRAY, [(UINT8, [1, 2]), (UINT8, []), (UINT8, [3])])),
        ("test.deeper", ARRAY, (ARRAY, [(ARRAY, [(STRING, ["a", "b"])])])),
        ("test.empty", ARRAY, (STRING, [])),
        ("general.basename", STRING, "after-the-arrays"),
    ]
    info = gguf.read_metadata(write(tmp_path / "arrays.gguf", build_gguf(kvs)))
    meta = info.metadata
    assert meta["general.tags"] == ["text-generation", "gguf"]
    assert meta["test.ints"] == [1, -2, 3]
    assert meta["test.flags"] == [False, True]
    assert meta["test.nested"] == [[1, 2], [], [3]]
    assert meta["test.deeper"] == [[["a", "b"]]]
    assert meta["test.empty"] == []
    assert meta["general.basename"] == "after-the-arrays"


def test_large_arrays_are_skipped_but_counted(tmp_path: Path) -> None:
    tokens = [f"tok{i}" for i in range(3000)]
    kvs = [
        *BASIC,
        ("tokenizer.ggml.tokens", ARRAY, (STRING, tokens)),
        ("tokenizer.ggml.token_type", ARRAY, (INT32, [1] * 3000)),
        ("tokenizer.ggml.nested", ARRAY, (ARRAY, [(UINT16, [1, 2, 3])] * 2000)),
        ("tokenizer.ggml.eos_token_id", UINT32, 2999),
    ]
    info = gguf.read_metadata(write(tmp_path / "vocab.gguf", build_gguf(kvs)))
    meta = info.metadata
    assert meta["tokenizer.ggml.tokens"] == SkippedValue("array[string]", 3000)
    assert meta["tokenizer.ggml.token_type"] == SkippedValue("array[int32]", 3000)
    assert meta["tokenizer.ggml.nested"] == SkippedValue("array[array]", 2000)
    # The reader stayed in step: the key after the skipped arrays is intact.
    assert meta["tokenizer.ggml.eos_token_id"] == 2999


def test_long_strings_are_skipped(tmp_path: Path) -> None:
    path = write(tmp_path / "long.gguf", build_gguf(BASIC))
    info = gguf.read_metadata(path, max_string_bytes=16)
    assert info.metadata["tokenizer.chat_template"] == SkippedValue("string", len(TEMPLATE))
    assert gguf.chat_template(info) is None
    assert gguf.architecture(info) == "qwen3"


def test_quant_names_from_file_type(tmp_path: Path) -> None:
    def quant(file_type: int | None) -> str | None:
        kvs = [("general.architecture", STRING, "llama")]
        if file_type is not None:
            kvs.append(("general.file_type", UINT32, file_type))
        return gguf.quant_name(gguf.read_metadata(write(tmp_path / "q.gguf", build_gguf(kvs))))

    assert quant(0) == "F32"
    assert quant(7) == "Q8_0"
    assert quant(30) == "IQ4_XS"
    assert quant(32) == "BF16"
    assert quant(38) == "MXFP4_MOE"
    assert quant(1024 | 15) == "Q4_K_M"  # the "guessed" flag is ignored
    assert quant(999) is None
    assert quant(None) is None


def test_missing_architecture_means_no_context(tmp_path: Path) -> None:
    info = gguf.read_metadata(write(tmp_path / "a.gguf", build_gguf([("x.y", UINT8, 1)])))
    assert gguf.architecture(info) is None
    assert gguf.context_length(info) is None
    assert gguf.chat_template(info) is None


# --------------------------------------------------------------------------------------------
# Tensor infos
# --------------------------------------------------------------------------------------------


def test_parameter_count_from_tensor_infos(tmp_path: Path) -> None:
    tensors = [("token_embd.weight", [8, 4], 0, 0), ("output_norm.weight", [8], 0, 128)]
    data = build_gguf(BASIC, tensors=tensors, data_bytes=160)
    info = gguf.read_metadata(write(tmp_path / "t.gguf", data))
    assert info.tensor_count == 2
    assert info.parameters == 40
    assert gguf.parameter_count(info) == 40
    assert info.data_offset is not None and info.data_offset % 32 == 0
    assert gguf.read_metadata(tmp_path / "t.gguf", tensors=False).parameters is None


def test_split_parts_report_no_parameter_count(tmp_path: Path) -> None:
    kvs = [*BASIC, ("split.count", UINT16, 2)]
    data = build_gguf(kvs, tensors=[("a", [4], 0, 0)], data_bytes=32)
    info = gguf.read_metadata(write(tmp_path / "s.gguf", data))
    assert info.parameters == 4
    assert gguf.parameter_count(info) is None


def test_custom_alignment(tmp_path: Path) -> None:
    kvs = [*BASIC, ("general.alignment", UINT32, 64)]
    data = build_gguf(kvs, tensors=[("a", [4], 0, 64)], data_bytes=128, alignment=64)
    info = gguf.read_metadata(write(tmp_path / "al.gguf", data))
    assert info.data_offset is not None and info.data_offset % 64 == 0


def test_truncated_tensor_data_is_detected(tmp_path: Path) -> None:
    data = build_gguf(BASIC, tensors=[("a", [1024], 0, 0), ("b", [1024], 0, 4096)])
    path = write(tmp_path / "cut.gguf", data + b"\0" * 100)
    with pytest.raises(GGUFError, match="truncated"):
        gguf.read_metadata(path)
    # Metadata alone is still readable.
    assert gguf.architecture(gguf.read_metadata(path, tensors=False)) == "qwen3"


@pytest.mark.parametrize(
    ("tensors", "kvs", "message"),
    [
        ([("a", [1, 1, 1, 1, 1], 0, 0)], [], "dimensions"),
        ([("a", [4], 0, 7)], [], "not aligned"),
        ([("x" * 65, [4], 0, 0)], [], "name longer"),
        ([("a", [4], 0, 0)], [("general.alignment", UINT32, 3)], "power of two"),
        ([("a", [4], 0, 0)], [("general.alignment", STRING, "32")], "power of two"),
    ],
)
def test_malformed_tensor_infos(
    tmp_path: Path, tensors: list[Tensor], kvs: list[KV], message: str
) -> None:
    data = build_gguf([*BASIC, *kvs], tensors=tensors, data_bytes=64)
    with pytest.raises(GGUFError, match=message):
        gguf.read_metadata(write(tmp_path / "bad.gguf", data))


# --------------------------------------------------------------------------------------------
# Malformed and hostile files
# --------------------------------------------------------------------------------------------


def test_every_truncation_of_the_metadata_raises(tmp_path: Path) -> None:
    kvs = [
        *BASIC,
        ("general.tags", ARRAY, (STRING, ["a", "bc"])),
        ("test.nested", ARRAY, (ARRAY, [(INT16, [1, 2])])),
    ]
    data = build_gguf(kvs, tensors=[("a", [2, 2], 0, 0)], data_bytes=32)
    metadata_end = len(build_gguf(kvs, alignment=1))  # header and key-value pairs, no padding
    path = tmp_path / "prefix.gguf"
    for cut in range(metadata_end):
        path.write_bytes(data[:cut])
        with pytest.raises(GGUFError):
            gguf.read_metadata(path, tensors=False)
    # With the tensor infos, every cut up to the start of the tensor data fails as well.
    data_offset = len(build_gguf(kvs, tensors=[("a", [2, 2], 0, 0)]))
    for cut in range(data_offset + 1):
        path.write_bytes(data[:cut])
        with pytest.raises(GGUFError):
            gguf.read_metadata(path)
    path.write_bytes(data)
    assert gguf.read_metadata(path).data_offset == data_offset


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GGML" + b"\0" * 20, "not a GGUF file"),
        (b"GGUF" + struct.pack("<IQQ", 1, 0, 0), "unsupported GGUF version 1"),
        (b"GGUF" + struct.pack("<IQQ", 4, 0, 0), "unsupported GGUF version 4"),
        (b"GGUF" + struct.pack(">IQQ", 3, 0, 0), "big-endian"),
        (b"", "truncated"),
    ],
)
def test_bad_headers(tmp_path: Path, data: bytes, message: str) -> None:
    with pytest.raises(GGUFError, match=message):
        gguf.read_metadata(write(tmp_path / "h.gguf", data))


def _header(kv_count: int, tensor_count: int = 0) -> bytes:
    return b"GGUF" + struct.pack("<IQQ", 3, tensor_count, kv_count)


@pytest.mark.parametrize(
    ("body", "kv_count", "message"),
    [
        # A key whose length claims far more bytes than the file holds.
        (struct.pack("<Q", 2**63), 1, "truncated"),
        # A string value of 2^62 bytes.
        (gguf_string("k") + struct.pack("<IQ", STRING, 2**62), 1, "truncated"),
        # An array of 2^62 uint64 values: rejected before looping or allocating.
        (gguf_string("k") + struct.pack("<IIQ", ARRAY, UINT64, 2**62), 1, "truncated"),
        # An array of 2^40 strings.
        (gguf_string("k") + struct.pack("<IIQ", ARRAY, STRING, 2**40), 1, "truncated"),
        # An unknown value type.
        (gguf_string("k") + struct.pack("<I", 13), 1, "unknown value type"),
        (gguf_string("k") + struct.pack("<IIQ", ARRAY, 42, 1), 1, "unknown array element"),
        # Booleans other than 0 and 1.
        (gguf_string("k") + struct.pack("<IB", BOOL, 2), 1, "invalid bool"),
        # Empty and duplicate keys.
        (gguf_string("") + struct.pack("<IB", UINT8, 1), 1, "empty"),
        (
            (gguf_string("k") + struct.pack("<IB", UINT8, 1)) * 2,
            2,
            "duplicate key",
        ),
    ],
)
def test_hostile_metadata(tmp_path: Path, body: bytes, kv_count: int, message: str) -> None:
    path = write(tmp_path / "hostile.gguf", _header(kv_count) + body + b"\0" * 64)
    with pytest.raises(GGUFError, match=message):
        gguf.read_metadata(path)


def test_huge_counts_in_the_header_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(GGUFError, match="metadata keys"):
        gguf.read_metadata(write(tmp_path / "kv.gguf", _header(2**40)))
    with pytest.raises(GGUFError, match="tensors"):
        gguf.read_metadata(write(tmp_path / "t.gguf", _header(0, tensor_count=2**40)))


def test_arrays_nested_too_deeply(tmp_path: Path) -> None:
    value: Any = (UINT8, [1])
    for _ in range(gguf.MAX_ARRAY_DEPTH):
        value = (ARRAY, [value])
    data = build_gguf([("deep", ARRAY, value)])
    with pytest.raises(GGUFError, match="nested deeper"):
        gguf.read_metadata(write(tmp_path / "deep.gguf", data))


def test_max_bytes_limits_the_read(tmp_path: Path) -> None:
    path = write(tmp_path / "big.gguf", build_gguf(BASIC))
    with pytest.raises(GGUFError, match="read limit"):
        gguf.read_metadata(path, max_bytes=64)
    assert gguf.read_metadata(path, max_bytes=1 << 20).metadata["general.name"] == (
        "Tiny Test Model"
    )


def test_invalid_utf8_is_replaced_not_fatal(tmp_path: Path) -> None:
    data = build_gguf([("general.name", STRING, b"bad \xff name")])
    info = gguf.read_metadata(write(tmp_path / "utf.gguf", data))
    assert info.metadata["general.name"] == "bad � name"
