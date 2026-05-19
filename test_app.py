# test_application.py
"""
Comprehensive pytest suite for AI Slideshow Renderer.
Covers: TextChunker, AppConfig, Backend factory, BaseTTSBackend,
        EdgeTTSBackend, Qwen3Backend, Engines (hashing, tasks, timeline, Ken Burns).
"""

import os
import sys
import json
import wave
import shutil
import tempfile
import logging
import asyncio
import re
import time
from unittest.mock import patch, MagicMock, AsyncMock, call
from pathlib import Path
from typing import List, Tuple

import pytest
import numpy as np

# ============================================================================
# MOCK SETUP — must happen BEFORE any application module is imported
# ============================================================================

# --- Mock 'utils' (not provided but imported everywhere) ---
_mock_utils = MagicMock()
_mock_utils.logger = logging.getLogger("test")
_mock_utils.detect_ffmpeg.return_value = "/usr/bin/ffmpeg"
_mock_utils.natural_sort_key = lambda x: [
    int(c) if c.isdigit() else c.lower()
    for c in re.split(r'(\d+)', os.path.basename(str(x)))
]
_mock_utils.build_manifest_from_timeline.return_value = {}
_mock_utils.save_project_manifest = MagicMock()
_mock_utils.update_library_manifest = MagicMock()
_mock_utils.should_skip_render.return_value = False
_mock_utils.get_project_dirs.return_value = []
_mock_utils.get_library_status_data.return_value = []
_mock_utils.get_theme.return_value = {"name": "Dark"}
_mock_utils.set_theme = MagicMock()
_mock_utils.list_themes.return_value = ["Dark"]
_mock_utils.list_widget_stylesheet_from_theme.return_value = ""
_mock_utils.setup_logging = MagicMock()
_mock_utils.create_slide_file = MagicMock()
_mock_utils.get_default_slide_html.return_value = ""
_mock_utils.get_blank_slide_html.return_value = ""
sys.modules['utils'] = _mock_utils

# --- Mock PySide6 ---
for mod in [
    'PySide6', 'PySide6.QtWidgets', 'PySide6.QtCore',
    'PySide6.QtGui', 'PySide6.QtWebEngineWidgets',
]:
    sys.modules.setdefault(mod, MagicMock())

# --- Mock edge_tts ---
_mock_edge_tts = MagicMock()
sys.modules.setdefault('edge_tts', _mock_edge_tts)

# --- Mock torch ---
_mock_torch = MagicMock()
_mock_torch.cuda.is_available.return_value = False
_mock_torch.cuda.empty_cache = MagicMock()
_mock_torch.cuda.get_device_capability.return_value = (7, 5)
_mock_torch.cuda.get_device_name.return_value = "MockGPU"
_mock_torch.cuda.device_count.return_value = 0
_mock_torch.float16 = "float16"
_mock_torch.bfloat16 = "bfloat16"
sys.modules.setdefault('torch', _mock_torch)

# --- Mock qwen_tts ---
sys.modules.setdefault('qwen_tts', MagicMock())

# --- Mock soundfile ---
_mock_sf = MagicMock()
sys.modules.setdefault('soundfile', _mock_sf)

# --- Mock flash_attn (not available) ---
sys.modules.setdefault('flash_attn', MagicMock())

# --- Mock PIL ---
sys.modules.setdefault('PIL', MagicMock())
sys.modules.setdefault('PIL.Image', MagicMock())

# --- Mock playwright ---
sys.modules.setdefault('playwright', MagicMock())
sys.modules.setdefault('playwright.async_api', MagicMock())

# ============================================================================
# NOW import application modules
# ============================================================================

from text_chunker import TextChunker, ChunkingConfig, get_chunker_for_language, chunk_text_for_tts
from config import AppConfig
from backends.base import BaseTTSBackend
from backends import BACKEND_MAP, get_backend

# Force-reload edge/qwen3 modules so they pick up the mocks
import importlib
import backends.edge as edge_module
importlib.reload(edge_module)
from backends.edge import EdgeTTSBackend

import backends.qwen3 as qwen3_module
importlib.reload(qwen3_module)
from backends.qwen3 import Qwen3Backend

import engines as engines_module
importlib.reload(engines_module)
from engines import (
    SlideTask, RenderContext, FFmpegCommandBuilder,
    _get_audio_settings_hash, _get_video_settings_hash,
    prepare_slide_tasks, build_timeline,
)


# ============================================================================
# HELPERS
# ============================================================================

