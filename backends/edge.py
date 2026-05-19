# backends/edge.py
"""
Edge TTS Backend Plugin.
Uses Microsoft Edge's online TTS service via the edge-tts package.

FEATURES:
- Free online TTS with high-quality neural voices
- Multiple languages and voice options
- Rate and pitch adjustment
- Long text handling with automatic chunking
- Audio concatenation for chunked segments
- Progress reporting for multi-chunk generation
"""

import os
import gc
import shutil
import logging
import asyncio
import wave
import tempfile
import struct
from typing import List, Tuple, Optional, Dict

# Import text chunker for long text handling
from text_chunker import TextChunker, ChunkingConfig, get_chunker_for_language

# ==============================================================================
# LOGIC IMPORTS
# ==============================================================================
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    edge_tts = None
    EDGE_TTS_AVAILABLE = False

# ==============================================================================
# UI IMPORTS
# ==============================================================================
from PySide6.QtWidgets import (
    QWidget, QFormLayout, QComboBox, QLineEdit, 
    QPlainTextEdit, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QSlider, QSpinBox, QDoubleSpinBox
)
from PySide6.QtCore import Qt

from backends.base import BaseTTSBackend
from utils import logger


class EdgeTTSBackend(BaseTTSBackend):
    """
    Concrete implementation of BaseTTSBackend for Microsoft Edge TTS.
    
    Uses the edge-tts package to access Microsoft's neural TTS voices
    available in the Edge browser. This is a free online service requiring
    an internet connection.
    """

    # Available voice categories organized by language/region
    EDGE_VOICES = {
        # English voices
        "English": [
            ("en-US-AvaMultilingualNeural", "Ava (US, Multilingual)"),
            ("en-US-AndrewMultilingualNeural", "Andrew (US, Multilingual)"),
            ("en-US-EmmaMultilingualNeural", "Emma (US, Multilingual)"),
            ("en-US-BrianMultilingualNeural", "Brian (US, Multilingual)"),
            ("en-US-AvaNeural", "Ava (US, Female)"),
            ("en-US-AndrewNeural", "Andrew (US, Male)"),
            ("en-US-EmmaNeural", "Emma (US, Female)"),
            ("en-US-BrianNeural", "Brian (US, Male)"),
            ("en-US-JennyNeural", "Jenny (US, Female)"),
            ("en-US-GuyNeural", "Guy (US, Male)"),
            ("en-GB-SoniaNeural", "Sonia (UK, Female)"),
            ("en-GB-RyanNeural", "Ryan (UK, Male)"),
            ("en-AU-NatashaNeural", "Natasha (AU, Female)"),
            ("en-AU-WilliamNeural", "William (AU, Male)"),
            ("en-CA-ClaraNeural", "Clara (CA, Female)"),
            ("en-CA-LiamNeural", "Liam (CA, Male)"),
            ("en-IN-NeerjaNeural", "Neerja (IN, Female)"),
            ("en-IN-PrabhatNeural", "Prabhat (IN, Male)"),
        ],
        # Chinese voices
        "Chinese": [
            ("zh-CN-XiaoxiaoNeural", "Xiaoxiao (Female)"),
            ("zh-CN-YunxiNeural", "Yunxi (Male)"),
            ("zh-CN-YunjianNeural", "Yunjian (Male)"),
            ("zh-CN-XiaoyiNeural", "Xiaoyi (Female)"),
            ("zh-CN-YunyangNeural", "Yunyang (Male)"),
            ("zh-CN-XiaochenNeural", "Xiaochen (Female)"),
            ("zh-CN-XiaohanNeural", "Xiaohan (Female)"),
            ("zh-CN-XiaomengNeural", "Xiaomeng (Female)"),
            ("zh-CN-XiaomoNeural", "Xiaomo (Female)"),
            ("zh-CN-XiaoruiNeural", "Xiaorui (Female)"),
            ("zh-CN-XiaoshuangNeural", "Xiaoshuang (Female)"),
            ("zh-CN-XiaoxuanNeural", "Xiaoxuan (Female)"),
            ("zh-CN-XiaoyanNeural", "Xiaoyan (Female)"),
            ("zh-CN-XiaoyouNeural", "Xiaoyou (Female)"),
            ("zh-CN-YunfengNeural", "Yunfeng (Male)"),
            ("zh-CN-YunhaoNeural", "Yunhao (Male)"),
            ("zh-CN-YunxiaNeural", "Yunxia (Male)"),
            ("zh-CN-YunyeNeural", "Yunye (Male)"),
            ("zh-CN-YunzeNeural", "Yunze (Male)"),
            ("zh-HK-HiuGaaiNeural", "HiuGaai (HK, Female)"),
            ("zh-HK-HiuMaanNeural", "HiuMaan (HK, Female)"),
            ("zh-HK-WanLungNeural", "WanLung (HK, Male)"),
            ("zh-TW-HsiaoChenNeural", "HsiaoChen (TW, Female)"),
            ("zh-TW-HsiaoYuNeural", "HsiaoYu (TW, Female)"),
            ("zh-TW-YunJheNeural", "YunJhe (TW, Male)"),
        ],
        # Japanese voices
        "Japanese": [
            ("ja-JP-NanamiNeural", "Nanami (Female)"),
            ("ja-JP-KeitaNeural", "Keita (Male)"),
            ("ja-JP-AoiNeural", "Aoi (Female)"),
            ("ja-JP-DaichiNeural", "Daichi (Male)"),
            ("ja-JP-MayuNeural", "Mayu (Female)"),
            ("ja-JP-NaokiNeural", "Naoki (Male)"),
            ("ja-JP-ShioriNeural", "Shiori (Female)"),
        ],
        # Korean voices
        "Korean": [
            ("ko-KR-SunHiNeural", "SunHi (Female)"),
            ("ko-KR-InJoonNeural", "InJoon (Male)"),
            ("ko-KR-BongJinNeural", "BongJin (Male)"),
            ("ko-KR-GookMinNeural", "GookMin (Male)"),
            ("ko-KR-JiMinNeural", "JiMin (Female)"),
            ("ko-KR-MinJungNeural", "MinJung (Female)"),
            ("ko-KR-MinSuNeural", "MinSu (Male)"),
            ("ko-KR-SeoHyeonNeural", "SeoHyeon (Female)"),
            ("ko-KR-SoonBokNeural", "SoonBok (Female)"),
            ("ko-KR-YuJinNeural", "YuJin (Female)"),
        ],
        # German voices
        "German": [
            ("de-DE-KatjaNeural", "Katja (Female)"),
            ("de-DE-ConradNeural", "Conrad (Male)"),
            ("de-DE-AmalaNeural", "Amala (Female)"),
            ("de-DE-BerndNeural", "Bernd (Male)"),
            ("de-DE-ChristophNeural", "Christoph (Male)"),
            ("de-DE-ElkeNeural", "Elke (Female)"),
            ("de-DE-GiselaNeural", "Gisela (Female)"),
            ("de-DE-KasperNeural", "Kasper (Male)"),
            ("de-DE-KillianNeural", "Killian (Male)"),
            ("de-DE-KlarissaNeural", "Klarissa (Female)"),
            ("de-DE-KlausNeural", "Klaus (Male)"),
            ("de-DE-LouisaNeural", "Louisa (Female)"),
            ("de-DE-MajaNeural", "Maja (Female)"),
            ("de-DE-RalfNeural", "Ralf (Male)"),
            ("de-DE-TanjaNeural", "Tanja (Female)"),
        ],
        # French voices
        "French": [
            ("fr-FR-DeniseNeural", "Denise (Female)"),
            ("fr-FR-HenriNeural", "Henri (Male)"),
            ("fr-FR-AlainNeural", "Alain (Male)"),
            ("fr-FR-BrigitteNeural", "Brigitte (Female)"),
            ("fr-FR-CelesteNeural", "Celeste (Female)"),
            ("fr-FR-CoralieNeural", "Coralie (Female)"),
            ("fr-FR-EloiseNeural", "Eloise (Female)"),
            ("fr-FR-JacquelineNeural", "Jacqueline (Female)"),
            ("fr-FR-JeromeNeural", "Jerome (Male)"),
            ("fr-FR-JosephineNeural", "Josephine (Female)"),
            ("fr-FR-MauriceNeural", "Maurice (Male)"),
            ("fr-FR-YvesNeural", "Yves (Male)"),
            ("fr-FR-YvetteNeural", "Yvette (Female)"),
            ("fr-CA-AntoineNeural", "Antoine (CA, Male)"),
            ("fr-CA-JeanNeural", "Jean (CA, Male)"),
            ("fr-CA-SylvieNeural", "Sylvie (CA, Female)"),
        ],
        # Russian voices
        "Russian": [
            ("ru-RU-SvetlanaNeural", "Svetlana (Female)"),
            ("ru-RU-DmitryNeural", "Dmitry (Male)"),
            ("ru-RU-DariyaNeural", "Dariya (Female)"),
        ],
        # Portuguese voices
        "Portuguese": [
            ("pt-BR-FranciscaNeural", "Francisca (BR, Female)"),
            ("pt-BR-AntonioNeural", "Antonio (BR, Male)"),
            ("pt-BR-BrendaNeural", "Brenda (BR, Female)"),
            ("pt-BR-DonatoNeural", "Donato (BR, Male)"),
            ("pt-BR-ElzaNeural", "Elza (BR, Female)"),
            ("pt-BR-FabioNeural", "Fabio (BR, Male)"),
            ("pt-BR-GiovannaNeural", "Giovanna (BR, Female)"),
            ("pt-BR-HumbertoNeural", "Humberto (BR, Male)"),
            ("pt-BR-JulioNeural", "Julio (BR, Male)"),
            ("pt-BR-LeilaNeural", "Leila (BR, Female)"),
            ("pt-BR-LeticiaNeural", "Leticia (BR, Female)"),
            ("pt-BR-ManuelaNeural", "Manuela (BR, Female)"),
            ("pt-BR-NicolauNeural", "Nicolau (BR, Male)"),
            ("pt-BR-ValerioNeural", "Valerio (BR, Male)"),
            ("pt-BR-YaraNeural", "Yara (BR, Female)"),
            ("pt-PT-RaquelNeural", "Raquel (PT, Female)"),
            ("pt-PT-DuarteNeural", "Duarte (PT, Male)"),
        ],
        # Spanish voices
        "Spanish": [
            ("es-ES-ElviraNeural", "Elvira (ES, Female)"),
            ("es-ES-AlvaroNeural", "Alvaro (ES, Male)"),
            ("es-ES-AbrilNeural", "Abril (ES, Female)"),
            ("es-ES-DarioNeural", "Dario (ES, Male)"),
            ("es-ES-ElenaNeural", "Elena (ES, Female)"),
            ("es-ES-EstrellaNeural", "Estrella (ES, Female)"),
            ("es-ES-IreneNeural", "Irene (ES, Female)"),
            ("es-ES-LauraNeural", "Laura (ES, Female)"),
            ("es-ES-LolaNeural", "Lola (ES, Female)"),
            ("es-ES-MariaNeural", "Maria (ES, Female)"),
            ("es-ES-MartinNeural", "Martin (ES, Male)"),
            ("es-ES-RaulNeural", "Raul (ES, Male)"),
            ("es-ES-SaulNeural", "Saul (ES, Male)"),
            ("es-ES-TeoNeural", "Teo (ES, Male)"),
            ("es-ES-TrianaNeural", "Triana (ES, Female)"),
            ("es-ES-VeraNeural", "Vera (ES, Female)"),
            ("es-MX-DaliaNeural", "Dalia (MX, Female)"),
            ("es-MX-JorgeNeural", "Jorge (MX, Male)"),
            ("es-MX-BeatrizNeural", "Beatriz (MX, Female)"),
            ("es-MX-CandelaNeural", "Candela (MX, Female)"),
            ("es-MX-CarlotaNeural", "Carlota (MX, Female)"),
            ("es-MX-CecilioNeural", "Cecilio (MX, Male)"),
            ("es-MX-GerardoNeural", "Gerardo (MX, Male)"),
            ("es-MX-LarissaNeural", "Larissa (MX, Female)"),
            ("es-MX-LibertoNeural", "Liberto (MX, Male)"),
            ("es-MX-LucianoNeural", "Luciano (MX, Male)"),
            ("es-MX-MarinaNeural", "Marina (MX, Female)"),
            ("es-MX-NuriaNeural", "Nuria (MX, Female)"),
            ("es-MX-PelayoNeural", "Pelayo (MX, Male)"),
            ("es-MX-RenataNeural", "Renata (MX, Female)"),
            ("es-MX-YagoNeural", "Yago (MX, Male)"),
        ],
        # Italian voices
        "Italian": [
            ("it-IT-ElsaNeural", "Elsa (Female)"),
            ("it-IT-IsabellaNeural", "Isabella (Female)"),
            ("it-IT-DiegoNeural", "Diego (Male)"),
            ("it-IT-BenignoNeural", "Benigno (Male)"),
            ("it-IT-CalimeroNeural", "Calimero (Male)"),
            ("it-IT-CataldoNeural", "Cataldo (Male)"),
            ("it-IT-FabiolaNeural", "Fabiola (Female)"),
            ("it-IT-FiammaNeural", "Fiamma (Female)"),
            ("it-IT-GiuseppeNeural", "Giuseppe (Male)"),
            ("it-IT-ImeldaNeural", "Imelda (Female)"),
            ("it-IT-IrmaNeural", "Irma (Female)"),
            ("it-IT-LisandroNeural", "Lisandro (Male)"),
            ("it-IT-PalmiraNeural", "Palmira (Female)"),
            ("it-IT-PierinaNeural", "Pierina (Female)"),
            ("it-IT-RinaldoNeural", "Rinaldo (Male)"),
        ],
    }

    # Supported languages for the language dropdown
    EDGE_LANGUAGES = list(EDGE_VOICES.keys())

    @staticmethod
    def get_ui_options() -> Dict:
        return {
            "languages": EdgeTTSBackend.EDGE_LANGUAGES,
            "voices": EdgeTTSBackend.EDGE_VOICES
        }

    # =================================================================
    # UI GENERATION FACTORY
    # =================================================================

    @staticmethod
    def get_settings_widget(mode: str, config, parent=None) -> QWidget:
        """
        Generate the settings widget for Edge TTS configuration.
        
        Note: 'mode' is ignored for Edge TTS as it has a single mode.
        """
        widget = QWidget(parent)
        layout = QFormLayout(widget)
        
        layout.addRow(QLabel("<b>Edge TTS Configuration</b>"))
        layout.addRow(QLabel("<i>Free online TTS using Microsoft Edge neural voices</i>"))
        layout.addRow(QLabel("<i>Requires internet connection</i>"))
        
        # Language selector
        lang_combo = QComboBox()
        lang_combo.addItems(EdgeTTSBackend.EDGE_LANGUAGES)
        current_lang = config.get("edge_language", "English")
        if current_lang in EdgeTTSBackend.EDGE_LANGUAGES:
            lang_combo.setCurrentText(current_lang)
        
        def on_language_changed(lang):
            config.set("edge_language", lang)
            # Update voice dropdown based on language
            voice_combo = widget.findChild(QComboBox, "voice_combo")
            if voice_combo:
                voice_combo.clear()
                voices = EdgeTTSBackend.EDGE_VOICES.get(lang, [])
                for voice_id, voice_name in voices:
                    voice_combo.addItem(voice_name, voice_id)
                # Set default voice for this language
                if voices:
                    current_voice = config.get("edge_voice", "")
                    found = False
                    for i in range(voice_combo.count()):
                        if voice_combo.itemData(i) == current_voice:
                            voice_combo.setCurrentIndex(i)
                            found = True
                            break
                    if not found:
                        voice_combo.setCurrentIndex(0)
                        config.set("edge_voice", voices[0][0])
        
        lang_combo.currentTextChanged.connect(on_language_changed)
        layout.addRow("Language:", lang_combo)
        
        # Voice selector (populated based on language)
        voice_combo = QComboBox()
        voice_combo.setObjectName("voice_combo")
        voices = EdgeTTSBackend.EDGE_VOICES.get(current_lang, [])
        for voice_id, voice_name in voices:
            voice_combo.addItem(voice_name, voice_id)
        
        current_voice = config.get("edge_voice", "")
        if current_voice:
            for i in range(voice_combo.count()):
                if voice_combo.itemData(i) == current_voice:
                    voice_combo.setCurrentIndex(i)
                    break
        
        def on_voice_changed(index):
            voice_id = voice_combo.itemData(index)
            config.set("edge_voice", voice_id)
        
        voice_combo.currentIndexChanged.connect(on_voice_changed)
        layout.addRow("Voice:", voice_combo)
        
        # Speech rate slider
        rate_layout = QHBoxLayout()
        rate_slider = QSlider(Qt.Orientation.Horizontal)
        rate_slider.setMinimum(-100)
        rate_slider.setMaximum(100)
        rate_slider.setValue(
            EdgeTTSBackend._parse_slider_value(
                config.get("edge_rate", "+0%"), prefix_chars="+", suffix_chars="%"
            )
        )
        rate_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        rate_slider.setTickInterval(25)
        
        rate_label = QLabel(f"{rate_slider.value()}%")
        rate_label.setMinimumWidth(50)
        
        def on_rate_changed(value):
            rate_label.setText(f"{value}%")

        def on_rate_released():
            config.set("edge_rate", f"{rate_slider.value():+d}%")
        
        rate_slider.valueChanged.connect(on_rate_changed)
        rate_slider.sliderReleased.connect(on_rate_released)
        rate_layout.addWidget(rate_slider)
        rate_layout.addWidget(rate_label)
        layout.addRow("Speech Rate:", rate_layout)
        
        # Pitch slider
        pitch_layout = QHBoxLayout()
        pitch_slider = QSlider(Qt.Orientation.Horizontal)
        pitch_slider.setMinimum(-50)
        pitch_slider.setMaximum(50)
        pitch_slider.setValue(
            EdgeTTSBackend._parse_slider_value(
                config.get("edge_pitch", "+0Hz"), prefix_chars="+", suffix_chars="Hz"
            )
        )
        pitch_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        pitch_slider.setTickInterval(10)
        
        pitch_label = QLabel(f"{pitch_slider.value():+d}Hz")
        pitch_label.setMinimumWidth(50)
        
        def on_pitch_changed(value):
            pitch_label.setText(f"{value:+d}Hz")
        
        def on_pitch_released():
            config.set("edge_pitch", f"{pitch_slider.value():+d}Hz")
        
        pitch_slider.valueChanged.connect(on_pitch_changed)
        pitch_slider.sliderReleased.connect(on_pitch_released)
        pitch_layout.addWidget(pitch_slider)
        pitch_layout.addWidget(pitch_label)
        layout.addRow("Pitch:", pitch_layout)
        
        # Volume slider
        volume_layout = QHBoxLayout()
        volume_slider = QSlider(Qt.Orientation.Horizontal)
        volume_slider.setMinimum(-100)
        volume_slider.setMaximum(100)
        volume_slider.setValue(
            EdgeTTSBackend._parse_slider_value(
                config.get("edge_volume", "+0%"), prefix_chars="+", suffix_chars="%"
            )
        )
        volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        volume_slider.setTickInterval(25)
        
        volume_label = QLabel(f"{volume_slider.value():+d}%")
        volume_label.setMinimumWidth(50)
        
        def on_volume_changed(value):
            volume_label.setText(f"{value:+d}%")

        def on_volume_released():
            config.set("edge_volume", f"{volume_slider.value():+d}%")
        
        volume_slider.valueChanged.connect(on_volume_changed)
        volume_slider.sliderReleased.connect(on_volume_released)
        volume_layout.addWidget(volume_slider)
        volume_layout.addWidget(volume_label)
        layout.addRow("Volume:", volume_layout)
        
        # Initialize voice dropdown with current language
        on_language_changed(current_lang)
        
        return widget

    @staticmethod
    def _parse_slider_value(raw_value: str, prefix_chars: str = "", suffix_chars: str = "%", default: int = 0) -> int:
        """Safely parse a config string like '+50%' or '-25Hz' into an int for a slider."""
        if not isinstance(raw_value, str):
            return default
        try:
            cleaned = raw_value
            for ch in prefix_chars:
                cleaned = cleaned.replace(ch, "")
            for ch in suffix_chars:
                cleaned = cleaned.replace(ch, "")
            return int(cleaned)
        except (ValueError, TypeError):
            return default
    # =================================================================
    # INITIALIZATION
    # =================================================================

    def __init__(self, config: dict):
        super().__init__(config)
        
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts library is not installed. Install via: pip install edge-tts")

    def initialize(self):
        """
        Edge TTS is an online service, so no model loading is required.
        This method validates the configuration.
        """
        logger.info("[EdgeTTSBackend] Initializing (online service, no local models needed)")
        
        # Validate voice setting
        voice = self.config.get("edge_voice", "")
        if not voice:
            # Set default voice based on language
            language = self.config.get("edge_language", "English")
            voices = self.EDGE_VOICES.get(language, [])
            if voices:
                self.config["edge_voice"] = voices[0][0]
                logger.info(f"[EdgeTTSBackend] Set default voice: {voices[0][0]}")
        
        logger.info(f"[EdgeTTSBackend] Voice: {self.config.get('edge_voice')}")
        logger.info(f"[EdgeTTSBackend] Rate: {self.config.get('edge_rate', '+0%')}")
        logger.info(f"[EdgeTTSBackend] Pitch: {self.config.get('edge_pitch', '+0Hz')}")

    def cleanup(self):
        """
        No local resources to clean up for Edge TTS.
        """
        logger.info("[EdgeTTSBackend] Cleanup called (no local resources to free)")
        self._cleanup_chunk_temp()
        gc.collect()

    # =================================================================
    # GENERATION INTERFACE
    # =================================================================

    def generate_batch(self, texts, output_paths):
        self._validate_batch_inputs(texts, output_paths)
        
        enable_chunking = self.config.get("enable_text_chunking", True)
        chunker = self._get_chunker() if enable_chunking else None
        temp_dir = self._get_local_temp_dir("chunks_edge")
        language = self.config.get("edge_language", "English")

        errors = []
        success_count = 0

        for i, (text, out_path) in enumerate(zip(texts, output_paths)):
            logger.info(f"[EdgeTTSBackend] Generating slide {i + 1}/{len(texts)}...")
            success, error = self._generate_single_with_chunking(
                text, out_path, i, language, chunker,
                self._generate_single, self._concatenate_audio_files, temp_dir
            )
            if success:
                success_count += 1
            elif error:
                errors.append(error)

        self._cleanup_chunk_temp()
        return (True, []) if success_count == len(texts) else (False, errors)

    def _generate_single(self, text: str, output_path: str) -> bool:
        """
        Generate audio for a single text string.
        """
        # Validate text - must not be empty
        if not text or not text.strip():
            raise ValueError("Cannot generate audio: text is empty or whitespace only")
        
        voice = self.config.get("edge_voice", "en-US-JennyNeural")
        rate = self.config.get("edge_rate", "+0%")
        pitch = self.config.get("edge_pitch", "+0Hz")
        volume_raw = self.config.get("edge_volume", "+0%")
        
        # Convert volume to relative format if needed
        volume = volume_raw
        if volume_raw and not volume_raw.startswith(('+', '-')):
            try:
                abs_val = int(volume_raw.replace('%', ''))
                relative_val = abs_val - 100
                volume = f"{relative_val:+d}%"
            except ValueError:
                volume = "+0%"
        
        try:
            communicate = edge_tts.Communicate(
                text=text.strip(),
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume
            )

            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
            # Edge TTS always outputs MP3 regardless of file extension.
            # Save to a temporary MP3 file first, then convert to WAV if needed.
            base_temp = self.config.get('_temp_dir', '')
            if base_temp and os.path.isdir(base_temp):
                mp3_temp = os.path.join(base_temp, f"edge_tmp_{id(communicate)}.mp3")
            else:
                mp3_temp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name

            self._run_async(communicate.save(mp3_temp))

            if output_path.endswith('.wav'):
                # Convert MP3 → WAV
                if self._convert_mp3_to_wav(mp3_temp, output_path):
                    self._safe_remove(mp3_temp)
                else:
                    # Conversion failed; move MP3 as a fallback with correct extension
                    fallback = output_path.replace('.wav', '.mp3')
                    shutil.move(mp3_temp, fallback)
                    logger.warning(f"[EdgeTTSBackend] WAV conversion failed, saved as MP3: {fallback}")
                    return False
            elif output_path.endswith('.mp3'):
                shutil.move(mp3_temp, output_path)
            else:
                if self._convert_mp3_to_wav(mp3_temp, output_path):
                    self._safe_remove(mp3_temp)
                else:
                    shutil.move(mp3_temp, output_path)
            
        except Exception as e:
            logger.error(f"[EdgeTTSBackend] Generation failed: {e}")
            return False
        return True
    
    @staticmethod
    def _safe_remove(path: str):
        """Safely remove a file, ignoring errors if it doesn't exist."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _run_async(coro):
        """
        Run an async coroutine safely from any context.
        
        Since generate_batch is called from within an already-running
        event loop (via engines.py asyncio.run), we must always run
        edge-tts coroutines in a separate thread with their own loop.
        """
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    
    def _convert_mp3_to_wav(self, mp3_path: str, wav_path: str) -> bool:
        try:
            import subprocess
            ffmpeg_path = self.config.get("ffmpeg_path", "ffmpeg")
            result = subprocess.run(
                [ffmpeg_path, "-y", "-i", mp3_path, "-ar", "24000", "-ac", "1", wav_path],
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True
            else:
                logger.warning(f"FFmpeg conversion failed: {result.stderr.decode()}")
                return False
                
        except Exception as e:
            logger.warning(f"Could not convert MP3 to WAV: {e}")
            return False

    def _concatenate_audio_files(self, audio_files: List[str], output_path: str) -> bool:
        if not audio_files:
            logger.error("No audio files to concatenate")
            return False
        
        logger.info(f"[EdgeTTSBackend] Concatenating {len(audio_files)} audio chunks...")
        
        try:
            import subprocess
            import tempfile
            
            base_temp = self.config.get('_temp_dir', '')
            if base_temp and os.path.isdir(base_temp):
                concat_file = os.path.join(base_temp, "edge_concat_list.txt")
            else:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    concat_file = f.name
            
            with open(concat_file, 'w') as f:
                for audio_file in audio_files:
                    abs_path = os.path.abspath(audio_file).replace(os.sep, '/')
                    # Escape single quotes for FFmpeg concat demuxer: replace ' with '\''
                    escaped_path = abs_path.replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")
            
            ffmpeg_path = self.config.get("ffmpeg_path", "ffmpeg")
            
            cmd = [
                ffmpeg_path, "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-ar", "24000", "-ac", "1",
                output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            
            try:
                os.remove(concat_file)
            except:
                pass
            
            if result.returncode == 0:
                logger.info(f"[EdgeTTSBackend] Successfully concatenated to: {output_path}")
                return True
            else:
                logger.error(f"FFmpeg concatenation failed: {result.stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to concatenate audio files: {e}")
            return False

    def _get_chunker(self) -> TextChunker:
        """Get a configured TextChunker instance from config settings."""
        config = ChunkingConfig(
            max_chars=self.config.get("chunk_max_chars", 500),
            max_sentences=self.config.get("chunk_max_sentences", 5),
            min_chunk_chars=self.config.get("chunk_min_chars", 50),
            language=self.config.get("edge_language", "English")
        )
        return TextChunker(config)

    # =================================================================
    # UTILITY METHODS
    # =================================================================

    @staticmethod
    async def list_available_voices(language: str = None) -> List[Dict]:
        try:
            voices = await edge_tts.list_voices()
            if language:
                voices = [v for v in voices if v['Locale'].startswith(language)]
            return voices
        except Exception as e:
            logger.error(f"Failed to list voices: {e}")
            return []