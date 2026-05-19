# text_chunker.py
"""
Text Chunking Utility for TTS Systems.

Handles long text input by splitting into manageable segments while
preserving sentence boundaries and natural speech patterns.
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass

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

    SENTENCE_ENDINGS = {
        "English": r'[.!?]+',
        "Chinese": r'[。！？]+',
        "Japanese": r'[。！？]+',
        "Korean": r'[。！？]+',
        "German": r'[.!?]+',
        "French": r'[.!?]+',
        "Russian": r'[.!?]+',
        "Portuguese": r'[.!?]+',
        "Spanish": r'[.!?]+',
        "Italian": r'[.!?]+',
        "default": r'[.!?。！？]+'
    }

    PAUSE_MARKERS = r'[,;:、，；：]'

    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()

    def get_sentence_pattern(self, language: str) -> str:
        return self.SENTENCE_ENDINGS.get(language, self.SENTENCE_ENDINGS["default"])

    def split_into_sentences(self, text: str, language: str = "English") -> List[str]:
        if not text or not text.strip():
            return []

        pattern = self.get_sentence_pattern(language)
        sentences = []
        parts = re.split(f'({pattern})', text)

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
        SPEAKING_RATES = {
            "English": 150, "Chinese": 200, "Japanese": 200,
            "Korean": 180, "German": 140, "French": 150,
            "Russian": 140, "Portuguese": 150, "Spanish": 160,
            "Italian": 160, "default": 150
        }
        rate = SPEAKING_RATES.get(language, SPEAKING_RATES["default"])
        if language in ["Chinese", "Japanese", "Korean"]:
            char_count = len(re.sub(r'\s', '', text))
            word_count = char_count / 3
        else:
            word_count = len(text.split())
        duration = (word_count / rate) * 60
        return duration + 0.5

    def needs_chunking(self, text: str) -> bool:
        if not text:
            return False
        return len(text) > self.config.max_chars

    def chunk_text(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        """
        Split text into chunks suitable for TTS processing.
        Always returns a list of (chunk_text, estimated_duration) tuples.
        """
        # ── Guard: empty input ──
        if not text or not text.strip():
            return []

        text = text.strip()

        # ── Check if text is short enough to return as-is ──
        if len(text) <= self.config.max_chars:
            sentences = self.split_into_sentences(text, language)
            if not sentences or len(sentences) <= self.config.max_sentences:
                return [(text, self.estimate_speech_duration(text, language))]
            # Short on chars but too many sentences — must re-chunk by sentences

        # ── Split into sentences ──
        sentences = self.split_into_sentences(text, language)

        if not sentences:
            return self._hard_split(text, language)

        # ── Group sentences into chunks ──
        chunks: List[Tuple[str, float]] = []
        current_chunk = ""
        current_sentences = 0

        for sentence in sentences:
            potential_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence

            # Check if adding this sentence would exceed a limit
            over_char_limit = len(potential_chunk) > self.config.max_chars
            over_sentence_limit = current_sentences >= self.config.max_sentences

            if over_char_limit or over_sentence_limit:
                # Flush current chunk regardless of its size —
                # min_chunk_chars only applies to the FINAL trailing piece
                if current_chunk:
                    chunks.append((current_chunk.strip(),
                                   self.estimate_speech_duration(current_chunk, language)))

                # Start new chunk with this sentence
                if len(sentence) > self.config.max_chars:
                    # Sentence itself is too long — hard-split it
                    chunks.extend(self._hard_split(sentence, language))
                    current_chunk = ""
                    current_sentences = 0
                else:
                    current_chunk = sentence
                    current_sentences = 1
            else:
                current_chunk = potential_chunk
                current_sentences += 1

        # ── Handle remaining text after the loop ──
        if current_chunk:
            if len(current_chunk) >= self.config.min_chunk_chars:
                chunks.append((current_chunk.strip(),
                               self.estimate_speech_duration(current_chunk, language)))
            else:
                # Too small — try merging into the previous chunk
                if chunks:
                    prev_text, prev_duration = chunks[-1]
                    combined = (prev_text + " " + current_chunk).strip()
                    if len(combined) <= self.config.max_chars * 1.2:
                        chunks[-1] = (combined,
                                      self.estimate_speech_duration(combined, language))
                    else:
                        chunks.append((current_chunk.strip(),
                                       self.estimate_speech_duration(current_chunk, language)))
                else:
                    chunks.append((current_chunk.strip(),
                                   self.estimate_speech_duration(current_chunk, language)))

        return chunks

    def _hard_split(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        chunks: List[Tuple[str, float]] = []
        pause_pattern = self.PAUSE_MARKERS
        remaining = text

        while len(remaining) > self.config.max_chars:
            search_region = remaining[:self.config.max_chars]
            matches = list(re.finditer(f'({pause_pattern})', search_region))
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
            if chunk:
                chunks.append((chunk, self.estimate_speech_duration(chunk, language)))
            remaining = remaining[break_point:].strip()

        if remaining:
            chunks.append((remaining, self.estimate_speech_duration(remaining, language)))

        return chunks


def get_chunker_for_language(language: str, max_chars: int = 500) -> TextChunker:
    config = ChunkingConfig(
        max_chars=max_chars,
        max_sentences=5 if language in ["English", "German", "French", "Russian",
                                         "Portuguese", "Spanish", "Italian"] else 3,
        min_chunk_chars=50,
        respect_sentence_bounds=True,
        language=language
    )
    return TextChunker(config)


def chunk_text_for_tts(text: str, language: str = "English", max_chars: int = 500) -> List[str]:
    chunker = get_chunker_for_language(language, max_chars)
    chunks_with_duration = chunker.chunk_text(text, language)
    return [chunk for chunk, _ in chunks_with_duration]