# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/parity-workbench.md
# ──────────────────────────────────────────────────────────────────────────────
"""Generation-related routes.

Extracted from main.py:
  - Lines ~13842–17286: lab pages, ComfyUI proxy, expression-sets, tasks,
    generation-prototype, A1111 bridge, parity workbench, dataset quality,
    ComfyUI generate-and-compare, generation templates, perceptual lab,
    model prototype, task retry variants.

All non-trivial handlers delegate to the corresponding main.py function via
lazy import; they will be moved to services/generation_service.py in a future
phase.

Self-contained handlers:
  - Lab page FileResponse routes
  - /tasks/ CRUD (uses task_manager directly)
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Optional

import requests
from dotenv import dotenv_values
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from fastapi import File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from core.lifespan import task_manager
from database import get_db

router = APIRouter(tags=["generation"])


_STYLE_PREFILL_MCP_IMAGE_TTL_SECONDS = 15 * 60
_STYLE_PREFILL_MCP_IMAGE_MAX_ITEMS = 256
_style_prefill_mcp_image_lock = Lock()
_style_prefill_mcp_image_store: dict[str, dict[str, Any]] = {}


_STYLE_PREFILL_DEFAULT_SYSTEM_PROMPT = (
    "You are an image style analysis assistant. Return strict JSON only with keys: "
    "summary, style_axes, quality_signals, defect_signals, candidate_styles, confidence. "
    "Use numeric 0-1 values for axis/quality/defect entries. "
    "style_axes should include abstraction, line_dominance, shading_complexity, texture_realism, "
    "material_response, geometry_fidelity, scene_detail_density, color_behavior. "
    "quality_signals should include global_coherence and intent_fit. "
    "defect_signals should include anatomy_distortion and artifact_burden. "
    "candidate_styles should be an array of objects with name and weight (0-1). "
    "confidence should include overall and safety_restriction_risk (0-1)."
)

_STYLE_PREFILL_DEFAULT_USER_PROMPT = (
    "Analyze this image for style prefill prototyping. Keep the response concise, strict JSON, "
    "and avoid extra commentary."
)


def _parse_json_from_text(raw_text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction for LLM responses.

    Accepts strict JSON, fenced JSON, or text containing a JSON object.
    """
    trimmed = raw_text.strip()
    if not trimmed:
        return None

    # Remove fenced code markers when present.
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            trimmed = "\n".join(lines[1:-1]).strip()
            if trimmed.lower().startswith("json"):
                trimmed = trimmed[4:].strip()

    # First attempt: strict parse.
    try:
        parsed = json.loads(trimmed)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: parse first JSON object span.
    left = trimmed.find("{")
    right = trimmed.rfind("}")
    if left >= 0 and right > left:
        try:
            parsed = json.loads(trimmed[left : right + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None

    return None


def _extract_completion_text(result_json: dict[str, Any]) -> str:
    """Extract assistant text from OpenAI-compatible chat completion payload."""
    choices = result_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""

    message = first.get("message")
    if not isinstance(message, dict):
        return ""

    content_obj = message.get("content")
    if isinstance(content_obj, str):
        return content_obj

    if isinstance(content_obj, list):
        text_parts: list[str] = []
        for part in content_obj:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        return "\n".join(text_parts).strip()

    return ""


def _extract_finish_reason(result_json: dict[str, Any]) -> str | None:
    """Extract finish_reason from first choice, if present."""
    choices = result_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    finish_reason = first.get("finish_reason")
    return finish_reason if isinstance(finish_reason, str) else None


def _is_maybe_truncated(finish_reason: str | None, completion_text: str) -> bool:
    """Best-effort truncation heuristic for partial JSON completions."""
    trimmed = completion_text.strip()
    if finish_reason in {"length", "max_tokens"}:
        return True
    return bool(trimmed.startswith("{") and not trimmed.endswith("}"))


def _is_safety_detail(detail: Any) -> bool:
    """Classify upstream error detail as safety rejection when possible."""
    if isinstance(detail, dict):
        if detail.get("error_kind") == "safety_rejection":
            return True
        lowered = json.dumps(detail).lower()
        return (
            "unsafe" in lowered
            or "sensitive content" in lowered
            or "content policy" in lowered
            or "safety" in lowered
        )
    if isinstance(detail, str):
        lowered = detail.lower()
        return (
            "unsafe" in lowered
            or "sensitive content" in lowered
            or "content policy" in lowered
            or "safety" in lowered
        )
    return False


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a permissive boolean from environment variables."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _merge_request_overrides(
    payload: dict[str, Any],
    request_overrides_json: str,
) -> dict[str, Any]:
    """Merge optional JSON overrides into the provider request payload."""
    overrides_raw = request_overrides_json.strip()
    if not overrides_raw:
        return payload

    try:
        overrides = json.loads(overrides_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"request_overrides_json is not valid JSON: {exc}",
        ) from exc

    if not isinstance(overrides, dict):
        raise HTTPException(
            status_code=400,
            detail="request_overrides_json must decode to a JSON object",
        )

    merged = dict(payload)
    merged.update(overrides)
    merged = _normalize_mcp_tools_shape(merged)
    return merged


def _parse_request_overrides_object(request_overrides_json: str) -> dict[str, Any]:
    """Parse optional request override JSON into an object."""
    overrides_raw = request_overrides_json.strip()
    if not overrides_raw:
        return {}

    try:
        overrides = json.loads(overrides_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"request_overrides_json is not valid JSON: {exc}",
        ) from exc

    if not isinstance(overrides, dict):
        raise HTTPException(
            status_code=400,
            detail="request_overrides_json must decode to a JSON object",
        )
    return overrides


def _normalize_mcp_tools_shape(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize shorthand MCP tool entries into tools[i].mcp object shape.

    Accepts entries such as:
      {"type":"mcp", "server_name":"zai_vision_mcp", "tool_name":"analyze_image", ...}
    and converts them to:
      {"type":"mcp", "mcp": {"server":..., "tool":..., "arguments":...}}
    """
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return payload

    normalized_tools: list[Any] = []
    for item in tools:
        if not isinstance(item, dict):
            normalized_tools.append(item)
            continue

        tool_type = str(item.get("type", "")).strip().lower()
        if tool_type != "mcp":
            normalized_tools.append(item)
            continue

        existing_mcp = item.get("mcp")
        if isinstance(existing_mcp, dict) and existing_mcp:
            normalized_tools.append(item)
            continue

        server = (
            item.get("server")
            or item.get("server_name")
            or item.get("mcp_server")
        )
        server_label = (
            item.get("server_label")
            or item.get("mcp_server_label")
            or server
        )
        tool = item.get("tool") or item.get("tool_name")
        arguments = (
            item.get("arguments")
            or item.get("params")
            or item.get("input")
            or {}
        )

        rebuilt = dict(item)
        rebuilt["mcp"] = {
            "server": server,
            "server_label": server_label,
            "tool": tool,
            "arguments": arguments if isinstance(arguments, dict) else {},
        }
        normalized_tools.append(rebuilt)

    normalized_payload = dict(payload)
    normalized_payload["tools"] = normalized_tools
    return normalized_payload


def _extract_responses_output_text(result_json: dict[str, Any]) -> str:
    """Extract assistant text from OpenAI Responses-style payload."""
    output_text = result_json.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = result_json.get("output")
    if not isinstance(output, list):
        return ""

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])

    return "\n".join(text_parts).strip()


def _post_responses_request(
    endpoint_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Call OpenAI Responses-compatible endpoint and return JSON payload."""
    try:
        response = requests.post(
            endpoint_url,
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach responses endpoint: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=_build_provider_error_detail(response, endpoint_url),
        )

    try:
        result_json = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Responses endpoint response is not valid JSON",
        ) from exc

    if not isinstance(result_json, dict):
        raise HTTPException(
            status_code=502,
            detail="Responses endpoint response must be a JSON object",
        )

    return result_json


def _extract_first_json_object_from_sse_or_body(raw_text: str) -> dict[str, Any] | None:
    """Extract first JSON object from raw body or SSE data lines."""
    trimmed = (raw_text or "").strip()
    if not trimmed:
        return None

    try:
        maybe = json.loads(trimmed)
        if isinstance(maybe, dict):
            return maybe
    except json.JSONDecodeError:
        pass

    # SSE can send multi-line events where one JSON payload is spread across
    # consecutive `data:` lines and events are separated by blank lines.
    data_lines: list[str] = []
    for line in trimmed.splitlines():
        line = line.strip()
        if not line:
            if data_lines:
                candidate = "\n".join(data_lines).strip()
                try:
                    maybe = json.loads(candidate)
                    if isinstance(maybe, dict):
                        return maybe
                except json.JSONDecodeError:
                    pass
                data_lines = []
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if data_lines:
        candidate = "\n".join(data_lines).strip()
        try:
            maybe = json.loads(candidate)
            if isinstance(maybe, dict):
                return maybe
        except json.JSONDecodeError:
            pass

    # Backward-compatible single-line SSE fallback.
    for line in trimmed.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        candidate = line[5:].strip()
        try:
            maybe = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(maybe, dict):
            return maybe

    # Last resort: pull first object span from mixed text.
    left = trimmed.find("{")
    right = trimmed.rfind("}")
    if left >= 0 and right > left:
        try:
            maybe = json.loads(trimmed[left : right + 1])
            if isinstance(maybe, dict):
                return maybe
        except json.JSONDecodeError:
            return None

    return None


def _extract_tools_list_from_mcp_result(parsed_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tools list from MCP result object."""
    result_obj = parsed_json.get("result")
    tools = result_obj.get("tools") if isinstance(result_obj, dict) else None
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, dict)]


def _probe_mcp_tools_list(
    mcp_url: str,
    mcp_headers: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]], str | None]:
    """Probe MCP tools/list and return (ok, tools, error_message)."""
    headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    for k, v in (mcp_headers or {}).items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            headers[k] = v

    payload = {
        "jsonrpc": "2.0",
        "id": "style-prefill-tools-list",
        "method": "tools/list",
        "params": {},
    }

    try:
        response = requests.post(
            mcp_url,
            headers=headers,
            json=payload,
            timeout=25,
        )
    except requests.RequestException as exc:
        return False, [], f"MCP probe request failed: {exc}"

    raw_text = response.text or ""
    if response.status_code >= 400:
        return (
            False,
            [],
            f"MCP probe returned HTTP {response.status_code}: {raw_text[:500]}",
        )

    parsed_json = _extract_first_json_object_from_sse_or_body(raw_text)
    if not isinstance(parsed_json, dict):
        preview = raw_text[:500].replace("\n", "\\n")
        return False, [], f"MCP probe returned unparseable response payload: {preview}"

    clean_tools = _extract_tools_list_from_mcp_result(parsed_json)
    if not clean_tools:
        return False, [], "MCP endpoint returned an empty tools list"

    return True, clean_tools, None


def _mcp_jsonrpc_request(
    mcp_url: str,
    headers: dict[str, Any],
    payload: dict[str, Any],
    timeout: int = 30,
) -> tuple[dict[str, Any], requests.structures.CaseInsensitiveDict[str]]:
    """Send a JSON-RPC request to MCP endpoint and parse JSON/SSE payload."""
    req_headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    for k, v in (headers or {}).items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            req_headers[k] = v

    try:
        response = requests.post(
            mcp_url,
            headers=req_headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"MCP request failed: {exc}") from exc

    raw_text = response.text or ""
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"MCP request returned HTTP {response.status_code}: {raw_text[:500]}",
        )

    parsed = _extract_first_json_object_from_sse_or_body(raw_text)
    if not isinstance(parsed, dict):
        preview = raw_text[:1200].replace("\n", "\\n")
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MCP returned unparseable response payload",
                "mcp_server_url": mcp_url,
                "payload_id": payload.get("id"),
                "payload_method": payload.get("method"),
                "response_preview": preview,
            },
        )

    return parsed, response.headers


def _select_analyze_image_tool_name(
    tools: list[dict[str, Any]],
    mcp_server_label: str,
) -> str:
    """Pick analyze-image tool name from discovered MCP tool metadata."""
    names: list[str] = []
    for item in tools:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())

    for name in names:
        if name.endswith("-analyze_image"):
            return name
    for name in names:
        if name == "analyze_image":
            return name

    label = mcp_server_label.strip() or "zai_vision_mcp"
    return f"{label}-analyze_image"


def _extract_mcp_tool_text(result_obj: dict[str, Any]) -> str:
    """Extract text from MCP tools/call result payload."""
    result = result_obj.get("result")
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip()


def _run_direct_mcp_style_prefill(
    *,
    mcp_url: str,
    mcp_headers: dict[str, Any],
    discovered_tools: list[dict[str, Any]],
    mcp_server_label: str,
    image_data_url: str,
    user_prompt: str,
    image: UploadFile,
    mime_type: str,
    content_size: int,
    model_name: str,
    resolved_endpoint: str,
    resolved_api_key: str,
    mcp_target_mode: str,
    mcp_execution_mode: str,
    selected_toolset: str,
    resolved_mcp_server_url: str,
    resolved_mcp_servers_header: str,
) -> dict[str, Any]:
    """Execute style prefill through direct MCP initialize + tools/call."""
    init_payload = {
        "jsonrpc": "2.0",
        "id": "style-prefill-init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "atelier-style-prefill", "version": "1.0.0"},
            "capabilities": {},
        },
    }
    init_result, init_headers = _mcp_jsonrpc_request(
        mcp_url,
        mcp_headers,
        init_payload,
        timeout=25,
    )

    call_headers = dict(mcp_headers)
    session_id = init_headers.get("mcp-session-id")
    if isinstance(session_id, str) and session_id.strip():
        call_headers["mcp-session-id"] = session_id.strip()

    tool_name = _select_analyze_image_tool_name(discovered_tools, mcp_server_label)
    call_payload = {
        "jsonrpc": "2.0",
        "id": "style-prefill-call",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "image_source": image_data_url,
                "prompt": user_prompt.strip(),
            },
        },
    }
    call_result, _ = _mcp_jsonrpc_request(
        mcp_url,
        call_headers,
        call_payload,
        timeout=60,
    )

    direct_text = _extract_mcp_tool_text(call_result)
    direct_structured = _parse_json_from_text(direct_text)
    return {
        "ok": True,
        "request": {
            "filename": image.filename,
            "content_type": mime_type,
            "size_bytes": content_size,
            "model": model_name,
            "endpoint_url": resolved_endpoint,
            "used_api_key": bool(resolved_api_key),
            "provider_route": "mcp_direct",
            "mcp_target_mode": mcp_target_mode,
            "mcp_execution_mode": mcp_execution_mode,
            "mcp_toolset_select": selected_toolset,
            "mcp_server_url": resolved_mcp_server_url,
            "mcp_server_label": mcp_server_label,
            "mcp_servers_header": resolved_mcp_servers_header,
            "mcp_tool_name": tool_name,
        },
        "structured": direct_structured,
        "raw_completion_text": direct_text,
        "completion_meta": {
            "json_parse_ok": direct_structured is not None,
            "usage": None,
            "hint": "Result came from direct MCP tools/call execution path.",
        },
        "raw_provider_response": {
            "initialize": init_result,
            "tool_call": call_result,
        },
    }


def _resolve_mcp_image_source(
    *,
    provided_image_source_url: str,
    image_data_url: str,
) -> str:
    """Resolve MCP image source and enforce size guard for embedded payloads."""
    resolved = provided_image_source_url.strip() or image_data_url
    if not provided_image_source_url.strip() and len(image_data_url) > 900_000:
        raise HTTPException(
            status_code=413,
            detail={
                "message": "MCP image payload too large for embedded data URL",
                "size_bytes": len(image_data_url),
                "hint": (
                    "Provide MCP image source URL to avoid sending a large embedded payload, "
                    "or upload a smaller image."
                ),
            },
        )
    return resolved


def _host_is_local_or_private(host: str) -> bool:
    """Return True when host looks non-public (localhost/private/LAN)."""
    host_norm = (host or "").strip().lower().strip("[]")
    if not host_norm:
        return True
    if host_norm in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host_norm.endswith(".local"):
        return True

    try:
        ip = ipaddress.ip_address(host_norm)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        # Non-IP hostnames are assumed public unless explicitly local-like.
        return False


def _is_localhost_host(host: str) -> bool:
    """Return True when host is localhost/loopback."""
    host_norm = (host or "").strip().lower().strip("[]")
    if host_norm in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host_norm)
        return ip.is_loopback
    except ValueError:
        return False


def _prune_style_prefill_mcp_images(now_ts: float | None = None) -> None:
    """Prune expired/old temporary MCP image entries."""
    now = now_ts or time.time()
    expired_tokens = [
        token
        for token, entry in _style_prefill_mcp_image_store.items()
        if not isinstance(entry, dict)
        or (now - float(entry.get("created_at", 0.0))) > _STYLE_PREFILL_MCP_IMAGE_TTL_SECONDS
    ]
    for token in expired_tokens:
        _style_prefill_mcp_image_store.pop(token, None)

    if len(_style_prefill_mcp_image_store) <= _STYLE_PREFILL_MCP_IMAGE_MAX_ITEMS:
        return

    # Remove oldest entries until under cap.
    ordered = sorted(
        _style_prefill_mcp_image_store.items(),
        key=lambda item: float(item[1].get("created_at", 0.0)) if isinstance(item[1], dict) else 0.0,
    )
    overflow = len(_style_prefill_mcp_image_store) - _STYLE_PREFILL_MCP_IMAGE_MAX_ITEMS
    for token, _ in ordered[:overflow]:
        _style_prefill_mcp_image_store.pop(token, None)


def _create_style_prefill_mcp_image_entry(
    *,
    content: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Create temporary hosted-image entry for MCP image_source URL usage."""
    token = uuid.uuid4().hex
    now = time.time()
    with _style_prefill_mcp_image_lock:
        _prune_style_prefill_mcp_images(now)
        _style_prefill_mcp_image_store[token] = {
            "bytes": content,
            "mime_type": mime_type,
            "filename": filename,
            "created_at": now,
            "last_accessed_at": None,
            "access_count": 0,
            "last_user_agent": None,
            "last_forwarded_for": None,
            "last_remote": None,
        }
    return token


def _build_style_prefill_mcp_image_url(
    *,
    request: Request,
    token: str,
    public_base_url: str,
) -> str:
    """Build absolute URL for temporary hosted image endpoint."""
    base = public_base_url.strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/api/style-prefill/mcp-image/{token}"


def _get_style_prefill_mcp_image_access_snapshot(token: str) -> dict[str, Any] | None:
    """Get immutable access snapshot for a temporary hosted image token."""
    with _style_prefill_mcp_image_lock:
        entry = _style_prefill_mcp_image_store.get(token)
        if not isinstance(entry, dict):
            return None
        return {
            "token": token,
            "created_at": entry.get("created_at"),
            "last_accessed_at": entry.get("last_accessed_at"),
            "access_count": int(entry.get("access_count") or 0),
            "last_user_agent": entry.get("last_user_agent"),
            "last_forwarded_for": entry.get("last_forwarded_for"),
            "last_remote": entry.get("last_remote"),
        }


@router.api_route("/style-prefill/mcp-image/{token}", methods=["GET", "HEAD"], response_class=Response)
async def style_prefill_mcp_image(token: str, request: Request) -> Response:
    """Serve temporary uploaded image bytes for MCP image_source URL access."""
    with _style_prefill_mcp_image_lock:
        _prune_style_prefill_mcp_images()
        entry = _style_prefill_mcp_image_store.get(token)
        if not isinstance(entry, dict):
            raise HTTPException(status_code=404, detail="MCP image token not found or expired")

        entry["access_count"] = int(entry.get("access_count") or 0) + 1
        entry["last_accessed_at"] = time.time()
        entry["last_user_agent"] = request.headers.get("user-agent")
        entry["last_forwarded_for"] = request.headers.get("x-forwarded-for")
        entry["last_remote"] = request.client.host if request.client else None

        content = entry.get("bytes") or b""
        mime_type = str(entry.get("mime_type") or "application/octet-stream")

    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    if request.method == "HEAD":
        return Response(status_code=200, media_type=mime_type, headers=headers)
    return Response(content=content, media_type=mime_type, headers=headers)


def _fetch_litellm_mcp_toolsets(
    api_base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Fetch MCP toolsets from LiteLLM MCP discovery endpoint."""
    base = api_base_url.strip()
    if not base:
        raise HTTPException(status_code=400, detail="LITELLM_API_URL is not configured")
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="LITELLM_API_KEY is not configured")

    # Support both base prefixes (e.g. .../v1) and full endpoint URLs
    # (e.g. .../v1/chat/completions) in LITELLM_API_URL.
    parts = urlsplit(base)
    path = parts.path or ""
    lower_path = path.lower()
    if lower_path.endswith("/v1/chat/completions"):
        base_path = path[: -len("/chat/completions")]
    elif lower_path.endswith("/chat/completions"):
        base_path = path[: -len("/chat/completions")]
    elif lower_path.endswith("/v1/responses"):
        base_path = path[: -len("/responses")]
    elif lower_path.endswith("/responses"):
        base_path = path[: -len("/responses")]
    else:
        base_path = path.rstrip("/")

    if not base_path:
        base_path = "/v1"

    normalized_base = urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))
    url = f"{normalized_base.rstrip('/')}/mcp/toolset"
    key = api_key.strip()
    headers = {
        "accept": "application/json",
        "x-litellm-api-key": key,
        # Keep Authorization as a compatibility fallback for deployments
        # that still expect bearer auth.
        "Authorization": f"Bearer {key}",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach MCP toolset endpoint: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=_build_provider_error_detail(response, url),
        )

    try:
        parsed = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="MCP toolset endpoint returned non-JSON response",
        ) from exc

    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=502,
            detail="Unexpected MCP toolset response shape (expected list)",
        )

    return [item for item in parsed if isinstance(item, dict)]


def _read_workspace_vscode_env() -> dict[str, str]:
    """Read /workspace/.vscode/.env when available for runtime discovery helpers."""
    repo_root = Path(__file__).resolve().parents[3]
    env_path = repo_root / ".vscode" / ".env"
    if not env_path.exists() or not env_path.is_file():
        return {}

    parsed = dotenv_values(str(env_path))
    return {k: v for k, v in parsed.items() if isinstance(v, str)}


def _resolve_mcp_server_url(
    submitted_mcp_server_url: str,
    mcp_target_mode: str,
    selected_toolset: str,
    litellm_api_url: str,
) -> str:
    """Resolve MCP URL, forcing toolset URL when toolset mode is selected."""
    resolved = submitted_mcp_server_url.strip() or "https://litellm.thewinguru.com/mcp/"
    # Normalize plain /mcp to /mcp/ to avoid 307 redirects that can drop headers.
    if resolved.endswith("/mcp"):
        resolved = f"{resolved}/"

    if mcp_target_mode.strip().lower() != "toolset" or not selected_toolset.strip():
        return resolved

    origin_candidate = ""
    try:
        parts = urlsplit(resolved)
        if parts.scheme and parts.netloc:
            origin_candidate = f"{parts.scheme}://{parts.netloc}"
    except Exception:
        origin_candidate = ""

    if not origin_candidate:
        try:
            parts = urlsplit(litellm_api_url.strip())
            if parts.scheme and parts.netloc:
                origin_candidate = f"{parts.scheme}://{parts.netloc}"
        except Exception:
            origin_candidate = ""

    if not origin_candidate:
        return resolved

    return f"{origin_candidate}/toolset/{selected_toolset.strip()}/mcp"


def _derive_responses_endpoint_from_api_url(api_url: str) -> str:
    """Derive a /v1/responses endpoint from a configured chat-completions URL."""
    raw = api_url.strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return ""

    path = parts.path or ""
    lower_path = path.lower()
    if lower_path.endswith("/v1/chat/completions"):
        new_path = path[: -len("/v1/chat/completions")] + "/v1/responses"
    elif lower_path.endswith("/chat/completions"):
        new_path = path[: -len("/chat/completions")] + "/responses"
    elif lower_path.endswith("/v1/responses"):
        new_path = path
    else:
        base_path = path.rstrip("/")
        if base_path.endswith("/v1"):
            new_path = f"{base_path}/responses"
        elif base_path:
            new_path = f"{base_path}/v1/responses"
        else:
            new_path = "/v1/responses"

    return urlunsplit((parts.scheme, parts.netloc, new_path, "", ""))


def _apply_override_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively substitute placeholder tokens in override payload values."""
    if isinstance(value, str):
        updated = value
        for token, replacement in replacements.items():
            updated = updated.replace(token, replacement)
        return updated
    if isinstance(value, Mapping):
        return {
            k: _apply_override_placeholders(v, replacements)
            for k, v in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_apply_override_placeholders(item, replacements) for item in value]
    return value


def _execute_with_optional_safety_fallback(
    payload: dict[str, Any],
    resolved_endpoint: str,
    resolved_api_key: str,
    auto_fallback_enabled: bool,
    resolved_fallback_endpoint: str,
    resolved_fallback_model: str,
    resolved_fallback_api_key: str,
) -> tuple[
    dict[str, Any],
    str,
    str,
    dict[str, str],
    str,
    bool,
    bool,
    str | None,
    str | dict[str, Any] | None,
]:
    """Run primary provider request and optionally fallback on safety rejection."""
    headers = {"Content-Type": "application/json"}
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"

    provider_route = "primary"
    fallback_attempted = False
    fallback_used = False
    fallback_reason: str | None = None
    fallback_error: str | dict[str, Any] | None = None

    try:
        result_json = _post_vision_completion(resolved_endpoint, headers, payload)
    except HTTPException as exc:
        should_fallback = (
            auto_fallback_enabled
            and _is_safety_detail(exc.detail)
            and bool(resolved_fallback_endpoint)
            and bool(resolved_fallback_model)
        )
        if not should_fallback:
            raise

        fallback_attempted = True
        fallback_reason = "safety_rejection"

        fallback_payload = dict(payload)
        fallback_payload["model"] = resolved_fallback_model

        fallback_headers = {"Content-Type": "application/json"}
        if resolved_fallback_api_key:
            fallback_headers["Authorization"] = f"Bearer {resolved_fallback_api_key}"

        try:
            result_json = _post_vision_completion(
                resolved_fallback_endpoint,
                fallback_headers,
                fallback_payload,
            )
            provider_route = "fallback"
            fallback_used = True
            resolved_endpoint = resolved_fallback_endpoint
            resolved_api_key = resolved_fallback_api_key
            headers = fallback_headers
            payload["model"] = resolved_fallback_model
        except HTTPException as fallback_exc:
            fallback_error = (
                fallback_exc.detail
                if isinstance(fallback_exc.detail, (str, dict))
                else str(fallback_exc.detail)
            )
            raise

    return (
        result_json,
        resolved_endpoint,
        resolved_api_key,
        headers,
        provider_route,
        fallback_attempted,
        fallback_used,
        fallback_reason,
        fallback_error,
    )


def _post_vision_completion(
    endpoint_url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Call OpenAI-compatible chat completions endpoint and return JSON payload."""
    try:
        response = requests.post(
            endpoint_url,
            headers=headers,
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach vision endpoint: {exc}",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=_build_provider_error_detail(response, endpoint_url),
        )

    try:
        result_json = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Vision endpoint response is not valid JSON",
        ) from exc

    if not isinstance(result_json, dict):
        raise HTTPException(
            status_code=502,
            detail="Vision endpoint response must be a JSON object",
        )

    return result_json


def _build_provider_error_detail(
    response: requests.Response,
    endpoint_url: str,
) -> dict[str, Any]:
    """Create a structured FastAPI detail payload for upstream provider errors."""
    response_text = response.text or ""
    detail_payload: dict[str, Any] = {
        "message": "Vision endpoint returned an error",
        "status_code": response.status_code,
        "response_text": response_text[:4000],
        "endpoint_url": endpoint_url,
        "hint": (
            "If this is a policy rejection (including NSFW), "
            "retry using a local vision backend endpoint."
        ),
    }
    try:
        detail_payload["response_json"] = response.json()
    except ValueError:
        pass

    lowered = json.dumps(detail_payload).lower()
    is_safety = (
        "unsafe" in lowered
        or "sensitive content" in lowered
        or "content policy" in lowered
        or "safety" in lowered
    )
    detail_payload["error_kind"] = "safety_rejection" if is_safety else "provider_error"
    return detail_payload


# ---------------------------------------------------------------------------
# ComfyUI proxy
# ---------------------------------------------------------------------------


@router.post("/comfyui-proxy/upload/image")
async def comfyui_proxy_upload_image(
    request: Request,
    target: str = Query(..., description="ComfyUI base URL, e.g. http://127.0.0.1:8188"),
):
    from main import comfyui_proxy_upload_image as _impl  # noqa: PLC0415

    return await _impl(request=request, target=target)


@router.post("/comfyui-proxy/prompt")
async def comfyui_proxy_prompt(
    target: str = Query(..., description="ComfyUI base URL"),
    prompt: str = Body(..., media_type="application/json"),
):
    from main import comfyui_proxy_prompt as _impl  # noqa: PLC0415

    return await _impl(target=target, prompt=prompt)


# ---------------------------------------------------------------------------
# Expression sets
# ---------------------------------------------------------------------------


@router.get("/expression-sets")
def get_expression_sets():
    from main import get_expression_sets as _impl  # noqa: PLC0415

    return _impl()


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------


@router.get("/tasks/", response_model=list[dict])
def list_background_tasks(limit: int = 20):
    capped_limit = max(1, min(int(limit), 50))
    return task_manager.list_tasks(limit=capped_limit)


@router.get("/tasks/{task_id}", response_model=dict)
def get_background_task(task_id: str):
    try:
        return task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/cancel", response_model=dict)
def cancel_background_task(task_id: str):
    try:
        return task_manager.cancel_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/tasks/{task_id}/retry_failed",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_failed_items_from_task(task_id: str):
    from main import retry_failed_items_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


@router.post(
    "/tasks/{task_id}/retry-missing",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_missing_failures_from_task(task_id: str):
    from main import retry_missing_failures_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


@router.post(
    "/tasks/{task_id}/retry-temporary",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_temporary_failures_from_task(task_id: str):
    from main import retry_temporary_failures_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


# ---------------------------------------------------------------------------
# Generation prototype
# ---------------------------------------------------------------------------


@router.get("/generation-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_generation_prototype(image_id: int):
    from main import get_civitai_generation_prototype as _impl  # noqa: PLC0415

    return _impl(image_id=image_id)


@router.get("/images/{file_hash}/generation-prototype", response_model=dict)
def get_local_generation_prototype(file_hash: str, db: Session = Depends(get_db)):
    from main import get_local_generation_prototype as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-workspace", response_model=dict
)
def get_civitai_generation_comfy_workspace(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-workflow-raw", response_model=dict
)
def get_civitai_generation_comfy_workflow_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_workflow_raw as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-prompt-raw", response_model=dict
)
def get_civitai_generation_comfy_prompt_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_prompt_raw as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-workspace", response_model=dict
)
def get_local_generation_comfy_workspace(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-workflow-raw", response_model=dict
)
def get_local_generation_comfy_workflow_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_workflow_raw as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-prompt-raw", response_model=dict
)
def get_local_generation_comfy_prompt_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_prompt_raw as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


# ---------------------------------------------------------------------------
# A1111 bridge / parity workbench
# ---------------------------------------------------------------------------


@router.post("/generation-prototype/a1111-bridge/analyze", response_model=dict)
def analyze_a1111_bridge(request: Request, db: Session = Depends(get_db)):
    from main import analyze_a1111_bridge as _impl  # noqa: PLC0415

    return _impl(request=request, db=db)


@router.post(
    "/generation-prototype/parity-workbench/candidate-audit", response_model=dict
)
@router.post("/generation-audit/analyze", response_model=dict)
def analyze_parity_candidate(request: Request, db: Session = Depends(get_db)):
    from main import analyze_parity_candidate as _impl  # noqa: PLC0415

    return _impl(request=request, db=db)


@router.post("/generation-prototype/a1111-bridge/save-analysis", response_model=dict)
def save_a1111_bridge_analysis(payload: Any = Body(...)):
    from main import save_a1111_bridge_analysis as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get(
    "/generation-prototype/a1111-bridge/dataset-quality", response_model=dict
)
def get_a1111_bridge_dataset_quality_report():
    from main import get_a1111_bridge_dataset_quality_report as _impl  # noqa: PLC0415

    return _impl()


# ---------------------------------------------------------------------------
# ComfyUI generate-and-compare
# ---------------------------------------------------------------------------


@router.post("/generation-prototype/comfy/generate-and-compare", response_model=dict)
def generate_and_compare_comfy_workspace(payload: Any = Body(...)):
    from main import generate_and_compare_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get("/generation-prototype/comfy/attempts", response_model=dict)
def list_comfy_generation_match_attempts(
    limit: int = Query(default=20, ge=1, le=200),
):
    from main import list_comfy_generation_match_attempts as _impl  # noqa: PLC0415

    return _impl(limit=limit)


# ---------------------------------------------------------------------------
# Generation templates
# ---------------------------------------------------------------------------


@router.post("/generation-templates/import-workspace", response_model=dict)
def import_generation_template_workspace(payload: Any = Body(...)):
    from main import import_generation_template_workspace as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get("/generation-templates", response_model=dict)
def list_generation_templates(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import list_generation_templates as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        db=db,
    )


@router.get("/generation-templates/token-preview", response_model=dict)
def preview_generation_template_tokens(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import preview_generation_template_tokens as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        db=db,
    )


@router.get("/generation-templates/{template_id}", response_model=dict)
def get_generation_template(template_id: int, db: Session = Depends(get_db)):
    from main import get_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, db=db)


@router.put("/generation-templates/{template_id}", response_model=dict)
def update_generation_template(
    template_id: int, payload: Any = Body(...), db: Session = Depends(get_db)
):
    from main import update_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, payload=payload, db=db)


@router.delete("/generation-templates/{template_id}", response_model=dict)
def delete_generation_template(template_id: int, db: Session = Depends(get_db)):
    from main import delete_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, db=db)


@router.post("/generation-templates/{template_id}/resolve", response_model=dict)
def resolve_generation_template(
    template_id: int, payload: Any = Body(default=None), db: Session = Depends(get_db)
):
    from main import resolve_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, payload=payload, db=db)


# ---------------------------------------------------------------------------
# Perceptual lab
# ---------------------------------------------------------------------------


@router.get("/images/{file_hash}/perceptual-lab/analyze", response_model=dict)
def analyze_local_image_perceptual_hashes(
    file_hash: str, db: Session = Depends(get_db)
):
    from main import analyze_local_image_perceptual_hashes as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get("/images/{file_hash}/perceptual-lab/similarity", response_model=dict)
def search_perceptual_similarity(
    file_hash: str,
    threshold: float = Query(default=0.9, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from main import search_perceptual_similarity as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, threshold=threshold, limit=limit, db=db)


# ---------------------------------------------------------------------------
# Model prototype
# ---------------------------------------------------------------------------


@router.get("/model-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_model_prototype(image_id: int):
    from main import get_civitai_model_prototype as _impl  # noqa: PLC0415

    return _impl(image_id=image_id)


@router.get("/images/{file_hash}/model-prototype", response_model=dict)
def get_local_model_prototype(file_hash: str, db: Session = Depends(get_db)):
    from main import get_local_model_prototype as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get("/model-prototype/catalog", response_model=dict)
def get_model_catalog_prototype(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_model_catalog_prototype as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get("/model-prototype/local-match-preview", response_model=dict)
def get_model_prototype_local_match_preview(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
):
    from main import get_model_prototype_local_match_preview as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
    )


@router.post("/model-prototype/local-model-download", response_model=dict)
def trigger_model_prototype_local_model_download(payload: dict = Body(...)):
    from main import trigger_model_prototype_local_model_download as _impl  # noqa: PLC0415

    return _impl(payload=payload)


# ---------------------------------------------------------------------------
# Style prefill prototype (single image, no DB writes)
# ---------------------------------------------------------------------------


@router.post("/style-prefill/analyze-single", response_model=dict)
async def analyze_single_image_style_prefill(
    image: UploadFile = File(...),
    model: str = Form(default="z-ai/glm-4.5v"),
    endpoint_url: str = Form(default=""),
    api_key: str = Form(default=""),
    system_prompt: str = Form(default=_STYLE_PREFILL_DEFAULT_SYSTEM_PROMPT),
    user_prompt: str = Form(default=_STYLE_PREFILL_DEFAULT_USER_PROMPT),
    max_tokens: int = Form(default=1400),
    temperature: float = Form(default=0.2),
    use_response_format: bool = Form(default=False),
    auto_retry_on_truncation: bool = Form(default=True),
    retry_max_tokens: int = Form(default=2400),
    fallback_model: str = Form(default=""),
    fallback_endpoint_url: str = Form(default=""),
    fallback_api_key: str = Form(default=""),
    auto_fallback_on_safety: bool = Form(default=False),
    request_overrides_json: str = Form(default=""),
    attach_image_to_messages: bool | None = Form(default=None),
):
    """Analyze one image with a LiteLLM/OpenAI-compatible vision endpoint.

    This prototype endpoint intentionally does not persist anything. It is
    designed to exercise the image -> model -> structured style prefill path.
    """
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 15MB)")

    resolved_endpoint = (
        endpoint_url.strip()
        or os.getenv("LITELLM_API_URL", "").strip()
        or os.getenv("LITELLM_VISION_ENDPOINT", "").strip()
        or "http://localhost:4000/v1/chat/completions"
    )
    resolved_api_key = api_key.strip() or os.getenv("LITELLM_API_KEY", "").strip()

    mime_type = image.content_type or "image/png"
    encoded_image = base64.b64encode(content).decode("ascii")
    image_data_url = f"data:{mime_type};base64,{encoded_image}"

    clamped_max_tokens = max(64, min(int(max_tokens), 4000))
    model_name = model.strip()
    # HTML checkbox semantics: unchecked sends no field at all.
    # When omitted, default to text-only for coding models.
    effective_attach_image = (
        attach_image_to_messages
        if attach_image_to_messages is not None
        else ("coding" not in model_name.lower())
    )

    user_content: Any
    if effective_attach_image:
        user_content = [
            {"type": "text", "text": user_prompt.strip()},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        user_content = user_prompt.strip()

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "temperature": max(0.0, min(float(temperature), 2.0)),
        "max_tokens": clamped_max_tokens,
    }
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}

    payload = _merge_request_overrides(payload, request_overrides_json)
    payload = _apply_override_placeholders(
        payload,
        {
            "{{IMAGE_DATA_URL}}": image_data_url,
            "{{IMAGE_MIME_TYPE}}": mime_type,
            "{{IMAGE_FILENAME}}": image.filename or "uploaded_image",
        },
    )

    resolved_fallback_endpoint = (
        fallback_endpoint_url.strip()
        or os.getenv("LITELLM_FALLBACK_API_URL", "").strip()
        or os.getenv("LITELLM_FALLBACK_VISION_ENDPOINT", "").strip()
    )
    resolved_fallback_model = (
        fallback_model.strip() or os.getenv("LITELLM_FALLBACK_MODEL", "").strip()
    )
    resolved_fallback_api_key = (
        fallback_api_key.strip() or os.getenv("LITELLM_FALLBACK_API_KEY", "").strip()
    )
    auto_fallback_enabled = (
        auto_fallback_on_safety
        or _env_bool("LITELLM_AUTO_FALLBACK_ON_SAFETY", default=False)
    )

    (
        result_json,
        resolved_endpoint,
        resolved_api_key,
        active_headers,
        provider_route,
        fallback_attempted,
        fallback_used,
        fallback_reason,
        fallback_error,
    ) = _execute_with_optional_safety_fallback(
        payload=payload,
        resolved_endpoint=resolved_endpoint,
        resolved_api_key=resolved_api_key,
        auto_fallback_enabled=auto_fallback_enabled,
        resolved_fallback_endpoint=resolved_fallback_endpoint,
        resolved_fallback_model=resolved_fallback_model,
        resolved_fallback_api_key=resolved_fallback_api_key,
    )

    raw_completion_text = _extract_completion_text(result_json)
    finish_reason = _extract_finish_reason(result_json)
    structured = _parse_json_from_text(raw_completion_text)
    maybe_truncated = _is_maybe_truncated(finish_reason, raw_completion_text)

    retry_attempted = False
    retry_succeeded = False
    retry_error: str | dict[str, Any] | None = None
    retry_used_max_tokens: int | None = None
    retry_target = max(64, min(int(retry_max_tokens), 4000))

    if auto_retry_on_truncation and maybe_truncated and retry_target > clamped_max_tokens:
        retry_attempted = True
        retry_used_max_tokens = retry_target
        retry_payload = dict(payload)
        retry_payload["max_tokens"] = retry_target
        try:
            retry_result_json = _post_vision_completion(
                resolved_endpoint,
                active_headers,
                retry_payload,
            )
            retry_raw_completion_text = _extract_completion_text(retry_result_json)
            retry_finish_reason = _extract_finish_reason(retry_result_json)
            retry_structured = _parse_json_from_text(retry_raw_completion_text)
            retry_maybe_truncated = _is_maybe_truncated(
                retry_finish_reason,
                retry_raw_completion_text,
            )

            # Prefer the retry output if it looks improved or is parseable JSON.
            if (retry_structured is not None and structured is None) or (
                not retry_maybe_truncated and maybe_truncated
            ):
                result_json = retry_result_json
                raw_completion_text = retry_raw_completion_text
                finish_reason = retry_finish_reason
                structured = retry_structured
                maybe_truncated = retry_maybe_truncated
            retry_succeeded = True
        except HTTPException as exc:
            retry_error = exc.detail if isinstance(exc.detail, (str, dict)) else str(exc.detail)

    return {
        "ok": True,
        "request": {
            "filename": image.filename,
            "content_type": mime_type,
            "size_bytes": len(content),
            "model": str(payload.get("model") or model.strip()),
            "endpoint_url": resolved_endpoint,
            "used_api_key": bool(resolved_api_key),
            "provider_route": provider_route,
            "attach_image_to_messages": effective_attach_image,
        },
        "structured": structured,
        "raw_completion_text": raw_completion_text,
        "completion_meta": {
            "finish_reason": finish_reason,
            "usage": result_json.get("usage") if isinstance(result_json, dict) else None,
            "maybe_truncated": maybe_truncated,
            "json_parse_ok": structured is not None,
            "retry": {
                "attempted": retry_attempted,
                "succeeded": retry_succeeded,
                "retry_max_tokens": retry_used_max_tokens,
                "error": retry_error,
            },
            "fallback": {
                "attempted": fallback_attempted,
                "used": fallback_used,
                "reason": fallback_reason,
                "error": fallback_error,
            },
            "hint": (
                "If maybe_truncated=true, increase max_tokens (e.g. 1800-2400) "
                "or simplify the response schema."
            ),
        },
        "raw_provider_response": result_json,
    }


@router.post("/style-prefill/analyze-single-responses-mcp", response_model=dict)
async def analyze_single_image_style_prefill_responses_mcp(
    request: Request,
    image: UploadFile = File(...),
    model: str = Form(default="zai-org/glm-4.7-coding"),
    endpoint_url: str = Form(default=""),
    api_key: str = Form(default=""),
    user_prompt: str = Form(default="Analyze this image for style prefill prototyping."),
    mcp_server_url: str = Form(default=""),
    mcp_server_label: str = Form(default="litellm"),
    mcp_servers_header: str = Form(default=""),
    mcp_target_mode: str = Form(default="server"),
    mcp_toolset_select: str = Form(default=""),
    mcp_image_source_url: str = Form(default=""),
    mcp_public_base_url: str = Form(default=""),
    mcp_allow_private_image_source: bool = Form(default=False),
    mcp_execution_mode: str = Form(default="direct"),
    request_overrides_json: str = Form(default=""),
):
    """Analyze one image using OpenAI Responses API with MCP tool integration."""
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 15MB)")

    resolved_endpoint = (
        endpoint_url.strip()
        or os.getenv("LITELLM_RESPONSES_API_URL", "").strip()
        or _derive_responses_endpoint_from_api_url(
            os.getenv("LITELLM_API_URL", "").strip()
        )
        or "http://localhost:4000/v1/responses"
    )
    resolved_api_key = api_key.strip() or os.getenv("LITELLM_API_KEY", "").strip()
    selected_toolset = mcp_toolset_select.strip()
    resolved_mcp_server_url = _resolve_mcp_server_url(
        submitted_mcp_server_url=(
            mcp_server_url.strip() or os.getenv("LITELLM_MCP_SERVER_URL", "").strip()
        ),
        mcp_target_mode=mcp_target_mode,
        selected_toolset=selected_toolset,
        litellm_api_url=os.getenv("LITELLM_API_URL", "").strip(),
    )

    is_toolset_mcp_url = "/toolset/" in resolved_mcp_server_url and resolved_mcp_server_url.rstrip("/").endswith("/mcp")
    resolved_mcp_server_label = mcp_server_label.strip() or "litellm"
    resolved_mcp_servers_header = (
        mcp_servers_header.strip()
        or os.getenv("LITELLM_MCP_SERVERS", "").strip()
        or ("" if is_toolset_mcp_url else "zai_vision_mcp")
    )

    mime_type = image.content_type or "image/png"
    encoded_image = base64.b64encode(content).decode("ascii")
    image_data_url = f"data:{mime_type};base64,{encoded_image}"
    hosted_mcp_image_token: str | None = None
    hosted_mcp_image_url: str | None = None
    if not mcp_image_source_url.strip():
        hosted_mcp_image_token = _create_style_prefill_mcp_image_entry(
            content=content,
            mime_type=mime_type,
            filename=image.filename or "uploaded_image",
        )
        hosted_mcp_image_url = _build_style_prefill_mcp_image_url(
            request=request,
            token=hosted_mcp_image_token,
            public_base_url=mcp_public_base_url,
        )

    mcp_image_source = _resolve_mcp_image_source(
        provided_image_source_url=(mcp_image_source_url.strip() or (hosted_mcp_image_url or "")),
        image_data_url=image_data_url,
    )

    if mcp_public_base_url.strip():
        source_host = (urlsplit(mcp_image_source).hostname or "")
        if _is_localhost_host(source_host):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "mcp_image_source resolved to localhost despite mcp_public_base_url",
                    "mcp_image_source": mcp_image_source,
                    "mcp_public_base_url": mcp_public_base_url.strip(),
                    "hint": (
                        "Check reverse proxy / forwarded host handling for this request. "
                        "The generated image source must use the provided mcp_public_base_url origin."
                    ),
                },
            )

    # Guardrail: remote MCP cannot fetch localhost/private callback URLs.
    # This avoids opaque downstream image-parse failures with access_count=0.
    source_parts = urlsplit(mcp_image_source)
    mcp_parts = urlsplit(resolved_mcp_server_url)
    if (
        hosted_mcp_image_url
        and source_parts.scheme in {"http", "https"}
        and mcp_parts.scheme in {"http", "https"}
        and source_parts.netloc
        and mcp_parts.netloc
    ):
        source_host = source_parts.hostname or ""
        mcp_host = mcp_parts.hostname or ""
        source_private = _host_is_local_or_private(source_host)
        mcp_private = _host_is_local_or_private(mcp_host)
        if source_private and not mcp_private and not mcp_allow_private_image_source:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Hosted image URL is not reachable by remote MCP server",
                    "mcp_image_source": mcp_image_source,
                    "mcp_server_url": resolved_mcp_server_url,
                    "hint": (
                        "Set mcp_public_base_url to a publicly reachable origin "
                        "so MCP can fetch /api/style-prefill/mcp-image/{token}, "
                        "or enable mcp_allow_private_image_source if your MCP host can access private LAN URLs."
                    ),
                },
            )

    payload: dict[str, Any] = {
        "model": model.strip(),
        "input": user_prompt.strip(),
        "tool_choice": "required",
        "tools": [
            {
                "type": "mcp",
                "mcp": {
                    "server": resolved_mcp_server_label,
                    "server_label": resolved_mcp_server_label,
                    "server_url": resolved_mcp_server_url,
                    "tool": "analyze_image",
                    "arguments": {
                        "image_source": "{{MCP_IMAGE_SOURCE}}",
                    },
                    "require_approval": "never",
                    "headers": {
                        "x-litellm-api-key": (
                            f"Bearer {resolved_api_key}" if resolved_api_key else ""
                        ),
                        "Authorization": (
                            f"Bearer {resolved_api_key}" if resolved_api_key else ""
                        ),
                        "Accept": "text/event-stream, application/json",
                        "Content-Type": "application/json",
                        "x-mcp-servers": resolved_mcp_servers_header,
                    },
                },
            }
        ],
    }

    overrides = _parse_request_overrides_object(request_overrides_json)
    payload.update(overrides)
    payload = _normalize_mcp_tools_shape(payload)

    # Guarantee MCP server routing header is never blank after overrides.
    tools_obj = payload.get("tools")
    if isinstance(tools_obj, list):
        for tool_item in tools_obj:
            if not isinstance(tool_item, dict):
                continue
            if str(tool_item.get("type", "")).lower() != "mcp":
                continue
            mcp_obj = tool_item.get("mcp")
            if not isinstance(mcp_obj, dict):
                continue
            headers_obj = mcp_obj.get("headers")
            if not isinstance(headers_obj, dict):
                headers_obj = {}
                mcp_obj["headers"] = headers_obj
            if (
                not is_toolset_mcp_url
                and not str(headers_obj.get("x-mcp-servers", "")).strip()
            ):
                headers_obj["x-mcp-servers"] = resolved_mcp_servers_header

    payload = _apply_override_placeholders(
        payload,
        {
            "{{IMAGE_DATA_URL}}": image_data_url,
            "{{MCP_IMAGE_SOURCE}}": mcp_image_source,
            "{{IMAGE_MIME_TYPE}}": mime_type,
            "{{IMAGE_FILENAME}}": image.filename or "uploaded_image",
        },
    )

    # Fail fast with a clear error before calling responses pipeline when MCP
    # endpoint is reachable but does not expose tools.
    tools_obj = payload.get("tools")
    if isinstance(tools_obj, list) and tools_obj:
        first_tool = tools_obj[0] if isinstance(tools_obj[0], dict) else None
        mcp_obj = first_tool.get("mcp") if isinstance(first_tool, dict) else None
        if isinstance(mcp_obj, dict):
            mcp_url = str(mcp_obj.get("server_url") or resolved_mcp_server_url)
            mcp_headers = mcp_obj.get("headers") if isinstance(mcp_obj.get("headers"), dict) else {}
            ok, discovered_tools, probe_error = _probe_mcp_tools_list(mcp_url, mcp_headers)
            if not ok:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "MCP tools/list preflight failed",
                        "mcp_server_url": mcp_url,
                        "mcp_server_label": mcp_obj.get("server_label") or resolved_mcp_server_label,
                        "mcp_servers_header": (
                            mcp_headers.get("x-mcp-servers") if isinstance(mcp_headers, dict) else None
                        ),
                        "probe_error": probe_error,
                        "hint": (
                            "Verify the selected MCP route exposes tools. "
                            "Discovery endpoint can list toolsets while MCP endpoint still returns no tools."
                        ),
                    },
                )

            # Direct MCP tool execution avoids current responses-tool conversion
            # failures seen on some LiteLLM/ZAI routes.
            if mcp_execution_mode.strip().lower() == "direct":
                direct_result = _run_direct_mcp_style_prefill(
                    mcp_url=mcp_url,
                    mcp_headers=mcp_headers,
                    discovered_tools=discovered_tools,
                    mcp_server_label=str(mcp_obj.get("server_label") or resolved_mcp_server_label),
                    image_data_url=mcp_image_source,
                    user_prompt=user_prompt,
                    image=image,
                    mime_type=mime_type,
                    content_size=len(content),
                    model_name=str(payload.get("model", model.strip())),
                    resolved_endpoint=resolved_endpoint,
                    resolved_api_key=resolved_api_key,
                    mcp_target_mode=mcp_target_mode,
                    mcp_execution_mode=mcp_execution_mode,
                    selected_toolset=selected_toolset,
                    resolved_mcp_server_url=resolved_mcp_server_url,
                    resolved_mcp_servers_header=resolved_mcp_servers_header,
                )
                direct_result.setdefault("request", {})
                direct_result["request"].update(
                    {
                        "mcp_image_source": mcp_image_source,
                        "mcp_image_source_mode": (
                            "provided_url"
                            if mcp_image_source_url.strip()
                            else "hosted_uploaded_image_url"
                        ),
                        "mcp_public_base_url": mcp_public_base_url.strip() or None,
                        "mcp_allow_private_image_source": mcp_allow_private_image_source,
                        "mcp_hosted_image_token": hosted_mcp_image_token,
                    }
                )
                direct_result.setdefault("completion_meta", {})
                direct_result["completion_meta"]["mcp_image_access"] = (
                    _get_style_prefill_mcp_image_access_snapshot(hosted_mcp_image_token)
                    if hosted_mcp_image_token
                    else None
                )
                return direct_result

    headers = {"Content-Type": "application/json"}
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"

    result_json = _post_responses_request(resolved_endpoint, headers, payload)
    raw_completion_text = _extract_responses_output_text(result_json)
    structured = _parse_json_from_text(raw_completion_text)

    return {
        "ok": True,
        "request": {
            "filename": image.filename,
            "content_type": mime_type,
            "size_bytes": len(content),
            "model": payload.get("model", model.strip()),
            "endpoint_url": resolved_endpoint,
            "used_api_key": bool(resolved_api_key),
            "provider_route": "responses_mcp",
            "mcp_target_mode": mcp_target_mode,
            "mcp_execution_mode": mcp_execution_mode,
            "mcp_toolset_select": selected_toolset,
            "mcp_image_source_mode": (
                "provided_url" if mcp_image_source_url.strip() else "hosted_uploaded_image_url"
            ),
            "mcp_image_source": mcp_image_source,
            "mcp_public_base_url": mcp_public_base_url.strip() or None,
            "mcp_allow_private_image_source": mcp_allow_private_image_source,
            "mcp_hosted_image_token": hosted_mcp_image_token,
            "mcp_server_url": resolved_mcp_server_url,
            "mcp_server_label": resolved_mcp_server_label,
            "mcp_servers_header": resolved_mcp_servers_header,
        },
        "structured": structured,
        "raw_completion_text": raw_completion_text,
        "completion_meta": {
            "json_parse_ok": structured is not None,
            "usage": result_json.get("usage") if isinstance(result_json, dict) else None,
            "hint": (
                "If raw_completion_text is empty, inspect raw_provider_response.output "
                "for MCP tool call results or adapter-specific blocks."
            ),
            "mcp_image_access": (
                _get_style_prefill_mcp_image_access_snapshot(hosted_mcp_image_token)
                if hosted_mcp_image_token
                else None
            ),
        },
        "raw_provider_response": result_json,
    }


@router.post("/style-prefill/mcp-probe-image-url", response_model=dict)
async def probe_style_prefill_mcp_image_url(
    request: Request,
    image: UploadFile = File(...),
    endpoint_url: str = Form(default=""),
    api_key: str = Form(default=""),
    mcp_server_url: str = Form(default=""),
    mcp_server_label: str = Form(default="litellm"),
    mcp_servers_header: str = Form(default=""),
    mcp_target_mode: str = Form(default="server"),
    mcp_toolset_select: str = Form(default=""),
    mcp_public_base_url: str = Form(default=""),
    mcp_allow_private_image_source: bool = Form(default=False),
    mcp_probe_prompt: str = Form(default="Briefly confirm this image URL is readable."),
):
    """Probe whether selected MCP route can fetch a hosted uploaded image URL."""
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 15MB)")

    resolved_api_key = api_key.strip() or os.getenv("LITELLM_API_KEY", "").strip()
    selected_toolset = mcp_toolset_select.strip()
    resolved_mcp_server_url = _resolve_mcp_server_url(
        submitted_mcp_server_url=(
            mcp_server_url.strip() or os.getenv("LITELLM_MCP_SERVER_URL", "").strip()
        ),
        mcp_target_mode=mcp_target_mode,
        selected_toolset=selected_toolset,
        litellm_api_url=(
            endpoint_url.strip() or os.getenv("LITELLM_API_URL", "").strip()
        ),
    )

    is_toolset_mcp_url = "/toolset/" in resolved_mcp_server_url and resolved_mcp_server_url.rstrip("/").endswith("/mcp")
    resolved_mcp_server_label = mcp_server_label.strip() or "litellm"
    resolved_mcp_servers_header = (
        mcp_servers_header.strip()
        or os.getenv("LITELLM_MCP_SERVERS", "").strip()
        or ("" if is_toolset_mcp_url else "zai_vision_mcp")
    )

    mime_type = image.content_type or "image/png"
    hosted_token = _create_style_prefill_mcp_image_entry(
        content=content,
        mime_type=mime_type,
        filename=image.filename or "uploaded_image",
    )
    hosted_url = _build_style_prefill_mcp_image_url(
        request=request,
        token=hosted_token,
        public_base_url=mcp_public_base_url,
    )

    if mcp_public_base_url.strip():
        hosted_host = (urlsplit(hosted_url).hostname or "")
        if _is_localhost_host(hosted_host):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Probe hosted image URL resolved to localhost despite mcp_public_base_url",
                    "mcp_image_source": hosted_url,
                    "mcp_public_base_url": mcp_public_base_url.strip(),
                    "hint": (
                        "Check reverse proxy / forwarded host handling for this request. "
                        "The generated image source must use the provided mcp_public_base_url origin."
                    ),
                },
            )

    source_parts = urlsplit(hosted_url)
    mcp_parts = urlsplit(resolved_mcp_server_url)
    source_host = source_parts.hostname or ""
    mcp_host = mcp_parts.hostname or ""
    source_private = _host_is_local_or_private(source_host)
    mcp_private = _host_is_local_or_private(mcp_host)
    if source_private and not mcp_private and not mcp_allow_private_image_source:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Hosted image URL is not reachable by remote MCP server",
                "mcp_image_source": hosted_url,
                "mcp_server_url": resolved_mcp_server_url,
                "hint": (
                    "Set mcp_public_base_url to a publicly reachable origin "
                    "so MCP can fetch /api/style-prefill/mcp-image/{token}, "
                    "or enable mcp_allow_private_image_source if your MCP host can access private LAN URLs."
                ),
            },
        )

    mcp_headers: dict[str, str] = {
        "Accept": "text/event-stream, application/json",
        "Content-Type": "application/json",
    }
    if resolved_api_key:
        mcp_headers["Authorization"] = f"Bearer {resolved_api_key}"
        mcp_headers["x-litellm-api-key"] = f"Bearer {resolved_api_key}"
    if resolved_mcp_servers_header:
        mcp_headers["x-mcp-servers"] = resolved_mcp_servers_header

    before_access = _get_style_prefill_mcp_image_access_snapshot(hosted_token)
    ok, discovered_tools, probe_error = _probe_mcp_tools_list(resolved_mcp_server_url, mcp_headers)
    if not ok:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "MCP tools/list preflight failed during reachability probe",
                "mcp_server_url": resolved_mcp_server_url,
                "mcp_server_label": resolved_mcp_server_label,
                "mcp_servers_header": resolved_mcp_servers_header,
                "probe_error": probe_error,
                "mcp_image_source": hosted_url,
                "mcp_image_access_before": before_access,
            },
        )

    tool_name = _select_analyze_image_tool_name(discovered_tools, resolved_mcp_server_label)
    init_payload = {
        "jsonrpc": "2.0",
        "id": "style-prefill-probe-init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "atelier-style-prefill-probe", "version": "1.0.0"},
            "capabilities": {},
        },
    }

    init_result, init_response_headers = _mcp_jsonrpc_request(
        resolved_mcp_server_url,
        mcp_headers,
        init_payload,
        timeout=25,
    )

    call_headers = dict(mcp_headers)
    session_id = init_response_headers.get("mcp-session-id")
    if isinstance(session_id, str) and session_id.strip():
        call_headers["mcp-session-id"] = session_id.strip()

    call_payload = {
        "jsonrpc": "2.0",
        "id": "style-prefill-probe-call",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {
                "image_source": hosted_url,
                "prompt": mcp_probe_prompt.strip(),
            },
        },
    }
    call_result, _ = _mcp_jsonrpc_request(
        resolved_mcp_server_url,
        call_headers,
        call_payload,
        timeout=60,
    )

    after_access = _get_style_prefill_mcp_image_access_snapshot(hosted_token)
    before_count = int((before_access or {}).get("access_count") or 0)
    after_count = int((after_access or {}).get("access_count") or 0)

    return {
        "ok": True,
        "probe": {
            "reachable": after_count > before_count,
            "access_count_delta": after_count - before_count,
            "mcp_image_source": hosted_url,
            "mcp_server_url": resolved_mcp_server_url,
            "mcp_server_label": resolved_mcp_server_label,
            "mcp_servers_header": resolved_mcp_servers_header,
            "tool_name": tool_name,
            "mcp_target_mode": mcp_target_mode,
            "mcp_toolset_select": selected_toolset,
            "mcp_public_base_url": mcp_public_base_url.strip() or None,
            "mcp_allow_private_image_source": mcp_allow_private_image_source,
        },
        "mcp_image_access_before": before_access,
        "mcp_image_access_after": after_access,
        "raw_provider_response": {
            "initialize": init_result,
            "tool_call": call_result,
        },
    }


@router.get("/style-prefill/mcp-toolsets", response_model=dict)
def list_style_prefill_mcp_toolsets(
    endpoint_base_url: str = Query(default=""),
    api_key: str = Query(default=""),
):
    """List available MCP toolsets from LiteLLM for the style prefill prototype UI."""
    vscode_env = _read_workspace_vscode_env()
    resolved_base = (
        endpoint_base_url.strip()
        or vscode_env.get("LITELLM_API_URL", "").strip()
        or os.getenv("LITELLM_API_URL", "").strip()
    )
    resolved_key = (
        api_key.strip()
        or vscode_env.get("LITELLM_API_KEY", "").strip()
        or os.getenv("LITELLM_API_KEY", "").strip()
    )

    toolsets = _fetch_litellm_mcp_toolsets(resolved_base, resolved_key)
    return {
        "ok": True,
        "endpoint_base_url": resolved_base,
        "count": len(toolsets),
        "toolsets": toolsets,
    }
