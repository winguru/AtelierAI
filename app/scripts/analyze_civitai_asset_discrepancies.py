#!/usr/bin/env python3
"""Analyze image library asset discrepancies — covers all active images, not just CivitAI.

Detects mismatches between database metadata, CivitAI payload metadata, and actual
downloaded files. Use this after a fresh import or to audit a running library.

Issue types detected:
  MISSING_LOCAL_FILE       — DB references a file that doesn't exist on disk
  EMPTY_FILE               — File on disk has 0 bytes
  CORRUPTED_MAGIC_BYTES    — File magic bytes don't match its extension
  CORRUPTED_CONTENT        — PIL cannot open the image (malformed/truncated)
  DB_SIZE_MISMATCH         — DB file_size differs from actual stat().st_size
  DB_MIME_EXT_MISMATCH     — DB mimetype category (image/video) differs from extension
  DB_DIMS_ZERO             — DB dimensions are zero for an image (width=0 or height=0)
  CIVITAI_STALE_URL        — Local=video but civitai.url points to image (metadata not updated)
  CIVITAI_UNEXPECTED_IMAGE — Local=image but civitai.url is a video URL with no archived static
  CIVITAI_MIME_CONFLICT    — DB mimetype conflicts with civitai.mimeType when both are present
    CIVITAI_DECLARED_VIDEO_SERVED_IMAGE — CivitAI declared video but fetched content was image

Severity levels:
  critical — data integrity problem; asset likely unusable or misleading
  warning  — inconsistency that should be reviewed; asset may still display
  info     — cosmetic/informational; low risk

Usage:
  python analyze_civitai_asset_discrepancies.py
  python analyze_civitai_asset_discrepancies.py --sample 200 --detailed
  python analyze_civitai_asset_discrepancies.py --all           # include non-CivitAI
  python analyze_civitai_asset_discrepancies.py --probe-http    # check remote 404s (slow)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: import path setup
from backend.config import IMAGE_LIBRARY_PATH, IMAGE_RESOURCES_PATH
from backend.database import SessionLocal
from backend.models import ImageModel

# Optional PIL for content-level corruption detection
try:
    from PIL import Image as PilImage
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "video/avi"}
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff"}
_VIDEO_EXTS  = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv"}
_IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}

# Magic byte signatures keyed by extension
_MAGIC = {
    ".png":  (0, b'\x89PNG\r\n\x1a\n'),
    ".jpg":  (0, b'\xff\xd8'),
    ".jpeg": (0, b'\xff\xd8'),
    ".webp": [(0, b'RIFF'), (8, b'WEBP')],   # two checks
    ".gif":  (0, b'GIF8'),
    ".mp4":  None,   # Too many valid box structures; skip magic for mp4
    ".webm": (0, b'\x1a\x45\xdf\xa3'),
}

_CIVITAI_IMAGE_ID_RE = re.compile(r"/images/(\d+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_mime(value: Optional[str]) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _asset_category(mime: Optional[str], path: Optional[str]) -> str:
    """Return 'image', 'video', or 'unknown'."""
    m = _norm_mime(mime)
    if m.startswith("video/"):
        return "video"
    if m.startswith("image/"):
        return "image"
    if path:
        s = Path(str(path)).suffix.lower()
        if s in _VIDEO_EXTS:
            return "video"
        if s in _IMAGE_EXTS:
            return "image"
    return "unknown"


def _url_asset_category(url: Optional[str]) -> str:
    """Determine asset category from a URL by inspecting the path suffix."""
    text = str(url or "").strip()
    if not text:
        return "unknown"
    try:
        suffix = Path(urlparse(text).path).suffix.lower()
    except Exception:
        return "unknown"
    if suffix in _VIDEO_EXTS:
        return "video"
    if suffix in _IMAGE_EXTS:
        return "image"
    return "unknown"


def _extract_civitai_image_id(source_url: Optional[str]) -> Optional[int]:
    text = str(source_url or "").strip()
    if not text:
        return None
    match = _CIVITAI_IMAGE_ID_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            pass
    return None


def _check_magic_bytes(file_path: Path) -> Optional[str]:
    """
    Return an error string if magic bytes are wrong, or None if OK.
    Only checked for extensions where we have a known signature.
    """
    suffix = file_path.suffix.lower()
    spec = _MAGIC.get(suffix)
    if spec is None:
        return None  # Unknown or skip (e.g. .mp4)

    try:
        with open(file_path, "rb") as f:
            header = f.read(16)

        # Single-check formats
        if isinstance(spec, tuple):
            offset, expected = spec
            actual = header[offset:offset + len(expected)]
            if actual != expected:
                return (
                    f"Expected magic {expected.hex()} at offset {offset}, "
                    f"got {actual.hex()}"
                )

        # Multi-check formats (e.g. WEBP needs RIFF at 0 and WEBP at 8)
        elif isinstance(spec, list):
            for offset, expected in spec:
                actual = header[offset:offset + len(expected)]
                if actual != expected:
                    return (
                        f"Expected {expected.hex()} at offset {offset}, "
                        f"got {actual.hex()}"
                    )

    except IOError as exc:
        return f"Cannot read file: {exc}"

    return None


def _check_pil_openable(file_path: Path) -> Optional[str]:
    """Try to open image with PIL. Returns error string or None."""
    if not _HAS_PIL or PilImage is None:  # type: ignore[truthy-function]
        return None
    suffix = file_path.suffix.lower()
    if suffix in _VIDEO_EXTS:
        return None  # PIL can't do video; skip
    try:
        with PilImage.open(file_path) as img:  # type: ignore[union-attr]
            img.verify()  # Checks structure without decoding all pixels
    except Exception as exc:
        return f"PIL cannot open: {exc}"
    return None


def _probe_http_status(url: str, timeout: int = 8) -> int:
    """Return HTTP status code for a URL, or 0 on connection error."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0 (compatible; AtelierAI-Audit/1.0)")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    issue_type: str
    severity: str   # "critical" | "warning" | "info"
    description: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageRecord:
    image_id: int
    file_hash: str
    source_url: Optional[str]
    is_civitai: bool
    db_mimetype: Optional[str]
    db_file_size: Optional[int]
    db_width: Optional[int]
    db_height: Optional[int]
    db_file_path: str
    civitai_url: Optional[str]
    civitai_mime: Optional[str]
    has_archived_static: bool
    issues: list[Issue] = field(default_factory=list)

    def has_issues(self) -> bool:
        return bool(self.issues)

    def by_severity(self, s: str) -> list[Issue]:
        return [i for i in self.issues if i.severity == s]


