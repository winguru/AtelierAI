#!/usr/bin/env python3
"""Attempt to repair PNG chunks by reinserting missing CR bytes before LF bytes.

This is a direct Python adaptation of a known Ruby one-off script. It scans for
PNG chunk type markers as raw text, then tries CR insertion combinations when
chunk CRC checks fail and the chunk appears to be missing bytes.
"""

from __future__ import annotations

import argparse
import itertools
import struct
import sys
import zlib
from pathlib import Path

CHUNK_TYPES = [b"IHDR", b"PLTE", b"IDAT", b"IEND", b"sBIT", b"pHYs", b"tEXt"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx == -1:
            break
        offsets.append(idx)
        start = idx + 1
    return offsets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair potentially CR-stripped PNG chunks by CRC brute-force."
    )
    parser.add_argument("input_file", type=Path, help="Path to source PNG")
    parser.add_argument(
        "output_file",
        nargs="?",
        type=Path,
        help="Path to write repaired PNG (default: <input>.recovered.png)",
    )
    parser.add_argument(
        "--max-combinations",
        type=int,
        default=2_000_000,
        help="Safety cap for brute-force combinations per chunk (default: 2000000)",
    )
    parser.add_argument(
        "--force-heuristic",
        action="store_true",
        help="Skip strict PNG parsing and use text-scan repair directly.",
    )
    return parser.parse_args()


def chunk_name(chunk: bytes) -> str:
    if len(chunk) >= 8:
        return chunk[4:8].decode("ascii", errors="replace")
    return "????"


def _is_valid_chunk_type(raw: bytes) -> bool:
    # PNG chunk type bytes are ASCII letters only.
    return len(raw) == 4 and all((65 <= b <= 90) or (97 <= b <= 122) for b in raw)


def _strict_png_diagnostics(data: bytes) -> tuple[bool, str, int, int]:
    """Return (ok, reason, chunk_count, bad_crc_count) using strict PNG parsing."""
    if len(data) < len(PNG_SIGNATURE) or data[:8] != PNG_SIGNATURE:
        return False, "Invalid PNG signature", 0, 0

    offset = 8
    chunk_count = 0
    bad_crc_count = 0

    while True:
        if offset + 12 > len(data):
            return False, f"Truncated chunk header at 0x{offset:x}", chunk_count, bad_crc_count

        length = struct.unpack(">I", data[offset:offset + 4])[0]
        ctype = data[offset + 4:offset + 8]
        if not _is_valid_chunk_type(ctype):
            return False, f"Invalid chunk type at 0x{offset + 4:x}", chunk_count, bad_crc_count

        end = offset + 12 + length
        if end > len(data):
            return False, f"Chunk {ctype.decode('ascii', errors='replace')} overruns file", chunk_count, bad_crc_count

        stored_crc = struct.unpack(">I", data[end - 4:end])[0]
        calc_crc = zlib.crc32(data[offset + 4:end - 4]) & 0xFFFFFFFF
        if stored_crc != calc_crc:
            bad_crc_count += 1

        chunk_count += 1
        if ctype == b"IEND":
            trailing = len(data) - end
            if trailing != 0:
                return False, f"Trailing bytes after IEND ({trailing} bytes)", chunk_count, bad_crc_count
            break

        offset = end

    return True, "OK", chunk_count, bad_crc_count


