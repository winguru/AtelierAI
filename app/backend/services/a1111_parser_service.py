"""Consolidated A1111 parameter text parsing and detection.

This module is the single source of truth for:
  - Parsing A1111 ``UserComment`` / ``parameters`` text into structured fields
  - Detecting A1111 feature signals (Hires fix, Regional Prompter, ADetailer)
  - Candidate extraction, prioritisation, and field hydration from multiple
    EXIF sources
  - Scalar / JSON comparison helpers used by the parity / audit pipeline

**Newline invariant** (validated 2026-04 across the full image library):
    Every A1111 parameter string in the wild (3,407 / 3,407 = 100 %)
    uses a literal newline (``\\n``) to separate the *positive prompt* from
    the generation parameters section (``Steps: …``).  The parser relies on
    this: it splits on ``\\n`` first, then locates the ``Negative prompt:``
    line or the first line beginning with ``Steps:`` to delineate sections.

Import convention (from main.py or other services):
    from services.a1111_parser_service import (
        parse_a1111_user_comment,
        looks_like_a1111_user_comment,
        ...
    )
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Regex / constant singletons  (previously duplicated across main.py,
# image_query_service.py, metadata_extraction.py)
# ---------------------------------------------------------------------------

_A1111_NEGATIVE_PROMPT_RE: re.Pattern = re.compile(
    r"^\s*negative\s+prompt\s*:\s*", re.IGNORECASE,
)

_A1111_KV_SPLIT_RE: re.Pattern = re.compile(
    r",\s*(?=[A-Za-z][A-Za-z0-9 _/\-]*\s*:)",
)

_A1111_SIZE_RE: re.Pattern = re.compile(
    r"^\s*(\d{2,6})\s*[xX]\s*(\d{2,6})\s*$",
)

_A1111_LORA_TAG_RE: re.Pattern = re.compile(
    r"<\s*lora\s*:\s*([^:>]+?)(?:\s*:\s*([-+]?\d*\.?\d+))?\s*>",
    re.IGNORECASE,
)

A1111_RP_DIRECTIVE_RE: re.Pattern = re.compile(
    r"\b(ADDCOMM|ADDROW|ADDCOL)\b", re.IGNORECASE,
)

A1111_HIRES_KEYWORDS: tuple[str, ...] = (
    "hires upscaler", "hires steps", "hires upscale",
    "hr upscaler", "hr upscale", "denoising strength",
)

COMFY_RP_EMULATION_SUPPORTED: bool = False

_A1111_SAMPLER_TO_COMFY_ALIASES: dict[str, str] = {
    "euler a": "euler_ancestral",
    "euler": "euler",
    "dpm++ 2m": "dpmpp_2m",
    "dpm++ 2m karras": "dpmpp_2m",
    "dpm++ sde": "dpmpp_sde",
    "dpm++ sde karras": "dpmpp_sde",
    "ddim": "ddim",
    "uni_pc": "uni_pc",
    "uni_pc_bh2": "uni_pc_bh2",
    "heun": "heun",
}

# ---------------------------------------------------------------------------
# Tiny generic helpers (also used by callers in main.py)
# ---------------------------------------------------------------------------


def _dict_payload(value: Any) -> dict:
    """Return *value* if it is already a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _list_payload(value: Any) -> list:
    """Return *value* if it is already a list, else an empty list."""
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Core type coercion
# ---------------------------------------------------------------------------


def _coerce_a1111_parameter_value(key: str, value: Any) -> Any:
    """Coerce a raw A1111 KV string value to a typed Python value."""
    key_normalized = str(key or "").strip().lower().replace("_", " ")
    text = str(value or "").strip()
    if not text:
        return None

    if key_normalized == "size":
        size_match = _A1111_SIZE_RE.match(text)
        if size_match:
            return f"{int(size_match.group(1))}x{int(size_match.group(2))}"
        return text

    if key_normalized in {"steps", "seed", "clip skip", "eta", "batch size", "batch count"}:
        try:
            return int(float(text))
        except ValueError:
            return text

    if key_normalized in {
        "cfg scale",
        "denoising strength",
        "hires upscale",
        "ensd",
        "variation seed strength",
    }:
        try:
            return float(text)
        except ValueError:
            return text

    return text


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def looks_like_a1111_user_comment(text: str) -> bool:
    """Quick heuristic: does *text* look like an A1111 packed parameter string?

    Checks for the presence of well-known A1111 KV markers such as
    ``Negative prompt:``, ``Steps:``, ``CFG scale:``, or ``Sampler:``.
    """
    sample = str(text or "").strip().lower()
    if not sample:
        return False
    return (
        "negative prompt:" in sample
        or "steps:" in sample
        or "cfg scale:" in sample
        or "sampler:" in sample
    )


