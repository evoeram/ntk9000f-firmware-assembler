# SilverStone F1 NTK-9000F Firmware Assembler

A small, deterministic Python CLI for comparing SilverStone F1 NTK-9000F
firmware update images with a full 9000FDUO dump and assembling a candidate full
image from matching 4 KiB blocks.

> **Status:** reverse-engineering helper, not an official updater. The produced
> image must be validated with your own hardware-specific process before any use.


## Requirements

- Python 3.10 or newer.
- No runtime third-party dependencies.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
f9000fw all
```

Generated artifacts are written to `build/` by default:

- `F9000_block_comparison.txt`
- `F9000_partition_analysis.txt`
- `F9000_final_full_firmware.bin`
- `F9000_final_summary.txt`
- `manifest.json`

## CLI usage

Run all steps:

```bash
f9000fw all \
  --update1 inputs/9000F_update.bin \
  --update2 inputs/9000F_update2.bin \
  --duo-dump inputs/9000FDUO_DUMP.bin \
  --out-dir build
```

Run individual steps:

```bash
f9000fw compare --out build/F9000_block_comparison.txt
f9000fw analyze --out build/F9000_partition_analysis.txt
f9000fw assemble --out build/F9000_final_full_firmware.bin
```

You can also run the script directly without installation:

```bash
python f9000fw.py all
```

The `--block-size` option accepts decimal or `0x`-prefixed values. The default is
`0x1000` (4 KiB), matching the original scripts.

## Assembly rules

For each block offset, the assembler compares update #1, update #2, and the DUO
dump:

1. `ALL_MATCH`, `UPDATES_MATCH`, or `U2_DUO_MATCH`: use update #2 when present,
   otherwise update #1.
2. `U1_DUO_MATCH`: use update #1.
3. `DIFFERENT`: prefer the DUO dump, then update #2, then update #1.

The manifest records the block size, comparison counts, CRC32 checksums, and
SHA-256 checksums for all inputs and the generated output so runs can be
reproduced.

## Safety notes

- This tool does not cryptographically validate firmware signatures, bootloader
  headers, flashing safety, or device compatibility.
- Always keep a verified backup dump before experimenting with firmware images.
- Treat every assembled image as untrusted until independently validated.