def _repair_png_heuristic(data: bytes, output_file: Path, max_combinations: int) -> int:
    """Heuristic repair mode: scans for known chunk names and brute-forces CR insertions."""

    # Correct header if necessary.
    if len(data) > 4 and data[4] != 0x0D:
        data = data[:4] + b"\r" + data[4:]

    offsets: list[int] = []
    for chunk_type in CHUNK_TYPES:
        for pos in find_all(data, chunk_type):
            start = pos - 4
            if start >= 0:
                offsets.append(start)

    offsets = sorted(set(offsets))
    offsets.append(len(data))

    fixed_chunks = 0
    with output_file.open("wb") as out:
        out.write(data[:8])

        for i in range(len(offsets) - 1):
            start = offsets[i]
            end = offsets[i + 1]
            chunk = data[start:end]

            if len(chunk) < 12:
                print(
                    f"[!] Cannot process short chunk-like segment @ 0x{start:x}"
                )
                out.write(chunk)
                continue

            size = struct.unpack(">I", chunk[0:4])[0]
            crc = struct.unpack(">I", chunk[-4:])[0]
            crc_actual = zlib.crc32(chunk[4:-4]) & 0xFFFFFFFF

            if crc_actual == crc:
                out.write(chunk)
                continue

            missing = size - (len(chunk) - 12)
            ctype = chunk_name(chunk)
            if missing == 0:
                print(
                    f"[!] Cannot fix broken {ctype} chunk @ 0x{start:x}: No missing bytes!"
                )
                out.write(chunk)
                continue
            if missing < 0:
                print(
                    f"[!] Cannot fix broken {ctype} chunk @ 0x{start:x}: Incorrect size!"
                )
                out.write(chunk)
                continue

            print(f"[*] {ctype} @ 0x{start:x} is missing {missing} bytes")

            linefeeds = [idx for idx, b in enumerate(chunk) if b == 0x0A]
            if len(linefeeds) < missing:
                print(
                    f"[!] Cannot fix broken {ctype} chunk @ 0x{start:x}: No valid solutions!"
                )
                out.write(chunk)
                continue

            total = 1
            # Compute nCk safely without importing math.comb for older compatibility.
            for n, d in zip(range(len(linefeeds), len(linefeeds) - missing, -1), range(1, missing + 1)):
                total = (total * n) // d

            if total <= 0:
                print(
                    f"[!] Cannot fix broken {ctype} chunk @ 0x{start:x}: No valid solutions!"
                )
                out.write(chunk)
                continue

            if total > max_combinations:
                print(
                    f"[!] Skipping {ctype} @ 0x{start:x}: {total} possibilities exceeds cap {max_combinations}"
                )
                out.write(chunk)
                continue

            print(f"[*] Trying all {total} possible ways to make CRC 0x{crc:08x} match...")

            success = False
            for combo in itertools.combinations(linefeeds, missing):
                candidate = bytearray(chunk)
                inserted = 0
                for pos in combo:
                    idx = pos + inserted
                    candidate[idx:idx] = b"\r"
                    inserted += 1

                candidate_crc = zlib.crc32(candidate[4:-4]) & 0xFFFFFFFF
                if candidate_crc == crc:
                    print(f"[+] Fixed broken {ctype} chunk @ 0x{start:x}")
                    out.write(candidate)
                    fixed_chunks += 1
                    success = True
                    break

            if not success:
                print(
                    f"[!] Cannot fix broken {ctype} chunk @ 0x{start:x}: No valid solutions!"
                )
                out.write(chunk)

    print(f"[+] Wrote repaired file: {output_file}")
    print(f"[+] Fixed chunks: {fixed_chunks}")
    return fixed_chunks


def repair_png(
    input_file: Path,
    output_file: Path,
    max_combinations: int,
    force_heuristic: bool,
) -> int:
    data = input_file.read_bytes()
    print(f"[+] Read file of size {len(data)}")

    if force_heuristic:
        print("[*] Running heuristic mode (--force-heuristic)")
        return _repair_png_heuristic(data, output_file, max_combinations)

    ok, reason, chunk_count, bad_crc_count = _strict_png_diagnostics(data)
    if ok:
        print(f"[+] Strict PNG parse OK ({chunk_count} chunks, {bad_crc_count} bad CRC)")
        if bad_crc_count == 0:
            output_file.write_bytes(data)
            print("[+] PNG appears structurally valid; copied file without modification.")
            print(f"[+] Wrote repaired file: {output_file}")
            print("[+] Fixed chunks: 0")
            return 0

        print("[*] CRC mismatches detected in strict parse; attempting heuristic repair.")
        return _repair_png_heuristic(data, output_file, max_combinations)

    print(f"[*] Strict PNG parse failed: {reason}")
    print("[*] Falling back to heuristic repair scan.")
    return _repair_png_heuristic(data, output_file, max_combinations)


def main() -> int:
    args = parse_args()
    input_file: Path = args.input_file
    output_file: Path = args.output_file or input_file.with_name(
        f"{input_file.stem}.recovered{input_file.suffix}"
    )

    if not input_file.exists() or not input_file.is_file():
        print(f"Input file not found: {input_file}", file=sys.stderr)
        return 2

    if input_file.suffix.lower() != ".png":
        print("Warning: input does not have .png extension; proceeding anyway.")

    try:
        repair_png(
            input_file,
            output_file,
            max_combinations=max(1, args.max_combinations),
            force_heuristic=bool(args.force_heuristic),
        )
        return 0
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