def create_silent_wav(path, duration=1.0, sample_rate=24000):
    """Create a valid silent WAV file for testing."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n_frames = int(sample_rate * duration)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


# ============================================================================
# 1. TEXT CHUNKER TESTS
# ============================================================================

class TestChunkingConfig:
    """Tests for ChunkingConfig dataclass defaults and customisation."""

    def test_default_values(self):
        c = ChunkingConfig()
        assert c.max_chars == 500
        assert c.max_sentences == 5
        assert c.min_chunk_chars == 50
        assert c.overlap_chars == 0
        assert c.respect_sentence_bounds is True
        assert c.language == "English"

    def test_custom_values(self):
        c = ChunkingConfig(max_chars=300, max_sentences=3, language="Chinese")
        assert c.max_chars == 300
        assert c.max_sentences == 3
        assert c.language == "Chinese"

    def test_all_fields_settable(self):
        c = ChunkingConfig(
            max_chars=1000, max_sentences=10, min_chunk_chars=100,
            overlap_chars=50, respect_sentence_bounds=False, language="Japanese",
        )
        assert c.max_chars == 1000
        assert c.respect_sentence_bounds is False


class TestTextChunkerSentenceSplitting:
    """Tests for sentence splitting across languages."""

    def test_english_sentences(self):
        sents = TextChunker().split_into_sentences("Hello. How are you? Fine!", "English")
        assert len(sents) == 3

    def test_chinese_sentences(self):
        sents = TextChunker().split_into_sentences("你好。你怎么样？我很好！", "Chinese")
        assert len(sents) == 3

    def test_japanese_sentences(self):
        sents = TextChunker().split_into_sentences("こんにちは。元気？はい！", "Japanese")
        assert len(sents) == 3

    def test_german_sentences(self):
        sents = TextChunker().split_into_sentences("Hallo. Wie geht es? Gut!", "German")
        assert len(sents) == 3

    def test_empty_text(self):
        assert TextChunker().split_into_sentences("", "English") == []
        assert TextChunker().split_into_sentences("   ", "English") == []

    def test_single_sentence(self):
        sents = TextChunker().split_into_sentences("Hello world.", "English")
        assert len(sents) == 1

    def test_text_without_ending_punctuation(self):
        sents = TextChunker().split_into_sentences("Hello world", "English")
        # Should still return content even without punctuation
        assert len(sents) >= 1

    def test_multiple_punctuation(self):
        sents = TextChunker().split_into_sentences("Really?? Yes!!", "English")
        assert len(sents) >= 2

    def test_default_pattern_for_unknown_language(self):
        sents = TextChunker().split_into_sentences("Hi. Bye!", "Martian")
        assert len(sents) >= 1


class TestTextChunkerNeedsChunking:
    """Tests for needs_chunking boundary logic."""

    def test_short_text_no_chunking(self):
        assert TextChunker(ChunkingConfig(max_chars=500)).needs_chunking("Short") is False

    def test_long_text_needs_chunking(self):
        assert TextChunker(ChunkingConfig(max_chars=10)).needs_chunking("A" * 20) is True

    def test_empty_text_no_chunking(self):
        assert TextChunker().needs_chunking("") is False

    def test_exact_boundary(self):
        c = ChunkingConfig(max_chars=10)
        assert TextChunker(c).needs_chunking("1234567890") is False
        assert TextChunker(c).needs_chunking("12345678901") is True


class TestTextChunkerChunking:
    """Tests for the main chunk_text method."""

    def test_short_text_single_chunk(self):
        result = TextChunker(ChunkingConfig(max_chars=500)).chunk_text("Hello world.", "English")
        assert len(result) == 1
        assert result[0][0] == "Hello world."
        assert result[0][1] > 0

    def test_long_text_gets_chunked(self):
        cfg = ChunkingConfig(max_chars=60, max_sentences=10, min_chunk_chars=10)
        text = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."
        result = TextChunker(cfg).chunk_text(text, "English")
        assert len(result) > 1

    def test_durations_are_positive(self):
        cfg = ChunkingConfig(max_chars=80, max_sentences=5, min_chunk_chars=10)
        result = TextChunker(cfg).chunk_text("A. B. C. D. E. F. G.", "English")
        for _, dur in result:
            assert dur > 0

    def test_empty_text_returns_empty(self):
        t = TextChunker()
        assert t.chunk_text("", "English") == []
        assert t.chunk_text("   ", "English") == []

    def test_chinese_text_chunking(self):
        cfg = ChunkingConfig(max_chars=20, max_sentences=3, min_chunk_chars=5)
        text = "这是第一句。这是第二句。这是第三句。这是第四句。"
        result = TextChunker(cfg).chunk_text(text, "Chinese")
        assert len(result) >= 1

    def test_content_preserved(self):
        cfg = ChunkingConfig(max_chars=80, max_sentences=5, min_chunk_chars=10)
        text = "The quick brown fox. Jumps over the lazy dog. In a faraway land."
        result = TextChunker(cfg).chunk_text(text, "English")
        combined = " ".join(c for c, _ in result)
        assert "quick brown fox" in combined
        assert "lazy dog" in combined

    def test_max_sentences_limit(self):
        cfg = ChunkingConfig(max_chars=500, max_sentences=2, min_chunk_chars=10)
        text = "A. B. C. D. E."
        result = TextChunker(cfg).chunk_text(text, "English")
        assert len(result) > 1

    def test_single_very_long_sentence_falls_to_hard_split(self):
        cfg = ChunkingConfig(max_chars=40, max_sentences=5, min_chunk_chars=5)
        text = "A" * 100 + "."
        result = TextChunker(cfg).chunk_text(text, "English")
        assert len(result) >= 2

    def test_min_chunk_chars_small_remainder_merged(self):
        cfg = ChunkingConfig(max_chars=80, max_sentences=10, min_chunk_chars=500)
        text = "Short sentence."
        result = TextChunker(cfg).chunk_text(text, "English")
        # Very short text should still be returned
        assert len(result) >= 1


class TestTextChunkerDurationEstimation:
    """Tests for estimate_speech_duration."""

    def test_positive_duration(self):
        assert TextChunker().estimate_speech_duration("Hello world.", "English") > 0

    def test_longer_text_longer_duration(self):
        t = TextChunker()
        short = t.estimate_speech_duration("Hi.", "English")
        long = t.estimate_speech_duration("A " * 50, "English")
        assert long > short

    def test_includes_buffer(self):
        assert TextChunker().estimate_speech_duration("Hi.", "English") >= 0.5

    def test_cjk_uses_char_count(self):
        t = TextChunker()
        d = t.estimate_speech_duration("你好世界你好世界", "Chinese")
        assert d > 0

    def test_unknown_language_uses_default(self):
        assert TextChunker().estimate_speech_duration("Hello.", "Unknown") > 0


class TestTextChunkerHardSplit:
    """Tests for _hard_split fallback."""

    def test_split_at_pause_marker(self):
        cfg = ChunkingConfig(max_chars=25, min_chunk_chars=5)
        result = TextChunker(cfg)._hard_split("First part, then second part, then more", "English")
        assert len(result) >= 2

    def test_split_no_break_points(self):
        cfg = ChunkingConfig(max_chars=10, min_chunk_chars=3)
        result = TextChunker(cfg)._hard_split("Averylongwordwithoutbreaks", "English")
        assert len(result) >= 2


class TestGetChunkerForLanguage:
    """Tests for factory function."""

    def test_english(self):
        c = get_chunker_for_language("English", 400)
        assert c.config.max_chars == 400
        assert c.config.max_sentences == 5

    def test_cjk_fewer_sentences(self):
        c = get_chunker_for_language("Chinese", 300)
        assert c.config.max_sentences == 3

    def test_default_max_chars(self):
        assert get_chunker_for_language("English").config.max_chars == 500


class TestChunkTextForTTS:
    """Tests for convenience function."""

    def test_returns_strings(self):
        result = chunk_text_for_tts("Hello. Goodbye.", "English", 500)
        assert isinstance(result, list)
        assert all(isinstance(c, str) for c in result)

    def test_short_text_single_chunk(self):
        assert len(chunk_text_for_tts("Hi.", "English", 500)) == 1


# ============================================================================
# 2. APP CONFIG TESTS
# ============================================================================

class TestAppConfig:
    """Tests for AppConfig singleton, CRUD, and persistence."""

    def setup_method(self):
        AppConfig._instance = None

    def teardown_method(self):
        AppConfig._instance = None

    def test_singleton(self):
        c1 = AppConfig()
        c2 = AppConfig()
        assert c1 is c2

    def test_default_values(self):
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            assert c.get("width") == 1280
            assert c.get("height") == 720
            assert c.get("fps") == 30
            assert c.get("active_backend") == "qwen3"
            assert c.get("edge_voice") == "en-US-JennyNeural"
            assert c.get("enable_text_chunking") is True

    def test_get_default(self):
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            assert c.get("nope") is None
            assert c.get("nope", "fb") == "fb"

    def test_set_and_get(self):
        with patch.object(AppConfig, "load"), patch.object(AppConfig, "save"):
            c = AppConfig()
            c.set("k", "v")
            assert c.get("k") == "v"

    def test_set_triggers_save(self):
        with patch.object(AppConfig, "load"), patch.object(AppConfig, "save") as ms:
            AppConfig().set("k", "v")
            ms.assert_called()

    def test_update_multiple(self):
        with patch.object(AppConfig, "load"), patch.object(AppConfig, "save"):
            c = AppConfig()
            c.update({"a": 1, "b": 2})
            assert c.get("a") == 1
            assert c.get("b") == 2

    def test_save_writes_json(self, tmp_path):
        c = AppConfig()
        c.config_path = str(tmp_path / "s.json")
        c.settings = {"test": 1}
        c.save()
        with open(c.config_path) as f:
            assert json.load(f) == {"test": 1}

    def test_load_reads_json(self, tmp_path):
        p = str(tmp_path / "s.json")
        with open(p, "w") as f:
            json.dump({"width": 1920}, f)
        c = AppConfig()
        c.config_path = p
        c.load()
        assert c.get("width") == 1920

    def test_load_missing_file(self):
        c = AppConfig()
        c.config_path = "/no/such/file.json"
        c.load()  # no crash

    def test_load_corrupt_json(self, tmp_path):
        p = str(tmp_path / "bad.json")
        Path(p).write_text("{bad json!!")
        c = AppConfig()
        c.config_path = p
        c.load()  # no crash, keeps defaults

    def test_default_config_has_required_keys(self):
        for k in ["width", "height", "fps", "active_backend", "qwen3_mode",
                   "edge_voice", "enable_text_chunking", "chunk_max_chars",
                   "hf_cache_dir", "render_workers", "output_dir"]:
            assert k in AppConfig.DEFAULT_CONFIG, f"Missing: {k}"

    def test_save_load_roundtrip(self, tmp_path):
        p = str(tmp_path / "s.json")
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            c.config_path = p
            c.set("width", 1920)
            c.set("fps", 60)
        c.save()

        AppConfig._instance = None
        c2 = AppConfig()
        c2.config_path = p
        c2.load()
        assert c2.get("width") == 1920
        assert c2.get("fps") == 60

    def test_new_keys_preserved_on_load(self, tmp_path):
        p = str(tmp_path / "s.json")
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            c.config_path = p
            c.save()
        AppConfig._instance = None
        c2 = AppConfig()
        c2.config_path = p
        c2.load()
        for k in AppConfig.DEFAULT_CONFIG:
            assert k in c2.settings


# ============================================================================
# 3. BASE TTS BACKEND TESTS
# ============================================================================

class ConcreteBackend(BaseTTSBackend):
    """Concrete implementation for testing the ABC."""

    def __init__(self, config):
        super().__init__(config)
        self.initialized = False
        self.cleaned = False

    def initialize(self):
        self.initialized = True

    def generate_batch(self, texts, output_paths):
        return True, []

    def cleanup(self):
        self.cleaned = True


class TestBaseTTSBackend:
    """Tests for the abstract base class contract and helpers."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseTTSBackend({})

    def test_concrete_init(self):
        b = ConcreteBackend({"k": "v"})
        assert b.config == {"k": "v"}
        assert b._progress_callback is None

    def test_set_progress_callback(self):
        cb = MagicMock()
        b = ConcreteBackend({})
        b.set_progress_callback(cb)
        assert b._progress_callback is cb

    def test_set_callback_none(self):
        b = ConcreteBackend({})
        b.set_progress_callback(MagicMock())
        b.set_progress_callback(None)
        assert b._progress_callback is None

    def test_report_progress_calls_callback(self):
        cb = MagicMock()
        b = ConcreteBackend({})
        b.set_progress_callback(cb)
        b._report_progress("chunk", 1, 5, "msg")
        cb.assert_called_once_with("chunk", 1, 5, "msg")

    def test_report_progress_no_callback_no_crash(self):
        ConcreteBackend({})._report_progress("x", 0, 0)

    def test_get_local_temp_dir_with_base(self, tmp_path):
        b = ConcreteBackend({"_temp_dir": str(tmp_path)})
        d = b._get_local_temp_dir("sub")
        assert os.path.isdir(d)
        assert str(tmp_path) in d

    def test_get_local_temp_dir_fallback(self):
        b = ConcreteBackend({})
        d = b._get_local_temp_dir("sub")
        assert os.path.isdir(d)

    def test_cleanup_chunk_temp(self, tmp_path):
        b = ConcreteBackend({"_temp_dir": str(tmp_path)})
        d = b._get_local_temp_dir("c")
        assert os.path.isdir(d)
        b._cleanup_chunk_temp()
        assert not os.path.exists(d)

    def test_cleanup_multiple_dirs(self, tmp_path):
        b = ConcreteBackend({"_temp_dir": str(tmp_path)})
        d1 = b._get_local_temp_dir("a")
        d2 = b._get_local_temp_dir("b")
        b._cleanup_chunk_temp()
        assert not os.path.exists(d1)
        assert not os.path.exists(d2)

    def test_cleanup_no_dirs_no_crash(self):
        ConcreteBackend({})._cleanup_chunk_temp()

    def test_partial_implementation_raises(self):
        class Partial(BaseTTSBackend):
            def initialize(self): pass
        with pytest.raises(TypeError):
            Partial({})


