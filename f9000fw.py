#!/usr/bin/env python3
"""Firmware comparison and assembly utilities for SilverStone F1 NTK-9000F images.

The tool compares two NTK-9000F update images with a full 9000FDUO dump and builds
an assembled candidate image using deterministic block-selection rules. It does
not validate firmware authenticity and must be used only with images you are
legally allowed to analyze.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_BLOCK_SIZE = 0x1000
DEFAULT_UPDATE1 = Path("inputs/9000F_update.bin")
DEFAULT_UPDATE2 = Path("inputs/9000F_update2.bin")
DEFAULT_DUO_DUMP = Path("inputs/9000FDUO_DUMP.bin")
DEFAULT_OUT_DIR = Path("build")


class MatchType(str, Enum):
    """Relationship between same-offset blocks from all input images."""

    ALL_MATCH = "ALL_MATCH"
    UPDATES_MATCH = "UPDATES_MATCH"
    U1_DUO_MATCH = "U1_DUO_MATCH"
    U2_DUO_MATCH = "U2_DUO_MATCH"
    DIFFERENT = "DIFFERENT"


@dataclass(frozen=True)
class ImageInfo:
    """Basic reproducibility metadata for an input or generated image."""

    path: str
    size: int
    crc32: str
    sha256: str


@dataclass(frozen=True)
class BlockComparison:
    """Comparison result for one fixed-size block offset."""

    index: int
    start: int
    end: int
    match_type: MatchType
    crc_update1: str | None
    crc_update2: str | None
    crc_duo: str | None
    selected_source: str


@dataclass(frozen=True)
class FirmwareInputs:
    """Loaded firmware image bytes."""

    update1_path: Path
    update2_path: Path
    duo_dump_path: Path
    update1: bytes
    update2: bytes
    duo_dump: bytes


def crc32_hex(data: bytes) -> str:
    """Return an uppercase eight-character CRC32 string."""

    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def sha256_hex(data: bytes) -> str:
    """Return a lowercase SHA-256 digest string."""

    return sha256(data).hexdigest()


def image_info(path: Path, data: bytes) -> ImageInfo:
    """Build serializable metadata for an image."""

    return ImageInfo(str(path), len(data), crc32_hex(data), sha256_hex(data))


def read_binary(path: Path) -> bytes:
    """Read a binary file and raise a user-friendly error when it is missing."""

    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise SystemExit(f"Input file not found: {path}") from exc


def load_inputs(update1: Path, update2: Path, duo_dump: Path) -> FirmwareInputs:
    """Load all required firmware images."""

    return FirmwareInputs(
        update1_path=update1,
        update2_path=update2,
        duo_dump_path=duo_dump,
        update1=read_binary(update1),
        update2=read_binary(update2),
        duo_dump=read_binary(duo_dump),
    )


def slice_block(data: bytes, offset: int, block_size: int) -> bytes:
    """Return a block at offset or an empty block if offset is past EOF."""

    if offset >= len(data):
        return b""
    return data[offset : offset + block_size]


def classify_blocks(inputs: FirmwareInputs, block_size: int = DEFAULT_BLOCK_SIZE) -> list[BlockComparison]:
    """Compare inputs block-by-block and record the source selected for assembly."""

    max_len = max(len(inputs.update1), len(inputs.update2), len(inputs.duo_dump))
    if max_len == 0:
        raise SystemExit("All input images are empty; nothing to compare.")

    num_blocks = (max_len + block_size - 1) // block_size
    comparisons: list[BlockComparison] = []

    for index in range(num_blocks):
        start = index * block_size
        u1_block = slice_block(inputs.update1, start, block_size)
        u2_block = slice_block(inputs.update2, start, block_size)
        duo_block = slice_block(inputs.duo_dump, start, block_size)

        match_type = classify_block(u1_block, u2_block, duo_block)
        selected_source = select_source(match_type, bool(u1_block), bool(u2_block), bool(duo_block))
        comparisons.append(
            BlockComparison(
                index=index,
                start=start,
                end=min(start + block_size - 1, max_len - 1),
                match_type=match_type,
                crc_update1=crc32_hex(u1_block) if u1_block else None,
                crc_update2=crc32_hex(u2_block) if u2_block else None,
                crc_duo=crc32_hex(duo_block) if duo_block else None,
                selected_source=selected_source,
            )
        )

    return comparisons


def classify_block(update1: bytes, update2: bytes, duo_dump: bytes) -> MatchType:
    """Classify byte equality for one block."""

    if update1 == update2 == duo_dump:
        return MatchType.ALL_MATCH
    if update1 == update2:
        return MatchType.UPDATES_MATCH
    if update1 == duo_dump:
        return MatchType.U1_DUO_MATCH
    if update2 == duo_dump:
        return MatchType.U2_DUO_MATCH
    return MatchType.DIFFERENT


def select_source(match_type: MatchType, has_update1: bool, has_update2: bool, has_duo: bool) -> str:
    """Return the image source used by the assembler for a classified block."""

    if match_type in {MatchType.ALL_MATCH, MatchType.UPDATES_MATCH, MatchType.U2_DUO_MATCH}:
        return "update2" if has_update2 else "update1"
    if match_type == MatchType.U1_DUO_MATCH:
        return "update1"
    if has_duo:
        return "duo_dump"
    if has_update2:
        return "update2"
    if has_update1:
        return "update1"
    return "empty"


def assemble_image(
    inputs: FirmwareInputs,
    comparisons: Sequence[BlockComparison],
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> bytes:
    """Assemble a candidate image according to comparison decisions."""

    chunks: list[bytes] = []
    for comparison in comparisons:
        offset = comparison.index * block_size
        if comparison.selected_source == "update2":
            chunks.append(slice_block(inputs.update2, offset, block_size))
        elif comparison.selected_source == "update1":
            chunks.append(slice_block(inputs.update1, offset, block_size))
        elif comparison.selected_source == "duo_dump":
            chunks.append(slice_block(inputs.duo_dump, offset, block_size))
        elif comparison.selected_source == "empty":
            continue
        else:  # defensive branch for future source names
            raise ValueError(f"Unknown selected source: {comparison.selected_source}")
    return b"".join(chunks)


def comparison_counts(comparisons: Iterable[BlockComparison]) -> dict[str, int]:
    """Count comparison outcomes by match type for reports and manifests."""

    counts = {match_type.value: 0 for match_type in MatchType}
    for comparison in comparisons:
        counts[comparison.match_type.value] += 1
    return counts


def render_comparison(comparisons: Iterable[BlockComparison], block_size: int) -> str:
    """Render the block comparison report."""

    rows = [f"Block comparison (size {block_size} bytes)"]
    comparisons = list(comparisons)
    rows.append(f"Total blocks: {len(comparisons)}")
    rows.append("")
    for item in comparisons:
        rows.append(
            f"0x{item.start:06X}-0x{item.end:06X}: {item.match_type.value} "
            f"| selected={item.selected_source} "
            f"| CRCs: u1={item.crc_update1} u2={item.crc_update2} duo={item.crc_duo}"
        )
    return "\n".join(rows) + "\n"


def render_analysis(inputs: FirmwareInputs, boundaries: Sequence[int]) -> str:
    """Render a small heuristic partition/header analysis report."""

    images = [
        ("F9000 update #1", inputs.update1),
        ("F9000 update #2", inputs.update2),
        ("F9000DUO dump", inputs.duo_dump),
    ]
    rows = ["BLDR reverse engineering (heuristic)", ""]
    for name, data in images:
        rows.append(f"{name}: size={len(data)} (0x{len(data):X}) crc32={crc32_hex(data)}")

    rows.extend(["", "Header DWORDs with differing values (little-endian):"])
    header_len = min(64, len(inputs.update1), len(inputs.update2), len(inputs.duo_dump))
    for offset in range(0, header_len, 4):
        v1 = int.from_bytes(inputs.update1[offset : offset + 4], "little")
        v2 = int.from_bytes(inputs.update2[offset : offset + 4], "little")
        vd = int.from_bytes(inputs.duo_dump[offset : offset + 4], "little")
        if len({v1, v2, vd}) > 1:
            rows.append(f"0x{offset:02X}: u1=0x{v1:08X} u2=0x{v2:08X} duo=0x{vd:08X}")

    rows.extend(
        [
            "",
            "Likely fields:",
            "0x18 changes between all known images and may be an image-specific length/check field.",
            "0x30 differs between update #2 and update #1/DUO in the original sample set.",
            "",
            "Probable partition boundary candidates:",
        ]
    )
    rows.extend(f"0x{boundary:X}" for boundary in boundaries)
    rows.append("")
    rows.append("Warning: this is a heuristic report, not a firmware safety or authenticity validation.")
    return "\n".join(rows) + "\n"


def write_manifest(
    path: Path,
    inputs: FirmwareInputs,
    output_path: Path | None,
    output_data: bytes | None,
    *,
    block_size: int,
    comparisons: Sequence[BlockComparison],
) -> None:
    """Write a reproducibility manifest with checksums and assembly metadata."""

    payload: dict[str, object] = {
        "block_size": block_size,
        "comparison_counts": comparison_counts(comparisons),
        "inputs": {
            "update1": asdict(image_info(inputs.update1_path, inputs.update1)),
            "update2": asdict(image_info(inputs.update2_path, inputs.update2)),
            "duo_dump": asdict(image_info(inputs.duo_dump_path, inputs.duo_dump)),
        },
        "total_blocks": len(comparisons),
    }
    if output_path is not None and output_data is not None:
        payload["output"] = asdict(image_info(output_path, output_data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_summary(final_image: bytes, comparisons: Sequence[BlockComparison]) -> str:
    """Render checksums and comparison counts for an assembled image."""

    rows = [
        f"Final candidate image size: {len(final_image)} bytes",
        f"CRC32: {crc32_hex(final_image)}",
        f"SHA256: {sha256_hex(final_image)}",
        "",
        "Comparison counts:",
    ]
    rows.extend(f"{key}: {value}" for key, value in comparison_counts(comparisons).items())
    return "\n".join(rows) + "\n"


def ensure_output_path(path: Path) -> Path:
    """Create the parent output directory and return the path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def parse_block_size(value: str) -> int:
    """Parse decimal or 0x-prefixed block-size CLI values."""

    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a decimal or 0x-prefixed integer") from exc


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Attach shared input and block-size flags."""

    parser.add_argument(
        "--update1",
        type=Path,
        default=DEFAULT_UPDATE1,
        help=f"first update image (default: {DEFAULT_UPDATE1})",
    )
    parser.add_argument(
        "--update2",
        type=Path,
        default=DEFAULT_UPDATE2,
        help=f"second/latest update image (default: {DEFAULT_UPDATE2})",
    )
    parser.add_argument(
        "--duo-dump",
        type=Path,
        default=DEFAULT_DUO_DUMP,
        help=f"full DUO dump image (default: {DEFAULT_DUO_DUMP})",
    )
    parser.add_argument(
        "--block-size",
        type=parse_block_size,
        default=DEFAULT_BLOCK_SIZE,
        help="comparison block size; accepts decimal or 0x-prefixed values",
    )


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Analyze and assemble SilverStone F1 NTK-9000F firmware images."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare", help="write a block-by-block comparison report"
    )
    add_common_args(compare)
    compare.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR / "F9000_block_comparison.txt",
        help="comparison report path",
    )

    analyze = subparsers.add_parser(
        "analyze", help="write a heuristic partition/header analysis report"
    )
    add_common_args(analyze)
    analyze.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR / "F9000_partition_analysis.txt",
        help="analysis report path",
    )

    assemble = subparsers.add_parser(
        "assemble", help="assemble a candidate full firmware image"
    )
    add_common_args(assemble)
    assemble.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR / "F9000_final_full_firmware.bin",
        help="assembled firmware path",
    )
    assemble.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_OUT_DIR / "F9000_final_summary.txt",
        help="summary report path",
    )
    assemble.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_OUT_DIR / "manifest.json",
        help="checksum manifest path",
    )

    all_cmd = subparsers.add_parser("all", help="run compare, analyze, and assemble")
    add_common_args(all_cmd)
    all_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="directory for all generated artifacts",
    )

    return parser


def validate_block_size(block_size: int) -> None:
    """Validate the block size supplied by the user."""

    if block_size <= 0:
        raise SystemExit("--block-size must be greater than zero")


def command_compare(args: argparse.Namespace) -> None:
    validate_block_size(args.block_size)
    inputs = load_inputs(args.update1, args.update2, args.duo_dump)
    comparisons = classify_blocks(inputs, args.block_size)
    ensure_output_path(args.out).write_text(
        render_comparison(comparisons, args.block_size), encoding="utf-8"
    )
    print(args.out)


def command_analyze(args: argparse.Namespace) -> None:
    validate_block_size(args.block_size)
    inputs = load_inputs(args.update1, args.update2, args.duo_dump)
    boundaries = [
        0x800,
        0x1000,
        0x2000,
        0x4000,
        len(inputs.update1),
        len(inputs.update2),
        len(inputs.duo_dump),
    ]
    ensure_output_path(args.out).write_text(
        render_analysis(inputs, sorted(set(boundaries))), encoding="utf-8"
    )
    print(args.out)


def command_assemble(args: argparse.Namespace) -> None:
    validate_block_size(args.block_size)
    inputs = load_inputs(args.update1, args.update2, args.duo_dump)
    comparisons = classify_blocks(inputs, args.block_size)
    final_image = assemble_image(inputs, comparisons, args.block_size)
    ensure_output_path(args.out).write_bytes(final_image)
    summary = render_summary(final_image, comparisons)
    ensure_output_path(args.summary).write_text(summary, encoding="utf-8")
    write_manifest(
        args.manifest,
        inputs,
        args.out,
        final_image,
        block_size=args.block_size,
        comparisons=comparisons,
    )
    print(args.out)
    print(args.summary)
    print(args.manifest)


def command_all(args: argparse.Namespace) -> None:
    validate_block_size(args.block_size)
    inputs = load_inputs(args.update1, args.update2, args.duo_dump)
    comparisons = classify_blocks(inputs, args.block_size)
    final_image = assemble_image(inputs, comparisons, args.block_size)

    comparison_path = args.out_dir / "F9000_block_comparison.txt"
    analysis_path = args.out_dir / "F9000_partition_analysis.txt"
    firmware_path = args.out_dir / "F9000_final_full_firmware.bin"
    summary_path = args.out_dir / "F9000_final_summary.txt"
    manifest_path = args.out_dir / "manifest.json"

    ensure_output_path(comparison_path).write_text(
        render_comparison(comparisons, args.block_size), encoding="utf-8"
    )
    boundaries = [
        0x800,
        0x1000,
        0x2000,
        0x4000,
        len(inputs.update1),
        len(inputs.update2),
        len(inputs.duo_dump),
    ]
    ensure_output_path(analysis_path).write_text(
        render_analysis(inputs, sorted(set(boundaries))), encoding="utf-8"
    )
    ensure_output_path(firmware_path).write_bytes(final_image)
    summary = render_summary(final_image, comparisons)
    ensure_output_path(summary_path).write_text(summary, encoding="utf-8")
    write_manifest(
        manifest_path,
        inputs,
        firmware_path,
        final_image,
        block_size=args.block_size,
        comparisons=comparisons,
    )

    for path in [
        comparison_path,
        analysis_path,
        firmware_path,
        summary_path,
        manifest_path,
    ]:
        print(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "compare": command_compare,
        "analyze": command_analyze,
        "assemble": command_assemble,
        "all": command_all,
    }
    handlers[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
