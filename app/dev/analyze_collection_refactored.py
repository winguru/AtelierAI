#!/usr/bin/env python3
"""Analyze a collection to find common tags, prompts, and elements."""

import re
import json
import argparse
from collections import Counter, defaultdict
from typing import Dict, List, Tuple
from src.civitai import CivitaiPrivateScraper
from src.console_utils import ConsoleFormatter


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

    def clean_prompt(self, prompt: str) -> str:
        """Clean and normalize a prompt string."""
        if not prompt:
            return ""
        prompt = re.sub(r'\s+', ' ', prompt)
        prompt = prompt.strip()
        return prompt

    def detect_prompt_style(self, prompt: str) -> str:
        """Detect if prompt is tag-style or NLP-style."""
        if not prompt:
            return 'tag'

        comma_count = prompt.count(',')
        word_count = len(prompt.split())
        has_weights = bool(re.search(r':\s*\d+\.?\d*', prompt))
        has_brackets = bool(re.search(r'<[^>]+>', prompt))

        if comma_count >= 3 and (comma_count / max(word_count, 1)) > 0.1:
            return 'tag'
        if has_weights or has_brackets:
            return 'tag'

        return 'nlp'

    def extract_tag_style_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from tag-style prompts."""
        if not prompt:
            return []

        prompt = re.sub(r'\s*:\s*-?\d+\.?\d*', '', prompt)
        prompt = re.sub(r'<[^>]+>', '', prompt)
        concepts = [c.strip() for c in prompt.split(',')]

        cleaned_concepts = []
        for concept in concepts:
            concept = re.sub(r'\s+', ' ', concept).strip()
            concept = re.sub(r'\([^)]*\)', '', concept).strip()
            if len(concept) >= 2:
                cleaned_concepts.append(concept.lower())

        return cleaned_concepts

    def extract_nlp_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from natural language prompts."""
        if not prompt:
            return []

        sentences = re.split(r'[.!?]+', prompt)
        stop_words = {
            'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'from', 'with', 'by', 'as', 'is', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'can', 'of', 'it', 'its', 'this',
            'that', 'these', 'those', 'his', 'her', 'their', 'our', 'my', 'your',
            'he', 'she', 'they', 'them', 'him', 'me', 'us', 'into', 'onto',
            'upon', 'over', 'under', 'through', 'during', 'before', 'after',
            'while', 'when', 'where', 'why', 'how', 'what', 'which', 'who',
            'whose', 'whom', 'if', 'then', 'so', 'because', 'since', 'although',
            'though', 'while', 'unless', 'until', 'also', 'very', 'too', 'quite',
            'rather', 'just', 'only', 'even', 'still', 'already', 'yet',
            'some', 'many', 'few', 'most', 'much', 'little', 'more', 'less'
        }

        concepts = []
        for sentence in sentences:
            phrases = [p.strip() for p in sentence.split(',')]

            for phrase in phrases:
                phrase = re.sub(r'\s+', ' ', phrase).strip()
                if len(phrase) < 4:
                    continue

                words = phrase.split()
                concept_words = []
                for word in words:
                    word_clean = word.lower().strip('.,!?;:')
                    if word_clean and word_clean not in stop_words:
                        concept_words.append(word_clean)

                if len(concept_words) >= 2:
                    concept = ' '.join(concept_words)
                    concepts.append(concept)
                elif len(concept_words) == 1:
                    concepts.append(concept_words[0])

        return concepts

    def extract_concepts(self, prompt: str) -> List[str]:
        """Extract concepts from a prompt, detecting style automatically."""
        if not prompt:
            return []

        style = self.detect_prompt_style(prompt)

        if style == 'tag':
            return self.extract_tag_style_concepts(prompt)
        else:
            return self.extract_nlp_concepts(prompt)

    def _get_phrase_stop_words(self) -> set:
        """Get the set of stop words for phrase extraction."""
        return {
            'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'from', 'with', 'by', 'as', 'is', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'must', 'can', 'of', 'it', 'its', 'this',
            'that', 'these', 'those', 'his', 'her', 'their', 'our', 'my', 'your',
            'into', 'onto', 'upon', 'over', 'under', 'through', 'during', 'before',
            'after', 'while', 'when', 'where', 'why', 'how', 'what', 'which', 'who',
            'whose', 'whom', 'if', 'then', 'so', 'because', 'since', 'although',
            'though', 'while', 'unless', 'until', 'also', 'very', 'too', 'quite',
            'rather', 'just', 'only', 'even', 'still', 'already', 'yet',
            'some', 'many', 'few', 'most', 'much', 'little', 'more', 'less'
        }

    def _extract_tag_phrases(self, prompt: str) -> List[str]:
        """Extract phrases from tag-style prompts."""
        phrases = []
        raw_phrases = [p.strip() for p in prompt.split(',')]

        for phrase in raw_phrases:
            phrase = re.sub(r'\s*:\s*-?\d+\.?\d*', '', phrase)
            phrase = re.sub(r'\([^)]*\)', '', phrase)
            phrase = re.sub(r'<[^>]+>', '', phrase)
            phrase = re.sub(r'\s+', ' ', phrase).strip().lower()

            if len(phrase) >= 2:
                phrases.append(phrase)

        return phrases

    def _extract_nlp_phrases(self, prompt: str, min_words: int, max_words: int) -> List[str]:
        """Extract phrases from NLP-style prompts."""
        stop_words = self._get_phrase_stop_words()
        phrases = []
        sentences = re.split(r'[.!?]+', prompt)

        for sentence in sentences:
            raw_phrases = [p.strip() for p in sentence.split(',')]

            for phrase in raw_phrases:
                phrase = re.sub(r'\s+', ' ', phrase).strip().lower()
                words = phrase.split()
                meaningful_words = [w for w in words if w not in stop_words]

                if len(meaningful_words) >= min_words and len(phrase) >= 3:
                    for n in range(min_words, min(max_words + 1, len(words) + 1)):
                        for i in range(len(words) - n + 1):
                            sub_phrase = ' '.join(words[i:i + n])
                            sub_words = sub_phrase.split()
                            sub_meaningful = [w for w in sub_words if w not in stop_words]

                            if len(sub_meaningful) >= 1:
                                phrases.append(sub_phrase)

        return phrases

    def extract_phrases(self, prompt: str, min_words: int = 2, max_words: int = 4) -> List[str]:
        """Extract common phrases from a prompt."""
        if not prompt:
            return []

        style = self.detect_prompt_style(prompt)

        if style == 'tag':
            return self._extract_tag_phrases(prompt)
        else:
            return self._extract_nlp_phrases(prompt, min_words, max_words)

    def analyze(self) -> None:
        """Analyze all scraped data and compile statistics."""
        for item in self.data:
            model = item.get('model', 'Unknown')
            model_version = item.get('model_version', 'Unknown')
            sampler = item.get('sampler', 'Unknown')
            steps = item.get('steps', 0)
            cfg = item.get('cfg_scale', 0)
            author = item.get('author', 'Unknown')

            self.models[model] += 1
            if model_version and model_version != 'Unknown':
                self.model_versions[f"{model} - {model_version}"] += 1
            self.samplers[sampler] += 1
            if steps and steps != 'Unknown':
                self.steps[str(steps)] += 1
            if cfg and cfg != 'Unknown':
                self.cfgs[str(cfg)] += 1
            self.authors[author] += 1

            loras = item.get('loras', [])
            for lora in loras:
                lora_name = lora.get('name', 'Unknown')
                lora_weight = lora.get('weight', 0)
                lora_model_id = lora.get('model_id')
                lora_version_id = lora.get('model_version_id')
                self.loras[lora_name] += 1
                self.lora_weights[lora_name].append(lora_weight)
                # Store model_id and version_id (first occurrence wins)
                if lora_name not in self.lora_model_ids:
                    self.lora_model_ids[lora_name] = {
                        'model_id': lora_model_id,
                        'model_version_id': lora_version_id
                    }

            # Count tags from image
            tags = item.get('tags', [])
            for tag in tags:
                self.tags[tag] += 1

            positive = item.get('prompt', '')
            negative = item.get('negative_prompt', '')

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


