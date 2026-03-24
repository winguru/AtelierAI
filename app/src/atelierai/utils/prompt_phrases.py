from __future__ import annotations

import re
from typing import Any, List, Optional


_PROMPT_DIRECTIVE_RE = re.compile(r"<[^>]*>")
_PROMPT_STRENGTH_SUFFIX_RE = re.compile(r"\s*:\s*-?\d+\.\d+\s*$")


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
        "over",
        "under",
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
    }
)


def normalize_phrase_breaks(prompt: str) -> str:
    """Treat structural prompt delimiters as comma-like separators.

    Parentheses and square brackets are used by many prompt syntaxes to adjust
    emphasis. For phrase parsing we strip those delimiter characters and treat
    each boundary as a natural split point.
    """
    normalized = str(prompt or "")
    normalized = normalized.replace("\\n", "\n")
    normalized = re.sub(r"(?i)<\s*break\s*>", ",", normalized)
    normalized = _PROMPT_DIRECTIVE_RE.sub(",", normalized)
    normalized = re.sub(r"(?i)(?<!\w)break(?!\w)", ",", normalized)
    normalized = re.sub(r"[\(\)\[\]]", ",", normalized)
    normalized = re.sub(r"\s*[\r\n]+\s*", ",", normalized)
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r",{2,}", ",", normalized)
    return normalized.strip(" ,")


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


def detect_prompt_style(prompt: str) -> str:
    """Detect if prompt text is tag-style or natural-language-style."""
    if not prompt:
        return "tag"

    normalized_prompt = normalize_phrase_breaks(prompt)
    comma_count = normalized_prompt.count(",")
    word_count = len(normalized_prompt.split())
    has_weights = bool(re.search(r":\s*\d+\.?\d*", normalized_prompt))
    has_brackets = bool(re.search(r"<[^>]+>", normalized_prompt))

    if comma_count >= 3 and (comma_count / max(word_count, 1)) > 0.1:
        return "tag"
    if has_weights or has_brackets:
        return "tag"

    return "nlp"


def extract_tag_style_concepts(prompt: str) -> List[str]:
    """Extract concepts from tag-style prompts."""
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    concepts = [c.strip() for c in normalized_prompt.split(",")]

    cleaned_concepts: List[str] = []
    for concept in concepts:
        concept = normalize_prompt_tag_name(_strip_prompt_strength_suffix(concept))
        if len(concept) >= 2:
            cleaned_concepts.append(concept)

    return cleaned_concepts


def extract_nlp_concepts(prompt: str) -> List[str]:
    """Extract concepts from natural language prompts."""
    if not prompt:
        return []

    normalized_prompt = normalize_phrase_breaks(prompt)
    sentences = re.split(r"[.!?]+", normalized_prompt)

    concepts: List[str] = []
    for sentence in sentences:
        phrases = [p.strip() for p in sentence.split(",")]

        for phrase in phrases:
            phrase = _strip_prompt_strength_suffix(phrase)
            phrase = re.sub(r"\s+", " ", phrase).strip()
            if len(phrase) < 4:
                continue

            words = phrase.split()
            concept_words = []
            for word in words:
                word_clean = word.lower().strip(".,!?;:")
                if word_clean and word_clean not in PHRASE_STOP_WORDS:
                    concept_words.append(word_clean)

            if len(concept_words) >= 2:
                concepts.append(" ".join(concept_words))
            elif len(concept_words) == 1:
                concepts.append(concept_words[0])

    return concepts


def extract_concepts(prompt: str) -> List[str]:
    """Extract concepts with automatic prompt style detection."""
    if not prompt:
        return []
    if detect_prompt_style(prompt) == "tag":
        return extract_tag_style_concepts(prompt)
    return extract_nlp_concepts(prompt)


def extract_tag_style_phrases(prompt: str) -> List[str]:
    """Extract phrases from tag-style prompts."""
    phrases: List[str] = []
    normalized_prompt = normalize_phrase_breaks(prompt)
    for raw in [p.strip() for p in normalized_prompt.split(",")]:
        phrase = normalize_prompt_tag_name(_strip_prompt_strength_suffix(raw))

        if len(phrase) >= 2:
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
    meaningful_words = [w for w in words if w not in PHRASE_STOP_WORDS]

    if len(meaningful_words) < min_words or len(normalized) < 3:
        return

    for n in range(min_words, min(max_words + 1, len(words) + 1)):
        for i in range(len(words) - n + 1):
            sub_phrase = " ".join(words[i : i + n])
            sub_words = sub_phrase.split()
            sub_meaningful = [w for w in sub_words if w not in PHRASE_STOP_WORDS]

            if len(sub_meaningful) >= 1:
                phrases.append(sub_phrase)


def extract_nlp_style_phrases(
    prompt: str,
    min_words: int,
    max_words: int,
) -> List[str]:
    """Extract phrases from natural language prompts."""
    phrases: List[str] = []
    normalized_prompt = normalize_phrase_breaks(prompt)
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
    if detect_prompt_style(prompt) == "tag":
        return extract_tag_style_phrases(prompt)
    return extract_nlp_style_phrases(prompt, min_words=min_words, max_words=max_words)


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

        normalized_name = normalize_prompt_tag_name(str(record.get("normalized_name") or record.get("name") or ""))
        if not normalized_name:
            continue

        current = dict(record)
        current["normalized_name"] = normalized_name
        if not isinstance(current.get("name"), str) or not str(current.get("name") or "").strip():
            current["name"] = normalized_name

        existing = merged.get(normalized_name)
        if existing is None:
            merged[normalized_name] = current
            continue

        existing_kind = str(existing.get("kind") or "")
        current_kind = str(current.get("kind") or "")
        if existing_kind != current_kind and current_kind:
            kinds = {kind for kind in (existing_kind, current_kind) if kind}
            existing["kind"] = "concept_phrase" if kinds == {"concept", "phrase"} else current_kind or existing_kind

        if not existing.get("source_type") and current.get("source_type"):
            existing["source_type"] = current.get("source_type")
        if not existing.get("source_label") and current.get("source_label"):
            existing["source_label"] = current.get("source_label")
        if existing.get("danbooru_tag_id") is None and current.get("danbooru_tag_id") is not None:
            existing["danbooru_tag_id"] = current.get("danbooru_tag_id")
        if existing.get("danbooru_term_id") is None and current.get("danbooru_term_id") is not None:
            existing["danbooru_term_id"] = current.get("danbooru_term_id")

        existing_confidence = existing.get("confidence")
        current_confidence = current.get("confidence")
        if isinstance(existing_confidence, (int, float)) and isinstance(current_confidence, (int, float)):
            existing["confidence"] = max(float(existing_confidence), float(current_confidence))

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
    """Build structured prompt analysis payloads for concepts, phrases, and merged prompt tags."""
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

    return {
        "prompt_style": prompt_style,
        "concepts": concept_records,
        "phrases": phrase_records,
        "prompt_tags": merge_prompt_tag_records(concept_records + phrase_records),
    }