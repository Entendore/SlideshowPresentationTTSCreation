# backends/__init__.py
from .base import BaseTTSBackend
from .qwen3 import Qwen3Backend
from .edge import EdgeTTSBackend

# Map of string names to classes
# Used by the UI to populate dropdowns and the engine to instantiate workers.
BACKEND_MAP = {
    "qwen3": Qwen3Backend,
    "edge": EdgeTTSBackend,
    # Future backends can be added here, e.g.:
    # "bark": BarkBackend,
    # "tortoise": TortoiseBackend,
}

def get_backend(backend_name: str, config: dict) -> BaseTTSBackend:
    """
    Factory function to return an initialized backend instance.
    
    Args:
        backend_name (str): The key for the backend (e.g., "qwen3").
        config (dict): The application configuration dictionary.
        
    Returns:
        BaseTTSBackend: An initialized backend instance.
        
    Raises:
        ValueError: If the backend name is not found in BACKEND_MAP.
        RuntimeError: If the backend fails to initialize (e.g., missing libraries, OOM).
    """
    backend_class = BACKEND_MAP.get(backend_name)
    
    if not backend_class:
        available = list(BACKEND_MAP.keys())
        raise ValueError(f"Backend '{backend_name}' not found. Available backends: {available}")
    
    # Instantiate the backend class
    instance = backend_class(config)
    
    # Initialize (Load Model into VRAM)
    try:
        # The initialize method handles model loading and hardware setup
        instance.initialize()
        return instance
    except Exception as e:
        # CRITICAL: If initialization fails, ensure we attempt cleanup to prevent VRAM leaks
        # (though instance.model is likely None if init failed, it's good practice)
        if hasattr(instance, 'cleanup'):
            try:
                instance.cleanup()
            except Exception:
                pass # Suppress cleanup errors during crash handling
        
        # Re-raise as RuntimeError for the engine to catch
        raise RuntimeError(f"Failed to initialize backend '{backend_name}': {e}")