def _print_overview(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print overview section."""
    total_items = len(analyzer.data)
    fmt.print_subheader("Overview")
    fmt.print_blank()
    fmt.print_key_value("Total Images", total_items)
    fmt.print_key_value("Unique Models", len(analyzer.models))
    fmt.print_key_value("Unique Samplers", len(analyzer.samplers))
    fmt.print_key_value("Total LoRAs Used", sum(analyzer.loras.values()))
    fmt.print_blank()


def _print_models_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print models section."""
    total_items = len(analyzer.data)
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


def _print_model_versions_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print model versions section."""
    if not analyzer.model_versions:
        return
    fmt.print_subheader("Top Model Versions")
    fmt.print_blank()
    top_versions = analyzer.get_top_items(analyzer.model_versions, 8)
    headers = ["Version", "Count"]
    rows = [[ver, str(count)] for ver, count in top_versions]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_samplers_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print samplers section."""
    total_items = len(analyzer.data)
    fmt.print_subheader("Sampler Distribution")
    fmt.print_blank()
    top_samplers = analyzer.get_top_items(analyzer.samplers, 10)
    headers = ["Sampler", "Count", "Percentage"]
    rows = []
    for sampler, count in top_samplers:
        percentage = (count / total_items) * 100 if total_items > 0 else 0
        rows.append([sampler if sampler != 'Unknown' else 'N/A', str(count), f"{percentage:.1f}%"])
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_steps_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print steps section."""
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
    """Print CFG scale section."""
    if not analyzer.cfgs:
        return
    fmt.print_subheader("CFG Scale Distribution")
    fmt.print_blank()
    top_cfgs = analyzer.get_top_items(analyzer.cfgs, 8)
    headers = ["CFG", "Count"]
    rows = [[cfg, str(count)] for cfg, count in top_cfgs]
    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_loras_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, line_length: int) -> None:
    """Print LoRAs section."""
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
            model_id = lora_info.get('model_id')
            version_id = lora_info.get('model_version_id')
            url = f"civitai.com/models/{model_id}?modelVersionId={version_id}" if model_id and version_id else ""
            row_data.extend([str(version_id) if version_id else "N/A", url])

        rows.append(row_data)

    fmt.print_table(headers, rows)
    fmt.print_blank()


def _print_tags_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print tags section."""
    if not analyzer.tags:
        return
    total_items = len(analyzer.data)
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


def _print_concepts_section(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter) -> None:
    """Print positive and negative concepts."""
    total_items = len(analyzer.data)
    if analyzer.positive_concepts:
        fmt.print_subheader("Most Common Positive Concepts")
        fmt.print_blank()
        top_concepts = analyzer.get_top_items(analyzer.positive_concepts, 30)
        headers = ["Concept", "Occurrences", "Percentage"]
        rows = []
        for concept, count in top_concepts:
            percentage = (count / total_items) * 100 if total_items > 0 else 0
            display_concept = concept[:40] + "..." if len(concept) > 40 else concept
            rows.append([display_concept, str(count), f"{percentage:.1f}%"])
        fmt.print_table(headers, rows)
        fmt.print_blank()

    if analyzer.negative_concepts:
        fmt.print_subheader("Most Common Negative Concepts")
        fmt.print_blank()
        top_neg_concepts = analyzer.get_top_items(analyzer.negative_concepts, 20)
        headers = ["Concept", "Count"]
        rows = [[concept, str(count)] for concept, count in top_neg_concepts]
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


def _print_sample_prompts(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, line_length: int) -> None:
    """Print sample prompts section."""
    fmt.print_subheader("Sample Prompts")
    fmt.print_blank()
    sorted_by_loras = sorted(analyzer.data, key=lambda x: len(x.get('loras', [])), reverse=True)
    top_lora_prompts = sorted_by_loras[:3]

    fmt.print_info("Prompts with most LoRAs:")
    for i, item in enumerate(top_lora_prompts, 1):
        fmt.print_blank()
        fmt.print_info(f"[{i}] Image ID: {item['image_id']}")
        fmt.print_key_value("Model", item['model'], indent=4)
        fmt.print_key_value("LoRAs", len(item.get('loras', [])), indent=4)
        for lora in item.get('loras', []):
            lora_name = lora.get('name')
            lora_weight = lora.get('weight')
            lora_model_id = lora.get('model_id')
            lora_version_id = lora.get('model_version_id')

            info_str = f"  - {lora_name} (weight: {lora_weight})"
            if lora_model_id and lora_version_id and line_length >= 120:
                info_str += f" → civitai.com/models/{lora_model_id}?modelVersionId={lora_version_id}"

            fmt.print_info(info_str, indent=6)
        prompt = item.get('prompt', '')[:200]
        if prompt:
            fmt.print_key_value("Prompt", f"{prompt}...", indent=4)
        fmt.print_blank()


def print_analysis_report(analyzer: CollectionAnalyzer, fmt: ConsoleFormatter, collection_id: int) -> None:
    """Print a comprehensive analysis report."""
    fmt.print_header(f"Collection Analysis: {collection_id}")
    fmt.print_blank()

    _print_overview(analyzer, fmt)
    _print_models_section(analyzer, fmt)
    _print_model_versions_section(analyzer, fmt)
    _print_samplers_section(analyzer, fmt)
    _print_steps_section(analyzer, fmt)
    _print_cfg_section(analyzer, fmt)
    _print_loras_section(analyzer, fmt, fmt.line_length)
    _print_tags_section(analyzer, fmt)
    _print_authors_section(analyzer, fmt)
    _print_concepts_section(analyzer, fmt)
    _print_phrases_section(analyzer, fmt)
    _print_sample_prompts(analyzer, fmt, fmt.line_length)

    fmt.print_header("Analysis Complete")
    fmt.print_blank()


# ==========================================
# Main Entry Point
# ==========================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze a Civitai collection to find common tags and patterns"
    )
    parser.add_argument(
        "collection_id",
        type=int,
        help="Civitai collection ID to analyze"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of images to analyze (default: 50, -1 for all)"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save analysis results to JSON file"
    )
    parser.add_argument(
        "--line-length",
        type=int,
        default=70,
        help="Console line width (default: 70)"
    )
    parser.add_argument(
        "--wide", "-w",
        action="store_true",
        help="Use wide output (120 line width) for LoRA Model ID and URL columns"
    )

    args = parser.parse_args()

    # Apply wide mode if requested
    if args.wide:
        args.line_length = 120

    # Initialize formatter
    fmt = ConsoleFormatter(line_length=args.line_length)

    fmt.print_header("Civitai Collection Analyzer")
    fmt.print_blank()

    # Scrape the collection - Now uses CivitaiPrivateScraper.scrape() directly
    # Note: CivitaiPrivateScraper now includes limit support and uses CivitaiAPI for tag fetching
    fmt.print_info(f"Scraping collection {args.collection_id}...")
    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # Apply limit
    limit = args.limit if args.limit > 0 else None
    limit_msg = f"first {args.limit} images" if args.limit > 0 else "all images"

    fmt.print_info(f"Fetching {limit_msg}...")

    data = scraper.scrape(args.collection_id, limit=limit)

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
        fmt.print_info(f"ℹ️  Collection has {scraped_count} images (less than limit {args.limit}).")
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
            "top_tags": analyzer.get_top_items(analyzer.tags, 50),
            "top_positive_concepts": analyzer.get_top_items(analyzer.positive_concepts, 100),
            "top_positive_phrases": analyzer.get_top_items(analyzer.positive_phrases, 50),
            "top_negative_concepts": analyzer.get_top_items(analyzer.negative_concepts, 50),
            "authors": analyzer.get_top_items(analyzer.authors, 20),
            "scraped_data": data
        }

        with open(filename, 'w') as f:
            json.dump(analysis_data, f, indent=2)

        fmt.print_info(f"Analysis saved to: {filename}")


if __name__ == "__main__":
    main()
