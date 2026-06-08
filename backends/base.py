# backends/base.py
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Callable
import os
import tempfile
from utils import logger
from text_chunker import TextChunker, ChunkingConfig


class BaseTTSBackend(ABC):
    """
    Abstract Base Class for all TTS Backends.
    
    All backend plugins (Qwen3, Bark, Tortoise, etc.) must inherit from this class
    and implement the defined methods to be compatible with the rendering engine.
    """

    DESCRIPTION: str = "Generic backend implementation."

    AUDIO_SETTINGS_KEYS: list = []
    
    def __init__(self, config: dict):
        """
        Initialize the backend with the application configuration.
        """
        self.config = config
        self._progress_callback: Optional[Callable] = None
        self._chunk_temp_dirs: List[str] = []

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

    def set_progress_callback(self, callback: Optional[Callable[[str, int, int], None]]):
        """
        Set a callback function to report generation progress.
        """
        self._progress_callback = callback

    def _get_local_temp_dir(self, subdir: str = "chunks") -> str:
        """
        Get a local temp directory for intermediate files.
        
        Uses the engine's local temp dir (passed via config['_temp_dir']) 
        instead of the system temp directory. Falls back to system temp
        if no local temp is configured.
        """
        base_temp = self.config.get('_temp_dir', '')
        if base_temp and os.path.isdir(base_temp):
            temp_dir = os.path.join(base_temp, subdir)
            os.makedirs(temp_dir, exist_ok=True)
        else:
            # Fallback to system temp (shouldn't happen in normal operation)
            temp_dir = tempfile.mkdtemp(prefix=f"tts_{subdir}_")
        
        self._chunk_temp_dirs.append(temp_dir)
        return temp_dir
    
    def _cleanup_chunk_temp(self):
        """
        Clean up all chunk temp directories created during generation.
        
        For local temp dirs (inside engine's temp), only removes the 
        chunk subdirectory contents. The engine's finally block handles 
        the full cleanup of the base temp dir.
        """
        import shutil
        base_temp = self.config.get('_temp_dir', '')
        
        for chunk_dir in self._chunk_temp_dirs:
            try:
                if not os.path.exists(chunk_dir):
                    continue
                    
                if base_temp and chunk_dir.startswith(base_temp):
                    # Local temp: remove chunk subdirectory (safe, won't affect parent)
                    shutil.rmtree(chunk_dir, ignore_errors=True)
                else:
                    # System temp: remove entirely
                    shutil.rmtree(chunk_dir, ignore_errors=True)
            except Exception:
                pass
        
        self._chunk_temp_dirs.clear()

    def _report_progress(self, stage: str, current: int, total: int, message: str = ""):
        """
        Internal method to report progress if a callback is set.
        """
        if self._progress_callback:
            self._progress_callback(stage, current, total, message)

    @abstractmethod
    def initialize(self):
        """
        Load models into memory (VRAM/RAM).
        
        This method is called once before generation begins. It should handle:
        - Downloading models if not present.
        - Loading models onto the correct device (CPU/GPU).
        - Setting up any necessary processors or tokenizers.
        """
        pass

    def _validate_batch_inputs(self, texts: List[str], output_paths: List[str]):
        """Validate batch inputs before generation. Call at the start of generate_batch."""
        if len(texts) != len(output_paths):
            raise ValueError(
                f"texts and output_paths must have the same length: "
                f"{len(texts)} != {len(output_paths)}"
            )
        
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            raise ValueError("Cannot generate audio: all texts are empty or whitespace only")
        
        # Ensure all output directories exist
        for path in output_paths:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

    @abstractmethod
    def generate_batch(self, texts: List[str], output_paths: List[str]) -> Tuple[bool, List[str]]:
        """
        Generate audio for a list of texts.
        
        Args:
            texts (List[str]): A list of strings to convert to speech.
                              IMPORTANT: Empty or whitespace-only texts are NOT allowed.
                              Implementations MUST raise ValueError if all texts are empty.
            output_paths (List[str]): A list of corresponding file paths where the 
                                      generated .wav files should be saved.
        
        Returns:
            Tuple[bool, List[str]]: 
                - bool: True if the entire batch was successful, False otherwise.
                - List[str]: A list of error messages. If successful, this should be empty.
                             If failed, it should contain error strings corresponding to inputs.
        
        Raises:
            ValueError: If all texts are empty or whitespace-only.
        """
        pass

    def _generate_single_with_chunking(
        self,
        text: str,
        output_path: str,
        slide_index: int,
        language: str,
        chunker: Optional[TextChunker],
        generate_single_fn: Callable[[str, str], bool],
        concatenate_fn: Callable[[List[str], str], bool],
        temp_dir: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Generate audio for one text item, handling chunking if needed.
        
        Returns (success, error_message_or_None).
        """
        if not text or not text.strip():
            return False, "Empty text"

        needs_chunking = chunker and chunker.needs_chunking(text)

        if needs_chunking:
            logger.info(f"[Backend] Slide {slide_index + 1}: Long text ({len(text)} chars), chunking...")
            chunks = chunker.chunk_text(text, language)
            total_chunks = len(chunks)
            logger.info(f"[Backend] Slide {slide_index + 1}: Split into {total_chunks} chunks")

            chunk_paths = []
            for chunk_idx, (chunk_text, estimated_duration) in enumerate(chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{slide_index}_{chunk_idx}.wav")
                self._report_progress("chunk", chunk_idx + 1, total_chunks,
                                      f"Chunk {chunk_idx + 1}/{total_chunks}")
                try:
                    if generate_single_fn(chunk_text, chunk_path):
                        chunk_paths.append(chunk_path)
                    else:
                        return False, f"Failed to generate chunk {chunk_idx + 1}"
                except Exception as e:
                    return False, f"Chunk {chunk_idx + 1}: {e}"

            if len(chunk_paths) == len(chunks):
                if concatenate_fn(chunk_paths, output_path):
                    # Cleanup chunk files
                    for cp in chunk_paths:
                        try:
                            os.remove(cp)
                        except OSError:
                            pass
                    return True, None
                else:
                    return False, f"Failed to concatenate chunks for slide {slide_index + 1}"
            else:
                return False, f"Only {len(chunk_paths)}/{total_chunks} chunks generated"
        else:
            try:
                if generate_single_fn(text, output_path):
                    return True, None
                else:
                    return False, f"Failed to generate audio for slide {slide_index + 1}"
            except Exception as e:
                return False, str(e)

    @abstractmethod
    def cleanup(self):
        """
        Unload models and free resources.
        
        This method is called after generation is complete or if the application
        needs to switch backends. It must:
        - Delete model references.
        - Invoke garbage collection.
        - Empty CUDA caches to free up VRAM for other processes.
        """
        pass