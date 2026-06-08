# backends/__init__.py
from .base import BaseTTSBackend
from .qwen3 import Qwen3Backend
from .edge import EdgeTTSBackend
from .omnivoice import OmniVoiceBackend

# Map of string names to classes
# Used by the UI to populate dropdowns and the engine to instantiate workers.
BACKEND_MAP = {
    "qwen3": Qwen3Backend,
    "edge": EdgeTTSBackend,
    "omnivoice": OmniVoiceBackend,
}


def get_backend_descriptions() -> dict:
    """
    Build a description dictionary from all registered backends.

    Returns:
        dict: Mapping of backend_name -> description string.
              Always includes a "default" key as fallback.
    """
    descriptions = {"default": BaseTTSBackend.DESCRIPTION}
    for name, cls in BACKEND_MAP.items():
        descriptions[name] = getattr(cls, "DESCRIPTION", BaseTTSBackend.DESCRIPTION)
    return descriptions

# Keys that are not owned by any single backend but still affect
# whether cached audio should be considered stale.
_GLOBAL_AUDIO_SETTINGS_KEYS = [
    "active_backend",
]

def get_all_audio_settings_keys() -> list:
    """
    Collect every config key that can invalidate cached audio.

    Combines:
      - Global keys (e.g. "active_backend")
      - Per-backend keys declared in each backend's AUDIO_SETTINGS_KEYS

    Duplicates are removed while preserving order.

    Returns:
        list[str]: Deduplicated list of config key names.
    """
    seen = set()
    result = []

    # Global keys first
    for key in _GLOBAL_AUDIO_SETTINGS_KEYS:
        if key not in seen:
            seen.add(key)
            result.append(key)

    # Per-backend keys
    for name, cls in BACKEND_MAP.items():
        for key in getattr(cls, "AUDIO_SETTINGS_KEYS", []):
            if key not in seen:
                seen.add(key)
                result.append(key)

    return result

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