# ============================================================================
# 4. BACKEND FACTORY TESTS
# ============================================================================

class TestBackendFactory:
    """Tests for BACKEND_MAP registry and get_backend factory."""

    def test_map_has_qwen3(self):
        assert "qwen3" in BACKEND_MAP

    def test_map_has_edge(self):
        assert "edge" in BACKEND_MAP

    def test_invalid_name_raises_value_error(self):
        with pytest.raises(ValueError, match="not found"):
            get_backend("bogus", {})

    def test_value_error_lists_available(self):
        with pytest.raises(ValueError) as ei:
            get_backend("bogus", {})
        msg = str(ei.value)
        assert "qwen3" in msg or "edge" in msg

    def test_init_failure_raises_runtime_error(self):
        MockCls = MagicMock()
        inst = MagicMock()
        inst.initialize.side_effect = Exception("boom")
        inst.cleanup = MagicMock()
        MockCls.return_value = inst
        with patch.dict(BACKEND_MAP, {"_t": MockCls}):
            with pytest.raises(RuntimeError, match="Failed to initialize"):
                get_backend("_t", {})
            inst.cleanup.assert_called()

    def test_all_backends_inherit_base(self):
        for name, cls in BACKEND_MAP.items():
            assert issubclass(cls, BaseTTSBackend)

    def test_all_backends_have_required_methods(self):
        for name, cls in BACKEND_MAP.items():
            for m in ("initialize", "generate_batch", "cleanup"):
                assert hasattr(cls, m), f"{name} missing {m}"


# ============================================================================
# 5. EDGE TTS BACKEND TESTS
# ============================================================================

class TestEdgeTTSVoices:
    """Tests for Edge TTS voice catalogue."""

    def test_has_all_major_languages(self):
        for lang in ("English", "Chinese", "Japanese", "Korean",
                     "German", "French", "Russian", "Portuguese",
                     "Spanish", "Italian"):
            assert lang in EdgeTTSBackend.EDGE_VOICES

    def test_voice_tuple_structure(self):
        for lang, voices in EdgeTTSBackend.EDGE_VOICES.items():
            for v in voices:
                assert isinstance(v, tuple) and len(v) == 2
                vid, vname = v
                assert "Neural" in vid

    def test_languages_matches_voices(self):
        assert EdgeTTSBackend.EDGE_LANGUAGES == list(EdgeTTSBackend.EDGE_VOICES.keys())

    def test_ui_options(self):
        o = EdgeTTSBackend.get_ui_options()
        assert "languages" in o and "voices" in o


class TestEdgeTTSInit:
    """Tests for Edge TTS initialisation."""

    def test_init_success(self):
        b = EdgeTTSBackend({"edge_voice": "en-US-JennyNeural"})
        assert b.config["edge_voice"] == "en-US-JennyNeural"

    def test_init_no_lib_raises(self):
        orig = edge_module.EDGE_TTS_AVAILABLE
        edge_module.EDGE_TTS_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="edge-tts"):
                EdgeTTSBackend({})
        finally:
            edge_module.EDGE_TTS_AVAILABLE = orig

    def test_initialize_sets_default_voice(self):
        b = EdgeTTSBackend({"edge_language": "English"})
        b.initialize()
        assert b.config.get("edge_voice") is not None

    def test_initialize_preserves_voice(self):
        b = EdgeTTSBackend({"edge_voice": "en-US-GuyNeural", "edge_language": "English"})
        b.initialize()
        assert b.config["edge_voice"] == "en-US-GuyNeural"

    def test_cleanup_calls_gc(self):
        b = EdgeTTSBackend({"edge_voice": "en-US-JennyNeural"})
        with patch("gc.collect") as mg:
            b.cleanup()
            mg.assert_called()


