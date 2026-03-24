from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass

from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_CHUNK_TYPES = {b"tEXt", b"zTXt", b"iTXt"}


@dataclass
class PngChunk:
    chunk_type: bytes
    data: bytes
    crc_stored: int
    crc_calc: int
    offset: int

    @property
    def crc_ok(self) -> bool:
        return self.crc_stored == self.crc_calc


@dataclass
class PngRepackResult:
    output_bytes: bytes
    parsed_chunks: int
    bad_crc_count: int
    exif_tag_count: int
    copied_text_chunks: int


@dataclass
class PngInspectionResult:
    parsed_chunks: int
    bad_crc_count: int
    is_damaged: bool
    parse_error: str | None = None


class PngRepacker:
    """Rebuild PNG bytes using image-bearing chunks and optional metadata copy."""

    def __init__(
        self,
        *,
        copy_exif: bool = True,
        copy_text: bool = True,
        keep_idat_separate: bool = False,
        strict_crc: bool = False,
    ) -> None:
        self.copy_exif = bool(copy_exif)
        self.copy_text = bool(copy_text)
        self.keep_idat_separate = bool(keep_idat_separate)
        self.strict_crc = bool(strict_crc)

    @staticmethod
    def _is_valid_chunk_type(value: bytes) -> bool:
        return len(value) == 4 and all((65 <= b <= 90) or (97 <= b <= 122) for b in value)

    @staticmethod
    def _pack_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    def _parse_chunks(self, raw: bytes) -> list[PngChunk]:
        if len(raw) < 8 or raw[:8] != PNG_SIGNATURE:
            raise ValueError("Invalid PNG signature")

        chunks: list[PngChunk] = []
        offset = 8
        while True:
            if offset + 12 > len(raw):
                raise ValueError(f"Truncated chunk header at 0x{offset:x}")

            length = struct.unpack(">I", raw[offset:offset + 4])[0]
            chunk_type = raw[offset + 4:offset + 8]
            if not self._is_valid_chunk_type(chunk_type):
                raise ValueError(f"Invalid chunk type at 0x{offset + 4:x}")

            data_start = offset + 8
            data_end = data_start + length
            crc_start = data_end
            crc_end = crc_start + 4
            if crc_end > len(raw):
                name = chunk_type.decode("ascii", errors="replace")
                raise ValueError(f"Chunk {name} overruns file")

            data = raw[data_start:data_end]
            crc_stored = struct.unpack(">I", raw[crc_start:crc_end])[0]
            crc_calc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF

            chunks.append(
                PngChunk(
                    chunk_type=chunk_type,
                    data=data,
                    crc_stored=crc_stored,
                    crc_calc=crc_calc,
                    offset=offset,
                )
            )

            offset = crc_end
            if chunk_type == b"IEND":
                break

        return chunks

    @staticmethod
    def _extract_exif_payload(raw: bytes) -> tuple[bytes | None, int]:
        try:
            with Image.open(io.BytesIO(raw)) as img:
                exif = img.getexif()
                tag_count = len(exif or {})
                if tag_count <= 0:
                    return None, 0

                exif_bytes = exif.tobytes()
                if exif_bytes.startswith(b"Exif\x00\x00"):
                    exif_bytes = exif_bytes[6:]
                if not exif_bytes:
                    return None, 0
                return exif_bytes, tag_count
        except Exception:
            return None, 0

    def inspect_bytes(self, raw: bytes) -> PngInspectionResult:
        try:
            chunks = self._parse_chunks(raw)
        except Exception as exc:
            return PngInspectionResult(
                parsed_chunks=0,
                bad_crc_count=0,
                is_damaged=True,
                parse_error=str(exc),
            )

        bad_crc_count = sum(1 for chunk in chunks if not chunk.crc_ok)
        ihdr_misplaced = not chunks or chunks[0].chunk_type != b"IHDR"
        return PngInspectionResult(
            parsed_chunks=len(chunks),
            bad_crc_count=bad_crc_count,
            is_damaged=bad_crc_count > 0 or ihdr_misplaced,
            parse_error="IHDR is not the first chunk" if ihdr_misplaced else None,
        )

    def repack_bytes(self, raw: bytes) -> PngRepackResult:
        chunks = self._parse_chunks(raw)
        bad_crc_count = sum(1 for c in chunks if not c.crc_ok)
        if self.strict_crc and bad_crc_count > 0:
            raise ValueError(f"Input PNG has {bad_crc_count} bad CRC chunk(s)")

        ihdr = next((c for c in chunks if c.chunk_type == b"IHDR"), None)
        if ihdr is None:
            raise ValueError("Missing required IHDR chunk")

        plte = next((c for c in chunks if c.chunk_type == b"PLTE"), None)
        trns = next((c for c in chunks if c.chunk_type == b"tRNS"), None)
        idats = [c for c in chunks if c.chunk_type == b"IDAT"]
        if not idats:
            raise ValueError("No IDAT chunks found")

        exif_payload = None
        exif_tag_count = 0
        if self.copy_exif:
            exif_payload, exif_tag_count = self._extract_exif_payload(raw)

        text_chunks: list[PngChunk] = []
        if self.copy_text:
            text_chunks = [c for c in chunks if c.chunk_type in TEXT_CHUNK_TYPES]

        out = bytearray()
        out += PNG_SIGNATURE
        out += self._pack_chunk(b"IHDR", ihdr.data)

        if exif_payload:
            out += self._pack_chunk(b"eXIf", exif_payload)

        if plte is not None:
            out += self._pack_chunk(b"PLTE", plte.data)
        if trns is not None:
            out += self._pack_chunk(b"tRNS", trns.data)

        for c in text_chunks:
            out += self._pack_chunk(c.chunk_type, c.data)

        if self.keep_idat_separate:
            for c in idats:
                out += self._pack_chunk(b"IDAT", c.data)
        else:
            merged_idat = b"".join(c.data for c in idats)
            out += self._pack_chunk(b"IDAT", merged_idat)

        out += self._pack_chunk(b"IEND", b"")

        return PngRepackResult(
            output_bytes=bytes(out),
            parsed_chunks=len(chunks),
            bad_crc_count=bad_crc_count,
            exif_tag_count=exif_tag_count,
            copied_text_chunks=len(text_chunks),
        )