# ---------------------------------------------------------------------------
# Core analysis per image
# ---------------------------------------------------------------------------

def _analyze_one(image: ImageModel, *, probe_http: bool) -> ImageRecord:
    # Coerce SQLAlchemy column values to plain Python types
    image_id: int      = int(image.id)  # type: ignore[arg-type]
    file_hash: str     = str(image.file_hash or "")
    file_path_str: str = str(image.file_path or "")
    db_mimetype: Optional[str]  = str(image.mimetype or "").strip() or None
    db_file_size: Optional[int] = int(image.file_size) if image.file_size is not None else None  # type: ignore[arg-type]
    db_width: Optional[int]     = int(image.width)  if image.width  is not None else None  # type: ignore[arg-type]
    db_height: Optional[int]    = int(image.height) if image.height is not None else None  # type: ignore[arg-type]

    source_url = str(image.source_url or "").strip()
    is_civitai = "civitai.com" in source_url

    json_meta: dict[str, Any] = {}
    raw_meta = image.json_metadata
    if isinstance(raw_meta, dict):
        json_meta = raw_meta

    civitai_block: dict[str, Any] = {}
    if isinstance(json_meta.get("civitai"), dict):
        civitai_block = json_meta["civitai"]  # type: ignore[assignment]
    civitai_source_variant = json_meta.get("civitai_source_variant")

    civitai_url  = str(civitai_block.get("url") or "").strip() or None
    civitai_mime = str(civitai_block.get("mimeType") or "").strip() or None
    has_archived = bool(json_meta.get("civitai_source_variant_static"))

    record = ImageRecord(
        image_id=image_id,
        file_hash=file_hash,
        source_url=source_url or None,
        is_civitai=is_civitai,
        db_mimetype=db_mimetype,
        db_file_size=db_file_size,
        db_width=db_width,
        db_height=db_height,
        db_file_path=file_path_str,
        civitai_url=civitai_url,
        civitai_mime=civitai_mime,
        has_archived_static=has_archived,
    )

    file_path = Path(IMAGE_LIBRARY_PATH) / file_path_str
    local_category = _asset_category(db_mimetype, file_path_str)
    civitai_url_category = _url_asset_category(civitai_url)

    # ---- MISSING / EMPTY file ------------------------------------------------
    if not file_path.exists():
        record.issues.append(Issue(
            "MISSING_LOCAL_FILE", "critical",
            f"File not on disk: {image.file_path}",
            {"file_path": str(image.file_path)},
        ))
        return record  # all further checks require the file

    actual_size = file_path.stat().st_size
    if actual_size == 0:
        record.issues.append(Issue(
            "EMPTY_FILE", "critical",
            "File exists but is 0 bytes",
            {"file_path": str(image.file_path)},
        ))
        return record

    # ---- Magic bytes ---------------------------------------------------------
    magic_error = _check_magic_bytes(file_path)
    if magic_error:
        record.issues.append(Issue(
            "CORRUPTED_MAGIC_BYTES", "critical",
            f"File magic bytes mismatch for {file_path.suffix}: {magic_error}",
            {"file_path": str(image.file_path), "error": magic_error},
        ))

    # ---- PIL content check (images only) ------------------------------------
    if not magic_error:  # Skip PIL if magic already failed
        pil_error = _check_pil_openable(file_path)
        if pil_error:
            record.issues.append(Issue(
                "CORRUPTED_CONTENT", "critical",
                f"Image content unreadable: {pil_error}",
                {"file_path": str(image.file_path), "error": pil_error},
            ))

    # ---- DB file_size vs actual ----------------------------------------------
    if db_file_size is not None and abs(db_file_size - actual_size) > 512:
        record.issues.append(Issue(
            "DB_SIZE_MISMATCH", "warning",
            f"DB file_size={db_file_size} but actual={actual_size} "
            f"(delta={abs(db_file_size - actual_size)})",
            {"db_size": db_file_size, "actual_size": actual_size},
        ))

    # ---- DB MIME vs file extension mismatch ---------------------------------
    ext_category = _asset_category(None, file_path_str)  # extension only
    if (local_category != "unknown" and ext_category != "unknown"
            and local_category != ext_category):
        record.issues.append(Issue(
            "DB_MIME_EXT_MISMATCH", "warning",
            f"DB mimetype '{db_mimetype}' is {local_category} "
            f"but file extension '{file_path.suffix}' implies {ext_category}",
            {"db_mimetype": db_mimetype, "extension": file_path.suffix},
        ))

    # ---- Zero dimensions for images -----------------------------------------
    if local_category == "image":
        w, h = db_width or 0, db_height or 0
        if w == 0 or h == 0:
            record.issues.append(Issue(
                "DB_DIMS_ZERO", "warning",
                f"Image has zero/null dimension in DB: width={w}, height={h}",
                {"width": w, "height": h},
            ))

    # ---- CivitAI-specific checks -------------------------------------------
    if is_civitai and civitai_url:

        # local=video but civitai.url is an image URL: stale metadata
        # (happens after backfill replaced a static image with a video download,
        #  but json_metadata.civitai.url was never refreshed)
        if local_category == "video" and civitai_url_category == "image":
            record.issues.append(Issue(
                "CIVITAI_STALE_URL", "warning",
                "Local asset is video but civitai.url points to an image URL "
                "(metadata not updated after video replacement)",
                {"db_mimetype": db_mimetype, "civitai_url": civitai_url},
            ))

        # local=image, civitai.url=video, no archived static variant
        if local_category == "image" and civitai_url_category == "video":
            if not has_archived:
                record.issues.append(Issue(
                    "CIVITAI_UNEXPECTED_IMAGE", "warning",
                    "Local is a static image but CivitAI metadata points to a video URL "
                    "and no static variant has been archived. "
                    "Run backfill_civitai_static_source_variants.py --apply.",
                    {
                        "db_mimetype": db_mimetype,
                        "civitai_url": civitai_url,
                        "has_archived_static": has_archived,
                    },
                ))

        # civitai.mimeType declared and conflicts with DB mimetype
        if civitai_mime:
            norm_db   = _norm_mime(db_mimetype)
            norm_civ  = _norm_mime(civitai_mime)
            if norm_db and norm_civ and norm_db != norm_civ:
                db_cat  = _asset_category(db_mimetype, None)
                civ_cat = _asset_category(civitai_mime, None)
                severity = "critical" if db_cat != civ_cat else "warning"
                record.issues.append(Issue(
                    "CIVITAI_MIME_CONFLICT", severity,
                    f"DB mimetype '{norm_db}' conflicts with civitai.mimeType '{norm_civ}'",
                    {"db_mimetype": norm_db, "civitai_mimetype": norm_civ},
                ))

        # Backfill metadata indicates CivitAI declared a video URL but served image bytes
        if isinstance(civitai_source_variant, dict):
            declared_variant_mime = _norm_mime(civitai_source_variant.get("declared_mimetype"))
            actual_variant_mime = _norm_mime(civitai_source_variant.get("actual_mimetype"))
            variant_reason = str(civitai_source_variant.get("reason") or "").strip()
            if declared_variant_mime.startswith("video/") and actual_variant_mime.startswith("image/"):
                record.issues.append(Issue(
                    "CIVITAI_DECLARED_VIDEO_SERVED_IMAGE", "warning",
                    "CivitAI declared video content but the archived response was an image",
                    {
                        "declared_mimetype": declared_variant_mime or None,
                        "actual_mimetype": actual_variant_mime or None,
                        "reason": variant_reason or None,
                    },
                ))

        # HTTP 404 probe (optional, slow)
        if probe_http:
            status = _probe_http_status(civitai_url)
            if status == 404:
                record.issues.append(Issue(
                    "REMOTE_URL_404", "warning",
                    f"CivitAI URL returned HTTP 404: {civitai_url[:120]}",
                    {"civitai_url": civitai_url, "http_status": status},
                ))
            elif status == 0:
                record.issues.append(Issue(
                    "REMOTE_URL_UNREACHABLE", "info",
                    f"CivitAI URL is unreachable (network/timeout): {civitai_url[:120]}",
                    {"civitai_url": civitai_url, "http_status": status},
                ))

    return record


