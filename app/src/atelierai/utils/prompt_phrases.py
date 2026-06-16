# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/prompt-extraction.md
# ──────────────────────────────────────────────────────────────────────────────
"""Prompt text parsing, style detection, concept and phrase extraction.

Supports two prompt styles with automatic detection:

* **Tag-style** (danbooru-like): comma-separated short descriptive phrases
  e.g. ``1girl, blue dress, cat ears, sitting on a bed``
* **NLP-style** (natural language): English sentences describing the scene
  e.g. ``An anime girl in a blue dress wearing cat ears sitting on a bed``

Both styles can appear in the same prompt (mixed-style).  The module extracts
meaningful concepts and phrases while filtering quality boosters, stripping
directives (LoRA, embeddings, etc.), and detecting JSON workflow payloads.

Key design principles:
* Quality boosters (masterpiece, best quality, etc.) are filtered — they are
  model-bias instructions, not content descriptions.
* Content-descriptive tags like "looking at viewer", "full body", "standing"
  are kept — they describe the image subject.
* LoRA trigger names are extracted as separate concepts.
* Tags do NOT automatically create concepts — they become authority_terms
  with concept_id=None, awaiting user-driven concept linkage.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# LoRA directives: <lora:name:weight> or <lora:name>
_PROMPT_LORA_RE = re.compile(
    r"<\s*lora\s*:\s*([^:>]+?)\s*(?::\s*-?\d+\.?\d*)?\s*>", re.IGNORECASE
)

# General directives: <anything> (embeddings, breakpoints, etc.)
_PROMPT_DIRECTIVE_RE = re.compile(r"<[^>]*>")

# Strength suffix: ":1.0" or ":-0.5" at end of a token
_PROMPT_STRENGTH_SUFFIX_RE = re.compile(r"\s*:\s*-?\d+\.?\d*\s*$")

# A1111 sampler metadata suffix: "Steps: 20, Sampler: euler..."
_A1111_PARAMS_RE = re.compile(
    r"\s*,?\s*Steps\s*:\s*\d+.*$", re.IGNORECASE | re.DOTALL
)

# Sentinels for escaped delimiters
_ESCAPED_OPEN_PAREN_SENTINEL = "__ATELIER_ESCAPED_OPEN_PAREN__"
_ESCAPED_CLOSE_PAREN_SENTINEL = "__ATELIER_ESCAPED_CLOSE_PAREN__"
_ESCAPED_OPEN_BRACKET_SENTINEL = "__ATELIER_ESCAPED_OPEN_BRACKET__"
_ESCAPED_CLOSE_BRACKET_SENTINEL = "__ATELIER_ESCAPED_CLOSE_BRACKET__"

# ---------------------------------------------------------------------------
# Phrase boundary words for NLP chunking
# ---------------------------------------------------------------------------
_PHRASE_BOUNDARY_WORDS = frozenset(
    {
        "wearing", "wears",
        "holding", "carrying",
    }
)

# ---------------------------------------------------------------------------
# Stop words for NLP extraction (structural grammar, not content)
# NOTE: Prepositions like "in", "on", "at", "to", "for", "from", "with", "by"
# are included here for NLP stop-word filtering but are NOT removed from
# tag-style concepts — tags like "looking at viewer" keep "at" intact because
# the whole comma-separated segment is the concept.
# ---------------------------------------------------------------------------
PHRASE_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "by",
        "as",
        "is",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "of",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "his",
        "her",
        "their",
        "our",
        "my",
        "your",
        "he",
        "she",
        "they",
        "them",
        "him",
        "me",
        "us",
        "into",
        "onto",
        "upon",
        "through",
        "during",
        "before",
        "after",
        "while",
        "when",
        "where",
        "why",
        "how",
        "what",
        "which",
        "who",
        "whose",
        "whom",
        "if",
        "then",
        "so",
        "because",
        "since",
        "although",
        "though",
        "unless",
        "until",
        "also",
        "very",
        "too",
        "quite",
        "rather",
        "just",
        "only",
        "even",
        "still",
        "already",
        "yet",
        "some",
        "many",
        "few",
        "most",
        "much",
        "little",
        "more",
        "less",
        "wit",  # common typo for "with" in prompts
    }
)

# ---------------------------------------------------------------------------
# Quality booster tags — these bias the model but are NOT content concepts.
# Things like "looking at viewer", "full body", "standing" are NOT here —
# they describe the subject and are valid concepts.
# ---------------------------------------------------------------------------
QUALITY_BOOSTER_TAGS = frozenset(
    {
        # Quality modifiers
        "masterpiece",
        "best quality",
        "high quality",
        "great quality",
        "amazing quality",
        "ultra quality",
        "absurdres",
        "highres",
        "hi res",
        "ultra high res",
        "incredibly absurdres",
        # Filesize indicators
        "huge filesize",
        "large filesize",
        "medium filesize",
        "small filesize",
        # Negative quality
        "lowres",
        "normal quality",
        "low quality",
        "worst quality",
        "bad quality",
        "worst",
        "low",
        "bad",
        "error",
        "blurry",
        "jpeg artifacts",
        "compression artifacts",
        "cropped",
        "cropped out",
        "out of frame",
        # Medium / style boosters (too generic to be useful concepts)
        "highly detailed",
        "ultra detailed",
        "extremely detailed",
        "incredibly detailed",
        "insane detail",
        "fine detail",
        "intricate detail",
        "detailed",
        "sharp focus",
        # Resolution boosters
        "8k",
        "4k",
        "2k",
        "full hd",
        "hd",
        "fhd",
        "uhd",
        # Lighting boosters (generic, not scene-specific)
        "cinematic lighting",
        "dramatic lighting",
        "volumetric lighting",
        "studio lighting",
        "beautiful lighting",
        # Trending / rating boosters
        "trending",
        "trending on artstation",
        "trending on deviantart",
        "popular",
        "award winning",
        "award-winning",
        "artstation",
        "deviantart",
        "pixiv",
        "newgrounds",
        # Rating / safety tags
        "safe",
        "safe for work",
        "sfw",
        "nsfw",
        "explicit",
        "questionable",
        # Source style tags (model-level instructions)
        "source_anime",
        "source anime",
        "anime_source",
        "anime source",
        "source_cartoon",
        "source cartoon",
        "source_furry",
        "source furry",
        "source_style",
        "source style",
        # Score / rating tags
        "score_9",
        "score_8",
        "score_7",
        "score_6",
        "score_5",
        "score_4",
        "score_3",
        "score_2",
        "score_1",
        "score_up",
        "rating_safe",
        "rating_general",
        "rating_questionable",
        "rating_explicit",
        # Fix / technique tags
        "highres fix",
        "highres fix",
        "hires fix",
        # Generic positive adjectives (too vague to be concepts)
        "first grade",
        "excellent",
        "wonderful",
        "fantastic",
        "brilliant",
        "magnificent",
        "superb",
        "stunning",
        "gorgeous",
        "amazing",
        "awesome",
        "incredible",
        "fabulous",
        "perfect",
        "flawless",
        "best",
        "top",
        "top tier",
        "god tier",
        "godtier",
        "very aesthetic",
        "extremely aesthetic",
        "aesthetic",
        # Recency tags
        "newest",
        "new",
        "recent",
        "latest",
        "up to date",
    }
)

# Maximum meaningful words allowed in a single concept (both tag and NLP)
_MAX_CONCEPT_WORDS = 6

# Maximum meaningful words for NLP concepts (before chunking)
_MAX_NLP_CONCEPT_WORDS = 6

# Minimum character length for a concept to be kept
_MIN_CONCEPT_CHARS = 2


# ---------------------------------------------------------------------------
# JSON / workflow detection
# ---------------------------------------------------------------------------


def _looks_like_json(text: str) -> bool:
    """Return True if text appears to be a JSON workflow blob."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[0] not in ("{", "["):
        return False
    # ComfyUI workflow indicators
    if '"class_type"' in stripped or '"inputs"' in stripped:
        return True
    if '"nodes"' in stripped or '"links"' in stripped:
        return True
    # Try to parse as JSON
    try:
        parsed = json.loads(stripped)
        return isinstance(parsed, (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Core normalisation
# ---------------------------------------------------------------------------


def normalize_phrase_breaks(prompt: str) -> str:
    r"""Treat structural prompt delimiters as comma-like separators.

    Handles:
    * Angle-bracket directives (LoRA, embeddings, breaks)
    * Parentheses / brackets (emphasis markers)
    * Newlines as separators
    * A1111 sampler parameter suffixes
    * Escaped literal delimiters (``\(``, ``\)``, ``\[``, ``\]``)

    Returns an empty string if the input appears to be a JSON workflow blob.
    """
    normalized = str(prompt or "")

    # Bail out on JSON workflow payloads
    if _looks_like_json(normalized):
        return ""

    # Handle escaped delimiters first
    normalized = normalized.replace("\\n", "\n")
    normalized = re.sub(r"\\+\(", _ESCAPED_OPEN_PAREN_SENTINEL, normalized)
    normalized = re.sub(r"\\+\)", _ESCAPED_CLOSE_PAREN_SENTINEL, normalized)
    normalized = re.sub(r"\\+\[", _ESCAPED_OPEN_BRACKET_SENTINEL, normalized)
    normalized = re.sub(r"\\+\]", _ESCAPED_CLOSE_BRACKET_SENTINEL, normalized)

    # Strip A1111 sampler metadata (Steps:, Sampler:, etc.)
    normalized = _A1111_PARAMS_RE.sub("", normalized)

    # Replace explicit <break> directives
    normalized = re.sub(r"(?i)<\s*break\s*>", ",", normalized)

    # Strip remaining <...> directives (LoRA names already extracted upstream)
    normalized = _PROMPT_DIRECTIVE_RE.sub(",", normalized)

    # Treat bare "break" keyword as separator
    normalized = re.sub(r"(?i)(?<!\w)break(?!\w)", ",", normalized)

    # Strip parentheses and brackets (emphasis markers) → comma separators
    normalized = re.sub(r"[\(\)\[\]]", ",", normalized)

    # Newlines → commas
    normalized = re.sub(r"\s*[\r\n]+\s*", ",", normalized)

    # Normalise comma spacing
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r",{2,}", ",", normalized)

    # Restore escaped literal delimiters
    normalized = normalized.replace(_ESCAPED_OPEN_PAREN_SENTINEL, "(")
    normalized = normalized.replace(_ESCAPED_CLOSE_PAREN_SENTINEL, ")")
    normalized = normalized.replace(_ESCAPED_OPEN_BRACKET_SENTINEL, "[")
    normalized = normalized.replace(_ESCAPED_CLOSE_BRACKET_SENTINEL, "]")

    return normalized.strip(" ,")


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


def _strip_prompt_strength_suffix(value: str) -> str:
    """Strip a trailing token-strength suffix like ':1.0' from prompt text."""
    return _PROMPT_STRENGTH_SUFFIX_RE.sub("", str(value or ""))


def normalize_prompt_tag_name(name: str) -> str:
    """Normalize a prompt tag name for stable matching and storage."""
    text = _PROMPT_DIRECTIVE_RE.sub(" ", str(name or ""))
    text = _strip_prompt_strength_suffix(text)
    text = text.strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.lower()


# ---------------------------------------------------------------------------
# Quality booster filtering
# ---------------------------------------------------------------------------


def _is_quality_booster(tag: str) -> bool:
    """Return True if tag is a quality booster / filler, not a content concept."""
    normalized = normalize_prompt_tag_name(tag)
    if not normalized:
        return True
    return normalized in QUALITY_BOOSTER_TAGS


# ---------------------------------------------------------------------------
# LoRA name extraction
# ---------------------------------------------------------------------------


def extract_lora_names(prompt: str) -> List[str]:
    """Extract LoRA names from <lora:name:weight> directives.

    Returns a list of normalized LoRA names (lowercased, underscores→spaces).
    """
    return [
        normalize_prompt_tag_name(name)
        for name in _PROMPT_LORA_RE.findall(prompt)
    ]


# ---------------------------------------------------------------------------
# Style detection
# ---------------------------------------------------------------------------


def detect_prompt_style(prompt: str) -> str:
    """Detect if prompt text is tag-style or natural-language-style.

    Returns one of: ``"tag"`` or ``"nlp"``.
    """
    if not prompt or not prompt.strip():
        return "tag"

    # JSON workflows are not prompts
    if _looks_like_json(prompt):
        return "tag"

    normalized_prompt = normalize_phrase_breaks(prompt)
    if not normalized_prompt:
        return "tag"

    segments = [s.strip() for s in normalized_prompt.split(",") if s.strip()]
    if not segments:
        return "tag"

    tag_style_count = 0
    nlp_style_count = 0

    for segment in segments:
        word_count = len(segment.split())
        if word_count <= 0:
            continue
        # Tag-style: short segments (<= 6 words) that don't read like sentences
        if word_count <= 6 and not _looks_like_sentence(segment):
            tag_style_count += 1
        else:
            nlp_style_count += 1

    total = tag_style_count + nlp_style_count
    if total == 0:
        return "tag"

    # If more than 60% of segments are tag-style, treat as tag
    if tag_style_count / total > 0.6:
        return "tag"
    # Otherwise nlp
    return "nlp"


def _looks_like_sentence(text: str) -> bool:
    """Heuristic: does this text read like a natural language phrase?"""
    text = text.strip()
    if not text:
        return False
    words = text.split()
    if len(words) <= 3:
        return False
    # Count function words (articles, prepositions, conjunctions)
    function_words = sum(
        1 for w in words if w.lower() in PHRASE_STOP_WORDS
    )
    # If more than 20% are function words, it's sentence-like
    return function_words / len(words) > 0.2


# ---------------------------------------------------------------------------
# Concept extraction — tag-style
# ---------------------------------------------------------------------------


def _chunk_long_tag(tag: str) -> List[str]:
    """Split an overly long tag segment into sub-tags.

    Strategy:
    1. Split on prepositions/boundary words first (e.g. "wit" typo).
    2. If no good boundary found, take sliding window of meaningful words.
    """
    words = tag.split()
    meaningful = [w for w in words if w not in PHRASE_STOP_WORDS]
    
    # If after removing stop words it's short enough, just return it
    if len(meaningful) <= _MAX_CONCEPT_WORDS:
        return [tag]
    
    # Try splitting on boundary/stop words
    chunks: List[str] = []
    current: List[str] = []
    for word in words:
        w_lower = word.lower()
        # Split on stop words that act as natural boundaries
        if w_lower in PHRASE_STOP_WORDS and current:
            chunk = " ".join(current)
            if len([w for w in current if w.lower() not in PHRASE_STOP_WORDS]) >= 2:
                chunks.append(chunk)
            current = []
        else:
            current.append(word)
    
    if current:
        chunk = " ".join(current)
        meaningful_in_chunk = [w for w in current if w.lower() not in PHRASE_STOP_WORDS]
        if len(meaningful_in_chunk) >= 2:
            chunks.append(chunk)
        elif len(meaningful_in_chunk) == 1 and not chunks:
            # Single meaningful word as fallback
            chunks.append(chunk)
    
    return chunks if chunks else [tag]


def extract_tag_style_concepts(prompt: str) -> List[str]:
    """Extract concepts from tag-style prompts.

    Splits on commas, normalises each segment, filters quality boosters
    and overly short fragments.  Tags are kept as-is — stop words are NOT
    removed because tag-style concepts like "looking at viewer" are atomic.
    """
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    if not normalized_prompt:
        return []

    segments = [s.strip() for s in normalized_prompt.split(",")]

    cleaned_concepts: List[str] = []
    for segment in segments:
        segment = _strip_prompt_strength_suffix(segment)
        normalized = normalize_prompt_tag_name(segment)

        if len(normalized) < _MIN_CONCEPT_CHARS:
            continue
        if _is_quality_booster(normalized):
            continue

        # Split overly long tag segments into sub-tags
        words = normalized.split()
        meaningful = [w for w in words if w not in PHRASE_STOP_WORDS]
        if len(meaningful) > _MAX_CONCEPT_WORDS:
            # Chunk on boundary words or just take meaningful word groups
            for chunk in _chunk_long_tag(normalized):
                chunk_norm = normalize_prompt_tag_name(chunk)
                if len(chunk_norm) >= _MIN_CONCEPT_CHARS and not _is_quality_booster(chunk_norm):
                    cleaned_concepts.append(chunk_norm)
        else:
            cleaned_concepts.append(normalized)

    return cleaned_concepts


# ---------------------------------------------------------------------------
# Concept extraction — NLP-style
# ---------------------------------------------------------------------------


def _chunk_nlp_phrase(phrase: str) -> List[str]:
    """Break an NLP phrase into meaningful concept-sized chunks.

    Strategy:
    1. If the whole phrase is short enough (<= _MAX_NLP_CONCEPT_WORDS
       meaningful words), keep it as one chunk.
    2. Otherwise, split on strong boundary words (``wearing``, ``holding``).
    3. For each chunk, remove stop words and keep the meaningful core.
    """
    phrase = phrase.strip()
    if not phrase:
        return []

    words = phrase.split()
    if not words:
        return []

    # Count meaningful (non-stop) words
    meaningful = [w.lower() for w in words if w.lower() not in PHRASE_STOP_WORDS]

    # Short enough — keep as-is
    if 1 <= len(meaningful) <= _MAX_NLP_CONCEPT_WORDS:
        return [" ".join(words)]

    # Too long — split on boundary words
    chunks: List[str] = []
    current_chunk: List[str] = []

    for word in words:
        w_lower = word.lower()

        # Strong boundary: verbs like "wearing", "holding"
        if w_lower in _PHRASE_BOUNDARY_WORDS and current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append(chunk_text)
            current_chunk = []

        current_chunk.append(word)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    # If we still have overly long chunks, truncate meaningful words
    result: List[str] = []
    for chunk in chunks:
        chunk_words = chunk.split()
        chunk_meaningful = [
            w for w in chunk_words if w.lower() not in PHRASE_STOP_WORDS
        ]
        if len(chunk_meaningful) > _MAX_NLP_CONCEPT_WORDS:
            # Take first N meaningful words (with surrounding stop words)
            kept: List[str] = []
            meaningful_count = 0
            for w in chunk_words:
                if w.lower() not in PHRASE_STOP_WORDS:
                    meaningful_count += 1
                kept.append(w)
                if meaningful_count >= _MAX_NLP_CONCEPT_WORDS:
                    break
            result.append(" ".join(kept))
        else:
            result.append(chunk)

    return result


def extract_nlp_concepts(prompt: str) -> List[str]:
    """Extract concepts from natural language prompts.

    Splits into sentences -> comma phrases -> chunks, then filters
    quality boosters and overly long fragments.
    """
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    if not normalized_prompt:
        return []

    sentences = re.split(r"[.!?]+", normalized_prompt)

    concepts: List[str] = []
    for sentence in sentences:
        if not sentence.strip():
            continue

        phrases = [p.strip() for p in sentence.split(",")]

        for phrase in phrases:
            phrase = _strip_prompt_strength_suffix(phrase)
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if len(phrase) < _MIN_CONCEPT_CHARS:
                continue

            # Get sub-chunks
            chunks = _chunk_nlp_phrase(phrase)

            for chunk in chunks:
                normalized = normalize_prompt_tag_name(chunk)
                if not normalized or len(normalized) < _MIN_CONCEPT_CHARS:
                    continue
                if _is_quality_booster(normalized):
                    continue

                # Check meaningful word count
                words = normalized.split()
                meaningful = [w for w in words if w not in PHRASE_STOP_WORDS]
                if not meaningful:
                    continue

                concepts.append(normalized)

    return concepts


# ---------------------------------------------------------------------------
# Unified extraction
# ---------------------------------------------------------------------------


def extract_concepts(prompt: str) -> List[str]:
    """Extract concepts with automatic prompt style detection.

    Also extracts LoRA trigger names as separate concepts.
    """
    if not prompt:
        return []

    lora_names = extract_lora_names(prompt)

    style = detect_prompt_style(prompt)
    if style == "nlp":
        concepts = extract_nlp_concepts(prompt)
    else:
        concepts = extract_tag_style_concepts(prompt)

    # Append LoRA names not already present
    existing = {normalize_prompt_tag_name(c) for c in concepts}
    for lora_name in lora_names:
        if lora_name and lora_name not in existing:
            concepts.append(lora_name)
            existing.add(lora_name)

    return concepts


# ---------------------------------------------------------------------------
# Phrase extraction
# ---------------------------------------------------------------------------


def extract_tag_style_phrases(prompt: str) -> List[str]:
    """Extract phrases from tag-style prompts (each comma-separated segment)."""
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    if not normalized_prompt:
        return []

    phrases: List[str] = []
    for raw in [p.strip() for p in normalized_prompt.split(",")]:
        phrase = normalize_prompt_tag_name(_strip_prompt_strength_suffix(raw))
        if len(phrase) >= _MIN_CONCEPT_CHARS and not _is_quality_booster(phrase):
            phrases.append(phrase)

    return phrases


def _extract_subphrases(
    phrase: str,
    phrases: List[str],
    min_words: int,
    max_words: int,
) -> None:
    """Extract bounded n-gram style subphrases from NLP text."""
    normalized = re.sub(r"\s+", " ", phrase).strip().lower()
    words = normalized.split()
    if not words:
        return

    meaningful_words = [w for w in words if w not in PHRASE_STOP_WORDS]

    if len(meaningful_words) < min_words or len(normalized) < 3:
        return

    for n in range(min_words, min(max_words + 1, len(words) + 1)):
        for i in range(len(words) - n + 1):
            sub_phrase = " ".join(words[i : i + n])
            sub_words = sub_phrase.split()
            sub_meaningful = [
                w for w in sub_words if w not in PHRASE_STOP_WORDS
            ]

            if len(sub_meaningful) >= 1 and not _is_quality_booster(sub_phrase):
                phrases.append(sub_phrase)


def extract_nlp_style_phrases(
    prompt: str,
    min_words: int,
    max_words: int,
) -> List[str]:
    """Extract phrases from natural language prompts."""
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    if not normalized_prompt:
        return []

    phrases: List[str] = []
    for sentence in re.split(r"[.!?]+", normalized_prompt):
        for raw_phrase in [p.strip() for p in sentence.split(",")]:
            raw_phrase = _strip_prompt_strength_suffix(raw_phrase)
            _extract_subphrases(raw_phrase, phrases, min_words, max_words)
    return phrases


def extract_phrases(
    prompt: str,
    min_words: int = 2,
    max_words: int = 4,
) -> List[str]:
    """Extract common phrases from a prompt."""
    if not prompt:
        return []
    if detect_prompt_style(prompt) == "nlp":
        return extract_nlp_style_phrases(
            prompt, min_words=min_words, max_words=max_words
        )
    return extract_tag_style_phrases(prompt)


# ---------------------------------------------------------------------------
# Prompt tag record building
# ---------------------------------------------------------------------------


def _build_prompt_tag_record(
    name: str,
    *,
    kind: str,
    prompt_role: str,
    source_type: Optional[str],
    source_label: Optional[str],
    confidence: float,
) -> dict[str, Any]:
    normalized_name = normalize_prompt_tag_name(name)
    return {
        "name": name,
        "normalized_name": normalized_name,
        "kind": kind,
        "source": "prompt",
        "prompt_role": prompt_role,
        "source_type": source_type,
        "source_label": source_label,
        "confidence": confidence,
        "danbooru_tag_id": None,
        "danbooru_term_id": None,
    }


def merge_prompt_tag_records(records: List[dict[str, Any]]) -> List[dict[str, Any]]:
    """Merge prompt tag records by normalized name while preserving kind richness."""
    merged: dict[str, dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        normalized_name = normalize_prompt_tag_name(
            str(record.get("normalized_name") or record.get("name") or "")
        )
        if not normalized_name:
            continue

        current = dict(record)
        current["normalized_name"] = normalized_name
        if (
            not isinstance(current.get("name"), str)
            or not str(current.get("name") or "").strip()
        ):
            current["name"] = normalized_name

        existing = merged.get(normalized_name)
        if existing is None:
            merged[normalized_name] = current
            continue

        existing_kind = str(existing.get("kind") or "")
        current_kind = str(current.get("kind") or "")
        if existing_kind != current_kind and current_kind:
            kinds = {kind for kind in (existing_kind, current_kind) if kind}
            existing["kind"] = (
                "concept_phrase"
                if kinds == {"concept", "phrase"}
                else current_kind or existing_kind
            )

        if not existing.get("source_type") and current.get("source_type"):
            existing["source_type"] = current.get("source_type")
        if not existing.get("source_label") and current.get("source_label"):
            existing["source_label"] = current.get("source_label")
        if (
            existing.get("danbooru_tag_id") is None
            and current.get("danbooru_tag_id") is not None
        ):
            existing["danbooru_tag_id"] = current.get("danbooru_tag_id")
        if (
            existing.get("danbooru_term_id") is None
            and current.get("danbooru_term_id") is not None
        ):
            existing["danbooru_term_id"] = current.get("danbooru_term_id")

        existing_confidence = existing.get("confidence")
        current_confidence = current.get("confidence")
        if isinstance(existing_confidence, (int, float)) and isinstance(
            current_confidence, (int, float)
        ):
            existing["confidence"] = max(
                float(existing_confidence), float(current_confidence)
            )

    return list(merged.values())


def build_prompt_tag_payload(
    prompt: str,
    *,
    prompt_role: str = "positive",
    source_type: Optional[str] = None,
    source_label: Optional[str] = None,
    min_words: int = 2,
    max_words: int = 4,
) -> dict[str, Any]:
    """Build structured prompt analysis payload.

    Returns a dict with:
    * ``prompt_style`` — detected style (``"tag"`` or ``"nlp"``)
    * ``concepts`` — list of concept records
    * ``phrases`` — list of phrase records
    * ``prompt_tags`` — merged and deduplicated tag records
    * ``lora_names`` — extracted LoRA trigger names
    """
    prompt_style = detect_prompt_style(prompt)

    concept_records: List[dict[str, Any]] = []
    seen_concepts: set[str] = set()
    for concept in extract_concepts(prompt):
        normalized_name = normalize_prompt_tag_name(concept)
        if not normalized_name or normalized_name in seen_concepts:
            continue
        seen_concepts.add(normalized_name)
        concept_records.append(
            _build_prompt_tag_record(
                concept,
                kind="concept",
                prompt_role=prompt_role,
                source_type=source_type,
                source_label=source_label,
                confidence=1.0,
            )
        )

    phrase_records: List[dict[str, Any]] = []
    seen_phrases: set[str] = set()
    for phrase in extract_phrases(prompt, min_words=min_words, max_words=max_words):
        normalized_name = normalize_prompt_tag_name(phrase)
        if not normalized_name or normalized_name in seen_phrases:
            continue
        seen_phrases.add(normalized_name)
        phrase_records.append(
            _build_prompt_tag_record(
                phrase,
                kind="phrase",
                prompt_role=prompt_role,
                source_type=source_type,
                source_label=source_label,
                confidence=0.95,
            )
        )

    lora_names = extract_lora_names(prompt)

    return {
        "prompt_style": prompt_style,
        "concepts": concept_records,
        "phrases": phrase_records,
        "prompt_tags": merge_prompt_tag_records(concept_records + phrase_records),
        "lora_names": lora_names,
    }