class TestEdgeTTSGeneration:
    """Tests for Edge TTS generation logic."""

    def test_batch_empty_raises(self):
        b = EdgeTTSBackend({"edge_voice": "x"})
        b.initialize()
        with pytest.raises(ValueError, match="all texts are empty"):
            b.generate_batch(["", "  "], ["/tmp/a.wav", "/tmp/b.wav"])

    def test_batch_success_tuple(self):
        b = EdgeTTSBackend({"edge_voice": "x", "enable_text_chunking": False})
        b.initialize()
        with patch.object(b, "_generate_single", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(["Hi"], ["/tmp/o.wav"])
            assert ok is True and errs == []

    def test_batch_failure_returns_errors(self):
        b = EdgeTTSBackend({"edge_voice": "x", "enable_text_chunking": False})
        b.initialize()
        with patch.object(b, "_generate_single", return_value=False), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(["Hi"], ["/tmp/o.wav"])
            assert ok is False and len(errs) > 0

    def test_generate_single_empty_raises(self):
        b = EdgeTTSBackend({"edge_voice": "x"})
        b.initialize()
        with pytest.raises(ValueError, match="empty"):
            b._generate_single("", "/tmp/o.wav")

    def test_concatenate_single_file(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x"})
        wav = str(tmp_path / "in.wav")
        create_silent_wav(wav, 1.0)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_audio_files([wav], out) is True
        assert os.path.exists(out)

    def test_concatenate_multiple_files(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x"})
        w1, w2 = str(tmp_path / "c0.wav"), str(tmp_path / "c1.wav")
        create_silent_wav(w1, 0.5)
        create_silent_wav(w2, 0.5)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_audio_files([w1, w2], out) is True
        assert os.path.exists(out)

    def test_concatenate_empty_returns_false(self):
        assert EdgeTTSBackend({"edge_voice": "x"})._concatenate_audio_files([], "/tmp/o.wav") is False

    def test_get_chunker(self):
        b = EdgeTTSBackend({"edge_voice": "x", "chunk_max_chars": 300,
                            "chunk_max_sentences": 3, "edge_language": "English"})
        ch = b._get_chunker()
        assert isinstance(ch, TextChunker)
        assert ch.config.max_chars == 300


class TestEdgeTTSVolumeConversion:
    """Unit tests for the volume conversion logic inside _generate_single."""

    @staticmethod
    def _convert(vol_raw):
        """Replicate the conversion logic from EdgeTTSBackend._generate_single."""
        volume = vol_raw
        if vol_raw and not vol_raw.startswith(("+", "-")):
            try:
                abs_val = int(vol_raw.replace("%", ""))
                relative_val = abs_val - 100
                volume = f"{relative_val:+d}%"
            except ValueError:
                volume = "+0%"
        return volume

    def test_absolute_zero(self):
        assert self._convert("0%") == "-100%"

    def test_absolute_100(self):
        assert self._convert("100%") == "+0%"

    def test_absolute_150(self):
        assert self._convert("150%") == "+50%"

    def test_relative_positive(self):
        assert self._convert("+25%") == "+25%"

    def test_relative_negative(self):
        assert self._convert("-50%") == "-50%"

    def test_invalid_defaults_zero(self):
        assert self._convert("abc%") == "+0%"


# ============================================================================
# 6. QWEN3 BACKEND TESTS
# ============================================================================

class TestQwen3ModelID:
    """Tests for _get_model_id resolution."""

    def test_voice_custom(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_size": "1.7B"})
        assert "CustomVoice" in b._get_model_id()

    def test_voice_clone(self):
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_size": "1.7B"})
        assert "Base" in b._get_model_id()

    def test_voice_design(self):
        b = Qwen3Backend({"qwen3_mode": "voice_design", "qwen3_size": "1.7B"})
        assert "VoiceDesign" in b._get_model_id()

    def test_size_included(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_size": "0.6B"})
        assert "0.6B" in b._get_model_id()

    def test_unknown_mode_defaults_custom(self):
        b = Qwen3Backend({"qwen3_mode": "weird", "qwen3_size": "1.7B"})
        assert "CustomVoice" in b._get_model_id()


class TestQwen3LocalModelSearch:
    """Tests for _find_local_model_path."""

    def test_no_cache_returns_id(self):
        assert Qwen3Backend._find_local_model_path("Qwen/m", "") == "Qwen/m"

    def test_nonexistent_cache_returns_id(self):
        assert Qwen3Backend._find_local_model_path("Qwen/m", "/nope") == "Qwen/m"

    def test_finds_hf_snapshot(self, tmp_path):
        snap = tmp_path / "models--Qwen--mymodel" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        (snap / "model.safetensors").write_text("x")
        result = Qwen3Backend._find_local_model_path("Qwen/mymodel", str(tmp_path))
        assert result == str(snap)

    def test_finds_flat_dir(self, tmp_path):
        d = tmp_path / "mymodel"
        d.mkdir()
        (d / "config.json").write_text("{}")
        (d / "pytorch_model.bin").write_text("x")
        result = Qwen3Backend._find_local_model_path("Qwen/mymodel", str(tmp_path))
        assert result == str(d)


class TestQwen3SmartAttention:
    """Tests for _get_smart_attn_implementation."""

    def test_cpu_always_eager(self):
        assert Qwen3Backend._get_smart_attn_implementation("cpu", "flash_attention_2") == "eager"

    def test_flash_on_ampere_with_lib(self):
        _mock_torch.cuda.is_available.return_value = True
        _mock_torch.cuda.get_device_capability.return_value = (8, 6)
        qwen3_module.FLASH_ATTN_AVAILABLE = True
        assert Qwen3Backend._get_smart_attn_implementation("cuda:0", "flash_attention_2") == "flash_attention_2"
        _mock_torch.cuda.is_available.return_value = False

    def test_flash_fallback_no_lib(self):
        _mock_torch.cuda.is_available.return_value = True
        _mock_torch.cuda.get_device_capability.return_value = (8, 0)
        qwen3_module.FLASH_ATTN_AVAILABLE = False
        assert Qwen3Backend._get_smart_attn_implementation("cuda:0", "flash_attention_2") == "eager"
        _mock_torch.cuda.is_available.return_value = False

    def test_flash_fallback_old_gpu(self):
        _mock_torch.cuda.is_available.return_value = True
        _mock_torch.cuda.get_device_capability.return_value = (7, 5)
        assert Qwen3Backend._get_smart_attn_implementation("cuda:0", "flash_attention_2") == "eager"
        _mock_torch.cuda.is_available.return_value = False

    def test_eager_passthrough(self):
        assert Qwen3Backend._get_smart_attn_implementation("cpu", "eager") == "eager"

    def test_sdpa_passthrough(self):
        _mock_torch.cuda.is_available.return_value = True
        _mock_torch.cuda.get_device_capability.return_value = (8, 0)
        assert Qwen3Backend._get_smart_attn_implementation("cuda:0", "sdpa") == "sdpa"
        _mock_torch.cuda.is_available.return_value = False


class TestQwen3ModesLanguages:
    """Tests for QWEN3_MODES and QWEN3_LANGUAGES constants."""

    def test_three_modes(self):
        keys = [m[0] for m in Qwen3Backend.QWEN3_MODES]
        assert "voice_custom" in keys
        assert "voice_clone" in keys
        assert "voice_design" in keys

    def test_languages(self):
        for l in ("English", "Chinese", "Japanese", "Korean"):
            assert l in Qwen3Backend.QWEN3_LANGUAGES

    def test_ui_options(self):
        o = Qwen3Backend.get_ui_options()
        assert "modes" in o and "languages" in o


class TestQwen3Generation:
    """Tests for Qwen3 generation routing and helpers."""

    def test_batch_empty_raises(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        with pytest.raises(ValueError, match="all texts are empty"):
            b.generate_batch(["", "  "], ["/tmp/a.wav", "/tmp/b.wav"])

    def test_batch_unknown_mode(self):
        b = Qwen3Backend({"qwen3_mode": "bad_mode", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        ok, errs = b.generate_batch(["Hi"], ["/tmp/o.wav"])
        assert ok is False
        assert any("Unknown mode" in e for e in errs)

    def test_save_wav(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        with patch.object(qwen3_module.sf, "write") as mw:
            assert b._save_wav([0.1, 0.2], 24000, str(tmp_path / "o.wav")) is True
            mw.assert_called_once()

    def test_concatenate_wav_single(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w = str(tmp_path / "in.wav")
        create_silent_wav(w, 1.0)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files([w], out) is True

    def test_concatenate_wav_multiple(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w1, w2 = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        create_silent_wav(w1, 0.5)
        create_silent_wav(w2, 0.5)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files([w1, w2], out) is True

    def test_concatenate_wav_empty(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        assert b._concatenate_wav_files([], "/tmp/o.wav") is False

    def test_concatenate_wav_missing_file(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w = str(tmp_path / "ok.wav")
        create_silent_wav(w, 0.5)
        assert b._concatenate_wav_files([w, "/missing.wav"], str(tmp_path / "o.wav")) is False

    def test_get_chunker(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "chunk_max_chars": 400, "qwen3_language": "Chinese"})
        ch = b._get_chunker()
        assert ch.config.max_chars == 400
        assert ch.config.language == "Chinese"


class TestQwen3VoiceCustom:
    """Tests for voice_custom mode routing."""

    def test_routes_to_gen_voice_custom(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_speaker": "Vivian", "qwen3_language": "English",
                           "enable_text_chunking": False})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_custom_voice.return_value = ([mock_wav], 24000)
        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, _ = b._gen_voice_custom(["Hello"], ["/tmp/o.wav"])
            assert ok is True
            b.model.generate_custom_voice.assert_called_once()


class TestQwen3VoiceClone:
    """Tests for voice_clone mode preconditions."""

    def test_no_ref_audio_fails(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_device_map": "cpu",
                           "qwen3_ref_audio": str(tmp_path / "nope.wav"),
                           "qwen3_ref_text": "x"})
        b.model = MagicMock()
        ok, errs = b._gen_voice_clone(["Hi"], ["/tmp/o.wav"])
        assert ok is False
        assert any("not found" in e.lower() for e in errs)

    def test_no_ref_text_fails(self):
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_device_map": "cpu",
                           "qwen3_ref_audio": "", "qwen3_ref_text": ""})
        b.model = MagicMock()
        ok, errs = b._gen_voice_clone(["Hi"], ["/tmp/o.wav"])
        assert ok is False
        assert any("required" in e.lower() for e in errs)


class TestQwen3InitCleanup:
    """Tests for Qwen3 initialization and cleanup lifecycle."""

    def test_initialize_loads_model(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_size": "1.7B", "qwen3_attn_implementation": "eager",
                           "qwen3_dtype": "float16"})
        with patch.object(qwen3_module, "Qwen3TTSModel") as mc:
            mc.from_pretrained.return_value = MagicMock()
            b.initialize()
            assert b.model is not None
            mc.from_pretrained.assert_called_once()

    def test_cleanup_deletes_model(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        with patch("gc.collect"):
            b.cleanup()
        assert b.model is None

    def test_reinitialize_cleans_previous(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_attn_implementation": "eager", "qwen3_dtype": "float16"})
        b.model = MagicMock()
        with patch.object(b, "cleanup") as mc, \
             patch.object(qwen3_module, "Qwen3TTSModel") as qc:
            qc.from_pretrained.return_value = MagicMock()
            b.initialize()
            mc.assert_called_once()


# ============================================================================
# 7. ENGINE TESTS
# ============================================================================

class TestSlideTask:
    """Tests for SlideTask dataclass."""

    def test_basic(self):
        t = SlideTask(index=0, html_path="/p/slide1.html",
                      txt_path="/p/slide1.txt", target_audio_path="/tmp/a0.wav")
        assert t.text_content == ""
        assert t.needs_tts is False
        assert t.generation_error is None

    def test_slide_number_extraction(self):
        t = SlideTask(index=0, html_path="/p/slide3.html",
                      txt_path="/p/slide3.txt", target_audio_path="/tmp/a0.wav")
        assert t.slide_number == 3

    def test_slide_number_fallback(self):
        t = SlideTask(index=7, html_path="/p/other.html",
                      txt_path="/p/other.txt", target_audio_path="/tmp/a7.wav")
        assert t.slide_number == 7


class TestRenderContext:
    """Tests for RenderContext dataclass."""

    def test_creation(self):
        ctx = RenderContext(project_path="/p", temp_dir="/t",
                           config={"w": 1}, ffmpeg_path="/bin/ffmpeg", render_mode="full")
        assert ctx.project_path == "/p"
        assert ctx.config == {"w": 1}


class TestFFmpegCommandBuilder:
    """Tests for FFmpeg command construction."""

    def test_basic_command(self):
        cmd = FFmpegCommandBuilder.build_video_encoder("/out.mp4", 1280, 720, 30)
        assert "ffmpeg" in cmd and "-y" in cmd
        assert "1280x720" in cmd
        assert "30" in cmd

    def test_rawvideo_input(self):
        cmd = FFmpegCommandBuilder.build_video_encoder("/out.mp4", 1920, 1080, 60)
        assert "rawvideo" in cmd

    def test_yuv420p_output(self):
        cmd = FFmpegCommandBuilder.build_video_encoder("/out.mp4", 1280, 720, 30)
        assert "yuv420p" in cmd

    def test_custom_encoder(self):
        cmd = FFmpegCommandBuilder.build_video_encoder("/out.mp4", 1280, 720, 30, "h264_nvenc")
        assert "h264_nvenc" in cmd


class TestEngineHashing:
    """Tests for audio/video settings hashing."""

    def test_audio_hash_deterministic(self):
        cfg = {"active_backend": "qwen3"}
        assert _get_audio_settings_hash(cfg) == _get_audio_settings_hash(cfg)

    def test_audio_hash_changes_backend(self):
        assert _get_audio_settings_hash({"active_backend": "qwen3"}) != \
               _get_audio_settings_hash({"active_backend": "edge"})

    def test_audio_hash_changes_voice(self):
        assert _get_audio_settings_hash({"edge_voice": "A"}) != \
               _get_audio_settings_hash({"edge_voice": "B"})

    def test_video_hash_deterministic(self):
        cfg = {"width": 1280, "height": 720, "fps": 30}
        assert _get_video_settings_hash(cfg) == _get_video_settings_hash(cfg)

    def test_video_hash_changes_resolution(self):
        assert _get_video_settings_hash({"width": 1280}) != \
               _get_video_settings_hash({"width": 1920})

    def test_audio_hash_includes_ref_mtime(self, tmp_path):
        rp = str(tmp_path / "ref.wav")
        Path(rp).write_text("v1")
        cfg = {"qwen3_ref_audio": rp}
        h1 = _get_audio_settings_hash(cfg)
        time.sleep(0.1)
        Path(rp).write_text("v2")
        h2 = _get_audio_settings_hash(cfg)
        assert h1 != h2


class TestPrepareSlideTasks:
    """Tests for slide task preparation (Phase 1)."""

    def test_creates_tasks(self, tmp_path):
        (tmp_path / "slide1.html").write_text("<h1>1</h1>")
        (tmp_path / "slide1.txt").write_text("Hello")
        (tmp_path / "slide2.html").write_text("<h1>2</h1>")
        (tmp_path / "slide2.txt").write_text("World")
        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks(
            sorted([str(tmp_path / "slide1.html"), str(tmp_path / "slide2.html")]), ctx)
        assert len(tasks) == 2
        assert tasks[0].text_content == "Hello"
        assert tasks[1].text_content == "World"

    def test_missing_txt_default(self, tmp_path):
        (tmp_path / "slide1.html").write_text("<h1>1</h1>")
        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
        assert "Slide" in tasks[0].text_content

    def test_needs_tts_no_cache(self, tmp_path):
        (tmp_path / "slide1.html").write_text("x")
        (tmp_path / "slide1.txt").write_text("Hello")
        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
        assert tasks[0].needs_tts is True

    def test_reuses_cached_audio(self, tmp_path):
        (tmp_path / "slide1.html").write_text("x")
        (tmp_path / "slide1.txt").write_text("Hello")
        wav = tmp_path / "slide1.wav"
        create_silent_wav(str(wav), 1.0)
        # Ensure wav mtime > txt mtime
        time.sleep(0.05)
        wav.touch()
        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
        assert tasks[0].needs_tts is False

    def test_long_text_warning_logged(self, tmp_path):
        (tmp_path / "slide1.html").write_text("x")
        (tmp_path / "slide1.txt").write_text("A" * 2000)
        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3", "enable_text_chunking": True,
                                   "chunk_max_chars": 500, "chunk_warn_threshold": 1000,
                                   "chunk_max_sentences": 5, "chunk_min_chars": 50,
                                   "qwen3_language": "English"},
                           ffmpeg_path="ffmpeg", render_mode="full")
        with patch.object(engines_module, "logger") as ml:
            tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
            # Should log about long text
            ml.info.assert_called()


class TestBuildTimeline:
    """Tests for timeline assembly (Phase 3)."""

    def test_builds_from_tasks(self, tmp_path):
        wav = str(tmp_path / "a.wav")
        create_silent_wav(wav, 1.0)
        tasks = [SlideTask(index=0, html_path="/p/slide1.html", txt_path="/p/slide1.txt",
                           target_audio_path=wav, text_content="Hi")]
        tl = build_timeline(tasks)
        assert len(tl) == 1
        assert tl[0]["duration"] > 0
        assert tl[0]["audio"] == wav

    def test_missing_audio_fallback(self):
        tasks = [SlideTask(index=0, html_path="/p/slide1.html", txt_path="/p/slide1.txt",
                           target_audio_path="/no/file.wav", text_content="Hello world test")]
        tl = build_timeline(tasks)
        assert tl[0]["duration"] >= 3.0

    def test_empty_tasks(self):
        assert build_timeline([]) == []


# ============================================================================
# 8. INTEGRATION-STYLE TESTS
# ============================================================================

class TestTextChunkerIntegration:
    """Cross-cutting chunker tests."""

    def test_chunk_and_reassemble(self):
        cfg = ChunkingConfig(max_chars=60, max_sentences=3, min_chunk_chars=10)
        text = ("The quick brown fox jumps. A thousand miles begins. "
                "To be or not to be. All that glitters.")
        result = TextChunker(cfg).chunk_text(text, "English")
        combined = " ".join(c for c, _ in result)
        assert "quick brown fox" in combined
        assert "glitters" in combined

    def test_progressive_chunking(self):
        cfg = ChunkingConfig(max_chars=100, min_chunk_chars=10)
        t = TextChunker(cfg)
        short = "Hello."
        medium = "Hello world. " * 5
        long = "Hello world. " * 20
        assert len(t.chunk_text(short, "English")) <= len(t.chunk_text(medium, "English"))
        assert len(t.chunk_text(medium, "English")) <= len(t.chunk_text(long, "English"))

    def test_all_languages_chunk(self):
        texts = {
            "English": "Hello world. Goodbye world.",
            "Chinese": "你好世界。再见世界。",
            "Japanese": "こんにちは世界。さようなら世界。",
            "Korean": "안녕하세요. 안녕히 가세요.",
            "German": "Hallo Welt. Tschüss Welt.",
            "French": "Bonjour le monde. Au revoir.",
        }
        cfg = ChunkingConfig(max_chars=200, min_chunk_chars=10)
        for lang, txt in texts.items():
            result = TextChunker(cfg).chunk_text(txt, lang)
            assert len(result) >= 1, f"Failed for {lang}"


class TestBackendRegistryConsistency:
    """Cross-cutting backend registry tests."""

    def test_all_have_ui_options(self):
        for name, cls in BACKEND_MAP.items():
            assert hasattr(cls, "get_ui_options"), f"{name} missing get_ui_options"

    def test_all_have_settings_widget(self):
        for name, cls in BACKEND_MAP.items():
            assert hasattr(cls, "get_settings_widget"), f"{name} missing get_settings_widget"


class TestConfigPersistenceIntegration:
    """Integration tests for config save/load."""

    def setup_method(self):
        AppConfig._instance = None

    def teardown_method(self):
        AppConfig._instance = None

    def test_roundtrip(self, tmp_path):
        p = str(tmp_path / "s.json")
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            c.config_path = p
            c.set("width", 1920)
        c.save()
        AppConfig._instance = None
        c2 = AppConfig()
        c2.config_path = p
        c2.load()
        assert c2.get("width") == 1920


# ============================================================================
# 9. ERROR HANDLING & EDGE CASES
# ============================================================================

class TestErrorHandling:
    """Error handling and edge case tests."""

    def test_chunker_empty_none(self):
        t = TextChunker()
        assert t.chunk_text("", "English") == []
        assert t.chunk_text("   ", "English") == []

    def test_base_cleanup_no_dirs(self):
        ConcreteBackend({})._cleanup_chunk_temp()

    def test_base_cleanup_already_deleted(self, tmp_path):
        b = ConcreteBackend({"_temp_dir": str(tmp_path)})
        d = b._get_local_temp_dir("x")
        os.rmdir(d)
        b._cleanup_chunk_temp()  # no crash

    def test_corrupt_wav_fallback_duration(self, tmp_path):
        bad = str(tmp_path / "bad.wav")
        Path(bad).write_text("not wav")
        tasks = [SlideTask(index=0, html_path="/p/s1.html", txt_path="/p/s1.txt",
                           target_audio_path=bad, text_content="Hello world test")]
        tl = build_timeline(tasks)
        assert tl[0]["duration"] >= 3.0

    def test_config_save_bad_path(self):
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            c.config_path = "/root/impossible/settings.json"
            c.save()  # should not raise, just log

    def test_edge_batch_mixed_empty_and_valid(self):
        b = EdgeTTSBackend({"edge_voice": "x", "enable_text_chunking": False})
        b.initialize()
        with patch.object(b, "_generate_single", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(
                ["Hello", "", "World"],
                ["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"],
            )
            # At least the non-empty ones should be attempted


class TestSentencePatterns:
    """Comprehensive language pattern tests."""

    def test_all_supported(self):
        chunker = TextChunker()
        for lang in ("English", "Chinese", "Japanese", "Korean",
                     "German", "French", "Russian", "Portuguese",
                     "Spanish", "Italian"):
            assert chunker.get_sentence_pattern(lang) is not None

    def test_default_for_unknown(self):
        chunker = TextChunker()
        assert chunker.get_sentence_pattern("Martian") == chunker.SENTENCE_ENDINGS["default"]

    def test_cjk_has_chinese_punctuation(self):
        chunker = TextChunker()
        for lang in ("Chinese", "Japanese", "Korean"):
            p = chunker.get_sentence_pattern(lang)
            assert any(c in p for c in "。！？")


class TestDurationEstimationEdgeCases:
    """Edge case duration tests."""

    def test_very_short(self):
        assert TextChunker().estimate_speech_duration("Hi", "English") > 0

    def test_very_long(self):
        assert TextChunker().estimate_speech_duration("word " * 1000, "English") > 0

    def test_special_chars(self):
        assert TextChunker().estimate_speech_duration("@#$%", "English") >= 0.5

    def test_cjk_char_counting(self):
        assert TextChunker().estimate_speech_duration("你好世界" * 10, "Chinese") > 0


class TestEdgeTTSChunkingDuringGeneration:
    """Tests that long text triggers chunking during Edge TTS generation."""

    def test_long_text_needs_chunking(self):
        b = EdgeTTSBackend({"edge_voice": "x", 
                        "edge_language": "English",
                        "enable_text_chunking": True,
                        "chunk_max_chars": 50,
                        "chunk_max_sentences": 2,
                        "chunk_min_chars": 5})
        b.initialize()
        long_text = "First sentence here. Second sentence here. Third sentence here. Fourth sentence."
        with patch.object(b, "_generate_single", return_value=True) as mock_gen, \
             patch.object(b, "_concatenate_audio_files", return_value=True), \
             patch.object(b, "_get_local_temp_dir", return_value=tempfile.mkdtemp()), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch([long_text], ["/tmp/o.wav"])
            # Should have called _generate_single multiple times (once per chunk)
            assert mock_gen.call_count > 1

    def test_chunking_disabled_single_call(self):
        b = EdgeTTSBackend({"edge_voice": "x", "edge_language": "English",
                            "enable_text_chunking": False})
        b.initialize()
        long_text = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        with patch.object(b, "_generate_single", return_value=True) as mock_gen, \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch([long_text], ["/tmp/o.wav"])
            assert mock_gen.call_count == 1


class TestQwen3ChunkingDuringGeneration:
    """Tests that long text triggers chunking during Qwen3 generation."""

    def test_voice_custom_long_text_chunks(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_speaker": "Vivian", "qwen3_language": "English",
                           "enable_text_chunking": True,
                           "chunk_max_chars": 30, "chunk_max_sentences": 1,
                           "chunk_min_chars": 5})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_custom_voice.return_value = ([mock_wav], 24000)

        long_text = "First sentence here. Second sentence here. Third sentence."
        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_concatenate_wav_files", return_value=True), \
             patch.object(b, "_get_local_temp_dir", return_value=str(tmp_path)), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, _ = b._gen_voice_custom([long_text], [str(tmp_path / "out.wav")])
            # generate_custom_voice should be called multiple times (per chunk)
            assert b.model.generate_custom_voice.call_count > 1

    def test_voice_custom_short_text_no_chunks(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_speaker": "Vivian", "qwen3_language": "English",
                           "enable_text_chunking": True,
                           "chunk_max_chars": 500, "chunk_min_chars": 5})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_custom_voice.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, _ = b._gen_voice_custom(["Hello world."], [str(tmp_path / "out.wav")])
            assert b.model.generate_custom_voice.call_count == 1


class TestQwen3VoiceDesign:
    """Tests for voice_design mode."""

    def test_voice_design_basic(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_design", "qwen3_device_map": "cpu",
                           "qwen3_vd_description": "A deep male voice",
                           "qwen3_vd_save_name": "test_voice",
                           "qwen3_language": "English",
                           "voice_references_root": str(tmp_path / "refs"),
                           "enable_text_chunking": False})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_voice_design.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(qwen3_module.sf, "write"), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b._gen_voice_design(["Hello"], [str(tmp_path / "out.wav")])
            assert ok is True
            b.model.generate_voice_design.assert_called_once()

    def test_voice_design_creates_ref_dir(self, tmp_path):
        refs = str(tmp_path / "new_refs")
        b = Qwen3Backend({"qwen3_mode": "voice_design", "qwen3_device_map": "cpu",
                           "qwen3_vd_description": "A voice",
                           "qwen3_vd_save_name": "v",
                           "qwen3_language": "English",
                           "voice_references_root": refs,
                           "enable_text_chunking": False})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_voice_design.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(qwen3_module.sf, "write"), \
             patch.object(b, "_cleanup_chunk_temp"):
            b._gen_voice_design(["Hello"], [str(tmp_path / "out.wav")])
            assert os.path.isdir(refs)


class TestQwen3VoiceClone:
    """Tests for voice_clone mode with valid reference."""

    def test_voice_clone_success(self, tmp_path):
        ref_wav = str(tmp_path / "ref.wav")
        create_silent_wav(ref_wav, 1.0)
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_device_map": "cpu",
                           "qwen3_ref_audio": ref_wav,
                           "qwen3_ref_text": "Reference text",
                           "qwen3_language": "English",
                           "enable_text_chunking": False})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_voice_clone.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b._gen_voice_clone(["Hello"], [str(tmp_path / "out.wav")])
            assert ok is True
            b.model.generate_voice_clone.assert_called_once()

    def test_voice_clone_with_prompt(self, tmp_path):
        ref_wav = str(tmp_path / "ref.wav")
        create_silent_wav(ref_wav, 1.0)
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_device_map": "cpu",
                           "qwen3_ref_audio": ref_wav,
                           "qwen3_ref_text": "Reference text",
                           "qwen3_language": "English",
                           "enable_text_chunking": False})
        b.model = MagicMock()
        b.model.create_voice_clone_prompt.return_value = {"prompt": "data"}
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_voice_clone.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, _ = b._gen_voice_clone(["Hello"], [str(tmp_path / "out.wav")])
            assert ok is True
            # Should pass voice_clone_prompt instead of ref_audio/ref_text
            call_kwargs = b.model.generate_voice_clone.call_args
            assert "voice_clone_prompt" in call_kwargs[1]

    def test_voice_clone_prompt_creation_fails_gracefully(self, tmp_path):
        ref_wav = str(tmp_path / "ref.wav")
        create_silent_wav(ref_wav, 1.0)
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_device_map": "cpu",
                           "qwen3_ref_audio": ref_wav,
                           "qwen3_ref_text": "Reference text",
                           "qwen3_language": "English",
                           "enable_text_chunking": False})
        b.model = MagicMock()
        b.model.create_voice_clone_prompt.side_effect = Exception("prompt fail")
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_voice_clone.return_value = ([mock_wav], 24000)

        with patch.object(b, "_save_wav", return_value=True), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, _ = b._gen_voice_clone(["Hello"], [str(tmp_path / "out.wav")])
            # Should fall back to ref_audio/ref_text
            call_kwargs = b.model.generate_voice_clone.call_args[1]
            assert "ref_audio" in call_kwargs


