# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/model-reference.md
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import joinedload

from models import GenerationProcess, GenerationStage, ImageModel


def _civitai_web_base_url() -> str:
    """Resolve the CivitAI web base URL from config."""
    try:
        import atelierai.config as cfg
        return getattr(cfg, "CIVITAI_WEB_BASE_URL", "https://civitai.red") or "https://civitai.red"
    except ImportError:
        return "https://civitai.red"


_HEX_HASH_RE = re.compile(r"\b[a-f0-9]{8,64}\b", flags=re.IGNORECASE)


DEFAULT_COMFYUI_BASE_URL = "http://localhost:8188"
DEFAULT_COMFYUI_CHECKPOINTS_URL = f"{DEFAULT_COMFYUI_BASE_URL}/api/lm/checkpoints/list"
DEFAULT_COMFYUI_LORAS_URL = f"{DEFAULT_COMFYUI_BASE_URL}/api/lm/loras/list"
DEFAULT_COMFYUI_CHECKPOINTS_METADATA_URL = f"{DEFAULT_COMFYUI_BASE_URL}/api/lm/checkpoints/metadata"
DEFAULT_COMFYUI_LORAS_METADATA_URL = f"{DEFAULT_COMFYUI_BASE_URL}/api/lm/loras/metadata"
DEFAULT_COMFYUI_DOWNLOAD_MODEL_URL = f"{DEFAULT_COMFYUI_BASE_URL}/api/lm/download-model"

