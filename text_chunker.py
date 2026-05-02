# text_chunker.py
"""
Text Chunking Utility for TTS Systems.

Handles long text input by splitting into manageable segments while
preserving sentence boundaries and natural speech patterns.
"""

import re
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger("AIRenderer")


@dataclass
class ChunkingConfig:
    """Configuration for text chunking behavior."""
    max_chars: int = 500          # Maximum characters per chunk
    max_sentences: int = 5        # Maximum sentences per chunk
    min_chunk_chars: int = 50     # Minimum characters to form a chunk
    overlap_chars: int = 0        # Characters to overlap between chunks (for context)
    respect_sentence_bounds: bool = True  # Split at sentence boundaries
    language: str = "English"     # Language for sentence detection


class TextChunker:
    """
    Intelligent text chunker that splits long texts into TTS-friendly segments.
    
    Features:
    - Respects sentence boundaries
    - Handles multiple languages
    - Preserves punctuation for natural speech
    - Configurable chunk sizes
    """
    
    # Language-specific sentence ending patterns
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
    
    # Pause markers that indicate natural speech breaks
    PAUSE_MARKERS = r'[,;:、，；：]'
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()
    
    def get_sentence_pattern(self, language: str) -> str:
        """Get the appropriate sentence ending pattern for a language."""
        return self.SENTENCE_ENDINGS.get(language, self.SENTENCE_ENDINGS["default"])
    
    def split_into_sentences(self, text: str, language: str = "English") -> List[str]:
        """
        Split text into sentences while preserving punctuation.
        
        Args:
            text: Input text to split
            language: Language for sentence detection
            
        Returns:
            List of sentences with their ending punctuation
        """
        if not text or not text.strip():
            return []
        
        # Get language-specific pattern
        pattern = self.get_sentence_pattern(language)
        
        # Split on sentence endings while keeping the delimiter
        sentences = []
        current_sentence = ""
        
        # Use regex to find sentence boundaries
        parts = re.split(f'({pattern})', text)
        
        for i in range(0, len(parts) - 1, 2):
            sentence = parts[i].strip()
            if i + 1 < len(parts):
                sentence += parts[i + 1]  # Add the punctuation back
            
            if sentence.strip():
                sentences.append(sentence.strip())
        
        # Handle any remaining text without ending punctuation
        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())
        
        return sentences
    
    def estimate_speech_duration(self, text: str, language: str = "English") -> float:
        """
        Estimate speech duration in seconds based on text length.
        
        Args:
            text: Input text
            language: Language for speech rate estimation
            
        Returns:
            Estimated duration in seconds
        """
        # Average speaking rates (words per minute)
        SPEAKING_RATES = {
            "English": 150,
            "Chinese": 200,
            "Japanese": 200,
            "Korean": 180,
            "German": 140,
            "French": 150,
            "Russian": 140,
            "Portuguese": 150,
            "Spanish": 160,
            "Italian": 160,
            "default": 150
        }
        
        rate = SPEAKING_RATES.get(language, SPEAKING_RATES["default"])
        
        # Count words (for Latin scripts) or characters (for CJK)
        if language in ["Chinese", "Japanese", "Korean"]:
            # For CJK languages, count characters
            char_count = len(re.sub(r'\s', '', text))
            # Approximate: 3 characters per "word" equivalent
            word_count = char_count / 3
        else:
            # For Latin scripts, count words
            word_count = len(text.split())
        
        # Duration in seconds
        duration = (word_count / rate) * 60
        
        # Add small buffer for pauses
        return duration + 0.5
    
    def needs_chunking(self, text: str) -> bool:
        """
        Determine if text needs to be chunked.
        
        Args:
            text: Input text
            
        Returns:
            True if text exceeds configured limits
        """
        if not text:
            return False
        return len(text) > self.config.max_chars
    
    def chunk_text(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        """
        Split text into chunks suitable for TTS processing.
        
        Args:
            text: Input text to chunk
            language: Language for sentence detection
            
        Returns:
            List of tuples (chunk_text, estimated_duration)
        """
        if not text or not text.strip():
            return []
        
        text = text.strip()
        
        # If text is short enough, return as-is
        if len(text) <= self.config.max_chars:
            duration = self.estimate_speech_duration(text, language)
            return [(text, duration)]
        
        logger.info(f"[TextChunker] Text length {len(text)} exceeds max {self.config.max_chars}, chunking...")
        
        # Split into sentences
        sentences = self.split_into_sentences(text, language)
        
        if not sentences:
            # Fallback: hard split by character limit
            logger.warning("[TextChunker] Could not split into sentences, using hard split")
            return self._hard_split(text, language)
        
        # Group sentences into chunks
        chunks = []
        current_chunk = ""
        current_sentences = 0
        
        for sentence in sentences:
            # Check if adding this sentence would exceed limits
            potential_chunk = current_chunk + " " + sentence if current_chunk else sentence
            
            if (len(potential_chunk) > self.config.max_chars or 
                current_sentences >= self.config.max_sentences):
                
                # Save current chunk if it has content
                if current_chunk and len(current_chunk) >= self.config.min_chunk_chars:
                    duration = self.estimate_speech_duration(current_chunk, language)
                    chunks.append((current_chunk.strip(), duration))
                    current_chunk = sentence
                    current_sentences = 1
                else:
                    # Current chunk too small, try to add anyway or handle very long sentence
                    if len(sentence) > self.config.max_chars:
                        # Very long sentence - need hard split
                        if current_chunk:
                            duration = self.estimate_speech_duration(current_chunk, language)
                            chunks.append((current_chunk.strip(), duration))
                        
                        # Hard split the long sentence
                        sub_chunks = self._hard_split(sentence, language)
                        chunks.extend(sub_chunks)
                        current_chunk = ""
                        current_sentences = 0
                    else:
                        current_chunk = potential_chunk
                        current_sentences += 1
            else:
                current_chunk = potential_chunk
                current_sentences += 1
        
        # Don't forget the last chunk
        if current_chunk and len(current_chunk) >= self.config.min_chunk_chars:
            duration = self.estimate_speech_duration(current_chunk, language)
            chunks.append((current_chunk.strip(), duration))
        elif current_chunk:
            # Small remaining chunk - append to previous if possible
            if chunks:
                prev_text, prev_duration = chunks[-1]
                combined = prev_text + " " + current_chunk
                if len(combined) <= self.config.max_chars * 1.2:  # Allow 20% overflow
                    duration = self.estimate_speech_duration(combined, language)
                    chunks[-1] = (combined.strip(), duration)
                else:
                    duration = self.estimate_speech_duration(current_chunk, language)
                    chunks.append((current_chunk.strip(), duration))
            else:
                duration = self.estimate_speech_duration(current_chunk, language)
                chunks.append((current_chunk.strip(), duration))
        
        logger.info(f"[TextChunker] Split into {len(chunks)} chunks")
        for i, (chunk, dur) in enumerate(chunks):
            logger.debug(f"  Chunk {i+1}: {len(chunk)} chars, ~{dur:.1f}s")
        
        return chunks
    
    def _hard_split(self, text: str, language: str = "English") -> List[Tuple[str, float]]:
        """
        Hard split text by character limit, trying to break at natural points.
        
        Args:
            text: Text to split
            language: Language for duration estimation
            
        Returns:
            List of (chunk, duration) tuples
        """
        chunks = []
        
        # Try to break at pause markers first
        pause_pattern = self.PAUSE_MARKERS
        
        remaining = text
        while len(remaining) > self.config.max_chars:
            # Look for a break point within the limit
            search_region = remaining[:self.config.max_chars]
            
            # Try to find a pause marker
            matches = list(re.finditer(f'({pause_pattern})', search_region))
            
            if matches:
                # Break at the last pause marker within limit
                break_point = matches[-1].end()
                chunk = remaining[:break_point].strip()
            else:
                # No pause marker, try to break at space
                last_space = search_region.rfind(' ')
                if last_space > self.config.min_chunk_chars:
                    break_point = last_space
                    chunk = remaining[:break_point].strip()
                else:
                    # Hard break at max_chars
                    break_point = self.config.max_chars
                    chunk = remaining[:break_point].strip()
            
            if chunk:
                duration = self.estimate_speech_duration(chunk, language)
                chunks.append((chunk, duration))
            
            remaining = remaining[break_point:].strip()
        
        if remaining:
            duration = self.estimate_speech_duration(remaining, language)
            chunks.append((remaining, duration))
        
        return chunks


def get_chunker_for_language(language: str, max_chars: int = 500) -> TextChunker:
    """
    Factory function to create a TextChunker configured for a specific language.
    
    Args:
        language: Target language
        max_chars: Maximum characters per chunk
        
    Returns:
        Configured TextChunker instance
    """
    config = ChunkingConfig(
        max_chars=max_chars,
        max_sentences=5 if language in ["English", "German", "French", "Russian", 
                                         "Portuguese", "Spanish", "Italian"] else 3,
        min_chunk_chars=50,
        respect_sentence_bounds=True,
        language=language
    )
    return TextChunker(config)


# Convenience function for direct use
def chunk_text_for_tts(text: str, language: str = "English", max_chars: int = 500) -> List[str]:
    """
    Split text into chunks suitable for TTS.
    
    Args:
        text: Input text
        language: Language for sentence detection
        max_chars: Maximum characters per chunk
        
    Returns:
        List of text chunks
    """
    chunker = get_chunker_for_language(language, max_chars)
    chunks_with_duration = chunker.chunk_text(text, language)
    return [chunk for chunk, _ in chunks_with_duration]