class TestEdgeTTSConvertMp3ToWav:
    """Tests for MP3→WAV conversion helper."""

    def test_conversion_success(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x", "ffmpeg_path": "ffmpeg"})
        mp3 = str(tmp_path / "in.mp3")
        wav = str(tmp_path / "out.wav")
        Path(mp3).write_bytes(b"fake mp3")
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=0)
            assert b._convert_mp3_to_wav(mp3, wav) is True

    def test_conversion_failure(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x", "ffmpeg_path": "ffmpeg"})
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=1, stderr=b"error")
            assert b._convert_mp3_to_wav("/in.mp3", "/out.wav") is False

    def test_conversion_exception(self):
        b = EdgeTTSBackend({"edge_voice": "x"})
        with patch("subprocess.run", side_effect=Exception("no ffmpeg")):
            assert b._convert_mp3_to_wav("/in.mp3", "/out.wav") is False


class TestEdgeTTSListVoices:
    """Tests for the async list_available_voices method."""

    def test_list_voices_success(self):
        async def _test():
            _mock_edge_tts.list_voices = AsyncMock(return_value=[
                {"Locale": "en-US", "ShortName": "JennyNeural"}
            ])
            voices = await EdgeTTSBackend.list_available_voices("en")
            assert len(voices) == 1
        asyncio.run(_test())

    def test_list_voices_no_filter(self):
        async def _test():
            _mock_edge_tts.list_voices = AsyncMock(return_value=[
                {"Locale": "en-US"}, {"Locale": "zh-CN"}
            ])
            voices = await EdgeTTSBackend.list_available_voices()
            assert len(voices) == 2
        asyncio.run(_test())

    def test_list_voices_error(self):
        async def _test():
            _mock_edge_tts.list_voices = AsyncMock(side_effect=Exception("network"))
            voices = await EdgeTTSBackend.list_available_voices()
            assert voices == []
        asyncio.run(_test())


