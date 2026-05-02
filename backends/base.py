# backends/base.py
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Callable

class BaseTTSBackend(ABC):
    """
    Abstract Base Class for all TTS Backends.
    
    All backend plugins (Qwen3, Bark, Tortoise, etc.) must inherit from this class
    and implement the defined methods to be compatible with the rendering engine.
    """
    
    def __init__(self, config: dict):
        """
        Initialize the backend with the application configuration.
        
        Args:
            config (dict): The global configuration dictionary containing settings
                           like device map, data types, and model paths.
        """
        self.config = config
        self._progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Optional[Callable[[str, int, int], None]]):
        """
        Set a callback function to report generation progress.
        
        Args:
            callback: A function that takes (stage_name, current, total) parameters
                     Example: callback("chunk", 2, 5) means processing chunk 2 of 5
        """
        self._progress_callback = callback

    def _report_progress(self, stage: str, current: int, total: int, message: str = ""):
        """
        Internal method to report progress if a callback is set.
        
        Args:
            stage: Progress stage name (e.g., "chunk", "slide")
            current: Current item number
            total: Total items
            message: Optional additional message
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