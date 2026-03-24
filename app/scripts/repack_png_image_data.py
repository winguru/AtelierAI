#!/usr/bin/env python3
"""CLI wrapper around atelierai.utils.PngRepacker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from atelierai.utils import PngRepacker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract image-bearing PNG chunks and rebuild a clean PNG."
    )
    parser.add_argument("input_file", type=Path, help="Source PNG file")
    parser.add_argument(
        "output_file",
        nargs="?",
        type=Path,
        help="Output PNG path (default: <input>.repacked.png)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when any input chunk CRC is invalid.",
    )
    parser.add_argument(
        "--keep-idat-separate",
        action="store_true",
        help="Keep original IDAT chunk boundaries instead of coalescing.",
    )
    parser.add_argument(
        "--no-copy-exif",
        action="store_true",
        help="Do not copy parsed EXIF/IFD tags into rebuilt PNG.",
    )
    parser.add_argument(
        "--no-copy-text",
        action="store_true",
        help="Do not copy PNG text metadata chunks (tEXt/zTXt/iTXt).",
    )
    return parser.parse_args()


def verify_with_pillow(path: Path) -> tuple[bool, str]:
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img2:
            size = img2.size
            mode = img2.mode
        return True, f"decode OK ({size[0]}x{size[1]}, mode={mode})"
    except Exception as exc:
        return False, f"decode failed: {type(exc).__name__}: {exc}"


def main() -> int:
    args = parse_args()
    input_file = args.input_file
    output_file = args.output_file or input_file.with_name(
        f"{input_file.stem}.repacked{input_file.suffix}"
    )

    if not input_file.exists() or not input_file.is_file():
        print(f"Input file not found: {input_file}", file=sys.stderr)
        return 2

    raw = input_file.read_bytes()
    print(f"[+] Read file of size {len(raw)}")

    repacker = PngRepacker(
        copy_exif=not args.no_copy_exif,
        copy_text=not args.no_copy_text,
        keep_idat_separate=bool(args.keep_idat_separate),
        strict_crc=bool(args.strict),
    )

    try:
        result = repacker.repack_bytes(raw)
    except Exception as exc:
        print(f"[!] Could not repack PNG: {exc}")
        return 1

    output_file.write_bytes(result.output_bytes)
    print(
        "[+] Repack stats: "
        f"parsed_chunks={result.parsed_chunks}, "
        f"bad_crc_count={result.bad_crc_count}, "
        f"exif_tags={result.exif_tag_count}, "
        f"text_chunks={result.copied_text_chunks}"
    )
    print(f"[+] Wrote repacked PNG: {output_file}")

    ok_in, msg_in = verify_with_pillow(input_file)
    ok_out, msg_out = verify_with_pillow(output_file)
    print(f"[+] Input verify:  {msg_in}")
    print(f"[+] Output verify: {msg_out}")

    if ok_out:
        print("[+] Repacked image is decodable.")
        return 0

    print("[!] Repacked image still fails decode; IDAT stream is likely corrupted.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