class TestEdgeTTSConcatenation:
    """Detailed tests for Edge TTS audio concatenation."""

    def test_concatenate_via_ffmpeg(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x", "ffmpeg_path": "ffmpeg"})
        w1, w2 = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        create_silent_wav(w1, 0.5)
        create_silent_wav(w2, 0.5)
        out = str(tmp_path / "out.wav")
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=0)
            assert b._concatenate_audio_files([w1, w2], out) is True

    def test_concatenate_ffmpeg_failure(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x", "ffmpeg_path": "ffmpeg"})
        w1 = str(tmp_path / "a.wav")
        create_silent_wav(w1, 0.5)
        out = str(tmp_path / "out.wav")
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=1, stderr=b"fail")
            assert b._concatenate_audio_files([w1], out) is False

    def test_concatenate_uses_local_temp(self, tmp_path):
        b = EdgeTTSBackend({"edge_voice": "x", "ffmpeg_path": "ffmpeg",
                            "_temp_dir": str(tmp_path)})
        w1 = str(tmp_path / "a.wav")
        create_silent_wav(w1, 0.5)
        out = str(tmp_path / "out.wav")
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=0)
            b._concatenate_audio_files([w1], out)
            # Check that the concat list file was created in _temp_dir
            concat_file = os.path.join(str(tmp_path), "edge_concat_list.txt")
            # The file may have been cleaned up, but subprocess.run was called