def looks_like_a1111_exif(exif: dict[str, Any]) -> bool:
    """Check whether an EXIF dict contains A1111-style generation metadata.

    Inspects ``parameters`` / ``Parameters`` keys first, then falls back to
    ``user_comment`` / ``UserComment``.  Rejects ComfyUI-style payloads
    (JSON starting with ``{`` / ``[`` that contain ``prompt`` / ``workflow``
    keys) and CivitAI resource payloads.
    """
    # Fast path: top-level parameters key
    for key in ("parameters", "Parameters"):
        candidate = exif.get(key)
        if isinstance(candidate, str) and candidate.strip():
            norm = candidate.strip().lower()
            has_steps = "steps:" in norm
            has_cfg = "cfg scale:" in norm
            has_sampler = "sampler:" in norm
            has_seed = "seed:" in norm
            has_negative = "negative prompt:" in norm
            if has_steps and (has_cfg or has_sampler or has_seed or has_negative):
                return True

    exact = exif.get("user_comment")
    if isinstance(exact, str) and exact.strip():
        return True

    legacy = exif.get("UserComment")
    if not isinstance(legacy, str):
        return False
    text = legacy.strip()
    if not text:
        return False

    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and (
                parsed.get("prompt") or parsed.get("workflow") or parsed.get("resource-stack")
            ):
                return False
        except (json.JSONDecodeError, TypeError):
            pass

    norm = text.lower()
    if "civitai resources:" in norm:
        return False
    has_steps = "steps:" in norm
    has_cfg = "cfg scale:" in norm
    has_sampler = "sampler:" in norm
    has_seed = "seed:" in norm
    has_negative = "negative prompt:" in norm
    return has_steps and (has_cfg or has_sampler or has_seed or has_negative)


