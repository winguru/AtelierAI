from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import joinedload

from models import GenerationProcess, GenerationStage, ImageModel


_HEX_HASH_RE = re.compile(r"\b[a-f0-9]{8,64}\b", flags=re.IGNORECASE)


class ModelReferenceService:
    """Aggregates model references from media metadata and optional local catalogs."""

    _RESOURCE_TYPE_ALIASES = {
        "model": "checkpoint",
        "checkpoint": "checkpoint",
        "ckpt": "checkpoint",
        "lora": "lora",
        "lycoris": "lora",
        "locon": "lora",
        "loha": "lora",
        "vae": "vae",
        "upscaler": "upscaler",
        "embedding": "textualinversion",
        "textualinversion": "textualinversion",
        "textual_inversion": "textualinversion",
    }

    def normalize_resource_type(self, value: Any) -> str:
        normalized = str(value or "other").strip().lower()
        return self._RESOURCE_TYPE_ALIASES.get(normalized, normalized or "other")

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

    def _list_payload(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _dict_payload(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

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
                warnings.append("No ComfyUI model catalog endpoint is configured yet. Provide exact API URLs or set env vars first.")
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
        resource_type = self.normalize_resource_type(
            entry.get("resource_type")
            or entry.get("type")
            or entry.get("modelType")
            or default_type
        )
        display_name = (
            entry.get("display_name")
            or entry.get("name")
            or entry.get("modelName")
            or entry.get("title")
            or entry.get("filename")
            or entry.get("file_name")
            or entry.get("path")
        )
        normalized_name = self.normalize_name_key(display_name)
        if not normalized_name:
            return None
        hashes = sorted(self._extract_hashes(entry))
        model_id = self._coerce_optional_int(
            entry.get("civitai_model_id")
            or entry.get("modelId")
            or entry.get("civitaiModelId")
        )
        version_id = self._coerce_optional_int(
            entry.get("civitai_model_version_id")
            or entry.get("modelVersionId")
            or entry.get("civitaiModelVersionId")
        )
        source_identifier = str(entry.get("source_identifier") or entry.get("path") or entry.get("filename") or "").strip() or None
        return {
            "resource_type": resource_type,
            "display_name": self.normalize_display_name(display_name),
            "normalized_name": normalized_name,
            "version_name": entry.get("version_name") or entry.get("versionName"),
            "civitai_model_id": model_id,
            "civitai_model_version_id": version_id,
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

    def fetch_local_catalog(
        self,
        *,
        catalog_url: Optional[str] = None,
        checkpoints_url: Optional[str] = None,
        loras_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        resolved_catalog_url = str(catalog_url or os.getenv("ATELIER_COMFYUI_MODEL_CATALOG_URL") or "").strip() or None
        resolved_checkpoints_url = str(checkpoints_url or os.getenv("ATELIER_COMFYUI_CHECKPOINTS_URL") or "").strip() or None
        resolved_loras_url = str(loras_url or os.getenv("ATELIER_COMFYUI_LORAS_URL") or "").strip() or None

        configured = bool(resolved_catalog_url or resolved_checkpoints_url or resolved_loras_url)
        result = {
            "configured": configured,
            "sources": {
                "catalog_url": resolved_catalog_url,
                "checkpoints_url": resolved_checkpoints_url,
                "loras_url": resolved_loras_url,
            },
            "entries": [],
            "raw": {},
            "error": None,
        }
        if not configured:
            return result

        entries: list[dict[str, Any]] = []
        try:
            session = requests.Session()
            if resolved_catalog_url:
                response = session.get(resolved_catalog_url, timeout=timeout_seconds)
                response.raise_for_status()
                payload = response.json()
                result["raw"]["catalog"] = payload
                entries.extend(self._entries_from_catalog_payload(payload))
            else:
                if resolved_checkpoints_url:
                    response = session.get(resolved_checkpoints_url, timeout=timeout_seconds)
                    response.raise_for_status()
                    payload = response.json()
                    result["raw"]["checkpoints"] = payload
                    entries.extend(self._entries_from_catalog_payload(payload, default_type="checkpoint"))
                if resolved_loras_url:
                    response = session.get(resolved_loras_url, timeout=timeout_seconds)
                    response.raise_for_status()
                    payload = response.json()
                    result["raw"]["loras"] = payload
                    entries.extend(self._entries_from_catalog_payload(payload, default_type="lora"))
        except (requests.RequestException, ValueError) as exc:
            result["error"] = f"Could not fetch the configured local model catalog: {exc}"
            result["entries"] = []
            return result

        result["entries"] = self._dedupe_catalog_entries(entries)
        return result

    def _types_match(self, reference_type: str, catalog_type: str) -> bool:
        left = self.normalize_resource_type(reference_type)
        right = self.normalize_resource_type(catalog_type)
        if left == right:
            return True
        if {left, right} <= {"checkpoint", "model"}:
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
                if reference_hashes and entry_hashes and reference_hashes & entry_hashes:
                    basis = "hash"
                elif reference_version_id is not None and entry.get("civitai_model_version_id") == reference_version_id:
                    basis = "civitai_model_version_id"
                elif reference_model_id is not None and entry.get("civitai_model_id") == reference_model_id:
                    basis = "civitai_model_id"
                elif reference_name and reference_name == str(entry.get("normalized_name") or "").strip().lower():
                    basis = "normalized_name"
                if basis is None:
                    continue
                matches.append(
                    {
                        "display_name": entry.get("display_name") or entry.get("normalized_name"),
                        "resource_type": entry_type,
                        "match_basis": basis,
                        "source_identifier": entry.get("source_identifier"),
                        "hashes": entry.get("hashes") or [],
                    }
                )
            next_reference = dict(reference)
            next_reference["local_installed"] = bool(matches)
            next_reference["local_matches"] = matches[:5]
            matched_references.append(next_reference)
        return matched_references

    def build_item_payload(self, source_payload: dict[str, Any], *, local_catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        local_catalog = local_catalog or {"configured": False, "entries": [], "raw": {}, "error": None}
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
        local_catalog = local_catalog or {"configured": False, "entries": [], "raw": {}, "error": None}
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
                    "raw": self._dict_payload(local_catalog.get("raw")),
                },
            },
            "error": None,
        }