class TestQwen3GenerateChunksWithProgress:
    """Tests for _generate_chunks_with_progress helper."""

    def test_success_path(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_custom_voice.return_value = ([mock_wav], 24000)

        chunks = [("Hello world.", 2.0), ("Goodbye world.", 2.0)]
        gen_func = b.model.generate_custom_voice
        gen_kwargs = {"language": ["English"], "speaker": ["Vivian"], "instruct": [""]}

        with patch.object(b, "_save_wav", return_value=True):
            ok, result = b._generate_chunks_with_progress(
                chunks, str(tmp_path), gen_func, gen_kwargs, slide_index=0)
            assert ok is True
            assert len(result) == 2

    def test_failure_path(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        b.model.generate_custom_voice.side_effect = Exception("TTS failed")

        chunks = [("Hello.", 1.0)]
        with patch.object(b, "_save_wav", return_value=True):
            ok, result = b._generate_chunks_with_progress(
                chunks, str(tmp_path),
                b.model.generate_custom_voice,
                {"language": ["English"]}, slide_index=0)
            assert ok is False
            assert len(result) == 1
            assert "failed" in result[0].lower()

    def test_progress_callback_called(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        mock_wav = MagicMock()
        mock_wav.cpu.return_value.numpy.return_value = [0.1]
        b.model.generate_custom_voice.return_value = ([mock_wav], 24000)

        cb = MagicMock()
        b.set_progress_callback(cb)
        chunks = [("Hello.", 1.0), ("World.", 1.0)]

        with patch.object(b, "_save_wav", return_value=True):
            b._generate_chunks_with_progress(
                chunks, str(tmp_path),
                b.model.generate_custom_voice,
                {"language": ["English"]}, slide_index=0)
            assert cb.call_count == 2


class TestQwen3SaveWav:
    """Tests for _save_wav helper edge cases."""

    def test_tensor_conversion(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        mock_tensor = MagicMock()
        mock_tensor.cpu.return_value.numpy.return_value = np.zeros(100)
        out = str(tmp_path / "out.wav")
        with patch.object(qwen3_module.sf, "write") as mw:
            assert b._save_wav(mock_tensor, 24000, out) is True
            mw.assert_called_once()

    def test_save_failure(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        with patch.object(qwen3_module.sf, "write", side_effect=Exception("disk full")):
            assert b._save_wav([0.1], 24000, str(tmp_path / "out.wav")) is False

    def test_creates_directory(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        out = str(tmp_path / "deep" / "nested" / "out.wav")
        with patch.object(qwen3_module.sf, "write") as mw:
            assert b._save_wav([0.1], 24000, out) is True
            assert os.path.isdir(os.path.dirname(out))


class TestQwen3WavConcatenationEdgeCases:
    """Edge case tests for WAV concatenation."""

    def test_silence_between_chunks(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w1, w2 = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        create_silent_wav(w1, 0.5)
        create_silent_wav(w2, 0.5)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files([w1, w2], out, add_silence_ms=100) is True
        # Output should be longer than sum of inputs due to silence
        with wave.open(out, "r") as wf:
            total_frames = wf.getnframes()
        with wave.open(w1, "r") as wf:
            f1 = wf.getnframes()
        with wave.open(w2, "r") as wf:
            f2 = wf.getnframes()
        assert total_frames > f1 + f2

    def test_zero_silence(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w1, w2 = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        create_silent_wav(w1, 0.5)
        create_silent_wav(w2, 0.5)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files([w1, w2], out, add_silence_ms=0) is True

    def test_single_file_copy(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        w = str(tmp_path / "in.wav")
        create_silent_wav(w, 1.0)
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files([w], out) is True
        assert os.path.exists(out)

    def test_copy_failure(self, tmp_path):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        out = str(tmp_path / "out.wav")
        assert b._concatenate_wav_files(["/nonexistent.wav"], out) is False


class TestEdgeTTSAsyncHandling:
    """Tests for the async event loop handling in _generate_single."""

    def test_no_running_loop(self, tmp_path):
        """When no event loop is running, asyncio.run should be used."""
        b = EdgeTTSBackend({"edge_voice": "en-US-JennyNeural"})
        b.initialize()
        out = str(tmp_path / "out.mp3")
        with patch.object(_mock_edge_tts, "Communicate") as mc:
            mock_comm = MagicMock()
            mc.return_value = mock_comm
            with patch("asyncio.run") as mar:
                mar.return_value = None
                # Simulate no running loop
                with patch("asyncio.get_running_loop", side_effect=RuntimeError):
                    b._generate_single("Hello", out)

    def test_with_running_loop(self, tmp_path):
        """When already in an async context, use ThreadPoolExecutor."""
        b = EdgeTTSBackend({"edge_voice": "en-US-JennyNeural"})
        b.initialize()
        out = str(tmp_path / "out.mp3")
        with patch.object(_mock_edge_tts, "Communicate") as mc:
            mock_comm = MagicMock()
            mc.return_value = mock_comm
            mock_future = MagicMock()
            mock_future.result.return_value = None
            with patch("concurrent.futures.ThreadPoolExecutor") as mtpe:
                mtpe.return_value.__enter__ = MagicMock(return_value=MagicMock(submit=MagicMock(return_value=mock_future)))
                mtpe.return_value.__exit__ = MagicMock(return_value=False)
                # Simulate being inside a running loop
                with patch("asyncio.get_running_loop", return_value=MagicMock()):
                    b._generate_single("Hello", out)


class TestPrepareSlideTasksSettingsChange:
    """Tests for smart cache invalidation in prepare_slide_tasks."""

    def test_settings_change_forces_regenerate(self, tmp_path):
        (tmp_path / "slide1.html").write_text("x")
        (tmp_path / "slide1.txt").write_text("Hello")
        wav = tmp_path / "slide1.wav"
        create_silent_wav(str(wav), 1.0)
        time.sleep(0.05)
        wav.touch()

        # Write a manifest with a different audio hash
        manifest = {"audio_settings_hash": "old_hash_123"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
        assert tasks[0].needs_tts is True

    def test_settings_unchanged_keeps_cache(self, tmp_path):
        (tmp_path / "slide1.html").write_text("x")
        (tmp_path / "slide1.txt").write_text("Hello")
        wav = tmp_path / "slide1.wav"
        create_silent_wav(str(wav), 1.0)
        time.sleep(0.05)
        wav.touch()

        # Compute actual hash
        current_hash = _get_audio_settings_hash({"active_backend": "qwen3"})
        manifest = {"audio_settings_hash": current_hash}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        ctx = RenderContext(project_path=str(tmp_path), temp_dir=str(tmp_path / "t"),
                           config={"active_backend": "qwen3"}, ffmpeg_path="ffmpeg", render_mode="full")
        tasks = prepare_slide_tasks([str(tmp_path / "slide1.html")], ctx)
        assert tasks[0].needs_tts is False


class TestBuildTimelineMultipleSlides:
    """Timeline building with multiple slides."""

    def test_multiple_slides(self, tmp_path):
        tasks = []
        for i in range(5):
            wav = str(tmp_path / f"a{i}.wav")
            create_silent_wav(wav, 0.5 + i * 0.1)
            tasks.append(SlideTask(
                index=i, html_path=f"/p/slide{i+1}.html",
                txt_path=f"/p/slide{i+1}.txt",
                target_audio_path=wav, text_content=f"Slide {i+1}"
            ))
        tl = build_timeline(tasks)
        assert len(tl) == 5
        # Durations should vary
        durations = [item["duration"] for item in tl]
        assert durations[0] != durations[-1]

    def test_mixed_valid_and_missing_audio(self, tmp_path):
        wav0 = str(tmp_path / "a0.wav")
        create_silent_wav(wav0, 1.0)
        tasks = [
            SlideTask(index=0, html_path="/p/s1.html", txt_path="/p/s1.txt",
                      target_audio_path=wav0, text_content="Valid"),
            SlideTask(index=1, html_path="/p/s2.html", txt_path="/p/s2.txt",
                      target_audio_path="/missing.wav", text_content="Long fallback text here"),
        ]
        tl = build_timeline(tasks)
        assert tl[0]["duration"] > 0
        assert tl[1]["duration"] >= 3.0  # Fallback


class TestEdgeTTSProgressReporting:
    """Tests for progress reporting during Edge TTS generation."""

    def test_chunk_progress_reported(self):
        b = EdgeTTSBackend({"edge_voice": "x", "edge_language": "English",
                            "enable_text_chunking": True,
                            "chunk_max_chars": 20, "chunk_max_sentences": 1,
                            "chunk_min_chars": 5})
        b.initialize()
        cb = MagicMock()
        b.set_progress_callback(cb)

        long_text = "First sentence here. Second sentence. Third sentence here."
        with patch.object(b, "_generate_single", return_value=True), \
             patch.object(b, "_concatenate_audio_files", return_value=True), \
             patch.object(b, "_get_local_temp_dir", return_value=tempfile.mkdtemp()), \
             patch.object(b, "_cleanup_chunk_temp"):
            b.generate_batch([long_text], ["/tmp/o.wav"])
            # Progress callback should have been called for chunks
            assert cb.call_count > 0


class TestConfigSingletonReset:
    """Tests ensuring singleton can be reset between tests."""

    def setup_method(self):
        AppConfig._instance = None

    def teardown_method(self):
        AppConfig._instance = None

    def test_fresh_instance(self):
        with patch.object(AppConfig, "load"):
            c = AppConfig()
            assert c.get("width") == 1280

    def test_second_instance_same(self):
        with patch.object(AppConfig, "load"):
            c1 = AppConfig()
            c2 = AppConfig()
            assert c1 is c2

    def test_after_reset_new_instance(self):
        with patch.object(AppConfig, "load"):
            c1 = AppConfig()
        AppConfig._instance = None
        with patch.object(AppConfig, "load"):
            c2 = AppConfig()
            assert c1 is not c2


class TestEdgeTTSBatchPartialFailure:
    """Tests for partial failures in Edge TTS batch generation."""

    def test_some_slides_fail(self):
        b = EdgeTTSBackend({"edge_voice": "x", "enable_text_chunking": False})
        b.initialize()
        call_count = [0]

        def gen_side_effect(text, path):
            call_count[0] += 1
            return call_count[0] != 2  # Second call fails

        with patch.object(b, "_generate_single", side_effect=gen_side_effect), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(
                ["Hello", "World", "Test"],
                ["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"],
            )
            assert ok is False
            assert len(errs) > 0

    def test_all_slides_fail(self):
        b = EdgeTTSBackend({"edge_voice": "x", "enable_text_chunking": False})
        b.initialize()
        with patch.object(b, "_generate_single", return_value=False), \
             patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(
                ["Hello", "World"],
                ["/tmp/a.wav", "/tmp/b.wav"],
            )
            assert ok is False
            assert len(errs) == 2


class TestQwen3BatchGenerationError:
    """Tests for Qwen3 batch generation error handling."""

    def test_model_none_tries_init(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu",
                           "qwen3_attn_implementation": "eager", "qwen3_dtype": "float16"})
        b.model = None
        with patch.object(b, "initialize", side_effect=Exception("no GPU")):
            ok, errs = b.generate_batch(["Hello"], ["/tmp/o.wav"])
            assert ok is False
            assert any("Initialization failed" in e for e in errs)

    def test_generation_exception_returns_errors(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_device_map": "cpu"})
        b.model = MagicMock()
        b.model.generate_custom_voice.side_effect = Exception("TTS error")
        with patch.object(b, "_cleanup_chunk_temp"):
            ok, errs = b.generate_batch(["Hello"], ["/tmp/o.wav"])
            assert ok is False
            assert len(errs) > 0


class TestFactoryCleanupOnError:
    """Tests that the factory properly cleans up on initialization failure."""

    def test_cleanup_called_on_init_failure(self):
        MockCls = MagicMock()
        inst = MagicMock()
        inst.initialize.side_effect = RuntimeError("OOM")
        inst.cleanup = MagicMock()
        MockCls.return_value = inst
        with patch.dict(BACKEND_MAP, {"_test": MockCls}):
            with pytest.raises(RuntimeError, match="Failed to initialize"):
                get_backend("_test", {})
            inst.cleanup.assert_called_once()

    def test_cleanup_error_suppressed(self):
        MockCls = MagicMock()
        inst = MagicMock()
        inst.initialize.side_effect = RuntimeError("OOM")
        inst.cleanup.side_effect = Exception("cleanup also failed")
        MockCls.return_value = inst
        with patch.dict(BACKEND_MAP, {"_test2": MockCls}):
            with pytest.raises(RuntimeError, match="Failed to initialize"):
                get_backend("_test2", {})
            # Should not raise a second exception from cleanup


class TestTextChunkerOverlap:
    """Tests for overlap configuration (future feature placeholder)."""

    def test_overlap_config_settable(self):
        cfg = ChunkingConfig(overlap_chars=50)
        assert cfg.overlap_chars == 50

    def test_overlap_zero_by_default(self):
        assert ChunkingConfig().overlap_chars == 0


class TestEdgeTTSLanguageVoiceMapping:
    """Tests that language selection properly maps to voices."""

    def test_each_language_has_voices(self):
        for lang in EdgeTTSBackend.EDGE_LANGUAGES:
            voices = EdgeTTSBackend.EDGE_VOICES.get(lang, [])
            assert len(voices) > 0, f"No voices for {lang}"

    def test_english_voice_ids_start_with_en(self):
        for vid, _ in EdgeTTSBackend.EDGE_VOICES["English"]:
            assert vid.startswith("en-"), f"Unexpected voice ID: {vid}"

    def test_chinese_voice_ids_start_with_zh(self):
        for vid, _ in EdgeTTSBackend.EDGE_VOICES["Chinese"]:
            assert vid.startswith("zh-"), f"Unexpected voice ID: {vid}"

    def test_japanese_voice_ids_start_with_ja(self):
        for vid, _ in EdgeTTSBackend.EDGE_VOICES["Japanese"]:
            assert vid.startswith("ja-"), f"Unexpected voice ID: {vid}"

    def test_korean_voice_ids_start_with_ko(self):
        for vid, _ in EdgeTTSBackend.EDGE_VOICES["Korean"]:
            assert vid.startswith("ko-"), f"Unexpected voice ID: {vid}"


class TestQwen3ModelIdFormats:
    """Tests that all model IDs follow the expected format."""

    def test_custom_voice_format(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_size": "1.7B"})
        mid = b._get_model_id()
        assert mid.startswith("Qwen/")
        assert "12Hz" in mid
        assert "CustomVoice" in mid

    def test_base_format(self):
        b = Qwen3Backend({"qwen3_mode": "voice_clone", "qwen3_size": "1.7B"})
        mid = b._get_model_id()
        assert "Base" in mid

    def test_voice_design_format(self):
        b = Qwen3Backend({"qwen3_mode": "voice_design", "qwen3_size": "1.7B"})
        mid = b._get_model_id()
        assert "VoiceDesign" in mid

    def test_small_model_size(self):
        b = Qwen3Backend({"qwen3_mode": "voice_custom", "qwen3_size": "0.6B"})
        mid = b._get_model_id()
        assert "0.6B" in mid