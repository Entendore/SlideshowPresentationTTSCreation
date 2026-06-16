# config.py
"""
Application Configuration Manager.

Singleton pattern with thread-safe initialization, auto-save on mutation,
and resolution validation (even dimensions required by FFmpeg yuv420p).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AppConfig:
    _instance: Optional[AppConfig] = None
    _initialized: bool = False
    APP_ROOT: str = os.path.dirname(os.path.abspath(__file__))

    # ─── Resolution presets ──────────────────────────────────────────────────

    RESOLUTION_PRESETS: Dict[str, Tuple[int, int]] = {
        "360p  (640×360)":   (640,   360),
        "480p  (854×480)":   (854,   480),
        "720p  (1280×720)":  (1280,  720),
        "1080p (1920×1080)": (1920,  1080),
        "1440p (2560×1440)": (2560,  1440),
        "4K    (3840×2160)": (3840,  2160),
    }

    ASPECT_RATIOS: Dict[str, Tuple[int, int]] = {
        "16:9": (16, 9),
        "4:3":  (4,  3),
        "1:1":  (1,  1),
        "21:9": (21, 9),
    }

    # ─── Defaults ────────────────────────────────────────────────────────────

    DEFAULT_CONFIG: Dict[str, Any] = {
        # General & Video
        "width": 1280, "height": 720, "fps": 30,
        "encoder": "auto", "preset": "fast",
        "transition_duration": 0.5, "theme": "Dark",
        # Project Management
        "projects_root": "Projects", "output_dir": "Output",
        # Render
        "render_workers": 3,
        # Hugging Face
        "hf_cache_dir": "E:\\cacheAI",
        "hf_datasets_dir": "E:\\cacheAI",
        "hf_use_symlinks": False,
        "instruction_folder_root": "",
        # Active Backend
        "active_backend": "qwen3",
        # Qwen3
        "qwen3_mode": "voice_custom",
        "qwen3_size": "1.7B",
        "qwen3_device_map": "cuda:0",
        "qwen3_dtype": "float16",
        "qwen3_attn_implementation": "flash_attention_2",
        "qwen3_base_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "qwen3_voicedesign_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "qwen3_custom_model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "qwen3_speaker": "Vivian",
        "qwen3_instruct": "",
        "voice_references_root": "voicereferences",
        "qwen3_ref_audio": "", "qwen3_ref_text": "",
        "qwen3_vd_description": "", "qwen3_vd_save_name": "my_designed_voice",
        "qwen3_language": "English",
        # Edge TTS
        "edge_language": "English", "edge_voice": "en-US-JennyNeural",
        "edge_rate": "+0%", "edge_pitch": "+0Hz", "edge_volume": "+0%",
        # OmniVoice
        "omnivoice_mode": "auto",
        "omnivoice_model_id": "k2-fsa/OmniVoice",
        "omnivoice_language": "English",
        "omnivoice_ref_audio": "", "omnivoice_ref_text": "",
        "omnivoice_instruct": "", "omnivoice_use_batch": True,
        # Text Chunking
        "enable_text_chunking": True, "chunk_max_chars": 500,
        "chunk_max_sentences": 5, "chunk_min_chars": 50,
        "chunk_audio_overlap_ms": 0, "chunk_warn_threshold": 1000,
        # Silent / Image
        "silent_slide_duration": 5.0, "pdf_import_dpi": 200,
    }

    _init_lock = threading.Lock()

    # ─── Singleton ───────────────────────────────────────────────────────────

    def __new__(cls) -> AppConfig:
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._initialized = False
            return cls._instance

    def __init__(self) -> None:
        with AppConfig._init_lock:
            if AppConfig._initialized:
                return
            self._lock = threading.Lock()
            self.config_path = str(Path(__file__).parent / "settings.json")
            self.settings: dict = copy.deepcopy(self.DEFAULT_CONFIG)
            self.load()
            self._sanitize_and_fix_resolution()
            AppConfig._initialized = True

    # ─── Resolution helpers ─────────────────────────────────────────────────

    @staticmethod
    def sanitize_resolution(width: int, height: int) -> Tuple[int, int]:
        """Ensure width and height are positive even integers (FFmpeg yuv420p)."""
        width  = max(64, int(width))
        height = max(64, int(height))
        if width  % 2: width  += 1
        if height % 2: height += 1
        return width, height

    def _sanitize_and_fix_resolution(self) -> None:
        w, h = self.sanitize_resolution(
            self.settings.get("width", 1280),
            self.settings.get("height", 720),
        )
        if w != self.settings.get("width") or h != self.settings.get("height"):
            logger.warning(
                f"[Settings] Resolution sanitized: "
                f"{self.settings.get('width')}×{self.settings.get('height')} → {w}×{h}"
            )
            self.settings["width"]  = w
            self.settings["height"] = h
            self.save()

    def set_resolution(self, width: int, height: int) -> None:
        """Set width + height atomically with validation."""
        width, height = self.sanitize_resolution(width, height)
        old_w, old_h = self.settings.get("width"), self.settings.get("height")
        self.settings["width"]  = width
        self.settings["height"] = height
        if old_w != width or old_h != height:
            logger.info(f"[Settings] Resolution: {old_w}×{old_h} → {width}×{height}")
        self.save()

    @classmethod
    def get_height_for_aspect_ratio(cls, width: int, ratio_name: str = "16:9") -> int:
        rw, rh = cls.ASPECT_RATIOS.get(ratio_name, (16, 9))
        _, height = cls.sanitize_resolution(width, int(width * rh / rw))
        return height

    @classmethod
    def get_width_for_aspect_ratio(cls, height: int, ratio_name: str = "16:9") -> int:
        rw, rh = cls.ASPECT_RATIOS.get(ratio_name, (16, 9))
        width, _ = cls.sanitize_resolution(int(height * rw / rh), height)
        return width

    # ─── Core methods ────────────────────────────────────────────────────────

    def load(self) -> None:
        path = Path(self.config_path)
        if not path.exists():
            self.save()
            return
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            merged = copy.deepcopy(self.DEFAULT_CONFIG)
            merged.update(loaded)
            self.settings = merged
            logger.info("[Settings] Configuration loaded.")
        except Exception as exc:
            logger.error(f"[Settings] Load error: {exc}")

    def save(self) -> None:
        try:
            with self._lock:
                path = Path(self.config_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(self.settings, indent=4, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.error(f"[Settings] Save error: {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value with validation and auto-save."""
        if key in ("width", "height"):
            try:
                value = int(value)
            except (ValueError, TypeError):
                logger.error(f"[Settings] Invalid {key} value: {value!r}")
                return
            value = max(64, value)
            if value % 2:
                adjusted = value + 1
                logger.warning(
                    f"[Settings] Adjusted '{key}' {value} → {adjusted} (even required)"
                )
                value = adjusted

        with self._lock:
            old_value = self.settings.get(key)
            self.settings[key] = value
            changed = (old_value != value)

        # IMPROVEMENT: Only log and save if the value actually changed,
        # preventing redundant disk I/O from UI slider spam.
        if changed:
            def _fmt(v: Any, max_len: int = 80) -> str:
                s = repr(v)
                return s if len(s) <= max_len else s[: max_len - 3] + "..."
            logger.info(
                f"[Settings] Changed '{key}': {_fmt(old_value)} → {_fmt(value)}"
            )
            self.save()

    def update(self, data: dict) -> None:
        """Batch-update with resolution validation."""
        for dim_key in ("width", "height"):
            if dim_key in data:
                try:
                    data[dim_key] = int(data[dim_key])
                except (ValueError, TypeError):
                    logger.error(
                        f"[Settings] Invalid {dim_key} in batch update: "
                        f"{data[dim_key]!r}"
                    )
                    data.pop(dim_key)

        if "width" in data or "height" in data:
            w = data.get("width", self.settings.get("width", 1280))
            h = data.get("height", self.settings.get("height", 720))
            w, h = self.sanitize_resolution(w, h)
            data["width"]  = w
            data["height"] = h
        with self._lock:
            self.settings.update(data)
        self.save()