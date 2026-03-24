#!/usr/bin/env python3
"""Analyze a collection to find common tags, prompts, and elements."""

import re
import json
import argparse

from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from atelierai.civitai import CivitaiPrivateScraper
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.console_utils import ConsoleFormatter
from atelierai.utils.prompt_phrases import (
    PHRASE_STOP_WORDS,
    detect_prompt_style as shared_detect_prompt_style,
    extract_concepts as shared_extract_concepts,
    extract_nlp_concepts as shared_extract_nlp_concepts,
    extract_nlp_style_phrases as shared_extract_nlp_style_phrases,
    extract_phrases as shared_extract_phrases,
    extract_tag_style_concepts as shared_extract_tag_style_concepts,
    extract_tag_style_phrases as shared_extract_tag_style_phrases,
    normalize_phrase_breaks as shared_normalize_phrase_breaks,
)


# ==========================================
# Collection Analyzer Class
# ==========================================


class CollectionAnalyzer:
    """Analyze scraped collection data to find common patterns."""
    def __init__(self, scraped_data: List[Dict]):
        """Initialize analyzer with scraped data."""
        self.data = scraped_data
        self.stats = defaultdict(int)
        self.models = Counter()
        self.model_versions = Counter()
        self.samplers = Counter()
        self.steps = Counter()
        self.cfgs = Counter()
        self.loras = Counter()
        self.lora_weights = defaultdict(list)
        self.lora_model_ids = {}  # Track model_id and version_id for LoRAs
        self.tags = Counter()  # Track tags
        self.positive_concepts = Counter()
        self.negative_concepts = Counter()
        self.positive_phrases = Counter()
        self.negative_phrases = Counter()
        self.authors = Counter()
        self.deleted_models = []  # Track deleted/unavailable models

    def clean_prompt(self, prompt: str) -> str:
        """Clean and normalize a prompt string."""
        if not prompt:
            return ""
        prompt = re.sub(r"\s+", " ", prompt)
        prompt = prompt.strip()
        return prompt

    def detect_prompt_style(self, prompt: str) -> str:
        """Detect if prompt is tag-style or NLP-style."""
        return shared_detect_prompt_style(prompt)

    def extract_tag_style_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from tag-style prompts."""
        return shared_extract_tag_style_concepts(prompt)

    def extract_nlp_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from natural language prompts."""
        return shared_extract_nlp_concepts(prompt)

    def extract_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from a prompt, detecting style automatically."""
        return shared_extract_concepts(prompt)

    def _get_phrase_stop_words(self) -> set:
        """Get the set of stop words for phrase extraction."""
        return set(PHRASE_STOP_WORDS)

    def _normalize_phrase_breaks(self, prompt: str) -> str:
        """Treat prompt newlines and explicit break markers as separators."""
        return shared_normalize_phrase_breaks(prompt)

    def _extract_tag_style_phrases(self, prompt: str) -> List[str]:
        """Extract phrases from tag-style prompts."""
        return shared_extract_tag_style_phrases(prompt)

    def _extract_nlp_style_phrases(
        self, prompt: str, min_words: int, max_words: int
    ) -> List[str]:
        """Extract phrases from natural language prompts."""
        return shared_extract_nlp_style_phrases(prompt, min_words, max_words)

    def extract_phrases(
        self, prompt: str, min_words: int = 2, max_words: int = 4
    ) -> List[str]:
        """Extract common phrases from a prompt."""
        return shared_extract_phrases(prompt, min_words=min_words, max_words=max_words)

    def analyze(self) -> None:
        """Analyze all scraped data and compile statistics."""
        for item in self.data:
            model = item.get("model", "Unknown")
            model_version = item.get("model_version", "Unknown")
            sampler = item.get("sampler", "Unknown")
            steps = item.get("steps", 0)
            cfg = item.get("cfg_scale", 0)
            author = item.get("author", "Unknown")

            self.models[model] += 1
            if model_version and model_version != "Unknown":
                self.model_versions[f"{model} - {model_version}"] += 1
            self.samplers[sampler] += 1
            if steps and steps != "Unknown":
                self.steps[str(steps)] += 1
            if cfg and cfg != "Unknown":
                self.cfgs[str(cfg)] += 1
            self.authors[author] += 1

            loras = item.get("loras", [])
            for lora in loras:
                lora_name = lora.get("name", "Unknown")
                lora_weight = lora.get("weight", 0)
                lora_model_id = lora.get("model_id")
                lora_version_id = lora.get("model_version_id")
                self.loras[lora_name] += 1
                self.lora_weights[lora_name].append(lora_weight)
                # Store model_id and version_id (first occurrence wins)
                if lora_name not in self.lora_model_ids:
                    self.lora_model_ids[lora_name] = {
                        "model_id": lora_model_id,
                        "model_version_id": lora_version_id,
                    }

            # Count tags from image
            tags = item.get("tags", [])
            for tag in tags:
                self.tags[tag] += 1

            positive = item.get("prompt", "")
            negative = item.get("negative_prompt", "")

            pos_concepts = self.extract_concepts(positive)
            neg_concepts = self.extract_concepts(negative)
            self.positive_concepts.update(pos_concepts)
            self.negative_concepts.update(neg_concepts)

            pos_phrases = self.extract_phrases(positive, min_words=2, max_words=4)
            neg_phrases = self.extract_phrases(negative, min_words=2, max_words=4)
            self.positive_phrases.update(pos_phrases)
            self.negative_phrases.update(neg_phrases)

    def get_top_items(self, counter: Counter, n: int = 10) -> List[Tuple[str, int]]:
        """Get top N items from a counter."""
        return counter.most_common(n)

    def get_average_weights(self) -> Dict[str, float]:
        """Calculate average weight for each LoRA."""
        avg_weights = {}
        for lora_name, weights in self.lora_weights.items():
            if weights:
                avg_weights[lora_name] = sum(weights) / len(weights)
        return avg_weights

    def check_lora_availability(self, api: CivitaiAPI) -> List[Dict]:
        """Check if LoRA models are available on CivitAI or have been deleted.

        Args:
            api: CivitaiAPI instance for checking model availability

        Returns:
            List of deleted/unavailable models with their details
        """
        deleted_models = []
        seen_models = set()  # Track (model_id, model_version_id) pairs to avoid duplicates

        for lora_name, model_info in self.lora_model_ids.items():
            model_id = model_info.get("model_id")
            model_version_id = model_info.get("model_version_id")

            if not model_id:
                continue

            # Create a unique key to avoid duplicate checks
            model_key = (model_id, model_version_id)
            if model_key in seen_models:
                continue
            seen_models.add(model_key)

            # Check model availability
            availability = api.check_model_availability(model_id, model_version_id)

            if not availability["available"]:
                deleted_models.append({
                    "name": lora_name,
                    "model_id": model_id,
                    "model_version_id": model_version_id,
                    "civitai_url": availability["civitai_url"],
                    "archive_url": availability["archive_url"],
                    "error": availability.get("error"),
                    "usage_count": self.loras[lora_name]
                })

        self.deleted_models = deleted_models
        return deleted_models


def _print_models_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, total_items: int
) -> None:
    """Print top models section."""
    fmt.print_subheader("Top Models")
    fmt.print_blank()
    top_models = analyzer.get_top_items(analyzer.models, 10)
    headers = ["Model", "Count", "Percentage"]
    rows = []
    for model, count in top_models:
        percentage = (count / total_items) * 100 if total_items > 0 else 0
        rows.append([model, str(count), f"{percentage:.1f}%"])
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_versions_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter
) -> None:
    """Print top model versions section."""
    if not analyzer.model_versions:
        return
    fmt.print_subheader("Top Model Versions")
    fmt.print_blank()
    top_versions = analyzer.get_top_items(analyzer.model_versions, 8)
    headers = ["Version", "Count"]
    rows = [[ver, str(count)] for ver, count in top_versions]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_samplers_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, total_items: int
) -> None:
    """Print sampler distribution section."""
    fmt.print_subheader("Sampler Distribution")
    fmt.print_blank()
    top_samplers = analyzer.get_top_items(analyzer.samplers, 10)
    headers = ["Sampler", "Count", "Percentage"]
    rows = []
    for sampler, count in top_samplers:
        percentage = (count / total_items) * 100 if total_items > 0 else 0
        rows.append(
            [
                sampler if sampler != "Unknown" else "N/A",
                str(count),
                f"{percentage:.1f}%",
            ]
        )
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_steps_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print steps distribution section."""
    if not analyzer.steps:
        return
    fmt.print_subheader("Steps Distribution")
    fmt.print_blank()
    top_steps = analyzer.get_top_items(analyzer.steps, 8)
    headers = ["Steps", "Count"]
    rows = [[steps, str(count)] for steps, count in top_steps]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_cfg_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print CFG scale distribution section."""
    if not analyzer.cfgs:
        return
    fmt.print_subheader("CFG Scale Distribution")
    fmt.print_blank()
    top_cfgs = analyzer.get_top_items(analyzer.cfgs, 8)
    headers = ["CFG", "Count"]
    rows = [[cfg, str(count)] for cfg, count in top_cfgs]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_loras_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, line_length: int
) -> None:
    """Print top LoRAs section."""
    if not analyzer.loras:
        return
    fmt.print_subheader("Top LoRAs")
    fmt.print_blank()
    top_loras = analyzer.get_top_items(analyzer.loras, 15)
    avg_weights = analyzer.get_average_weights()
    show_extended = line_length >= 120

    headers = ["LoRA Name", "Usage", "Avg Weight"]
    if show_extended:
        headers.extend(["Model ID", "URL"])

    rows = []
    for lora, count in top_loras:
        avg_weight = avg_weights.get(lora, 0)
        row_data = [lora, str(count), f"{avg_weight:.2f}"]

        if show_extended:
            lora_info = analyzer.lora_model_ids.get(lora, {})
            model_id = lora_info.get("model_id")
            version_id = lora_info.get("model_version_id")
            url = ""
            if model_id and version_id:
                url = f"civitai.com/models/{model_id}?modelVersionId={version_id}"
            row_data.extend([str(version_id) if version_id else "N/A", url])

        rows.append(row_data)

    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_deleted_models_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter
) -> None:
    """Print deleted/unavailable models section with archive links."""
    if not analyzer.deleted_models:
        return

    fmt.print_subheader("Deleted/Unavailable Models")
    fmt.print_blank()
    fmt.print_warning(f"Found {len(analyzer.deleted_models)} model(s) that have been removed from Civitai:")
    fmt.print_blank()

    for model in analyzer.deleted_models:
        status_icon = "🗑️"
        status_text = "Deleted"
        if model.get('model_status'):
            status_text = f"Status: {model['model_status']}"

        fmt.print_info(f"{status_icon} {model['name']}", indent=2)
        fmt.print_key_value(status_text, model.get('model_status', 'Unknown'), indent=4)
        fmt.print_key_value("Model ID", model['model_id'], indent=4)
        if model['model_version_id']:
            fmt.print_key_value("Version ID", model['model_version_id'], indent=4)
        fmt.print_key_value("Usage Count", model['usage_count'], indent=4)
        if model.get('error'):
            fmt.print_key_value("Error", model['error'], indent=4)
        fmt.print_info(f"CivitAI URL: {model['civitai_url']}", indent=4)
        fmt.print_info(f"📦 Archive URL: {model['archive_url']}", indent=4)
        fmt.print_blank()

    fmt.print_info("💡 Tip: The archive site (civitaiarchive.com) may have backups of deleted models.")
    fmt.print_blank()


def _print_tags_section(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, total_items: int
) -> None:
    """Print top tags section."""
    if not analyzer.tags:
        return
    fmt.print_subheader("Top Tags")
    fmt.print_blank()
    top_tags = analyzer.get_top_items(analyzer.tags, 30)
    headers = ["Tag", "Occurrences", "Percentage"]
    rows = []
    for tag, count in top_tags:
        percentage = (count / total_items) * 100 if total_items > 0 else 0
        display_tag = tag[:40] + "..." if len(tag) > 40 else tag
        rows.append([display_tag, str(count), f"{percentage:.1f}%"])
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_authors_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print authors section."""
    if len(analyzer.authors) <= 1:
        return
    fmt.print_subheader("Authors")
    fmt.print_blank()
    top_authors = analyzer.get_top_items(analyzer.authors, 10)
    headers = ["Author", "Images"]
    rows = [[author, str(count)] for author, count in top_authors]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_concepts_section(
    analyzer: CollectionAnalyzer,
    fmt: ConsoleFormatter,
    total_items: int,
    concept_type: str,
) -> None:
    """Print concepts section (positive or negative)."""
    if concept_type == "positive":
        if not analyzer.positive_concepts:
            return
        concepts = analyzer.positive_concepts
        title = "Most Common Positive Concepts"
        count = 30
    else:
        if not analyzer.negative_concepts:
            return
        concepts = analyzer.negative_concepts
        title = "Most Common Negative Concepts"
        count = 20

    fmt.print_subheader(title)
    fmt.print_blank()
    top_concepts = analyzer.get_top_items(concepts, count)

    if concept_type == "positive":
        headers = ["Concept", "Occurrences", "Percentage"]
        rows = []
        for concept, cnt in top_concepts:
            percentage = (cnt / total_items) * 100 if total_items > 0 else 0
            display_concept = concept[:40] + "..." if len(concept) > 40 else concept
            rows.append([display_concept, str(cnt), f"{percentage:.1f}%"])
    else:
        headers = ["Concept", "Count"]
        rows = [[concept, str(cnt)] for concept, cnt in top_concepts]

    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_phrases_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print positive phrases section."""
    if not analyzer.positive_phrases:
        return
    fmt.print_subheader("Most Common Positive Phrase Sequences")
    fmt.print_blank()
    top_phrases = analyzer.get_top_items(analyzer.positive_phrases, 20)
    headers = ["Phrase", "Occurrences"]
    rows = [[phrase, str(count)] for phrase, count in top_phrases]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_sample_prompts(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, line_length: int
) -> None:
    """Print sample prompts section."""
    fmt.print_subheader("Sample Prompts")
    fmt.print_blank()
    sorted_by_loras = sorted(
        analyzer.data, key=lambda x: len(x.get("loras", [])), reverse=True
    )
    top_lora_prompts = sorted_by_loras[:5]

    fmt.print_info("Prompts with most LoRAs:")
    for i, item in enumerate(top_lora_prompts, 1):
        fmt.print_blank()
        fmt.print_info(f"[{i}] Image ID: {item['image_id']} → https://civitai.com/images/{item['image_id']}")
        fmt.print_key_value("Model", item["model"], indent=4)
        fmt.print_key_value("LoRAs", len(item.get("loras", [])), indent=4)
        for lora in item.get("loras", []):
            lora_name = lora.get("name")
            lora_weight = lora.get("weight")
            lora_model_id = lora.get("model_id")
            lora_version_id = lora.get("model_version_id")

            info_str = f"  {fmt.char('bullet')} {lora_name} (weight: {lora_weight})"
            if lora_model_id and lora_version_id and line_length >= 120:
                info_str += f" → https://civitai.com/models/{lora_model_id}?modelVersionId={lora_version_id}"

            fmt.print_info(info_str, indent=6)
        prompt = item.get("prompt", "")
        if prompt:
            # fmt.print_key_value("Prompt", f"{prompt[:200]}...", indent=4)  # Historical --- IGNORE ---
            fmt.print_wrapped_text("Prompt", prompt)
        fmt.print_blank()


def print_analysis_report(
    analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, collection_id: int
) -> None:
    """Print a comprehensive analysis report."""
    total_items = len(analyzer.data)
    line_length = fmt.line_length

    fmt.print_header(f"Collection Analysis: {collection_id}")
    fmt.print_blank()

    fmt.print_subheader("Overview")
    fmt.print_blank()
    fmt.print_key_value("Total Images", total_items)
    fmt.print_key_value("Unique Models", len(analyzer.models))
    fmt.print_key_value("Unique Samplers", len(analyzer.samplers))
    fmt.print_key_value("Total LoRAs Used", sum(analyzer.loras.values()))
    fmt.print_blank()

    _print_models_section(analyzer, fmt, total_items)
    _print_versions_section(analyzer, fmt)
    _print_samplers_section(analyzer, fmt, total_items)
    _print_steps_section(analyzer, fmt)
    _print_cfg_section(analyzer, fmt)
    _print_loras_section(analyzer, fmt, line_length)
    _print_deleted_models_section(analyzer, fmt)  # New: Print deleted models
    _print_tags_section(analyzer, fmt, total_items)
    _print_authors_section(analyzer, fmt)
    _print_concepts_section(analyzer, fmt, total_items, "positive")
    _print_phrases_section(analyzer, fmt)
    _print_concepts_section(analyzer, fmt, total_items, "negative")
    _print_sample_prompts(analyzer, fmt, line_length)

    fmt.print_header("Analysis Complete")
    fmt.print_blank()


# ==========================================
# Main Entry Point
# ==========================================


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze a CivitAI collection to find common tags and patterns"
    )
    parser.add_argument(
        "collection_id", type=int, help="CivitAI collection ID to analyze"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of images to analyze (default: 50, -1 for all)",
    )
    parser.add_argument(
        "--save", action="store_true", help="Save analysis results to JSON file"
    )
    parser.add_argument(
        "--line-length", type=int, help="Console line width (default: 70)"
    )
    parser.add_argument(
        "--wide",
        "-w",
        action="store_true",
        help="Use wide output (120 line width) for LoRA Model ID and URL columns",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode to print request details and validate session token",
    )

    args = parser.parse_args()

    # Apply wide mode if requested
    if args.wide:
        args.line_length = 120

    # Initialize formatter
    fmt = ConsoleFormatter(line_length=args.line_length)

    fmt.print_header("CivitAI Collection Analyzer")
    fmt.print_blank()

    # Scrape the collection - Now uses CivitaiPrivateScraper.scrape() directly
    # Note: CivitaiPrivateScraper now includes limit support and uses CivitaiAPI for tag fetching
    fmt.print_info(f"Scraping collection {args.collection_id}...")
    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # Apply limit
    limit = args.limit if args.limit > 0 else None
    limit_msg = f"first {args.limit} images" if args.limit > 0 else "all images"

    fmt.print_info(f"Fetching {limit_msg}...")

    data = scraper.scrape(args.collection_id, limit=limit, debug=args.debug)

    if not data:
        fmt.print_error("No data found! Check collection ID and authentication.")
        return

    scraped_count = len(data)

    fmt.print_success(f"Successfully scraped {scraped_count} images!")

    # Check if there might be more images
    if args.limit > 0 and scraped_count == args.limit:
        fmt.print_blank()
        fmt.print_info(f"ℹ️  Fetched {args.limit} images (limit reached).")
        fmt.print_info("   Use '--limit -1' to fetch all images from this collection.")
    elif args.limit > 0 and scraped_count < args.limit:
        fmt.print_blank()
        fmt.print_info(
            f"ℹ️  Collection has {scraped_count} images (less than limit {args.limit})."
        )
        fmt.print_info("   This is all available images in the collection.")
    else:
        fmt.print_blank()
        fmt.print_info(f"ℹ️  Fetched all {scraped_count} images from collection.")

    fmt.print_blank()

    # Analyze
    fmt.print_info("Analyzing collection data...")
    analyzer = CollectionAnalyzer(data)
    analyzer.analyze()
    fmt.print_blank()

    # Check for deleted/unavailable models
    fmt.print_info("Checking model availability...")
    deleted_models = analyzer.check_lora_availability(scraper.api)
    if deleted_models:
        fmt.print_warning(f"Found {len(deleted_models)} deleted/unavailable model(s)!")
    else:
        fmt.print_success("All LoRA models are available on Civitai.")
    fmt.print_blank()

    # Print report
    print_analysis_report(analyzer, fmt, args.collection_id)

    # Save to JSON if requested
    if args.save:
        filename = f"collection_{args.collection_id}_analysis.json"
        analysis_data = {
            "collection_id": args.collection_id,
            "limit_applied": args.limit if args.limit > 0 else "all",
            "total_images_scraped": len(data),
            "top_models": analyzer.get_top_items(analyzer.models, 20),
            "top_model_versions": analyzer.get_top_items(analyzer.model_versions, 20),
            "top_samplers": analyzer.get_top_items(analyzer.samplers, 20),
            "top_steps": analyzer.get_top_items(analyzer.steps, 10),
            "top_cfgs": analyzer.get_top_items(analyzer.cfgs, 10),
            "top_loras": analyzer.get_top_items(analyzer.loras, 30),
            "lora_average_weights": analyzer.get_average_weights(),
            "lora_model_ids": analyzer.lora_model_ids,
            "deleted_models": analyzer.deleted_models,  # New: Include deleted models
            "top_tags": analyzer.get_top_items(analyzer.tags, 50),
            "top_positive_concepts": analyzer.get_top_items(
                analyzer.positive_concepts, 100
            ),
            "top_positive_phrases": analyzer.get_top_items(
                analyzer.positive_phrases, 50
            ),
            "top_negative_concepts": analyzer.get_top_items(
                analyzer.negative_concepts, 50
            ),
            "authors": analyzer.get_top_items(analyzer.authors, 20),
            "scraped_data": data,
        }

        with open(filename, "w") as f:
            json.dump(analysis_data, f, indent=2)

        fmt.print_info(f"Analysis saved to: {filename}")


if __name__ == "__main__":
    main()