# ---------------------------------------------------------------------------
# Run / report
# ---------------------------------------------------------------------------

def _run_analysis(
    *,
    sample_limit: Optional[int],
    civitai_only: bool,
    detailed: bool,
    probe_http: bool,
) -> None:
    db = SessionLocal()
    try:
        query = db.query(ImageModel).filter(
            ImageModel.image_status == "active"
        ).order_by(ImageModel.id.asc())

        if civitai_only:
            query = query.filter(ImageModel.source_url.like("%civitai%"))

        if sample_limit:
            query = query.limit(sample_limit)

        images = query.all()
    finally:
        db.close()

    print(f"\nScanning {len(images)} image records…", flush=True)

    records: list[ImageRecord] = []
    for i, image in enumerate(images, 1):
        if i % 500 == 0:
            print(f"  {i}/{len(images)}…", flush=True)
        records.append(_analyze_one(image, probe_http=probe_http))

    # ---- aggregate -----------------------------------------------------------
    issues_by_type: dict[str, list[ImageRecord]] = defaultdict(list)
    for rec in records:
        seen_types: set[str] = set()
        for issue in rec.issues:
            if issue.issue_type not in seen_types:
                issues_by_type[issue.issue_type].append(rec)
                seen_types.add(issue.issue_type)

    total_with_issues = sum(1 for r in records if r.has_issues())
    total_critical = sum(len(r.by_severity("critical")) for r in records)
    total_warning  = sum(len(r.by_severity("warning")) for r in records)
    total_info     = sum(len(r.by_severity("info")) for r in records)

    # ---- print ---------------------------------------------------------------
    W = 80
    print()
    print("=" * W)
    print("ASSET DISCREPANCY ANALYSIS REPORT")
    print("=" * W)
    print(f"  Images scanned      : {len(records)}")
    print(f"  Images with issues  : {total_with_issues}")
    print(f"  Critical issues     : {total_critical}")
    print(f"  Warnings            : {total_warning}")
    print(f"  Info                : {total_info}")
    print()

    if not issues_by_type:
        print("  No issues found. Library looks healthy.")
        print("=" * W + "\n")
        return

    SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
    def _type_severity(t: str) -> int:
        for rec in issues_by_type[t]:
            for iss in rec.issues:
                if iss.issue_type == t:
                    return SEVERITY_ORDER.get(iss.severity, 9)
        return 9

    ordered_types = sorted(issues_by_type.keys(), key=_type_severity)

    for issue_type in ordered_types:
        recs = issues_by_type[issue_type]
        # Find one representative issue to get severity
        rep_issue: Optional[Issue] = None
        for r in recs:
            for iss in r.issues:
                if iss.issue_type == issue_type:
                    rep_issue = iss
                    break
            if rep_issue:
                break
        severity_label = rep_issue.severity.upper() if rep_issue else "?"
        print(f"  [{severity_label:8s}] {issue_type}  —  {len(recs)} record(s)")

        if detailed and rep_issue:
            print(f"           {rep_issue.description}")

        if detailed:
            for rec in recs[:5]:
                for iss in rec.issues:
                    if iss.issue_type == issue_type:
                        print(f"           • ID {rec.image_id}  {rec.file_hash[:16]}…  {iss.description}")
                        break
            if len(recs) > 5:
                print(f"           … and {len(recs) - 5} more")
            print()

    print()
    print("RECOMMENDED ACTIONS:")
    print("-" * W)
    _print_recommendations(issues_by_type)
    print("=" * W + "\n")

    # ---- JSON report ---------------------------------------------------------
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanned": len(records),
        "with_issues": total_with_issues,
        "by_type": {t: len(r) for t, r in issues_by_type.items()},
        "records": [],
    }
    for rec in records:
        if rec.has_issues():
            report["records"].append({
                "image_id": rec.image_id,
                "file_hash": rec.file_hash,
                "source_url": rec.source_url,
                "is_civitai": rec.is_civitai,
                "issues": [
                    {
                        "type": iss.issue_type,
                        "severity": iss.severity,
                        "description": iss.description,
                        "details": iss.details,
                    }
                    for iss in rec.issues
                ],
            })

    report_file = Path(__file__).parent.parent / "data" / "asset_discrepancy_report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Full report saved → {report_file}\n")