class ModelReferenceService:
    """Aggregates model references from media metadata and optional local catalogs."""

    _RESOURCE_TYPE_ALIASES = {
        "model": "checkpoint",
        "checkpoint": "checkpoint",
        "ckpt": "checkpoint",
        "diffusion_model": "diffusion_model",
        "lora": "lora",
        "lycoris": "lora",
        "locon": "lora",
        "loha": "lora",
        "dora": "dora",
        "vae": "vae",
        "upscaler": "upscaler",
        "embedding": "textualinversion",
        "textualinversion": "textualinversion",
        "textual_inversion": "textualinversion",
    }

    # Maps variant resource types to their parent category for
    # broad matching (e.g. diffusion_model → checkpoint, dora → lora).
    _RESOURCE_TYPE_PARENTS: dict[str, str] = {
        "diffusion_model": "checkpoint",
        "dora": "lora",
    }

    def normalize_resource_type(self, value: Any) -> str:
        normalized = str(value or "other").strip().lower()
        return self._RESOURCE_TYPE_ALIASES.get(normalized, normalized or "other")

    def parent_resource_type(self, value: Any) -> str:
        """Return the parent category for a resource type.

        Variant types like ``diffusion_model`` map to ``checkpoint``; ``dora``
        maps to ``lora``.  Types without an explicit parent mapping are returned
        unchanged.
        """
        normalized = self.normalize_resource_type(value)
        return self._RESOURCE_TYPE_PARENTS.get(normalized, normalized)

    def normalize_display_name(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = urlparse(text)
        if parsed.scheme and parsed.path:
            text = parsed.path
        text = text.replace("\\", "/")
        return text.rsplit("/", 1)[-1].strip() or None

    def normalize_name_key(self, value: Any) -> Optional[str]:
        text = self.normalize_display_name(value)
        if not text:
            return None
        stem = Path(text).stem.strip() if "." in text else text.strip()
        stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip().lower()
        return stem or None

    def _model_folder_segment_for_type(self, resource_type: str) -> str:
        normalized = self.normalize_resource_type(resource_type)
        mapping = {
            "checkpoint": "checkpoints",
            "lora": "loras",
            "vae": "vae",
            "upscaler": "upscale_models",
            "textualinversion": "embeddings",
        }
        return mapping.get(normalized, "")

    def _derive_catalog_source_identifier(self, entry: dict[str, Any], resource_type: str) -> Optional[str]:
        if not isinstance(entry, dict):
            return None

        def _clean_text(value: Any) -> str:
            return str(value or "").strip().replace("\\", "/")

        folder = _clean_text(entry.get("folder"))
        file_path = _clean_text(entry.get("file_path"))
        file_name = _clean_text(entry.get("file_name") or entry.get("filename"))

        candidates = [
            entry.get("source_identifier"),
            entry.get("relative_path"),
            entry.get("model_path"),
            entry.get("path"),
            entry.get("file_path"),
            entry.get("filename"),
            entry.get("file_name"),
        ]

        model_segment = self._model_folder_segment_for_type(resource_type)
        for candidate in candidates:
            raw = _clean_text(candidate)
            if not raw:
                continue

            if model_segment:
                marker = f"/models/{model_segment}/"
                lowered = raw.lower()
                marker_index = lowered.find(marker)
                if marker_index >= 0:
                    suffix = raw[marker_index + len(marker):].strip("/")
                    if suffix:
                        return suffix

            if raw.startswith("/"):
                base = raw.rsplit("/", 1)[-1]
                if folder and base:
                    return f"{folder}/{base}"
                if base:
                    return base

            if "/" in raw:
                return raw.strip("/")

            if folder:
                return f"{folder}/{raw}"
            return raw

        if file_path:
            basename = file_path.rsplit("/", 1)[-1]
            if basename:
                if folder:
                    return f"{folder}/{basename}"
                return basename

        if file_name:
            if folder:
                return f"{folder}/{file_name}"
            return file_name

        return None

    def _list_payload(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _dict_payload(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _file_basename(self, value: Any) -> Optional[str]:
        text = str(value or "").strip().replace("\\", "/")
        if not text:
            return None
        return text.rsplit("/", 1)[-1].strip() or None

    def _compose_catalog_display_name(
        self,
        *,
        model_name: Any,
        version_name: Any,
        file_name: Any,
        fallback: Any,
    ) -> Optional[str]:
        model_text = str(model_name or "").strip()
        version_text = str(version_name or "").strip()
        file_text = str(file_name or "").strip()
        fallback_text = self.normalize_display_name(fallback)

        if model_text and version_text:
            base = f"{model_text} ({version_text})"
        else:
            base = model_text or version_text or (fallback_text or "")

        if not base:
            return None
        if file_text:
            return f"{base} - {file_text}"
        return base

    def _coerce_optional_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _looks_like_hash(self, value: str) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and _HEX_HASH_RE.fullmatch(text))

    def _extract_hashes(self, value: Any, *, hashes: Optional[set[str]] = None) -> set[str]:
        if hashes is None:
            hashes = set()
        if value is None:
            return hashes
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key or "").strip().lower()
                if "hash" in key_text or key_text in {"sha", "sha256", "model_hash"}:
                    self._extract_hashes(item, hashes=hashes)
                elif isinstance(item, (dict, list, tuple, set)):
                    self._extract_hashes(item, hashes=hashes)
                elif isinstance(item, str):
                    self._extract_hashes(item, hashes=hashes)
            return hashes
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._extract_hashes(item, hashes=hashes)
            return hashes
        if isinstance(value, str):
            for match in _HEX_HASH_RE.findall(value):
                normalized = match.strip().lower()
                if len(normalized) >= 8:
                    hashes.add(normalized)
        return hashes

    def _reference_key(self, reference: dict[str, Any]) -> tuple[Any, ...]:
        version_id = reference.get("civitai_model_version_id")
        if version_id is not None:
            return ("version", int(version_id))
        model_id = reference.get("civitai_model_id")
        if model_id is not None:
            return ("model", reference.get("resource_type"), int(model_id))
        hashes = tuple(sorted(str(item) for item in reference.get("hashes") or []))
        if hashes:
            return ("hash", reference.get("resource_type"), hashes)
        source_identifier = str(reference.get("source_identifier") or "").strip().lower()
        if source_identifier:
            return ("source", source_identifier)
        normalized_name = str(reference.get("normalized_name") or "").strip().lower()
        if normalized_name:
            return ("name", reference.get("resource_type"), normalized_name)
        return ("fallback", reference.get("resource_type"), id(reference))

    def _merge_reference(self, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        for field_name in (
            "resource_type",
            "display_name",
            "normalized_name",
            "version_name",
            "base_model_name",
            "civitai_model_id",
            "civitai_model_version_id",
            "source_identifier",
        ):
            if not merged.get(field_name) and incoming.get(field_name):
                merged[field_name] = incoming.get(field_name)
        merged["is_primary"] = bool(merged.get("is_primary") or incoming.get("is_primary"))
        merged["remote_hosted"] = bool(merged.get("remote_hosted") or incoming.get("remote_hosted"))
        merged_hashes = set(str(item) for item in merged.get("hashes") or [])
        merged_hashes.update(str(item) for item in incoming.get("hashes") or [])
        merged["hashes"] = sorted(merged_hashes)
        merged["observation_count"] = int(merged.get("observation_count") or 0) + int(incoming.get("observation_count") or 0)

        targets = {
            (str(item.get("target_mode") or ""), str(item.get("target_key") or "")): item
            for item in merged.get("targets") or []
            if isinstance(item, dict)
        }
        for target in incoming.get("targets") or []:
            if not isinstance(target, dict):
                continue
            target_key = (str(target.get("target_mode") or ""), str(target.get("target_key") or ""))
            if target_key not in targets:
                targets[target_key] = target
        merged["targets"] = list(targets.values())
        merged["target_count"] = len(targets)

        usages = list(merged.get("usages") or [])
        existing_usage_keys = {
            (
                str(item.get("target_key") or ""),
                item.get("process_index"),
                item.get("stage_index"),
                str(item.get("stage_role") or ""),
            )
            for item in usages
            if isinstance(item, dict)
        }
        for usage in incoming.get("usages") or []:
            if not isinstance(usage, dict):
                continue
            usage_key = (
                str(usage.get("target_key") or ""),
                usage.get("process_index"),
                usage.get("stage_index"),
                str(usage.get("stage_role") or ""),
            )
            if usage_key in existing_usage_keys:
                continue
            usages.append(usage)
            existing_usage_keys.add(usage_key)
        merged["usages"] = usages[:24]
        return merged

    def _summarize_references(self, references: list[dict[str, Any]], local_catalog: dict[str, Any]) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        matched_local = 0
        remote_hosted = 0
        for reference in references:
            resource_type = str(reference.get("resource_type") or "other")
            by_type[resource_type] = int(by_type.get(resource_type, 0)) + 1
            if reference.get("local_installed"):
                matched_local += 1
            if reference.get("remote_hosted"):
                remote_hosted += 1
        return {
            "reference_count": len(references),
            "matched_local_count": matched_local,
            "remote_hosted_count": remote_hosted,
            "by_type": {key: by_type[key] for key in sorted(by_type)},
            "local_catalog_entry_count": len(local_catalog.get("entries") or []),
        }

    def _build_validation(self, references: list[dict[str, Any]], local_catalog: dict[str, Any], *, catalog_expected: bool) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        if not references:
            warnings.append("No model references were extracted from the selected source.")
        if catalog_expected:
            if not local_catalog.get("configured"):
                warnings.append("No ComfyUI LoRA Manager base URL or API endpoint overrides are configured yet.")
            elif local_catalog.get("error"):
                warnings.append(str(local_catalog.get("error")))
            elif not local_catalog.get("entries"):
                warnings.append("The configured local model catalog returned no parseable entries.")
        status = "ok"
        if errors:
            status = "error"
        elif warnings:
            status = "warning"
        return {
            "status": status,
            "warnings": warnings,
            "errors": errors,
            "warning_count": len(warnings),
            "error_count": len(errors),
        }

    def _normalize_reference_observation(
        self,
        resource: dict[str, Any],
        *,
        target_mode: str,
        target_key: str,
        target_label: str,
        process_index: int,
        stage_index: Optional[int],
        stage_role: Optional[str],
        stage_label: Optional[str],
    ) -> dict[str, Any]:
        resource_type = self.normalize_resource_type(resource.get("resource_type"))
        display_name = resource.get("display_name") or self.normalize_display_name(resource.get("source_identifier"))
        normalized_name = resource.get("normalized_name") or self.normalize_name_key(display_name or resource.get("source_identifier"))
        hashes = sorted(self._extract_hashes(resource.get("raw_resource_json") or {}))
        if self._looks_like_hash(str(resource.get("source_identifier") or "")):
            hashes.append(str(resource.get("source_identifier")).strip().lower())
        hashes = sorted(set(hashes))
        return {
            "resource_type": resource_type,
            "display_name": display_name,
            "normalized_name": normalized_name,
            "version_name": resource.get("version_name"),
            "base_model_name": resource.get("base_model_name"),
            "civitai_model_id": self._coerce_optional_int(resource.get("civitai_model_id")),
            "civitai_model_version_id": self._coerce_optional_int(resource.get("civitai_model_version_id")),
            "source_identifier": str(resource.get("source_identifier") or "").strip() or None,
            "is_primary": bool(resource.get("is_primary")),
            "remote_hosted": bool(resource.get("civitai_model_id") or resource.get("civitai_model_version_id")),
            "hashes": hashes,
            "observation_count": 1,
            "targets": [
                {
                    "target_mode": target_mode,
                    "target_key": target_key,
                    "target_label": target_label,
                }
            ],
            "usages": [
                {
                    "target_key": target_key,
                    "target_label": target_label,
                    "process_index": process_index,
                    "stage_index": stage_index,
                    "stage_role": stage_role,
                    "stage_label": stage_label,
                }
            ],
        }

    def extract_references_from_generation_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        target = self._dict_payload(payload.get("target"))
        mode = str(payload.get("mode") or "inspection").strip() or "inspection"
        target_key = str(target.get("file_hash") or target.get("image_id") or target.get("image_db_id") or "unknown")
        target_label = str(target.get("source_url") or target.get("file_hash") or target.get("image_id") or target_key)
        normalized = self._dict_payload(payload.get("normalized"))
        processes = self._list_payload(normalized.get("processes"))

        aggregated: dict[tuple[Any, ...], dict[str, Any]] = {}
        for process_index, process in enumerate(processes):
            if not isinstance(process, dict):
                continue
            process_resources = self._list_payload(process.get("resources"))
            for resource in process_resources:
                if not isinstance(resource, dict):
                    continue
                normalized_resource = self._normalize_reference_observation(
                    resource,
                    target_mode=mode,
                    target_key=target_key,
                    target_label=target_label,
                    process_index=process_index,
                    stage_index=None,
                    stage_role=None,
                    stage_label=None,
                )
                key = self._reference_key(normalized_resource)
                if key in aggregated:
                    aggregated[key] = self._merge_reference(aggregated[key], normalized_resource)
                else:
                    aggregated[key] = normalized_resource

            for stage in self._list_payload(process.get("stages")):
                if not isinstance(stage, dict):
                    continue
                stage_index = self._coerce_optional_int(stage.get("stage_index"))
                stage_role = str(stage.get("stage_role") or "").strip() or None
                stage_label = str(stage.get("stage_label") or stage.get("method_variant") or "").strip() or None
                for resource in self._list_payload(stage.get("resources")):
                    if not isinstance(resource, dict):
                        continue
                    normalized_resource = self._normalize_reference_observation(
                        resource,
                        target_mode=mode,
                        target_key=target_key,
                        target_label=target_label,
                        process_index=process_index,
                        stage_index=stage_index,
                        stage_role=stage_role,
                        stage_label=stage_label,
                    )
                    key = self._reference_key(normalized_resource)
                    if key in aggregated:
                        aggregated[key] = self._merge_reference(aggregated[key], normalized_resource)
                    else:
                        aggregated[key] = normalized_resource

        references = list(aggregated.values())
        references.sort(key=lambda item: (str(item.get("resource_type") or ""), str(item.get("display_name") or item.get("normalized_name") or "")))
        return references

    def _normalize_catalog_entry(self, entry: dict[str, Any], default_type: Optional[str] = None) -> Optional[dict[str, Any]]:
        civitai_payload = entry.get("civitai") if isinstance(entry.get("civitai"), dict) else {}
        model_payload = entry.get("model") if isinstance(entry.get("model"), dict) else {}
        resource_type = self.normalize_resource_type(
            entry.get("resource_type")
            or entry.get("type")
            or entry.get("modelType")
            or entry.get("sub_type")
            or default_type
        )
        file_path = entry.get("file_path") or entry.get("path")
        file_name = (
            entry.get("file_name")
            or entry.get("filename")
            or self._file_basename(file_path)
        )
        model_name = (
            model_payload.get("name")
            or entry.get("model_name")
            or entry.get("modelName")
            or entry.get("title")
        )
        version_name = (
            entry.get("version_name")
            or entry.get("versionName")
            or entry.get("name")
        )
        display_name = self._compose_catalog_display_name(
            model_name=model_name,
            version_name=version_name,
            file_name=file_name,
            fallback=(
                entry.get("display_name")
                or entry.get("name")
                or entry.get("modelName")
                or entry.get("title")
                or entry.get("filename")
                or entry.get("file_name")
                or entry.get("path")
            ),
        )
        normalized_name = self.normalize_name_key(version_name or model_name or display_name)
        if not normalized_name:
            return None
        hashes = sorted(self._extract_hashes(entry))
        civitai_model_id = self._coerce_optional_int(civitai_payload.get("modelId"))
        civitai_version_id = self._coerce_optional_int(
            civitai_payload.get("modelVersionId")
            or civitai_payload.get("versionId")
            or civitai_payload.get("id")
        )

        # LoRA Manager APIs often provide authoritative IDs under entry.civitai,
        # where modelId is the base model and id is the model version.
        model_id = self._coerce_optional_int(
            civitai_model_id
            or entry.get("civitai_model_id")
            or entry.get("modelId")
            or entry.get("civitaiModelId")
        )
        version_id = self._coerce_optional_int(
            civitai_version_id
            or entry.get("civitai_model_version_id")
            or entry.get("modelVersionId")
            or entry.get("civitaiModelVersionId")
        )

        civitai_url = None
        if model_id is not None:
            civitai_url = f"{_civitai_web_base_url()}/models/{int(model_id)}"
            if version_id is not None:
                civitai_url = f"{civitai_url}?modelVersionId={int(version_id)}"

        source_identifier = self._derive_catalog_source_identifier(entry, resource_type)
        return {
            "resource_type": resource_type,
            "display_name": display_name,
            "normalized_name": normalized_name,
            "version_name": version_name,
            "model_name": model_name,
            "file_name": file_name,
            "file_path": str(file_path).strip() if file_path else None,
            "civitai_model_id": model_id,
            "civitai_model_version_id": version_id,
            "civitai_url": civitai_url,
            "source_identifier": source_identifier,
            "hashes": hashes,
            "raw_entry": entry,
        }

    def _entries_from_catalog_payload(self, payload: Any, *, default_type: Optional[str] = None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    normalized = self._normalize_catalog_entry(item, default_type=default_type)
                    if normalized is not None:
                        entries.append(normalized)
            return entries

        if not isinstance(payload, dict):
            return entries

        singular = self._normalize_catalog_entry(payload, default_type=default_type)
        if singular is not None:
            entries.append(singular)

        for key, value in payload.items():
            key_type = self.normalize_resource_type(key)
            if key in {"items", "data", "models", "entries"} and isinstance(value, list):
                entries.extend(self._entries_from_catalog_payload(value, default_type=default_type))
            elif key_type in {"checkpoint", "lora", "vae", "upscaler", "textualinversion"}:
                entries.extend(self._entries_from_catalog_payload(value, default_type=key_type))
        return entries

    def _compact_catalog_payload(self, payload: Any, *, sample_limit: int = 5) -> Any:
        if isinstance(payload, dict):
            compact: dict[str, Any] = {}
            passthrough_keys = {
                "total",
                "page",
                "page_size",
                "total_pages",
                "fetched_pages",
                "fetched_item_count",
            }
            for key in passthrough_keys:
                if key in payload:
                    compact[key] = payload.get(key)

            items = payload.get("items")
            if isinstance(items, list):
                compact["returned_items"] = len(items)
                compact["sample_items"] = [
                    {
                        "model_name": item.get("model_name") if isinstance(item, dict) else None,
                        "file_name": item.get("file_name") if isinstance(item, dict) else None,
                        "base_model": item.get("base_model") if isinstance(item, dict) else None,
                        "sub_type": item.get("sub_type") if isinstance(item, dict) else None,
                        "resource_type": item.get("resource_type") if isinstance(item, dict) else None,
                        "civitai": item.get("civitai") if isinstance(item, dict) else None,
                    }
                    for item in items[: max(0, int(sample_limit))]
                ]

            if compact:
                return compact
            return {"type": "object", "keys": sorted(payload.keys())}

        if isinstance(payload, list):
            return {
                "type": "list",
                "count": len(payload),
                "sample": payload[: max(0, int(sample_limit))],
            }

        return payload

    def _compact_catalog_raw(self, raw_payloads: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, payload in (raw_payloads or {}).items():
            compact[str(key)] = self._compact_catalog_payload(payload)
        return compact

    def _dedupe_catalog_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
        for entry in entries:
            key = self._reference_key(entry)
            if key in deduped:
                merged = dict(deduped[key])
                merged_hashes = set(str(item) for item in merged.get("hashes") or [])
                merged_hashes.update(str(item) for item in entry.get("hashes") or [])
                merged["hashes"] = sorted(merged_hashes)
                if not merged.get("display_name") and entry.get("display_name"):
                    merged["display_name"] = entry.get("display_name")
                deduped[key] = merged
            else:
                deduped[key] = entry
        result = list(deduped.values())
        result.sort(key=lambda item: (str(item.get("resource_type") or ""), str(item.get("display_name") or item.get("normalized_name") or "")))
        return result

    def _resolve_local_catalog_sources(
        self,
        *,
        catalog_url: Optional[str] = None,
        checkpoints_url: Optional[str] = None,
        loras_url: Optional[str] = None,
        checkpoints_metadata_url: Optional[str] = None,
        loras_metadata_url: Optional[str] = None,
    ) -> dict[str, Any]:
        resolved_catalog_url = self._normalize_comfyui_base_url(catalog_url or os.getenv("ATELIER_COMFYUI_BASE_URL"))
        resolved_checkpoints_url = str(checkpoints_url or "").strip() or None
        resolved_loras_url = str(loras_url or "").strip() or None
        resolved_checkpoints_metadata_url = str(
            checkpoints_metadata_url or os.getenv("ATELIER_COMFYUI_CHECKPOINTS_METADATA_URL") or ""
        ).strip() or None
        resolved_loras_metadata_url = str(
            loras_metadata_url or os.getenv("ATELIER_COMFYUI_LORAS_METADATA_URL") or ""
        ).strip() or None
        resolved_download_model_url = str(os.getenv("ATELIER_COMFYUI_DOWNLOAD_MODEL_URL") or "").strip() or None

        if resolved_catalog_url:
            if not resolved_checkpoints_url:
                resolved_checkpoints_url = f"{resolved_catalog_url}/api/lm/checkpoints/list"
            if not resolved_loras_url:
                resolved_loras_url = f"{resolved_catalog_url}/api/lm/loras/list"
            if not resolved_checkpoints_metadata_url:
                resolved_checkpoints_metadata_url = f"{resolved_catalog_url}/api/lm/checkpoints/metadata"
            if not resolved_loras_metadata_url:
                resolved_loras_metadata_url = f"{resolved_catalog_url}/api/lm/loras/metadata"
            if not resolved_download_model_url:
                resolved_download_model_url = f"{resolved_catalog_url}/api/lm/download-model"
        elif not resolved_checkpoints_url and not resolved_loras_url:
            resolved_catalog_url = DEFAULT_COMFYUI_BASE_URL
            resolved_checkpoints_url = DEFAULT_COMFYUI_CHECKPOINTS_URL
            resolved_loras_url = DEFAULT_COMFYUI_LORAS_URL
            if not resolved_checkpoints_metadata_url:
                resolved_checkpoints_metadata_url = DEFAULT_COMFYUI_CHECKPOINTS_METADATA_URL
            if not resolved_loras_metadata_url:
                resolved_loras_metadata_url = DEFAULT_COMFYUI_LORAS_METADATA_URL
            if not resolved_download_model_url:
                resolved_download_model_url = DEFAULT_COMFYUI_DOWNLOAD_MODEL_URL

        return {
            "configured": bool(resolved_catalog_url or resolved_checkpoints_url or resolved_loras_url),
            "catalog_url": resolved_catalog_url,
            "checkpoints_url": resolved_checkpoints_url,
            "loras_url": resolved_loras_url,
            "checkpoints_metadata_url": resolved_checkpoints_metadata_url,
            "loras_metadata_url": resolved_loras_metadata_url,
            "download_model_url": resolved_download_model_url,
        }

    def _default_model_root_for_type(self, resource_type: Optional[str]) -> Optional[str]:
        normalized = self.normalize_resource_type(resource_type or "")
        mapping = {
            "lora": "/workspace/ComfyUI/models/loras",
            "checkpoint": "/workspace/ComfyUI/models/checkpoints",
            "model": "/workspace/ComfyUI/models/checkpoints",
            "vae": "/workspace/ComfyUI/models/vae",
            "upscaler": "/workspace/ComfyUI/models/upscale_models",
            "textualinversion": "/workspace/ComfyUI/models/embeddings",
        }
        return mapping.get(normalized)

    def download_local_model(
        self,
        *,
        civitai_model_id: Any,
        civitai_model_version_id: Any,
        resource_type: Optional[str] = None,
        relative_path: str = "",
        use_default_paths: bool = False,
        download_id: Optional[str] = None,
        catalog_url: Optional[str] = None,
        checkpoints_url: Optional[str] = None,
        loras_url: Optional[str] = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        model_id = self._coerce_optional_int(civitai_model_id)
        version_id = self._coerce_optional_int(civitai_model_version_id)
        if model_id is None or version_id is None:
            return {
                "ok": False,
                "error": "Both CivitAI model and version IDs are required to download a local model.",
                "request": None,
            }

        sources = self._resolve_local_catalog_sources(
            catalog_url=catalog_url,
            checkpoints_url=checkpoints_url,
            loras_url=loras_url,
        )
        endpoint_url = str(sources.get("download_model_url") or "").strip()
        if not endpoint_url:
            return {
                "ok": False,
                "error": "No LoRA Manager download-model endpoint is configured.",
                "request": None,
                "sources": sources,
            }

        resolved_model_root = self._default_model_root_for_type(resource_type)
        if not resolved_model_root:
            return {
                "ok": False,
                "error": "Could not determine target model_root for this resource type.",
                "request": None,
                "sources": sources,
            }

        request_payload = {
            "model_id": str(int(model_id)),
            "model_version_id": int(version_id),
            "model_root": resolved_model_root,
            "relative_path": str(relative_path or ""),
            "use_default_paths": bool(use_default_paths),
            "download_id": str(download_id or int(time.time() * 1000)),
        }

        try:
            with requests.Session() as session:
                response = session.post(
                    endpoint_url,
                    json=request_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                response_payload = response.json() if response.content else {"success": True}
        except (requests.RequestException, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Could not start LoRA Manager download: {exc}",
                "request": request_payload,
                "endpoint_url": endpoint_url,
                "sources": sources,
            }

        return {
            "ok": True,
            "endpoint_url": endpoint_url,
            "sources": sources,
            "request": request_payload,
            "result": response_payload,
            "error": None,
        }

    def _strip_html_text(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain).strip()
        return plain or None

    def fetch_local_model_preview(
        self,
        *,
        search_name: str,
        resource_type: Optional[str] = None,
        file_path: Optional[str] = None,
        file_name: Optional[str] = None,
        model_name: Optional[str] = None,
        version_name: Optional[str] = None,
        civitai_model_id: Optional[int] = None,
        civitai_model_version_id: Optional[int] = None,
        catalog_url: Optional[str] = None,
        checkpoints_url: Optional[str] = None,
        loras_url: Optional[str] = None,
        checkpoints_metadata_url: Optional[str] = None,
        loras_metadata_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        normalized_search = str(search_name or "").strip()
        if not normalized_search:
            return {
                "ok": False,
                "error": "Search name is required.",
                "configured": False,
                "sources": {},
                "preview": None,
            }

        sources = self._resolve_local_catalog_sources(
            catalog_url=catalog_url,
            checkpoints_url=checkpoints_url,
            loras_url=loras_url,
            checkpoints_metadata_url=checkpoints_metadata_url,
            loras_metadata_url=loras_metadata_url,
        )
        configured = bool(sources.get("configured"))
        if not configured:
            return {
                "ok": False,
                "error": "No LoRA Manager catalog URL is configured.",
                "configured": False,
                "sources": sources,
                "preview": None,
            }

        normalized_type = self.normalize_resource_type(resource_type or "")
        normalized_file_path = str(file_path or "").strip()
        normalized_file_name = self._file_basename(file_name)
        normalized_model_name = str(model_name or "").strip()
        normalized_version_name = str(version_name or "").strip()
        requested_model_id = self._coerce_optional_int(civitai_model_id)
        requested_version_id = self._coerce_optional_int(civitai_model_version_id)

        search_candidates = [
            normalized_file_name,
            normalized_version_name,
            normalized_model_name,
            normalized_search,
        ]
        effective_search_name = next((item for item in search_candidates if str(item or "").strip()), normalized_search)
        effective_search_key = self.normalize_name_key(effective_search_name) or self.normalize_name_key(normalized_search)

        def _build_preview(metadata: dict[str, Any], endpoint_type: str) -> dict[str, Any]:
            normalized_entry = self._normalize_catalog_entry(metadata, default_type=endpoint_type) or {}
            image_items = metadata.get("images") if isinstance(metadata.get("images"), list) else []
            first_image = image_items[0] if image_items and isinstance(image_items[0], dict) else {}
            creator_payload = metadata.get("creator") if isinstance(metadata.get("creator"), dict) else {}
            model_payload = metadata.get("model") if isinstance(metadata.get("model"), dict) else {}

            return {
                "display_name": normalized_entry.get("display_name") or metadata.get("name") or metadata.get("model_name"),
                "version_name": metadata.get("name") or metadata.get("version_name") or normalized_entry.get("version_name"),
                "model_name": model_payload.get("name") or metadata.get("model_name") or normalized_entry.get("model_name"),
                "file_name": normalized_entry.get("file_name") or metadata.get("file_name") or self._file_basename(metadata.get("file_path") or metadata.get("path")),
                "file_path": normalized_entry.get("file_path") or metadata.get("file_path") or metadata.get("path"),
                "model_type": model_payload.get("type") or metadata.get("modelType") or normalized_entry.get("resource_type"),
                "creator_username": creator_payload.get("username"),
                "creator_image": creator_payload.get("image"),
                "civitai_model_id": normalized_entry.get("civitai_model_id") or self._coerce_optional_int(metadata.get("modelId")),
                "civitai_model_version_id": normalized_entry.get("civitai_model_version_id") or self._coerce_optional_int(metadata.get("id") or metadata.get("modelVersionId")),
                "civitai_url": normalized_entry.get("civitai_url"),
                "base_model": metadata.get("baseModel") or metadata.get("base_model"),
                "description": self._strip_html_text(metadata.get("description")),
                "preview_image_url": first_image.get("url"),
                "preview_image_width": first_image.get("width"),
                "preview_image_height": first_image.get("height"),
            }

        metadata_endpoint_url: Optional[str] = None
        metadata_endpoint_type: Optional[str] = None
        if normalized_file_path and normalized_type in {"checkpoint", "model"} and sources.get("checkpoints_metadata_url"):
            metadata_endpoint_url = str(sources.get("checkpoints_metadata_url") or "").strip()
            metadata_endpoint_type = "checkpoint"
        elif normalized_file_path and normalized_type == "lora" and sources.get("loras_metadata_url"):
            metadata_endpoint_url = str(sources.get("loras_metadata_url") or "").strip()
            metadata_endpoint_type = "lora"

        if metadata_endpoint_url and metadata_endpoint_type:
            try:
                with requests.Session() as session:
                    response = session.get(
                        metadata_endpoint_url,
                        params={"file_path": normalized_file_path},
                        timeout=timeout_seconds,
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                payload = {
                    "success": False,
                    "metadata": None,
                    "_metadata_fetch_error": str(exc),
                }

            metadata = payload.get("metadata") if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict) else None
            if isinstance(metadata, dict):
                return {
                    "ok": True,
                    "configured": configured,
                    "sources": sources,
                    "endpoint_type": metadata_endpoint_type,
                    "endpoint_url": metadata_endpoint_url,
                    "query": {"file_path": normalized_file_path},
                    "preview": _build_preview(metadata, metadata_endpoint_type),
                    "raw": {
                        "success": payload.get("success") if isinstance(payload, dict) else None,
                    },
                    "error": None,
                }

        endpoint_candidates: list[tuple[str, Optional[str]]] = []
        if normalized_type in {"checkpoint", "model"}:
            endpoint_candidates.append(("checkpoint", sources.get("checkpoints_url")))
        elif normalized_type == "lora":
            endpoint_candidates.append(("lora", sources.get("loras_url")))
        else:
            endpoint_candidates.extend([
                ("checkpoint", sources.get("checkpoints_url")),
                ("lora", sources.get("loras_url")),
            ])

        query_params = {
            "page": 1,
            "page_size": 100,
            "sort_by": "date:desc",
            "folder": "",
            "search": effective_search_name,
            "fuzzy": "true",
            "search_filename": "true",
            "search_modelname": "true",
            "search_tags": "true",
            "search_creator": "false",
            "recursive": "true",
            "tag_logic": "any",
        }

        with requests.Session() as session:
            for endpoint_type, endpoint_url in endpoint_candidates:
                if not endpoint_url:
                    continue
                try:
                    response = session.get(endpoint_url, params=query_params, timeout=timeout_seconds)
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError) as exc:
                    return {
                        "ok": False,
                        "error": f"Could not fetch preview data from local catalog endpoint: {exc}",
                        "configured": configured,
                        "sources": sources,
                        "endpoint_type": endpoint_type,
                        "endpoint_url": endpoint_url,
                        "query": query_params,
                        "preview": None,
                    }

                metadata = payload.get("metadata") if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict) else None
                if metadata is None and isinstance(payload, dict):
                    items = payload.get("items") if isinstance(payload.get("items"), list) else []
                    if items:
                        exact_name = effective_search_key
                        ranked = sorted(
                            [item for item in items if isinstance(item, dict)],
                            key=lambda item: (
                                0 if (
                                    requested_version_id is not None
                                    and self._coerce_optional_int(
                                        (item.get("civitai") or {}).get("id") if isinstance(item.get("civitai"), dict) else item.get("id")
                                    ) == requested_version_id
                                ) else 1,
                                0 if (
                                    requested_model_id is not None
                                    and self._coerce_optional_int(
                                        (item.get("civitai") or {}).get("modelId") if isinstance(item.get("civitai"), dict) else item.get("modelId")
                                    ) == requested_model_id
                                ) else 1,
                                0 if self.normalize_name_key(item.get("file_name") or item.get("filename")) == exact_name else 1,
                                0 if self.normalize_name_key(item.get("name") or "") == exact_name else 1,
                                0 if self.normalize_name_key(item.get("model_name") or "") == exact_name else 1,
                            ),
                        )
                        if ranked:
                            metadata = ranked[0]

                if not isinstance(metadata, dict):
                    continue

                preview = _build_preview(metadata, endpoint_type)

                return {
                    "ok": True,
                    "configured": configured,
                    "sources": sources,
                    "endpoint_type": endpoint_type,
                    "endpoint_url": endpoint_url,
                    "query": query_params,
                    "preview": preview,
                    "raw": {
                        "success": payload.get("success") if isinstance(payload, dict) else None,
                    },
                    "error": None,
                }

        return {
            "ok": False,
            "configured": configured,
            "sources": sources,
            "query": query_params,
            "preview": None,
            "error": "No preview metadata found for the selected local match.",
        }

    def fetch_local_catalog(
        self,
        *,
        catalog_url: Optional[str] = None,
        checkpoints_url: Optional[str] = None,
        loras_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
        include_full_raw: bool = False,
    ) -> dict[str, Any]:
        sources = self._resolve_local_catalog_sources(
            catalog_url=catalog_url,
            checkpoints_url=checkpoints_url,
            loras_url=loras_url,
        )
        configured = bool(sources.get("configured"))
        resolved_catalog_url = sources.get("catalog_url")
        resolved_checkpoints_url = sources.get("checkpoints_url")
        resolved_loras_url = sources.get("loras_url")
        result = {
            "configured": configured,
            "sources": {
                "catalog_url": resolved_catalog_url,
                "checkpoints_url": resolved_checkpoints_url,
                "loras_url": resolved_loras_url,
            },
            "entries": [],
            "raw": {},
            "raw_compacted": not bool(include_full_raw),
            "error": None,
        }
        if not configured:
            return result

        entries: list[dict[str, Any]] = []
        try:
            session = requests.Session()
            if resolved_checkpoints_url:
                payload = self._fetch_paginated_catalog_payload(
                    session,
                    resolved_checkpoints_url,
                    timeout_seconds=timeout_seconds,
                )
                result["raw"]["checkpoints"] = payload
                entries.extend(self._entries_from_catalog_payload(payload, default_type="checkpoint"))
            if resolved_loras_url:
                payload = self._fetch_paginated_catalog_payload(
                    session,
                    resolved_loras_url,
                    timeout_seconds=timeout_seconds,
                )
                result["raw"]["loras"] = payload
                entries.extend(self._entries_from_catalog_payload(payload, default_type="lora"))
        except (requests.RequestException, ValueError) as exc:
            result["error"] = f"Could not fetch the configured local model catalog: {exc}"
            result["entries"] = []
            return result

        result["entries"] = self._dedupe_catalog_entries(entries)
        if not include_full_raw:
            result["raw"] = self._compact_catalog_raw(result.get("raw") or {})
        return result

    def _fetch_paginated_catalog_payload(
        self,
        session: requests.Session,
        url: str,
        *,
        timeout_seconds: float,
        page_size: int = 200,
        max_pages: int = 200,
    ) -> Any:
        # Always send page_size on the first request so the server uses our
        # desired page size from the start.  Some endpoints (e.g. ComfyUI
        # LoRA Manager) cap the effective page_size server-side, and if the
        # first request omits page_size the server returns a *different*
        # default (e.g. 20) which makes total_pages inconsistent with later
        # requests that do include page_size.
        response = session.get(
            url,
            params={"page_size": page_size},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        first_payload = response.json()

        if not isinstance(first_payload, dict):
            return first_payload

        first_items = first_payload.get("items")
        if not isinstance(first_items, list):
            return first_payload

        total_items = self._coerce_optional_int(first_payload.get("total"))
        total_pages = self._coerce_optional_int(first_payload.get("total_pages")) or 1
        if total_pages <= 1:
            return first_payload

        merged_items = list(first_items)
        fetched_pages = 1

        for page in range(2, min(total_pages, max_pages) + 1):
            page_response = session.get(
                url,
                params={"page": page, "page_size": page_size},
                timeout=timeout_seconds,
            )
            page_response.raise_for_status()
            page_payload = page_response.json()
            if not isinstance(page_payload, dict):
                break

            reported_page = self._coerce_optional_int(page_payload.get("page"))
            if reported_page is not None and reported_page != page:
                # Endpoint ignored pagination parameters; avoid appending duplicate first-page results.
                break

            page_items = page_payload.get("items")
            if not isinstance(page_items, list) or not page_items:
                break

            merged_items.extend(page_items)
            fetched_pages += 1

            reported_total_pages = self._coerce_optional_int(page_payload.get("total_pages"))
            if reported_total_pages is not None:
                total_pages = reported_total_pages

            # If we already have all items, stop early.
            if total_items is not None and len(merged_items) >= total_items:
                break

        merged_payload = dict(first_payload)
        merged_payload["items"] = merged_items
        merged_payload["fetched_pages"] = fetched_pages
        merged_payload["fetched_item_count"] = len(merged_items)
        return merged_payload

    def _normalize_comfyui_base_url(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None

        if "://" not in text:
            text = f"http://{text}"

        parsed = urlparse(text)
        scheme = parsed.scheme.lower() if parsed.scheme else "http"
        if scheme not in {"http", "https"}:
            scheme = "http"

        host_port = parsed.netloc.strip()
        if not host_port:
            host_port = parsed.path.split("/", 1)[0].strip()
        if not host_port:
            return None

        if host_port.startswith("["):
            if "]" in host_port:
                suffix = host_port.split("]", 1)[1]
                if not suffix.startswith(":"):
                    host_port = f"{host_port}:8188"
            else:
                host_port = f"{host_port}:8188"
        elif ":" not in host_port:
            host_port = f"{host_port}:8188"

        return f"{scheme}://{host_port}"

    def _types_match(self, reference_type: str, catalog_type: str) -> bool:
        left = self.normalize_resource_type(reference_type)
        right = self.normalize_resource_type(catalog_type)
        if left == right:
            return True
        if {left, right} <= {"checkpoint", "model"}:
            return True
        return False

    def _compact_name_key(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[^a-z0-9]+", "", text)

    def _hashes_overlap(self, left_hashes: set[str], right_hashes: set[str]) -> bool:
        if not left_hashes or not right_hashes:
            return False
        for left in left_hashes:
            left_text = str(left or "").strip().lower()
            if len(left_text) < 8:
                continue
            for right in right_hashes:
                right_text = str(right or "").strip().lower()
                if len(right_text) < 8:
                    continue
                if left_text == right_text or left_text.startswith(right_text) or right_text.startswith(left_text):
                    return True
        return False

    def apply_local_catalog_matches(self, references: list[dict[str, Any]], local_catalog: dict[str, Any]) -> list[dict[str, Any]]:
        catalog_entries = local_catalog.get("entries") or []
        if not isinstance(catalog_entries, list) or not catalog_entries:
            return [dict(reference, local_installed=False, local_matches=[]) for reference in references]

        matched_references: list[dict[str, Any]] = []
        for reference in references:
            matches: list[dict[str, Any]] = []
            reference_hashes = {str(item).lower() for item in reference.get("hashes") or []}
            reference_name = str(reference.get("normalized_name") or "").strip().lower()
            reference_name_candidates = {
                str(reference.get("normalized_name") or "").strip().lower(),
                str(self.normalize_name_key(reference.get("display_name")) or "").strip().lower(),
                str(self.normalize_name_key(reference.get("version_name")) or "").strip().lower(),
                str(self.normalize_name_key(reference.get("source_identifier")) or "").strip().lower(),
            }
            reference_name_candidates = {name for name in reference_name_candidates if name}
            reference_compact_candidates = {
                self._compact_name_key(name)
                for name in reference_name_candidates
                if self._compact_name_key(name)
            }
            reference_model_id = reference.get("civitai_model_id")
            reference_version_id = reference.get("civitai_model_version_id")
            reference_type = str(reference.get("resource_type") or "other")
            for entry in catalog_entries:
                if not isinstance(entry, dict):
                    continue
                entry_type = str(entry.get("resource_type") or "other")
                if not self._types_match(reference_type, entry_type):
                    continue
                basis: Optional[str] = None
                entry_hashes = {str(item).lower() for item in entry.get("hashes") or []}
                if reference_version_id is not None and entry.get("civitai_model_version_id") == reference_version_id:
                    basis = "civitai_model_version_id"
                elif reference_model_id is not None and entry.get("civitai_model_id") == reference_model_id:
                    basis = "civitai_model_id"
                elif self._hashes_overlap(reference_hashes, entry_hashes):
                    basis = "hash"
                elif reference_name and reference_name == str(entry.get("normalized_name") or "").strip().lower():
                    basis = "normalized_name"
                else:
                    entry_name_candidates = {
                        str(entry.get("normalized_name") or "").strip().lower(),
                        str(self.normalize_name_key(entry.get("display_name")) or "").strip().lower(),
                        str(self.normalize_name_key(entry.get("source_identifier")) or "").strip().lower(),
                    }
                    entry_name_candidates = {name for name in entry_name_candidates if name}
                    entry_compact_candidates = {
                        self._compact_name_key(name)
                        for name in entry_name_candidates
                        if self._compact_name_key(name)
                    }
                    if reference_compact_candidates and entry_compact_candidates:
                        if reference_compact_candidates & entry_compact_candidates:
                            basis = "name_fuzzy"
                if basis is None:
                    continue
                matches.append(
                    {
                        "display_name": entry.get("display_name") or entry.get("normalized_name"),
                        "version_name": entry.get("version_name"),
                        "model_name": entry.get("model_name"),
                        "file_name": entry.get("file_name"),
                        "file_path": entry.get("file_path"),
                        "resource_type": entry_type,
                        "match_basis": basis,
                        "source_identifier": entry.get("source_identifier"),
                        "model_path": entry.get("source_identifier"),
                        "civitai_model_id": entry.get("civitai_model_id"),
                        "civitai_model_version_id": entry.get("civitai_model_version_id"),
                        "civitai_url": entry.get("civitai_url"),
                        "hashes": entry.get("hashes") or [],
                    }
                )
            next_reference = dict(reference)
            next_reference["local_installed"] = bool(matches)
            next_reference["local_matches"] = matches[:5]
            matched_references.append(next_reference)
        return matched_references

    def build_item_payload(self, source_payload: dict[str, Any], *, local_catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        local_catalog = local_catalog or {"configured": False, "entries": [], "raw": {}, "raw_compacted": True, "error": None}
        extracted_references = self.extract_references_from_generation_payload(source_payload)
        references = self.apply_local_catalog_matches(extracted_references, local_catalog)
        summary = self._summarize_references(references, local_catalog)
        source_overview = self._dict_payload(source_payload.get("overview"))
        overview = {
            **source_overview,
            **summary,
        }
        validation = self._build_validation(references, local_catalog, catalog_expected=True)
        return {
            "ok": validation.get("status") != "error",
            "mode": str(source_payload.get("mode") or "inspection"),
            "target": self._dict_payload(source_payload.get("target")),
            "overview": overview,
            "normalized": {
                "references": references,
                "summary": summary,
                "local_catalog": {
                    "configured": bool(local_catalog.get("configured")),
                    "entry_count": len(local_catalog.get("entries") or []),
                    "sources": self._dict_payload(local_catalog.get("sources")),
                },
            },
            "validation": validation,
            "raw": {
                "source_inspection": source_payload,
                "local_catalog_fetch": {
                    "sources": self._dict_payload(local_catalog.get("sources")),
                    "error": local_catalog.get("error"),
                    "raw_compacted": bool(local_catalog.get("raw_compacted", True)),
                    "raw": self._dict_payload(local_catalog.get("raw")),
                },
            },
            "error": None,
        }

    def _reference_from_db_resource(
        self,
        resource: Any,
        *,
        image: ImageModel,
        process_index: int,
        stage_index: Optional[int],
        stage_role: Optional[str],
        stage_label: Optional[str],
    ) -> dict[str, Any]:
        raw_resource = resource.raw_resource_json if isinstance(resource.raw_resource_json, dict) else resource.raw_resource_json
        return self._normalize_reference_observation(
            {
                "resource_type": resource.resource_type,
                "display_name": resource.display_name,
                "normalized_name": resource.normalized_name,
                "version_name": resource.version_name,
                "base_model_name": resource.base_model_name,
                "civitai_model_id": resource.civitai_model_id,
                "civitai_model_version_id": resource.civitai_model_version_id,
                "source_identifier": resource.source_identifier,
                "is_primary": resource.is_primary,
                "raw_resource_json": raw_resource,
            },
            target_mode="local",
            target_key=str(image.file_hash),
            target_label=str(image.file_name or image.file_hash),
            process_index=process_index,
            stage_index=stage_index,
            stage_role=stage_role,
            stage_label=stage_label,
        )

    def build_library_catalog_payload(
        self,
        db,
        *,
        image_limit: int = 250,
        local_catalog: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        local_catalog = local_catalog or {"configured": False, "entries": [], "raw": {}, "raw_compacted": True, "error": None}
        images = (
            db.query(ImageModel)
            .options(
                joinedload(ImageModel.generation_processes).joinedload(GenerationProcess.resources),
                joinedload(ImageModel.generation_processes)
                .joinedload(GenerationProcess.stages)
                .joinedload(GenerationStage.resources),
            )
            .filter((ImageModel.image_status.is_(None)) | (ImageModel.image_status == "active"))
            .order_by(ImageModel.id.desc())
            .limit(max(1, int(image_limit)))
            .all()
        )

        aggregated: dict[tuple[Any, ...], dict[str, Any]] = {}
        images_with_refs = 0
        for image in images:
            image_had_refs = False
            for process_index, process in enumerate(image.generation_processes or []):
                for resource in process.resources or []:
                    observation = self._reference_from_db_resource(
                        resource,
                        image=image,
                        process_index=process_index,
                        stage_index=None,
                        stage_role=None,
                        stage_label=None,
                    )
                    key = self._reference_key(observation)
                    if key in aggregated:
                        aggregated[key] = self._merge_reference(aggregated[key], observation)
                    else:
                        aggregated[key] = observation
                    image_had_refs = True
                for stage in process.stages or []:
                    for resource in stage.resources or []:
                        observation = self._reference_from_db_resource(
                            resource,
                            image=image,
                            process_index=process_index,
                            stage_index=stage.stage_index,
                            stage_role=stage.stage_role,
                            stage_label=stage.stage_label,
                        )
                        key = self._reference_key(observation)
                        if key in aggregated:
                            aggregated[key] = self._merge_reference(aggregated[key], observation)
                        else:
                            aggregated[key] = observation
                        image_had_refs = True
            if image_had_refs:
                images_with_refs += 1

        references = list(aggregated.values())
        references.sort(key=lambda item: (str(item.get("resource_type") or ""), str(item.get("display_name") or item.get("normalized_name") or "")))
        references = self.apply_local_catalog_matches(references, local_catalog)
        summary = self._summarize_references(references, local_catalog)
        overview = {
            "image_limit": max(1, int(image_limit)),
            "image_count": len(images),
            "images_with_references": images_with_refs,
            **summary,
        }
        validation = self._build_validation(references, local_catalog, catalog_expected=True)
        return {
            "ok": validation.get("status") != "error",
            "mode": "catalog",
            "target": {
                "image_limit": max(1, int(image_limit)),
            },
            "overview": overview,
            "normalized": {
                "references": references,
                "summary": summary,
                "local_catalog": {
                    "configured": bool(local_catalog.get("configured")),
                    "entry_count": len(local_catalog.get("entries") or []),
                    "sources": self._dict_payload(local_catalog.get("sources")),
                },
            },
            "validation": validation,
            "raw": {
                "local_catalog_fetch": {
                    "sources": self._dict_payload(local_catalog.get("sources")),
                    "error": local_catalog.get("error"),
                    "raw_compacted": bool(local_catalog.get("raw_compacted", True)),
                    "raw": self._dict_payload(local_catalog.get("raw")),
                },
            },
            "error": None,
        }