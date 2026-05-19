# backends/base.py
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Callable
import os
import tempfile

class BaseTTSBackend(ABC):
    """
    Abstract Base Class for all TTS Backends.
    
    All backend plugins (Qwen3, Bark, Tortoise, etc.) must inherit from this class
    and implement the defined methods to be compatible with the rendering engine.
    """
    
    def __init__(self, config: dict):
        """
        Initialize the backend with the application configuration.
        """
        self.config = config
        self._progress_callback: Optional[Callable] = None
        self._chunk_temp_dirs: List[str] = []

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