def _print_recommendations(issues_by_type: dict[str, list[ImageRecord]]) -> None:
    recs = {
        "MISSING_LOCAL_FILE": (
            "Files are in the DB but gone from disk. "
            "Re-import from source or tombstone the records."
        ),
        "EMPTY_FILE": (
            "Zero-byte files on disk. Delete and re-download from CivitAI "
            "or use the Repair endpoint."
        ),
        "CORRUPTED_MAGIC_BYTES": (
            "File magic bytes don't match the extension. "
            "The download may have been interrupted or the file renamed incorrectly. "
            "Use the Repair endpoint or re-download from source."
        ),
        "CORRUPTED_CONTENT": (
            "PIL cannot open the image. File may be truncated or malformed. "
            "Use the Repair endpoint (runs repack_png_image_data where applicable) "
            "or re-download."
        ),
        "DB_SIZE_MISMATCH": (
            "DB file_size column is stale. Run a rescan or update via "
            "the /images/{hash}/rescan endpoint to refresh metadata."
        ),
        "DB_MIME_EXT_MISMATCH": (
            "DB mimetype doesn't match the file extension category (image vs video). "
            "May indicate a wrong extension or incorrect MIME stored at import time. "
            "Inspect and correct via the rescan endpoint."
        ),
        "DB_DIMS_ZERO": (
            "Image dimensions are 0 in DB. Run a rescan to re-extract dimensions."
        ),
        "CIVITAI_STALE_URL": (
            "Local asset was replaced with a video but json_metadata.civitai.url still "
            "points to the old static image URL. Re-enrich from CivitAI API to refresh "
            "the metadata, or manually update civitai.url in json_metadata."
        ),
        "CIVITAI_UNEXPECTED_IMAGE": (
            "Local file is a static image but CivitAI metadata says it should be a video, "
            "and no archived static variant exists. "
            "Run: python scripts/backfill_civitai_static_source_variants.py --apply"
        ),
        "CIVITAI_MIME_CONFLICT": (
            "DB MIME type and civitai.mimeType disagree. "
            "Cross-reference the actual file to determine ground truth and update accordingly."
        ),
        "CIVITAI_DECLARED_VIDEO_SERVED_IMAGE": (
            "CivitAI metadata advertises video but the fetched content resolved to an image. "
            "Keep this as a reviewed exception, or refresh from CivitAI if upstream metadata changes."
        ),
        "REMOTE_URL_404": (
            "CivitAI resource is no longer available (404). "
            "The remote variant cannot be fetched. Consider marking as unavailable."
        ),
        "REMOTE_URL_UNREACHABLE": (
            "CivitAI URL was unreachable during probe. May be transient. Re-run with "
            "--probe-http to re-check."
        ),
    }
    for issue_type, affected in issues_by_type.items():
        advice = recs.get(issue_type)
        if advice:
            print(f"  {issue_type} ({len(affected)}):")
            print(f"    {advice}")
            print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze image asset metadata discrepancies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sample", type=int, default=None,
                        help="Limit to first N images (for exploration)")
    parser.add_argument("--all", dest="all_images", action="store_true",
                        help="Scan ALL active images, not just CivitAI-sourced ones")
    parser.add_argument("--detailed", action="store_true",
                        help="Print individual record details under each issue category")
    parser.add_argument("--probe-http", action="store_true",
                        help="HEAD-request each remote CivitAI URL to detect 404s (slow)")
    args = parser.parse_args()

    civitai_only = not args.all_images

    if not _HAS_PIL:
        print("Note: Pillow not installed — CORRUPTED_CONTENT checks are disabled.", flush=True)

    try:
        _run_analysis(
            sample_limit=args.sample,
            civitai_only=civitai_only,
            detailed=args.detailed,
            probe_http=args.probe_http,
        )
    except KeyboardInterrupt:
        print("\n\nAnalysis interrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()

