# text_chunker.py
"""
Text Chunking Utility for TTS Systems.

Handles long text input by splitting into manageable segments while
preserving sentence boundaries and natural speech patterns.

Key improvements:
  - Pre-compiled regex patterns (per-language, cached)
  - Language constants as a Literal type for IDE autocompletion
  - Separated chunking strategies (sentence-based, hard-split) into
    clearly named private methods
  - IMPROVED: Better structured logging with context
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass          # FIX: Removed unused `field` import
from typing import Dict, FrozenSet, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

SUPPORTED_LANGUAGE = Literal[
    "English", "Chinese", "Japanese", "Korean",
    "German", "French", "Russian", "Portuguese", "Spanish", "Italian",
]

CJK_LANGUAGES: FrozenSet[str] = frozenset({"Chinese", "Japanese", "Korean"})

SPEAKING_RATES: Dict[str, int] = {
    "English":    150, "Chinese":    200, "Japanese":   200,
    "Korean":     180, "German":     140, "French":     150,
    "Russian":    140, "Portuguese": 150, "Spanish":    160,
    "Italian":    160,
}

DEFAULT_SPEAKING_RATE = 150

# ═══════════════════════════════════════════════════════════════════════════════
#  PRE-COMPILED REGEX PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

_SENTENCE_PATTERNS: Dict[str, re.Pattern] = {
    "English":    re.compile(r'([.!?]+)'),
    "Chinese":    re.compile(r'([。！？]+)'),
    "Japanese":   re.compile(r'([。！？]+)'),
    "Korean":     re.compile(r'([。！？]+)'),
    "German":     re.compile(r'([.!?]+)'),
    "French":     re.compile(r'([.!?]+)'),
    "Russian":    re.compile(r'([.!?]+)'),
    "Portuguese": re.compile(r'([.!?]+)'),
    "Spanish":    re.compile(r'([.!?]+)'),
    "Italian":    re.compile(r'([.!?]+)'),
}
_DEFAULT_SENTENCE_PATTERN = re.compile(r'([.!?。！？]+)')
_PAUSE_PATTERN = re.compile(r'([,;:、，；：])')


@dataclass
class ChunkingConfig:
    """Configuration for text chunking behavior."""
    max_chars: int = 500
    max_sentences: int = 5
    min_chunk_chars: int = 50
    overlap_chars: int = 0
    respect_sentence_bounds: bool = True
    language: str = "English"


class TextChunker:
    """Splits long text into TTS-friendly chunks while preserving sentence boundaries."""

    def __init__(self, config: Optional[ChunkingConfig] = None) -> None:
        self.config = config or ChunkingConfig()
        self._sentence_pattern = _SENTENCE_PATTERNS.get(
            self.config.language, _DEFAULT_SENTENCE_PATTERN
        )

    # ─── Public API ──────────────────────────────────────────────────────────

    def needs_chunking(self, text: str) -> bool:
        """Check if text exceeds the character or sentence limit for a single TTS call."""
        if not text:
            return False
        if len(text) > self.config.max_chars:
            return True
        sentences = self.split_into_sentences(text, self.config.language)
        return len(sentences) > self.config.max_sentences

    def chunk_text(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        """Split text into chunks suitable for TTS processing.

        Returns:
            A list of (chunk_text, estimated_duration) tuples.
        """
        if not text or not text.strip():
            return []

        text = text.strip()

        if len(text) <= self.config.max_chars:
            sentences = self.split_into_sentences(text, language)
            if not sentences or len(sentences) <= self.config.max_sentences:
                return [(text, self.estimate_speech_duration(text, language))]

        sentences = self.split_into_sentences(text, language)

        if not sentences:
            logger.debug(
                f"[Chunker] No sentence boundaries found for {len(text)}-char text "
                f"— falling back to hard split"
            )
            return self._hard_split(text, language)

        chunks = self._chunk_by_sentences(sentences, language)
        logger.debug(
            f"[Chunker] Split {len(text)}-char text into {len(chunks)} chunk(s) "
            f"(max_chars={self.config.max_chars})"
        )
        return chunks

    def split_into_sentences(self, text: str, language: str = "English") -> List[str]:
        """Split text into sentences using language-appropriate delimiters."""
        if not text or not text.strip():
            return []

        pattern = _SENTENCE_PATTERNS.get(language, self._sentence_pattern)

        parts = pattern.split(text)
        sentences: list[str] = []

        for i in range(0, len(parts) - 1, 2):
            sentence = parts[i].strip()
            if i + 1 < len(parts):
                sentence += parts[i + 1]
            if sentence.strip():
                sentences.append(sentence.strip())

        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())

        return sentences

    def estimate_speech_duration(self, text: str, language: str = "English") -> float:
        """Estimate audio duration in seconds based on word/character count."""
        rate = SPEAKING_RATES.get(language, DEFAULT_SPEAKING_RATE)

        if language in CJK_LANGUAGES:
            char_count = len(re.sub(r'\s', '', text))
            word_count = char_count / 3
        else:
            word_count = len(text.split())

        duration = (word_count / rate) * 60
        return duration + 0.5

    # ─── Private Helpers ─────────────────────────────────────────────────────

    def _chunk_by_sentences(
        self, sentences: List[str], language: str
    ) -> List[Tuple[str, float]]:
        """Group sentences into chunks respecting max_chars and max_sentences."""
        chunks: list[tuple[str, float]] = []
        current_chunk = ""
        current_sentences = 0

        for sentence in sentences:
            potential_chunk = (
                (current_chunk + " " + sentence).strip()
                if current_chunk
                else sentence
            )

            over_char_limit = len(potential_chunk) > self.config.max_chars
            over_sentence_limit = current_sentences >= self.config.max_sentences

            if over_char_limit or over_sentence_limit:
                if current_chunk:
                    chunks.append((
                        current_chunk.strip(),
                        self.estimate_speech_duration(current_chunk, language),
                    ))

                if len(sentence) > self.config.max_chars:
                    logger.debug(
                        f"[Chunker] Single sentence ({len(sentence)} chars) exceeds "
                        f"max_chars ({self.config.max_chars}) — using hard split"
                    )
                    chunks.extend(self._hard_split(sentence, language))
                    current_chunk = ""
                    current_sentences = 0
                else:
                    current_chunk = sentence
                    current_sentences = 1
            else:
                current_chunk = potential_chunk
                current_sentences += 1

        if current_chunk:
            if len(current_chunk) < self.config.min_chunk_chars and chunks:
                prev_text, _ = chunks[-1]
                combined = (prev_text + " " + current_chunk).strip()
                if len(combined) <= self.config.max_chars * 1.2:
                    chunks[-1] = (
                        combined,
                        self.estimate_speech_duration(combined, language),
                    )
                else:
                    chunks.append((
                        current_chunk.strip(),
                        self.estimate_speech_duration(current_chunk, language),
                    ))
            else:
                chunks.append((
                    current_chunk.strip(),
                    self.estimate_speech_duration(current_chunk, language),
                ))

        return chunks

    def _hard_split(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        """Fallback splitter for text with no sentence boundaries.

        Attempts to break at pause markers (commas, semicolons) or spaces.
        """
        chunks: list[tuple[str, float]] = []
        remaining = text

        while len(remaining) > self.config.max_chars:
            search_region = remaining[: self.config.max_chars]

            matches = list(_PAUSE_PATTERN.finditer(search_region))
            if matches:
                break_point = matches[-1].end()
                chunk = remaining[:break_point].strip()
            else:
                last_space = search_region.rfind(' ')
                if last_space > self.config.min_chunk_chars:
                    break_point = last_space
                    chunk = remaining[:break_point].strip()
                else:
                    break_point = self.config.max_chars
                    chunk = remaining[:break_point].strip()
                    logger.warning(
                        f"[Chunker] Forced hard split in middle of word/phrase "
                        f"at char {break_point} (no sentence boundary, comma, or space found)"
                    )

            if chunk:
                chunks.append((chunk, self.estimate_speech_duration(chunk, language)))
            remaining = remaining[break_point:].strip()

        if remaining:
            chunks.append((remaining, self.estimate_speech_duration(remaining, language)))

        return chunks


# ═══════════════════════════════════════════════════════════════════════════════
#  MODULE-LEVEL CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_chunker_for_language(language: str, max_chars: int = 500) -> TextChunker:
    """Factory function to get a configured TextChunker for a specific language."""
    config = ChunkingConfig(
        max_chars=max_chars,
        max_sentences=5 if language not in CJK_LANGUAGES else 3,
        min_chunk_chars=50,
        respect_sentence_bounds=True,
        language=language,
    )
    return TextChunker(config)


def chunk_text_for_tts(
    text: str, language: str = "English", max_chars: int = 500
) -> List[str]:
    """Split text into chunks, returning only the text strings (no durations)."""
    chunker = get_chunker_for_language(language, max_chars)
    return [chunk for chunk, _ in chunker.chunk_text(text, language)]