def _get_a1111_text(exif: dict[str, Any]) -> str:
    """Extract normalised A1111 parameter text from EXIF fields."""
    for key in ("parameters", "Parameters", "user_comment", "UserComment"):
        value = exif.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text.startswith("{") or text.startswith("["):
            continue
        return text.lower()
    return ""


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_a1111_user_comment(text: str) -> dict[str, Any]:
    """Parse a raw A1111 ``UserComment`` / ``parameters`` string.

    The parser first splits on newlines to separate sections (positive
    prompt, negative prompt, KV metadata), then uses
    ``_A1111_KV_SPLIT_RE`` to split the metadata blob into key-value pairs.

    **Newline invariant**: 100 % of observed A1111 texts in the image library
    use a newline (``\\n``) before the generation parameters (``Steps:``).
    The parser depends on this split to correctly isolate the positive prompt.

    Returns a dict with keys:
        raw_text, positive_prompt, negative_prompt, parameters,
        lora_tags, parsed_fields, warnings
    """
    raw_text = str(text or "").replace("\r\n", "\n").strip().strip("\x00").strip()
    if not raw_text:
        return {
            "raw_text": "",
            "positive_prompt": "",
            "negative_prompt": "",
            "parameters": {},
            "lora_tags": [],
            "warnings": ["A1111 metadata text was empty."],
        }

    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    positive_lines: list[str] = []
    negative_prompt = ""
    metadata_lines: list[str] = []

    negative_index = next(
        (index for index, line in enumerate(lines) if _A1111_NEGATIVE_PROMPT_RE.match(line)),
        -1,
    )
    if negative_index >= 0:
        positive_lines = lines[:negative_index]
        negative_line = lines[negative_index]
        negative_prompt = _A1111_NEGATIVE_PROMPT_RE.sub("", negative_line).strip()
        metadata_lines = lines[negative_index + 1:]
    else:
        # No "Negative prompt:" — use newline invariant: find first line
        # beginning with "Steps:" to separate positive prompt from KV metadata.
        metadata_start = next(
            (index for index, line in enumerate(lines) if line.lower().startswith("steps:")),
            -1,
        )
        if metadata_start >= 0:
            positive_lines = lines[:metadata_start]
            metadata_lines = lines[metadata_start:]
        else:
            positive_lines = lines

    positive_prompt = "\n".join(positive_lines).strip()
    metadata_blob = ", ".join(metadata_lines).strip(" ,")
    if not metadata_blob and positive_prompt.lower().startswith("steps:"):
        metadata_blob = positive_prompt
        positive_prompt = ""

    parameter_pairs: dict[str, Any] = {}
    if metadata_blob:
        segments = [
            segment.strip()
            for segment in _A1111_KV_SPLIT_RE.split(metadata_blob)
            if segment.strip()
        ]
        for segment in segments:
            if ":" not in segment:
                continue
            key, value = segment.split(":", 1)
            key = str(key).strip()
            if not key:
                continue
            coerced = _coerce_a1111_parameter_value(key, value)
            if coerced is None:
                continue
            parameter_pairs[key] = coerced

    if not negative_prompt:
        for key in ("Negative prompt", "negative prompt", "Negative Prompt"):
            if key in parameter_pairs:
                negative_prompt = str(parameter_pairs.get(key) or "").strip()
                break

    parsed_size = _A1111_SIZE_RE.match(str(parameter_pairs.get("Size") or ""))
    width = int(parsed_size.group(1)) if parsed_size else None
    height = int(parsed_size.group(2)) if parsed_size else None

    lora_tags: list[dict[str, Any]] = []
    for match in _A1111_LORA_TAG_RE.finditer(positive_prompt):
        lora_name = str(match.group(1) or "").strip()
        if not lora_name:
            continue
        weight_value = match.group(2)
        weight = None
        if weight_value not in (None, ""):
            try:
                weight = float(weight_value)
            except ValueError:
                weight = None
        lora_tags.append({
            "name": lora_name,
            "weight": weight,
        })

    warnings: list[str] = []
    if not looks_like_a1111_user_comment(raw_text):
        warnings.append("Metadata text did not match common A1111 parameter patterns.")
    if not positive_prompt:
        warnings.append("Positive prompt could not be confidently extracted.")
    if not parameter_pairs:
        warnings.append("No key/value parameter fields were detected in metadata text.")

    return {
        "raw_text": raw_text,
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "parameters": parameter_pairs,
        "lora_tags": lora_tags,
        "parsed_fields": {
            "sampler": parameter_pairs.get("Sampler") or parameter_pairs.get("sampler"),
            "scheduler": (
                parameter_pairs.get("Schedule type")
                or parameter_pairs.get("Scheduler")
                or parameter_pairs.get("scheduler")
            ),
            "seed": parameter_pairs.get("Seed") or parameter_pairs.get("seed"),
            "steps": parameter_pairs.get("Steps") or parameter_pairs.get("steps"),
            "cfg_scale": (
                parameter_pairs.get("CFG scale")
                or parameter_pairs.get("Cfg scale")
                or parameter_pairs.get("cfg scale")
            ),
            "model": parameter_pairs.get("Model") or parameter_pairs.get("model"),
            "model_hash": (
                parameter_pairs.get("Model hash") or parameter_pairs.get("model hash")
            ),
            "width": width,
            "height": height,
            "denoising_strength": (
                parameter_pairs.get("Denoising strength")
                or parameter_pairs.get("denoising strength")
            ),
            "clip_skip": (
                parameter_pairs.get("Clip skip") or parameter_pairs.get("clip skip")
            ),
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Candidate extraction & hydration
# ---------------------------------------------------------------------------


def extract_a1111_user_comment_candidates(
    generation_payload: dict,
) -> list[dict[str, str]]:
    """Extract candidate A1111 text strings from multiple EXIF sub-sources.

    Returns a list of ``{"source": ..., "text": ...}`` dicts, deduplicated
    by lowercased text content, ordered by their appearance in *generation_payload*.
    """
    raw = _dict_payload(generation_payload.get("raw"))
    merged = _dict_payload(raw.get("merged"))

    candidates: list[tuple[str, Any]] = [
        ("raw.merged.UserComment", merged.get("UserComment")),
        ("raw.merged.user_comment", merged.get("user_comment")),
        ("raw.merged.parameters", merged.get("parameters")),
        ("raw.merged.Parameters", merged.get("Parameters")),
        ("raw.exif_data.UserComment", _dict_payload(raw.get("exif_data")).get("UserComment")),
        ("raw.exif_data.user_comment", _dict_payload(raw.get("exif_data")).get("user_comment")),
        ("raw.exif_data.parameters", _dict_payload(raw.get("exif_data")).get("parameters")),
        ("raw.exif_data_fresh.UserComment", _dict_payload(raw.get("exif_data_fresh")).get("UserComment")),
        ("raw.exif_data_fresh.user_comment", _dict_payload(raw.get("exif_data_fresh")).get("user_comment")),
        ("raw.exif_data_fresh.parameters", _dict_payload(raw.get("exif_data_fresh")).get("parameters")),
        (
            "raw.sidecar.exif_data.UserComment",
            _dict_payload(_dict_payload(raw.get("sidecar")).get("exif_data")).get("UserComment"),
        ),
        (
            "raw.sidecar.exif_data.user_comment",
            _dict_payload(_dict_payload(raw.get("sidecar")).get("exif_data")).get("user_comment"),
        ),
        (
            "raw.sidecar.exif_data.parameters",
            _dict_payload(_dict_payload(raw.get("sidecar")).get("exif_data")).get("parameters"),
        ),
        (
            "raw.db.exif_data.UserComment",
            _dict_payload(_dict_payload(raw.get("db")).get("exif_data")).get("UserComment"),
        ),
        (
            "raw.db.exif_data.user_comment",
            _dict_payload(_dict_payload(raw.get("db")).get("exif_data")).get("user_comment"),
        ),
        (
            "raw.db.exif_data.parameters",
            _dict_payload(_dict_payload(raw.get("db")).get("exif_data")).get("parameters"),
        ),
    ]

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, value in candidates:
        if value is None:
            continue
        text = str(value).replace("\r\n", "\n").strip().strip("\x00").strip()
        if not text:
            continue
        dedupe_key = text.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append({"source": source, "text": text})
    return normalized


def _a1111_candidate_source_priority(source: Any) -> int:
    """Rank EXIF sub-sources by freshness / authority (lower = better)."""
    source_text = str(source or "").strip().lower()
    if source_text.startswith("raw.exif_data_fresh"):
        return 0
    if source_text.startswith("raw.exif_data"):
        return 1
    if source_text.startswith("raw.sidecar.exif_data"):
        return 2
    if source_text.startswith("raw.db.exif_data"):
        return 3
    if source_text.startswith("raw.merged"):
        return 4
    return 5


def select_preferred_a1111_user_comment_candidate(
    candidates: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Select the best A1111 text candidate by source priority and text length."""
    valid_candidates = [item for item in candidates if isinstance(item, dict)]
    if not valid_candidates:
        return None

    a1111_candidates = [
        item
        for item in valid_candidates
        if looks_like_a1111_user_comment(str(item.get("text") or ""))
    ]
    pool = a1111_candidates if a1111_candidates else valid_candidates
    return min(
        pool,
        key=lambda item: (
            _a1111_candidate_source_priority(item.get("source")),
            -len(str(item.get("text") or "")),
        ),
    )


def build_authoritative_a1111_parse_payload(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Parse the best A1111 candidate and hydrate missing fields from others.

    Returns ``(parse_payload, preferred_candidate)``.
    """
    preferred_candidate = select_preferred_a1111_user_comment_candidate(candidates)
    if not preferred_candidate:
        return (
            {
                "raw_text": "",
                "positive_prompt": "",
                "negative_prompt": "",
                "parameters": {},
                "lora_tags": [],
                "parsed_fields": {},
                "warnings": [
                    "No A1111 user_comment/parameters metadata was found in local payloads."
                ],
            },
            None,
        )

    parse_payload = parse_a1111_user_comment(
        str(preferred_candidate.get("text") or "")
    )
    hydrated_fields: list[str] = []
    hydrated_sources: list[str] = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source = str(candidate.get("source") or "").strip()
        if not source or source == str(preferred_candidate.get("source") or "").strip():
            continue
        candidate_text = str(candidate.get("text") or "").strip()
        if not candidate_text or not looks_like_a1111_user_comment(candidate_text):
            continue

        candidate_parse = parse_a1111_user_comment(candidate_text)
        candidate_fields = _dict_payload(candidate_parse.get("parsed_fields"))
        parse_fields = _dict_payload(parse_payload.get("parsed_fields"))

        source_hydrated = False
        for key, value in candidate_fields.items():
            if parse_fields.get(key) in (None, "") and value not in (None, ""):
                parse_fields[key] = value
                hydrated_fields.append(str(key))
                source_hydrated = True

        parse_payload["parsed_fields"] = parse_fields

        if (
            not str(parse_payload.get("negative_prompt") or "").strip()
            and str(candidate_parse.get("negative_prompt") or "").strip()
        ):
            parse_payload["negative_prompt"] = str(
                candidate_parse.get("negative_prompt") or ""
            ).strip()
            hydrated_fields.append("negative_prompt")
            source_hydrated = True

        if (
            not str(parse_payload.get("positive_prompt") or "").strip()
            and str(candidate_parse.get("positive_prompt") or "").strip()
        ):
            parse_payload["positive_prompt"] = str(
                candidate_parse.get("positive_prompt") or ""
            ).strip()
            hydrated_fields.append("positive_prompt")
            source_hydrated = True

        parse_parameters = _dict_payload(parse_payload.get("parameters"))
        candidate_parameters = _dict_payload(candidate_parse.get("parameters"))
        for key, value in candidate_parameters.items():
            if parse_parameters.get(key) in (None, "") and value not in (None, ""):
                parse_parameters[key] = value
                source_hydrated = True
        parse_payload["parameters"] = parse_parameters

        if source_hydrated:
            hydrated_sources.append(source)

    warnings = [
        str(item)
        for item in _list_payload(parse_payload.get("warnings"))
        if str(item).strip()
    ]
    if hydrated_fields:
        warnings.append(
            "Hydrated missing A1111 fields from secondary metadata sources: "
            + ", ".join(sorted(set(hydrated_fields)))
        )
    parse_payload["warnings"] = warnings
    parse_payload["source_authority"] = {
        "preferred_source": preferred_candidate.get("source"),
        "hydrated_sources": sorted(set(hydrated_sources)),
        "hydrated_field_count": len(set(hydrated_fields)),
    }
    return parse_payload, preferred_candidate


# ---------------------------------------------------------------------------
# Feature / capability detection
# ---------------------------------------------------------------------------


def build_a1111_capability_signals(
    parse_payload: dict[str, Any],
) -> dict[str, Any]:
    """Detect Hires fix, Regional Prompter, and ADetailer signals from parsed A1111 data."""
    parameters = {
        str(key or "").strip().lower(): value
        for key, value in _dict_payload(parse_payload.get("parameters")).items()
    }
    raw_text = str(parse_payload.get("raw_text") or "").lower()
    positive_prompt = str(parse_payload.get("positive_prompt") or "")
    rp_directives = sorted(
        {str(match) for match in A1111_RP_DIRECTIVE_RE.findall(positive_prompt)}
    )

    hires_detected = any(
        key.startswith("hires ") or key.startswith("hr ") for key in parameters
    )
    adetailer_detected = any(
        key.startswith("adetailer") for key in parameters
    )
    rp_detected = (
        any(
            key.startswith("rp ") or "regional prompt" in key
            for key in parameters
        )
        or (
            "rp active" in raw_text
            or "regional prompt" in raw_text
            or bool(rp_directives)
        )
    )

    rp_supported = bool(COMFY_RP_EMULATION_SUPPORTED)

    unsupported_features: list[str] = []
    if rp_detected and not rp_supported:
        unsupported_features.append("regional_prompting_rp")

    partial_features: list[str] = []
    if hires_detected:
        partial_features.append("hires_fix")
    if adetailer_detected:
        partial_features.append("adetailer")

    known_additions = {
        "hires_upscaler": {
            "label": "Hires upscaler",
            "detected": hires_detected,
            "support_level": "partial",
        },
        "adetailer": {
            "label": "ADetailer",
            "detected": adetailer_detected,
            "support_level": "partial",
        },
        "regional_prompter": {
            "label": "Regional Prompter (RP)",
            "detected": rp_detected,
            "support_level": "supported" if rp_supported else "unsupported",
            "directives": rp_directives,
        },
    }

    detected_additions = [
        {
            "key": key,
            "label": str(item.get("label") or key),
            "support_level": str(item.get("support_level") or "unknown"),
            **(
                {"directives": item.get("directives")}
                if item.get("directives")
                else {}
            ),
        }
        for key, item in known_additions.items()
        if bool(item.get("detected"))
    ]

    other_markers = sorted(
        {
            key
            for key in parameters
            if key.startswith("script ")
            or key.startswith("alwayson scripts")
            or key.startswith("controlnet ")
        }
    )

    return {
        "hires_fix_detected": hires_detected,
        "adetailer_detected": adetailer_detected,
        "rp_detected": rp_detected,
        "rp_directives": rp_directives,
        "unsupported_features": unsupported_features,
        "partially_supported_features": partial_features,
        "known_additions": known_additions,
        "detected_additions": detected_additions,
        "other_addition_markers": other_markers,
    }


def detect_a1111_features_from_exif(exif: dict[str, Any]) -> dict[str, bool]:
    """Detect A1111 features from a raw EXIF dict.

    Convenience wrapper used by ingestion pipelines that only have EXIF
    (not a parsed A1111 payload).

    Returns dict with keys:
        has_a1111_metadata, a1111_hires, a1111_regional_prompter, a1111_adetailer
    """
    is_a1111 = looks_like_a1111_exif(exif)
    if not is_a1111:
        return {
            "has_a1111_metadata": False,
            "a1111_hires": False,
            "a1111_regional_prompter": False,
            "a1111_adetailer": False,
        }

    text = _get_a1111_text(exif)
    hires = any(kw in text for kw in A1111_HIRES_KEYWORDS) if text else False
    user_comment_text = str(
        exif.get("user_comment") or exif.get("UserComment") or ""
    )
    rp = (
        "rp active" in text
        or "regional prompt" in text
        or bool(A1111_RP_DIRECTIVE_RE.search(user_comment_text))
    ) if text else False
    adetailer = "adetailer" in text if text else False

    return {
        "has_a1111_metadata": True,
        "a1111_hires": bool(hires),
        "a1111_regional_prompter": bool(rp),
        "a1111_adetailer": bool(adetailer),
    }


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------


def sanitize_a1111_positive_prompt_for_comfy(
    prompt_text: Any,
) -> tuple[str, list[str]]:
    """Strip RP directives from positive prompt text when RP is unsupported.

    Returns ``(sanitized_text, removed_directives)``.
    """
    if COMFY_RP_EMULATION_SUPPORTED:
        return str(prompt_text or ""), []

    text = str(prompt_text or "")
    if not text.strip():
        return "", []

    directives_found = [
        str(match) for match in A1111_RP_DIRECTIVE_RE.findall(text)
    ]
    if not directives_found:
        return text, []

    directives_set = {item.upper() for item in directives_found}
    sanitized_lines: list[str] = []
    for raw_line in text.split("\n"):
        parts = [segment.strip() for segment in raw_line.split(",")]
        kept_parts: list[str] = []
        for part in parts:
            if not part:
                continue
            token = part.strip().upper()
            if token in directives_set and A1111_RP_DIRECTIVE_RE.fullmatch(token):
                continue
            stripped_part = A1111_RP_DIRECTIVE_RE.sub("", part).strip()
            stripped_part = re.sub(r"\s{2,}", " ", stripped_part).strip(" ,")
            if stripped_part:
                kept_parts.append(stripped_part)
        if kept_parts:
            sanitized_lines.append(", ".join(kept_parts))

    sanitized = "\n".join(sanitized_lines).strip()
    return sanitized, sorted(directives_set)


# ---------------------------------------------------------------------------
# Scalar / JSON comparison helpers
# ---------------------------------------------------------------------------


def _normalize_scalar_for_lookup(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text.casefold()
    return None


def _flatten_json_scalars(
    value: Any,
    *,
    prefix: str = "",
    output: Optional[dict[str, Any]] = None,
    depth: int = 0,
    max_depth: int = 20,
) -> dict[str, Any]:
    if output is None:
        output = {}
    if depth > max_depth:
        return output

    if isinstance(value, dict):
        for key, nested in value.items():
            nested_key = str(key)
            path = f"{prefix}.{nested_key}" if prefix else nested_key
            _flatten_json_scalars(
                nested, prefix=path, output=output, depth=depth + 1, max_depth=max_depth
            )
        return output

    if isinstance(value, list):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            _flatten_json_scalars(
                nested, prefix=path, output=output, depth=depth + 1, max_depth=max_depth
            )
        return output

    output[prefix or "$"] = value
    return output


def _build_scalar_lookup(flattened: dict[str, Any]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for path, value in flattened.items():
        normalized = _normalize_scalar_for_lookup(value)
        if normalized is None:
            continue
        lookup.setdefault(normalized, []).append(path)
    return lookup


def _compare_json_scalar_structures(
    left: Any,
    right: Any,
    *,
    sample_limit: int = 25,
) -> dict[str, Any]:
    left_flat = _flatten_json_scalars(left)
    right_flat = _flatten_json_scalars(right)

    left_paths = set(left_flat.keys())
    right_paths = set(right_flat.keys())
    shared_paths = left_paths & right_paths

    matches = 0
    mismatches: list[dict[str, Any]] = []
    for path in sorted(shared_paths):
        if left_flat.get(path) == right_flat.get(path):
            matches += 1
            continue
        if len(mismatches) < sample_limit:
            mismatches.append({
                "path": path,
                "left": left_flat.get(path),
                "right": right_flat.get(path),
            })

    left_only_samples = sorted(left_paths - right_paths)[:sample_limit]
    right_only_samples = sorted(right_paths - left_paths)[:sample_limit]
    mismatch_count = max(len(shared_paths) - matches, 0)
    shared_total = len(shared_paths)
    similarity_score = (matches / shared_total) if shared_total else 0.0

    return {
        "left_scalar_count": len(left_flat),
        "right_scalar_count": len(right_flat),
        "shared_path_count": shared_total,
        "matching_value_count": matches,
        "mismatch_count": mismatch_count,
        "left_only_count": max(len(left_paths - right_paths), 0),
        "right_only_count": max(len(right_paths - left_paths), 0),
        "similarity_score": round(similarity_score, 6),
        "left_only_path_samples": left_only_samples,
        "right_only_path_samples": right_only_samples,
        "mismatch_samples": mismatches,
    }


def build_a1111_field_alignment(
    parsed_fields: dict[str, Any],
    workflow_payload: Any,
    *,
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Compare parsed A1111 fields against a workflow JSON payload."""
    flattened = _flatten_json_scalars(workflow_payload)
    lookup = _build_scalar_lookup(flattened)

    alignments: dict[str, Any] = {}
    matched_fields = 0
    candidate_fields = 0

    for field_name, field_value in parsed_fields.items():
        if field_value in (None, ""):
            continue
        candidate_fields += 1
        normalized = _normalize_scalar_for_lookup(field_value)
        matched_paths = lookup.get(normalized, []) if normalized is not None else []
        if matched_paths:
            matched_fields += 1
        alignments[field_name] = {
            "value": field_value,
            "match_count": len(matched_paths),
            "path_samples": matched_paths[:sample_limit],
        }

    score = (matched_fields / candidate_fields) if candidate_fields else 0.0
    return {
        "candidate_field_count": candidate_fields,
        "matched_field_count": matched_fields,
        "alignment_score": round(score, 6),
        "fields": alignments,
    }


# ---------------------------------------------------------------------------
# Normalisation helpers (used by parity / audit pipeline)
# ---------------------------------------------------------------------------


def _normalize_sampler_name_for_comfy(value: Any) -> dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        return {
            "source": value,
            "normalized": None,
            "mapped": False,
            "notes": ["Sampler is missing."],
        }
    key = raw.lower().strip()
    mapped = _A1111_SAMPLER_TO_COMFY_ALIASES.get(key)
    if mapped:
        return {
            "source": raw,
            "normalized": mapped,
            "mapped": mapped != raw,
            "notes": [f"Mapped A1111 sampler '{raw}' to Comfy sampler '{mapped}'."],
        }
    return {
        "source": raw,
        "normalized": raw,
        "mapped": False,
        "notes": [f"No explicit sampler alias mapping for '{raw}'; using raw value."],
    }


def _normalize_scheduler_name_for_comfy(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _normalize_model_name_key(value: Any) -> Optional[str]:
    """Normalise a model name for comparison using the model_reference_service."""
    # Import here to avoid circular dependency at module level
    from services import model_reference_service
    normalized = str(
        model_reference_service.normalize_name_key(value) or ""
    ).strip().lower()
    return normalized or None


def _extract_hex_hash_tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    text = str(value).strip().lower()
    if not text:
        return set()
    return {
        token
        for token in re.findall(r"[0-9a-f]{8,64}", text)
        if len(token) >= 8
    }


def _hash_token_sets_match(left_tokens: set[str], right_tokens: set[str]) -> bool:
    if not left_tokens or not right_tokens:
        return False
    for left_token in left_tokens:
        for right_token in right_tokens:
            if left_token.startswith(right_token) or right_token.startswith(left_token):
                return True
    return False


def _normalize_prompt_text_for_match(
    value: Any,
    *,
    strip_lora_tags: bool = False,
) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if strip_lora_tags:
        text = re.sub(r"<\s*lora\s*:[^>]+>", " ", text, flags=re.IGNORECASE)
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _find_first_text_diff(left: str, right: str) -> Optional[dict[str, Any]]:
    if left == right:
        return None
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return {
                "index": index,
                "left_char": left[index],
                "right_char": right[index],
                "left_char_code": ord(left[index]),
                "right_char_code": ord(right[index]),
            }
    if len(left) != len(right):
        return {
            "index": limit,
            "left_char": left[limit] if len(left) > limit else "",
            "right_char": right[limit] if len(right) > limit else "",
            "left_char_code": ord(left[limit]) if len(left) > limit else None,
            "right_char_code": ord(right[limit]) if len(right) > limit else None,
        }
    return None


def _is_missing_process_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _is_parameter_like_workflow_path(path: str) -> bool:
    text = str(path or "")
    if not text:
        return False
    lowered = text.lower()
    excluded_markers = [
        ".id",
        ".order",
        ".link",
        ".links[",
    ]
    return not any(marker in lowered for marker in excluded_markers)


def _field_value_matches_expected(
    field_name: str,
    local_value: Any,
    expected_value: Any,
) -> bool:
    if _is_missing_process_value(local_value) or _is_missing_process_value(expected_value):
        return False

    if field_name == "sampler_name":
        local_norm = _normalize_sampler_name_for_comfy(local_value).get("normalized")
        expected_norm = _normalize_sampler_name_for_comfy(expected_value).get("normalized")
        return bool(
            local_norm and expected_norm and str(local_norm).lower() == str(expected_norm).lower()
        )

    if field_name == "scheduler_name":
        local_norm = _normalize_scheduler_name_for_comfy(local_value)
        expected_norm = _normalize_scheduler_name_for_comfy(expected_value)
        return bool(local_norm and expected_norm and local_norm == expected_norm)

    if field_name == "model":
        local_norm = _normalize_model_name_key(local_value)
        expected_norm = _normalize_model_name_key(expected_value)
        if not local_norm or not expected_norm:
            return False
        if local_norm == expected_norm:
            return True
        return local_norm in expected_norm or expected_norm in local_norm

    if field_name == "model_hash":
        local_tokens = _extract_hex_hash_tokens(local_value)
        expected_tokens = _extract_hex_hash_tokens(expected_value)
        return _hash_token_sets_match(local_tokens, expected_tokens)

    if field_name in {"prompt_positive", "prompt_negative"}:
        local_norm = _normalize_prompt_text_for_match(local_value)
        expected_norm = _normalize_prompt_text_for_match(expected_value)
        if local_norm and expected_norm and local_norm == expected_norm:
            return True
        local_norm_lora = _normalize_prompt_text_for_match(
            local_value, strip_lora_tags=True
        )
        expected_norm_lora = _normalize_prompt_text_for_match(
            expected_value, strip_lora_tags=True
        )
        return bool(
            local_norm_lora and expected_norm_lora and local_norm_lora == expected_norm_lora
        )

    local_num = _to_float(local_value)
    expected_num = _to_float(expected_value)
    if local_num is not None and expected_num is not None:
        return abs(local_num - expected_num) <= 1e-6

    local_text = str(local_value or "").strip().casefold()
    expected_text = str(expected_value or "").strip().casefold()
    if not local_text or not expected_text:
        return False
    return local_text == expected_text


def _extract_expected_workflow_parameters(
    workflow_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    nodes = workflow_payload.get("nodes")
    if not isinstance(nodes, list):
        return results

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        class_type = str(
            node.get("class_type") or node.get("type") or ""
        ).strip()
        class_key = class_type.lower()
        widgets = (
            node.get("widgets_values")
            if isinstance(node.get("widgets_values"), list)
            else []
        )

        def _add(field: str, widget_index: int, key_name: str) -> None:
            if widget_index >= len(widgets):
                return
            value = widgets[widget_index]
            if _is_missing_process_value(value):
                return
            results.append({
                "field": field,
                "expected_value": value,
                "node_class": class_type or "unknown",
                "path": f"nodes[{idx}].widgets_values[{widget_index}]",
                "key": key_name,
            })

        if "ksampler" in class_key:
            _add("sampler_name", 4, "sampler")
            _add("scheduler_name", 5, "scheduler")
            _add("denoise", 6, "denoise")
        if "checkpointloader" in class_key:
            _add("model", 0, "ckpt_name")
        if class_key == "vaeloader":
            _add("vae_name", 0, "vae_name")

    return results


def build_prompt_mismatch_diagnostics(
    local_value: Any,
    parameter_scalars: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Produce diagnostic info when local prompt doesn't match any workflow scalar."""
    local_raw = str(local_value or "")
    local_norm = _normalize_prompt_text_for_match(local_raw)
    local_norm_lora = _normalize_prompt_text_for_match(
        local_raw, strip_lora_tags=True
    )
    candidates: list[tuple[str, str, str]] = []
    for path, expected_value in parameter_scalars:
        if not isinstance(expected_value, str):
            continue
        expected_raw = str(expected_value)
        if len(expected_raw.strip()) < 40:
            continue
        candidates.append(
            (path, expected_raw, _normalize_prompt_text_for_match(expected_raw))
        )

    if not candidates:
        return {
            "closest_path": None,
            "raw_equal": False,
            "normalized_equal": False,
            "local_length": len(local_raw),
            "expected_length": None,
            "first_diff": None,
        }

    best_path, best_raw, best_norm = max(
        candidates,
        key=lambda c: (
            int(c[2] == local_norm),
            int(c[2] == local_norm_lora),
            len(set(c[2]) & set(local_norm)),
        ),
    )

    return {
        "closest_path": best_path,
        "raw_equal": local_raw == best_raw,
        "normalized_equal": local_norm == best_norm,
        "local_length": len(local_raw),
        "expected_length": len(best_raw),
        "first_diff": _find_first_text_diff(local_raw, best_raw),
    }


def build_semantic_workflow_match_buckets(
    canonical_fields: dict[str, Any],
    workflow_payload: dict[str, Any],
    *,
    model_hash_evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Match canonical A1111 fields against workflow JSON scalars."""
    flattened = _flatten_json_scalars(workflow_payload)
    parameter_scalars = [
        (path, value)
        for path, value in flattened.items()
        if _is_parameter_like_workflow_path(path)
    ]

    local_fields = {
        "prompt_positive": canonical_fields.get("prompt_positive"),
        "prompt_negative": canonical_fields.get("prompt_negative"),
        "sampler_name": canonical_fields.get("sampler_name"),
        "scheduler_name": canonical_fields.get("scheduler_name"),
        "seed": canonical_fields.get("seed"),
        "steps": canonical_fields.get("steps"),
        "cfg_scale": canonical_fields.get("cfg_scale"),
        "width": canonical_fields.get("width"),
        "height": canonical_fields.get("height"),
        "denoise": canonical_fields.get("denoise"),
        "clip_skip": canonical_fields.get("clip_skip"),
        "model": canonical_fields.get("model"),
        "model_hash": canonical_fields.get("model_hash"),
    }

    matched: list[dict[str, Any]] = []
    local_only: list[dict[str, Any]] = []
    matched_field_names: set[str] = set()
    for field_name, local_value in local_fields.items():
        if _is_missing_process_value(local_value):
            continue
        match_paths = [
            path
            for path, expected_value in parameter_scalars
            if _field_value_matches_expected(field_name, local_value, expected_value)
        ]
        if match_paths:
            matched_field_names.add(field_name)
            matched.append({
                "field": field_name,
                "local_value": local_value,
                "match_basis": "semantic_match",
                "match_count": len(match_paths),
                "path_samples": match_paths[:10],
            })
        elif (
            field_name == "model_hash"
            and isinstance(model_hash_evidence, dict)
            and model_hash_evidence.get("confirmed_exact_match")
        ):
            matched_field_names.add(field_name)
            evidence_sources = _list_payload(model_hash_evidence.get("sources"))
            tier = str(model_hash_evidence.get("confirmation_tier") or "unknown")
            cross_detail = model_hash_evidence.get("cross_source_detail")
            matched.append({
                "field": field_name,
                "local_value": local_value,
                "match_basis": f"verified_by_model_verification:{tier}",
                "confirmation_tier": tier,
                "match_count": max(1, len(evidence_sources)),
                "path_samples": [
                    str(item.get("source") or "external_model_hash")
                    for item in evidence_sources[:10]
                    if isinstance(item, dict)
                ] or ["external_model_hash"],
                "cross_source_detail": cross_detail,
                "evidence": model_hash_evidence,
            })
        else:
            mismatch_entry = {
                "field": field_name,
                "local_value": local_value,
                "match_basis": "no_workflow_comparison",
                "match_count": 0,
                "path_samples": [],
            }
            if field_name in {"prompt_positive", "prompt_negative"}:
                mismatch_entry["prompt_diagnostics"] = build_prompt_mismatch_diagnostics(
                    local_value, parameter_scalars
                )
            local_only.append(mismatch_entry)

    expected_params = _extract_expected_workflow_parameters(workflow_payload)
    mismatched: list[dict[str, Any]] = []
    workflow_only: list[dict[str, Any]] = []
    for expected in expected_params:
        field_name = str(expected.get("field") or "").strip()
        expected_value = expected.get("expected_value")
        local_value = local_fields.get(field_name)
        if _is_missing_process_value(local_value):
            workflow_only.append({
                **expected,
                "reason": "no_local_candidate_value",
            })
            continue
        if _field_value_matches_expected(field_name, local_value, expected_value):
            continue
        mismatched.append({
            "field": field_name,
            "local_value": local_value,
            "expected_value": expected_value,
            "node_class": expected.get("node_class"),
            "path": expected.get("path"),
            "reason": "field_value_differs_from_expected_workflow",
        })

    return {
        "counts": {
            "matched": len(matched),
            "mismatched": len(mismatched),
            "local_only": len(local_only),
            "workflow_only": len(workflow_only),
        },
        "matched": matched,
        "mismatched": mismatched,
        "local_only": local_only,
        "workflow_only": workflow_only,
        "expected_workflow_parameter_samples": expected_params[:25],
    }
