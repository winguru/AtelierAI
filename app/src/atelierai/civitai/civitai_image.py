#!/usr/bin/env python3
"""CivitaiImage class for consistent image data handling and URL construction."""

import json
from importlib import import_module
from typing import Any, Dict, List, Optional
from .console_utils import ConsoleFormatter


def _get_config_value(name: str) -> Optional[str]:
    """Load a config value from the runtime config module."""
    for module_name in ("atelierai.config", "config", "backend.config"):
        try:
            mod = import_module(module_name)
        except ModuleNotFoundError:
            continue
        value = getattr(mod, name, None)
        if value is not None:
            return value
    return None


_CIVITAI_CDN_BASE = (
    _get_config_value("CIVITAI_CDN_BASE_URL")
    or "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"
)
_CIVITAI_WEB_BASE = _get_config_value("CIVITAI_WEB_BASE_URL") or "https://civitai.red"


class CivitaiImage:
    """Represents a CivitAI image with consistent data access and URL construction."""

    def __init__(
        self,
        image_id: int,
        url_hash: Optional[str] = None,
        image_name: Optional[str] = None,
        mime_type: Optional[str] = "image/jpeg",
    ):
        """Initialize a CivitaiImage.

        Args:
            image_id: CivitAI image ID
            url_hash: Image URL hash (GUID)
            image_name: Image filename
            mime_type: Image MIME type for extension detection
        """
        self.image_id = image_id
        self.url_hash = url_hash
        self.image_name = image_name or f"image_{image_id}"
        self.mime_type = mime_type or "image/jpeg"

        # Basic info from image.get endpoint
        self.author = "Unknown"
        self.created_at = None
        self.nsfw = False
        self.nsfw_level: Optional[int] = None
        self.published_at = None
        self.post_id: Optional[int] = None
        self.url_hash_only = True  # If True, need to construct full URL

        # Generation data from getGenerationData endpoint
        self.model = "Unknown"
        self.model_version = "Unknown"
        self.base_model = "Unknown"
        self.sampler = "Unknown"
        self.steps = 0
        self.cfg_scale = 0.0
        self.seed = 0
        self.width = 0
        self.height = 0
        self.prompt = ""
        self.negative_prompt = ""
        self.process = "unknown"
        self.engine = "unknown"

        # Resources (LoRAs, models, embeddings)
        self.loras: List[Dict] = []
        self.models: List[Dict] = []
        self.embeddings: List[Dict] = []

        # Other metadata
        self.tags: List[str] = []
        self.clip_skip = None
        self.workflow = None
        self.draft = None

    @property
    def image_url(self) -> str:
        """Get the full image URL.

        Constructs URL from hash if needed, or returns existing URL.
        """
        if self.url_hash and self.url_hash_only:
            safe_name = self._get_safe_filename()
            transform_segment = "original=true"
            if str(self.mime_type or "").lower().startswith("video/"):
                transform_segment = "transcode=true,original=true"
            return (
                f"{_CIVITAI_CDN_BASE}/"
                f"{self.url_hash}/{transform_segment}/{safe_name}"
            )
        return self.url_hash or ""

    @property
    def display_url(self) -> str:
        """Get URL for display (shortened if too long)."""
        url = self.image_url
        return url[:80] + "..." if len(url) > 80 else url

    def _get_safe_filename(self) -> str:
        """Get a safe filename with correct extension based on MIME type."""
        # Get expected extension based on MIME type
        target_ext = self._get_extension_from_mime(self.mime_type)

        image_name = (
            self.image_name
            if isinstance(self.image_name, str) and self.image_name.strip()
            else None
        )
        if not image_name:
            return f"image_{self.image_id}{target_ext}"

        # Split current name into root and extension
        base_name, current_ext = (
            image_name.rsplit(".", 1) if "." in image_name else (image_name, "")
        )

        # Normalize current extension to lowercase for comparison
        if current_ext:
            current_ext = current_ext.lower()

        # Scenario 1: No extension exists (e.g. "image_name")
        if not current_ext:
            return f"{base_name}{target_ext}"

        # Scenario 2: Extension matches target (e.g. name="img.jpeg", mime="image/jpeg")
        if current_ext == target_ext:
            return image_name

        # Scenario 3: Extension mismatch (e.g. name="img.jpeg", mime="image/png")
        # We strip the wrong extension and add the correct one derived from MIME type
        return f"{base_name}{target_ext}"

    def _get_extension_from_mime(self, mime_type: Optional[str]) -> str:
        """Map CivitAI MIME types to file extensions."""
        if not mime_type:
            return ".jpeg"  # Default fallback

        mime_lower = mime_type.lower()

        if "png" in mime_lower:
            return ".png"
        elif "webp" in mime_lower:
            return ".webp"
        elif "tiff" in mime_lower or "tif" in mime_lower:
            return ".tif"  # CivitAI sometimes uses .jtif, .tif is standard
        elif "mp4" in mime_lower:
            return ".mp4"
        elif "jpeg" in mime_lower or "jpg" in mime_lower:
            return ".jpeg"
        else:
            # Fallback for unknown types
            return ".jpeg"

    def merge_basic_info(self, basic_data: Dict, api=None) -> None:
        """Merge basic image information from image.get endpoint.

        Args:
            basic_data: Response from image.get endpoint
            api: Optional CivitaiAPI instance for fetching tags
        """
        if not basic_data:
            return

        # Extract basic fields
        self.image_id = basic_data.get("id", self.image_id)
        self.url_hash = basic_data.get("url", self.url_hash)
        self.created_at = basic_data.get("createdAt")
        self.published_at = basic_data.get("publishedAt")

        # Post association
        raw_post_id = basic_data.get("postId")
        if raw_post_id is not None:
            try:
                self.post_id = int(raw_post_id)
            except (TypeError, ValueError):
                pass

        # Author information
        user = basic_data.get("user")
        if user and isinstance(user, dict):
            username = user.get("username")
            if isinstance(username, str) and username.strip():
                self.author = username.strip()
        else:
            username = basic_data.get("username")
            if isinstance(username, str) and username.strip():
                self.author = username.strip()

        # NSFW status
        raw_nsfw_level = basic_data.get("nsfwLevel")
        normalized_nsfw_level: Optional[int] = None
        if isinstance(raw_nsfw_level, bool):
            normalized_nsfw_level = int(raw_nsfw_level)
        elif isinstance(raw_nsfw_level, int):
            normalized_nsfw_level = raw_nsfw_level
        elif isinstance(raw_nsfw_level, str):
            cleaned_nsfw_level = raw_nsfw_level.strip()
            if cleaned_nsfw_level:
                try:
                    normalized_nsfw_level = int(float(cleaned_nsfw_level))
                except ValueError:
                    normalized_nsfw_level = None

        self.nsfw_level = normalized_nsfw_level
        if normalized_nsfw_level is not None:
            self.nsfw = normalized_nsfw_level > 0
        else:
            self.nsfw = bool(raw_nsfw_level)

        # Tags - fetch from API if provided
        if api:
            from .civitai_api import CivitaiAPI

            if not isinstance(api, CivitaiAPI):
                api = CivitaiAPI.get_instance()
            self.tags = api.fetch_image_tags(self.image_id)
            if self.tags:
                print(f"  [OK] Fetched {len(self.tags)} tags for image {self.image_id}")
            else:
                if (
                    hasattr(api, "is_rate_limited")
                    and callable(api.is_rate_limited)
                    and api.is_rate_limited()
                ):
                    remaining = 0.0
                    if hasattr(api, "rate_limit_remaining_seconds") and callable(
                        api.rate_limit_remaining_seconds
                    ):
                        remaining = float(api.rate_limit_remaining_seconds() or 0.0)
                    print(
                        f"  [INFO] Tag fetch deferred due to CivitAI rate limit backoff ({remaining:.1f}s remaining)"
                    )
                else:
                    print(f"  [WARN] No tags found for image {self.image_id}")

        # Check for tags directly in basic_data (fallback)
        elif "tags" in basic_data and isinstance(basic_data["tags"], list):
            self.tags = basic_data["tags"]

        # Fallback: no tags available
        else:
            self.tags = []

        # Image name and MIME type
        basic_name = basic_data.get("name")
        if isinstance(basic_name, str) and basic_name.strip():
            self.image_name = basic_name.strip()

        basic_mime_type = basic_data.get("mimeType")
        if isinstance(basic_mime_type, str) and basic_mime_type.strip():
            self.mime_type = basic_mime_type.strip()

    def merge_generation_data(self, generation_data: Dict) -> None:
        """Merge generation data from getGenerationData endpoint.

        Args:
            generation_data: Response from image.getGenerationData endpoint
        """
        if not generation_data:
            return

        meta = generation_data.get("meta")
        if not isinstance(meta, dict):
            meta = {}

        resources = generation_data.get("resources")
        if not isinstance(resources, list):
            resources = []

        def first_meta_value(*keys: str, default: Any = None) -> Any:
            for key in keys:
                value = meta.get(key)
                if value is None:
                    continue
                if isinstance(value, str):
                    cleaned = value.strip()
                    if not cleaned:
                        continue
                    return cleaned
                return value
            return default

        def to_int(value: Any, default: int = 0) -> int:
            if value is None:
                return default
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(float(value.strip()))
                except ValueError:
                    return default
            return default

        def to_float(value: Any, default: float = 0.0) -> float:
            if value is None:
                return default
            if isinstance(value, bool):
                return float(int(value))
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    return default
            return default

        # Extract generation parameters from meta
        self.base_model = first_meta_value("baseModel", "base_model", default="Unknown")
        self.sampler = first_meta_value(
            "sampler", "samplerName", "sampler_name", default="Unknown"
        )
        self.steps = to_int(
            first_meta_value("steps", "numSteps", "step_count", default=0), default=0
        )
        self.cfg_scale = to_float(
            first_meta_value(
                "cfgScale", "cfg_scale", "cfg", "guidanceScale", "guidance", default=0
            ),
            default=0,
        )
        self.seed = to_int(
            first_meta_value("seed", "seedValue", "seed_value", default=0), default=0
        )
        self.width = to_int(
            first_meta_value("width", "imageWidth", "image_width", default=0), default=0
        )
        self.height = to_int(
            first_meta_value("height", "imageHeight", "image_height", default=0),
            default=0,
        )
        self.prompt = str(
            first_meta_value(
                "prompt", "Prompt", "positivePrompt", "positive_prompt", default=""
            )
        )
        self.negative_prompt = str(
            first_meta_value(
                "negativePrompt", "negative_prompt", "negative", default=""
            )
        )
        self.process = str(
            first_meta_value("process", "generationProcess", default="unknown")
        )
        self.engine = str(
            first_meta_value("engine", "software", "tool", default="unknown")
        )
        self.clip_skip = first_meta_value("clipSkip", "clip_skip")
        self.workflow = first_meta_value("workflow", "Workflow")
        self.draft = first_meta_value("draft", "isDraft")

        # Process resources
        self._process_resources(resources)

    def _process_resources(self, resources: List[Dict]) -> None:
        """Process and categorize resources into models, LoRAs, and embeddings.

        Args:
            resources: List of resource dictionaries from API
        """
        self.loras = []
        self.models = []
        self.embeddings = []

        for resource in resources:
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("modelType", "").lower()
            if not resource_type:
                resource_type = resource.get("type", "").lower()

            model_name = resource.get("modelName", "Unknown")
            model_version_id = resource.get("modelVersionId")
            strength = resource.get("strength") or 1.0
            version_name = resource.get("versionName", "")
            base_model = resource.get("baseModel", "Unknown")

            # Categorize by type
            if resource_type == "lora":
                # Extract both Model Weight and CLIP Weight if available
                lora_data = {
                    "name": model_name,
                    "weight": strength,  # Model weight
                    "modelId": resource.get("id", "Unknown"),
                    "modelVersionId": model_version_id,
                    "versionName": version_name,
                    "baseModel": base_model,
                }

                # Check for CLIP weight in resource (optional parameter)
                clip_weight = resource.get("clipWeight")
                if clip_weight is not None:
                    lora_data["clip_weight"] = clip_weight

                self.loras.append(lora_data)
            elif resource_type == "checkpoint":
                self.model = model_name
                self.model_version = version_name or "Unknown"
                self.models.append(
                    {
                        "name": model_name,
                        "version": version_name,
                        "modelId": resource.get("id", "Unknown"),
                        "modelVersionId": model_version_id,
                        "baseModel": base_model,
                    }
                )
            elif (
                resource_type == "textualinversion" or "embedding" in model_name.lower()
            ):
                self.embeddings.append(
                    {
                        "name": model_name,
                        "weight": strength,
                        "modelId": resource.get("id", "Unknown"),
                        "modelVersionId": model_version_id,
                        "versionName": version_name,
                        "baseModel": base_model,
                    }
                )

        # If we have models, update main model from first checkpoint
        if not self.models and self.model == "Unknown":
            # Look for main model in resources list
            for res in resources:
                if not isinstance(res, dict):
                    continue
                if res.get("type") == "checkpoint":
                    self.model = res.get("modelName", "Unknown")
                    break

    def to_dict(self, include_full_url: bool = False) -> Dict:
        """Convert to dictionary for JSON export or API responses.

        Args:
            include_full_url: If True, includes full image URL (can be long).
                           If False, returns URL hash only.

        Returns:
            Dictionary representation of image data
        """
        data = {
            "image_id": self.image_id,
            "url": self.image_url if include_full_url else self.url_hash,
            "author": self.author,
            "created_at": self.created_at,
            "nsfw": self.nsfw,
            "nsfwLevel": self.nsfw_level,
            "nsfw_level": self.nsfw_level,
            "post_id": self.post_id,
            "model": self.model,
            "model_version": self.model_version,
            "base_model": self.base_model,
            "sampler": self.sampler,
            "steps": self.steps,
            "cfg_scale": self.cfg_scale,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "loras": self.loras,
            "models": self.models,
            "embeddings": self.embeddings,
            "tags": self.tags,
            "clip_skip": self.clip_skip,
            "workflow": self.workflow,
            "process": self.process,
            "engine": self.engine,
        }

        # Remove None values to clean up JSON
        return {k: v for k, v in data.items() if v is not None and v != "Unknown"}

    @staticmethod
    def _print_basic_info(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print basic image information."""
        fmt.print_subheader("Basic Information")
        fmt.print_blank()
        fmt.print_key_value("Image ID", image.image_id)
        fmt.print_key_value("URL", image.image_url)

        if image.author and image.author != "Unknown":
            author_url = f"{_CIVITAI_WEB_BASE}/user/{image.author}"
            fmt.print_key_value("Author URL", author_url)

        fmt.print_key_value("Author", image.author)
        fmt.print_key_value("NSFW", image.nsfw)
        fmt.print_key_value("Created At", image.created_at or "Unknown")
        fmt.print_blank()

    @staticmethod
    def _print_model_info(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print model generation information."""
        fmt.print_subheader("Model Information")
        fmt.print_blank()
        fmt.print_key_value("Model", image.model)

        if image.models:
            fmt.print_key_value("Model ID", image.models[0].get("modelId", "Unknown"))
        else:
            fmt.print_key_value("Model ID", "Unknown")

        fmt.print_key_value("Model Version", image.model_version)
        fmt.print_key_value("Base Model", image.base_model)
        fmt.print_key_value("Sampler", image.sampler)
        fmt.print_key_value("Steps", image.steps)
        fmt.print_key_value("CFG Scale", image.cfg_scale)
        fmt.print_key_value("Seed", image.seed)
        fmt.print_key_value("Size", f"{image.width}x{image.height}")
        fmt.print_blank()

    @staticmethod
    def _print_loras(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print LoRA information."""
        if not image.loras:
            return

        fmt.print_subheader(f"LoRAs ({len(image.loras)} used)")
        fmt.print_blank()

        headers = ["Name", "Model Weight"]
        rows = []
        for lora in image.loras:
            name = lora.get("name", "Unknown")
            weight_val = lora.get("weight", 0)
            weight = f"{weight_val:.2f}"
            rows.append([name, weight])

        fmt.print_table(headers, rows)
        fmt.print_blank()

    @staticmethod
    def _print_tags(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print tags information."""
        if not image.tags:
            return

        fmt.print_subheader(f"Tags ({len(image.tags)} found)")
        fmt.print_blank()
        tags_str = ", ".join(image.tags)
        fmt.print_wrapped_text(tags_str, indent=0)
        fmt.print_blank()

    @staticmethod
    def _print_prompts(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print positive and negative prompts."""
        if image.prompt:
            fmt.print_subheader("Positive Prompt")
            fmt.print_blank()
            fmt.print_wrapped_text(image.prompt, indent=0)
            fmt.print_blank()

        if image.negative_prompt:
            fmt.print_subheader("Negative Prompt")
            fmt.print_blank()
            fmt.print_wrapped_text(image.negative_prompt, indent=0)
            fmt.print_blank()

    @staticmethod
    def _print_additional_params(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print additional parameters."""
        fmt.print_subheader("Additional Parameters")
        fmt.print_blank()
        meta_params = {
            "clipSkip": image.clip_skip,
            "workflow": image.workflow,
            "draft": image.draft,
            "process": image.process,
            "engine": image.engine,
        }

        for key, value in meta_params.items():
            if value is not None:
                if isinstance(value, dict):
                    value_str = str(value)
                    display_val = (
                        value_str[:80] + "..." if len(value_str) > 80 else value_str
                    )
                    fmt.print_key_value(key.capitalize(), display_val)
                elif isinstance(value, list):
                    value_str = str(value)
                    display_val = (
                        value_str[:80] + "..." if len(value_str) > 80 else value_str
                    )
                    fmt.print_key_value(key.capitalize(), display_val)
                else:
                    fmt.print_key_value(
                        key.capitalize(), str(value) if value != "Unknown" else "N/A"
                    )
        fmt.print_blank()

    @staticmethod
    def _print_resource_list(
        key: str, value: List[Dict], fmt: ConsoleFormatter
    ) -> None:
        """Print resource lists (loras, embeddings, models)."""
        if not value:
            fmt.print_key_value(key, "[] (empty list)", indent=2)
        else:
            fmt.print_key_value(key, f"List with {len(value)} items:", indent=2)
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    fmt.print_key_value(f"  [{idx}]", "", indent=4)
                    for k, v in item.items():
                        if k == "name":
                            fmt.print_key_value(f"    {k}", v, indent=6)
                        elif k == "weight" or k == "clip_weight":
                            fmt.print_key_value(f"    {k}", f"{v:.2f}", indent=6)
                        else:
                            fmt.print_key_value(f"    {k}", v, indent=6)
                else:
                    item_str = str(item)
                    fmt.print_key_value(f"  [{idx}]", item_str, indent=4)

    @staticmethod
    def _print_generic_list(key: str, value: List, fmt: ConsoleFormatter) -> None:
        """Print generic list fields."""
        if not value:
            display_value = "[] (empty list)"
            fmt.print_key_value(key, display_value, indent=2)
        else:
            fmt.print_key_value(key, f"List with {len(value)} items:", indent=2)
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    item_str = str(item)[:80]
                else:
                    item_str = str(item)
                if len(item_str) > 80:
                    item_str = item_str[:80] + "..."
                fmt.print_key_value(f"  [{idx}]", item_str, indent=4)

    @staticmethod
    def _print_raw_data(image: "CivitaiImage", fmt: ConsoleFormatter) -> None:
        """Print raw scraped data."""
        fmt.print_subheader("Raw Scraped Data")
        fmt.print_blank()
        fmt.print_info("All available scraped data:")
        fmt.print_blank()

        data = image.to_dict(include_full_url=False)

        for key, value in data.items():
            if key in ["prompt", "negative_prompt"]:
                continue

            if key in ["loras", "embeddings", "models"]:
                CivitaiImage._print_resource_list(key, value, fmt)
                continue

            if isinstance(value, list):
                CivitaiImage._print_generic_list(key, value, fmt)
                continue

            if isinstance(value, str) and len(value) > 100:
                display_value = value[:100] + "..."
            elif isinstance(value, dict):
                display_value = f"<{type(value).__name__}>: {str(value)[:80]}..."
            else:
                display_value = str(value)

            if key not in ["loras", "embeddings"]:
                fmt.print_key_value(key, display_value, indent=2)

        fmt.print_blank()

    @staticmethod
    def print_details(
        image: "CivitaiImage", fmt: Optional[ConsoleFormatter] = None
    ) -> None:
        """Print comprehensive details for a CivitaiImage instance.

        Args:
            image: CivitaiImage instance to print
            fmt: ConsoleFormatter instance (creates new one if None)
        """
        if fmt is None:
            fmt = ConsoleFormatter()

        if not image:
            fmt.print_error("No image data available!")
            return

        fmt.print_header("CivitAI Image Analysis")
        fmt.print_blank()

        CivitaiImage._print_basic_info(image, fmt)
        CivitaiImage._print_model_info(image, fmt)
        CivitaiImage._print_loras(image, fmt)
        CivitaiImage._print_tags(image, fmt)
        CivitaiImage._print_prompts(image, fmt)
        CivitaiImage._print_additional_params(image, fmt)
        CivitaiImage._print_raw_data(image, fmt)

    def __str__(self) -> str:
        """String representation of image (for debugging)."""
        return (
            f"CivitaiImage(id={self.image_id}, "
            f"author={self.author}, "
            f"model={self.model}, "
            f"loras={len(self.loras)}, "
            f"size={self.width}x{self.height})"
        )

    @classmethod
    def from_collection_item(cls, item: Dict, generation_data: Dict) -> "CivitaiImage":
        """Create CivitaiImage from collection item and generation data.

        This matches the current collection scraper workflow.

        Args:
            item: Collection item from fetch_collection_items
            generation_data: Generation data from fetch_image_details

        Returns:
            CivitaiImage instance
        """
        item_user = item.get("user") if isinstance(item.get("user"), dict) else {}
        item_account = (
            item.get("account") if isinstance(item.get("account"), dict) else {}
        )

        image = cls(
            image_id=item.get("id", 0),
            url_hash=item.get("url"),
            image_name=item.get("name"),
            mime_type=item.get("mimeType", "image/jpeg"),
        )

        # Merge basic info from collection item
        image.author = (
            item.get("username")
            or (item_user.get("username") if item_user else None)
            or (item_account.get("username") if item_account else None)
            or "Unknown"
        )
        image.created_at = item.get("createdAt")
        raw_nsfw_level = item.get("nsfwLevel")
        if isinstance(raw_nsfw_level, bool):
            image.nsfw_level = int(raw_nsfw_level)
        elif isinstance(raw_nsfw_level, int):
            image.nsfw_level = raw_nsfw_level
        elif isinstance(raw_nsfw_level, str):
            cleaned_nsfw_level = raw_nsfw_level.strip()
            if cleaned_nsfw_level:
                try:
                    image.nsfw_level = int(float(cleaned_nsfw_level))
                except ValueError:
                    image.nsfw_level = None
        image.nsfw = (
            image.nsfw_level > 0
            if image.nsfw_level is not None
            else bool(raw_nsfw_level)
        )

        # Merge generation data
        image.merge_generation_data(generation_data)

        return image

    @classmethod
    def from_single_image(
        cls, basic_info: Dict, generation_data: Dict, api=None
    ) -> "CivitaiImage":
        """Create CivitaiImage from separate API calls.

        For single image analysis where we call image.get and getGenerationData.

        Args:
            basic_info: Response from image.get endpoint
            generation_data: Response from image.getGenerationData endpoint
            api: Optional CivitaiAPI instance for fetching tags

        Returns:
            CivitaiImage instance
        """
        basic_info = basic_info if isinstance(basic_info, dict) else {}
        generation_data = generation_data if isinstance(generation_data, dict) else {}

        image_id = (
            (basic_info.get("id") if basic_info else None)
            or (generation_data.get("id") if generation_data else None)
            or 0
        )

        image = cls(
            image_id=image_id,
            url_hash=basic_info.get("url") if basic_info else None,
            image_name=basic_info.get("name") if basic_info else None,
            mime_type=basic_info.get("mimeType") if basic_info else "image/jpeg",
        )

        # Merge both data sources (pass API for tag fetching)
        if basic_info:
            image.merge_basic_info(basic_info, api=api)
        if generation_data:
            image.merge_generation_data(generation_data)

        return image


# ===== Utility Functions =====


def merge_image_data(
    collection_item: Optional[Dict],
    generation_data: Dict,
    single_image_mode: bool = False,
) -> CivitaiImage:
    """Helper function to merge image data from different sources.

    Args:
        collection_item: Collection item data (from scrape workflow)
        generation_data: Generation data from API
        single_image_mode: If True, expects separate basic_info and generation_data

    Returns:
        CivitaiImage instance with merged data
    """
    if single_image_mode:
        # For single image, we expect generation_data to have both sources
        return CivitaiImage.from_single_image(collection_item or {}, generation_data)
    else:
        # For collection workflow, we have separate sources
        return CivitaiImage.from_collection_item(collection_item or {}, generation_data)


def format_loras(loras: List[Dict]) -> str:
    """Format LoRA list for display.

    Args:
        loras: List of LoRA dictionaries

    Returns:
        Formatted string
    """
    if not loras:
        return "No LoRAs used"

    return ", ".join([f"{lora['name']} (w:{lora['weight']:.2f})" for lora in loras])


if __name__ == "__main__":
    # Example usage
    print("CivitaiImage class demonstration\n")

    # Create from single image mode
    basic_info = {
        "id": 12345,
        "url": "abc123hash",
        "name": "test_image",
        "mimeType": "image/png",
        "user": {"username": "TestUser"},
        "createdAt": "2026-01-15T12:00:00Z",
        "nsfwLevel": 0,
    }

    generation_data = {
        "meta": {
            "baseModel": "SDXL",
            "prompt": "test prompt",
            "steps": 30,
            "sampler": "DPM++ 2M Karras",
        },
        "resources": [{"modelType": "lora", "modelName": "Test LoRA", "strength": 0.8}],
    }

    image = CivitaiImage.from_single_image(basic_info, generation_data)

    print(f"Image: {image}")
    print(f"URL: {image.display_url}")
    print(f"LoRAs: {format_loras(image.loras)}")
    print(f"Dictionary: {json.dumps(image.to_dict(), indent=2)}")
