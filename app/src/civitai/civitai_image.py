#!/usr/bin/env python3
"""CivitaiImage class for consistent image data handling and URL construction."""

import json
from typing import Dict, List, Optional
from .console_utils import ConsoleFormatter


class CivitaiImage:
    """Represents a Civitai image with consistent data access and URL construction."""

    def __init__(
        self,
        image_id: int,
        url_hash: Optional[str] = None,
        image_name: Optional[str] = None,
        mime_type: Optional[str] = "image/jpeg"
    ):
        """Initialize a CivitaiImage.

        Args:
            image_id: Civitai image ID
            url_hash: Image URL hash (GUID)
            image_name: Image filename
            mime_type: Image MIME type for extension detection
        """
        self.image_id = image_id
        self.url_hash = url_hash
        self.image_name = image_name or "unknown"
        self.mime_type = mime_type or "image/jpeg"

        # Basic info from image.get endpoint
        self.author = "Unknown"
        self.created_at = None
        self.nsfw = False
        self.published_at = None
        self.url_hash_only = True  # If True, need to construct full URL

        # Generation data from getGenerationData endpoint
        self.model = "Unknown"
        self.model_version = "Unknown"
        self.base_model = "Unknown"
        self.sampler = "Unknown"
        self.steps = 0
        self.cfg_scale = 0
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
            return (
                f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/"
                f"{self.url_hash}/original=true/quality=90/{safe_name}"
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

        # Split current name into root and extension
        base_name, current_ext = self.image_name.rsplit(".", 1) if "." in self.image_name else (self.image_name, "")

        # Normalize current extension to lowercase for comparison
        if current_ext:
            current_ext = current_ext.lower()

        # Scenario 1: No extension exists (e.g. "image_name")
        if not current_ext:
            return f"{base_name}{target_ext}"

        # Scenario 2: Extension matches target (e.g. name="img.jpeg", mime="image/jpeg")
        if current_ext == target_ext:
            return self.image_name

        # Scenario 3: Extension mismatch (e.g. name="img.jpeg", mime="image/png")
        # We strip the wrong extension and add the correct one derived from MIME type
        return f"{base_name}{target_ext}"

    def _get_extension_from_mime(self, mime_type: Optional[str]) -> str:
        """Map Civitai MIME types to file extensions."""
        if not mime_type:
            return ".jpeg"  # Default fallback

        mime_lower = mime_type.lower()

        if "png" in mime_lower:
            return ".png"
        elif "webp" in mime_lower:
            return ".webp"
        elif "tiff" in mime_lower or "tif" in mime_lower:
            return ".tif"  # Civitai sometimes uses .jtif, .tif is standard
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

        # Author information
        user = basic_data.get("user")
        if user and isinstance(user, dict):
            self.author = user.get("username", "Unknown")
        elif basic_data.get("username"):
            self.author = basic_data.get("username")

        # NSFW status
        nsfw_level = basic_data.get("nsfwLevel", 0)
        self.nsfw = bool(nsfw_level > 0) if isinstance(nsfw_level, int) else bool(nsfw_level)

        # Tags - fetch from API if provided
        if api:
            from .civitai_api import CivitaiAPI
            if not isinstance(api, CivitaiAPI):
                api = CivitaiAPI.get_instance()
            self.tags = api.fetch_image_tags(self.image_id)
            if self.tags:
                print(f"  [OK] Fetched {len(self.tags)} tags for image")
            else:
                print("  [WARN] No tags found for this image")

        # Check for tags directly in basic_data (fallback)
        elif "tags" in basic_data and isinstance(basic_data["tags"], list):
            self.tags = basic_data["tags"]

        # Fallback: no tags available
        else:
            self.tags = []

        # Image name and MIME type
        self.image_name = basic_data.get("name", self.image_name)
        self.mime_type = basic_data.get("mimeType", self.mime_type)

    def merge_generation_data(self, generation_data: Dict) -> None:
        """Merge generation data from getGenerationData endpoint.

        Args:
            generation_data: Response from image.getGenerationData endpoint
        """
        if not generation_data:
            return

        meta = generation_data.get("meta", {})
        resources = generation_data.get("resources", [])

        # Extract generation parameters from meta
        self.base_model = meta.get("baseModel", "Unknown")
        self.sampler = meta.get("sampler", "Unknown")
        self.steps = meta.get("steps", 0)
        self.cfg_scale = meta.get("cfgScale", 0)
        self.seed = meta.get("seed", 0)
        self.width = meta.get("width", 0)
        self.height = meta.get("height", 0)
        self.prompt = meta.get("prompt", "")
        self.negative_prompt = meta.get("negativePrompt", "")
        self.process = meta.get("process", "unknown")
        self.engine = meta.get("engine", "unknown")
        self.clip_skip = meta.get("clipSkip")
        self.workflow = meta.get("workflow")
        self.draft = meta.get("draft")

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
                    "baseModel": base_model
                }

                # Check for CLIP weight in resource (optional parameter)
                clip_weight = resource.get("clipWeight")
                if clip_weight is not None:
                    lora_data["clip_weight"] = clip_weight

                self.loras.append(lora_data)
            elif resource_type == "checkpoint":
                self.model = model_name
                self.model_version = version_name or "Unknown"
                self.models.append({
                    "name": model_name,
                    "version": version_name,
                    "modelId": resource.get("id", "Unknown"),
                    "modelVersionId": model_version_id,
                    "baseModel": base_model
                })
            elif resource_type == "textualinversion" or "embedding" in model_name.lower():
                self.embeddings.append({
                    "name": model_name,
                    "weight": strength,
                    "modelId": resource.get("id", "Unknown"),
                    "modelVersionId": model_version_id,
                    "versionName": version_name,
                    "baseModel": base_model
                })

        # If we have models, update main model from first checkpoint
        if not self.models and self.model == "Unknown":
            # Look for main model in resources list
            for res in resources:
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
    def _print_basic_info(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
        """Print basic image information."""
        fmt.print_subheader("Basic Information")
        fmt.print_blank()
        fmt.print_key_value("Image ID", image.image_id)
        fmt.print_key_value("URL", image.image_url)

        if image.author and image.author != "Unknown":
            author_url = f"https://civitai.com/user/{image.author}"
            fmt.print_key_value("Author URL", author_url)

        fmt.print_key_value("Author", image.author)
        fmt.print_key_value("NSFW", image.nsfw)
        fmt.print_key_value("Created At", image.created_at or "Unknown")
        fmt.print_blank()

    @staticmethod
    def _print_model_info(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
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
    def _print_loras(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
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
    def _print_tags(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
        """Print tags information."""
        if not image.tags:
            return

        fmt.print_subheader(f"Tags ({len(image.tags)} found)")
        fmt.print_blank()
        tags_str = ", ".join(image.tags)
        fmt.print_wrapped_text(tags_str, indent=0)
        fmt.print_blank()

    @staticmethod
    def _print_prompts(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
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
    def _print_additional_params(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
        """Print additional parameters."""
        fmt.print_subheader("Additional Parameters")
        fmt.print_blank()
        meta_params = {
            "clipSkip": image.clip_skip,
            "workflow": image.workflow,
            "draft": image.draft,
            "process": image.process,
            "engine": image.engine
        }

        for key, value in meta_params.items():
            if value is not None:
                if isinstance(value, dict):
                    value_str = str(value)
                    display_val = value_str[:80] + "..." if len(value_str) > 80 else value_str
                    fmt.print_key_value(key.capitalize(), display_val)
                elif isinstance(value, list):
                    value_str = str(value)
                    display_val = value_str[:80] + "..." if len(value_str) > 80 else value_str
                    fmt.print_key_value(key.capitalize(), display_val)
                else:
                    fmt.print_key_value(key.capitalize(), str(value) if value != "Unknown" else "N/A")
        fmt.print_blank()

    @staticmethod
    def _print_resource_list(key: str, value: List[Dict], fmt: ConsoleFormatter) -> None:
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
    def _print_raw_data(image: 'CivitaiImage', fmt: ConsoleFormatter) -> None:
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
    def print_details(image: 'CivitaiImage', fmt: Optional[ConsoleFormatter] = None) -> None:
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

        fmt.print_header("Civitai Image Analysis")
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
    def from_collection_item(cls, item: Dict, generation_data: Dict) -> 'CivitaiImage':
        """Create CivitaiImage from collection item and generation data.

        This matches the current collection scraper workflow.

        Args:
            item: Collection item from fetch_collection_items
            generation_data: Generation data from fetch_image_details

        Returns:
            CivitaiImage instance
        """
        image = cls(
            image_id=item.get("id", 0),
            url_hash=item.get("url"),
            image_name=item.get("name"),
            mime_type=item.get("mimeType", "image/jpeg")
        )

        # Merge basic info from collection item
        image.author = (
            item.get("username")
            or item.get("user", {}).get("username")
            or item.get("account", {}).get("username")
            or "Unknown"
        )
        image.created_at = item.get("createdAt")
        image.nsfw = bool(item.get("nsfwLevel", 0) > 0)

        # Merge generation data
        image.merge_generation_data(generation_data)

        return image

    @classmethod
    def from_single_image(cls, basic_info: Dict, generation_data: Dict, api=None) -> 'CivitaiImage':
        """Create CivitaiImage from separate API calls.

        For single image analysis where we call image.get and getGenerationData.

        Args:
            basic_info: Response from image.get endpoint
            generation_data: Response from image.getGenerationData endpoint
            api: Optional CivitaiAPI instance for fetching tags

        Returns:
            CivitaiImage instance
        """
        image_id = (basic_info.get("id") if basic_info else None) or (generation_data.get("id") if generation_data else None) or 0

        image = cls(
            image_id=image_id,
            url_hash=basic_info.get("url") if basic_info else None,
            image_name=basic_info.get("name") if basic_info else None,
            mime_type=basic_info.get("mimeType") if basic_info else "image/jpeg"
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
    single_image_mode: bool = False
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

    return ", ".join([
        f"{lora['name']} (w:{lora['weight']:.2f})"
        for lora in loras
    ])


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
        "nsfwLevel": 0
    }

    generation_data = {
        "meta": {
            "baseModel": "SDXL",
            "prompt": "test prompt",
            "steps": 30,
            "sampler": "DPM++ 2M Karras"
        },
        "resources": [
            {"modelType": "lora", "modelName": "Test LoRA", "strength": 0.8}
        ]
    }

    image = CivitaiImage.from_single_image(basic_info, generation_data)

    print(f"Image: {image}")
    print(f"URL: {image.display_url}")
    print(f"LoRAs: {format_loras(image.loras)}")
    print(f"Dictionary: {json.dumps(image.to_dict(), indent=2)}")
