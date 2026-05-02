# config.py
import os
import json
from utils import logger

class AppConfig:
    """
    Application Configuration Manager.
    Implements a Singleton pattern to ensure consistent settings across the app.
    Auto-saves to settings.json on change and auto-loads on instantiation.
    """
    _instance = None

    DEFAULT_CONFIG = {
        # ------------------------------------------------------------------
        # General & Video Settings
        # ------------------------------------------------------------------
        "width": 1280,
        "height": 720,
        "fps": 30,
        "encoder": "auto",
        "preset": "fast",
        "transition_duration": 0.5,
        
        # ------------------------------------------------------------------
        # Project Management
        # ------------------------------------------------------------------
        "projects_root": "Projects",
        "output_dir": "Output",
        
        # ------------------------------------------------------------------
        # Render Settings
        # ------------------------------------------------------------------
        "render_workers": 3,
        "enable_zoom": False,
        "zoom_factor": 1.1,
        
        # ------------------------------------------------------------------
        # HUGGING FACE CACHE & DATASETS
        # ------------------------------------------------------------------
        "hf_cache_dir": "E:\\cacheAI", 
        "hf_datasets_dir": "E:\\cacheAI",
        "hf_use_symlinks": False,
        
        # ------------------------------------------------------------------
        # ACTIVE BACKEND
        # ------------------------------------------------------------------
        "active_backend": "qwen3",

        # ------------------------------------------------------------------
        # QWEN3 BACKEND SETTINGS
        # ------------------------------------------------------------------
        # Modes: "voice_clone", "voice_design", "voice_custom"
        "qwen3_mode": "voice_custom", 
        "qwen3_size": "1.7B", 
        
        "qwen3_device_map": "cuda:0",
        "qwen3_dtype": "float16",
        "qwen3_attn_implementation": "flash_attention_2", 
        
        # Model IDs
        "qwen3_base_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "qwen3_voicedesign_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "qwen3_custom_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",

        # Voice Custom Settings
        "qwen3_speaker": "Vivian",
        "qwen3_instruct": "",

        # Voice Clone Settings
        "voice_references_root": "voicereferences", 
        "qwen3_ref_audio": "", 
        "qwen3_ref_text": "",
        
        # Voice Design Settings
        "qwen3_vd_description": "", # Description for generating voice
        "qwen3_vd_save_name": "my_designed_voice", # Filename to save as reference
        
        # Common
        "qwen3_language": "English",

        # ------------------------------------------------------------------
        # EDGE TTS BACKEND SETTINGS
        # ------------------------------------------------------------------
        # Edge TTS is a free online TTS service from Microsoft Edge
        # Requires internet connection
        "edge_language": "English",           # Language for voice selection
        "edge_voice": "en-US-JennyNeural",    # Voice ID (format: locale-VoiceNameNeural)
        "edge_rate": "+0%",                   # Speech rate: -100% to +100%
        "edge_pitch": "+0Hz",                 # Pitch adjustment: -50Hz to +50Hz
        "edge_volume": "+0%",                 # Volume: -100% to +100% (relative, NOT absolute)

        # ------------------------------------------------------------------
        # TEXT CHUNKING SETTINGS (Long Text Handling)
        # ------------------------------------------------------------------
        "enable_text_chunking": True,        # Enable automatic text chunking
        "chunk_max_chars": 500,              # Maximum characters per chunk
        "chunk_max_sentences": 5,            # Maximum sentences per chunk
        "chunk_min_chars": 50,               # Minimum characters to form a chunk
        "chunk_audio_overlap_ms": 0,         # Audio overlap between chunks (ms) - future
        "chunk_warn_threshold": 1000,        # Warn if text exceeds this length
    }

    def __new__(cls):
        """Singleton implementation: ensures only one instance exists."""
        if cls._instance is None:
            cls._instance = super(AppConfig, cls).__new__(cls)
            cls._instance.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
            cls._instance.settings = cls.DEFAULT_CONFIG.copy()
            cls._instance.load()
        return cls._instance

    def load(self):
        """Load configuration from settings.json."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self.settings.update(loaded)
                logger.info("Configuration loaded successfully.")
            except Exception as e:
                logger.error(f"Error loading config: {e}")

    def save(self):
        """Save current configuration to settings.json."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def get(self, key, default=None):
        """Get a configuration value."""
        return self.settings.get(key, default)

    def set(self, key, value):
        """
        Set a configuration value and auto-save immediately.
        This ensures settings persist across reloads.
        """
        self.settings[key] = value
        self.save()

    def update(self, data):
        """Update multiple configuration values and auto-save."""
        self.settings.update(data)
        self.save()