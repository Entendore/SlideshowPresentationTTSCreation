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
        rate_slider.setValue(int(config.get("edge_rate", "0").replace("%", "")) if isinstance(config.get("edge_rate", "0%"), str) else 0)
        rate_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        rate_slider.setTickInterval(25)
        
        rate_label = QLabel(f"{rate_slider.value()}%")
        rate_label.setMinimumWidth(50)
        
        def on_rate_changed(value):
            rate_label.setText(f"{value}%")

        def on_rate_released():
            config.set("edge_rate", f"{rate_slider.value()}%")
        
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
        pitch_slider.setValue(int(config.get("edge_pitch", "+0Hz").replace("+", "").replace("Hz", "")) if isinstance(config.get("edge_pitch", "+0Hz"), str) else 0)
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
        volume_slider.setValue(0)  # Default to 0 (no change)
        volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        volume_slider.setTickInterval(25)

        def on_volume_released():
            config.set("edge_volume", f"{volume_slider.value():+d}%")
        
        # Parse current volume value
        current_volume = config.get("edge_volume", "+0%")
        if isinstance(current_volume, str):
            vol_str = current_volume.replace("%", "").replace("+", "")
            try:
                volume_slider.setValue(int(vol_str))
            except ValueError:
                volume_slider.setValue(0)
        
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
        gc.collect()

    # =================================================================
    # GENERATION INTERFACE
    # =================================================================

    def generate_batch(self, texts: List[str], output_paths: List[str]) -> Tuple[bool, List[str]]:
        """
        Generate audio for a batch of texts using Edge TTS.
        
        Args:
            texts: List of text strings to convert to speech
            output_paths: List of output file paths for the generated audio
            
        Returns:
            Tuple of (success, error_messages)
            
        Raises:
            ValueError: If all texts are empty (no valid content to generate)
        """
        errors = []
        success_count = 0
        
        # Validate that we have at least some non-empty text
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            raise ValueError("Cannot generate audio: all texts are empty or whitespace only")
        
        # Get chunker for long text handling
        enable_chunking = self.config.get("enable_text_chunking", True)
        chunker = self._get_chunker() if enable_chunking else None
        
        # Create temp directory for chunks
        temp_dir = tempfile.mkdtemp(prefix="edge_tts_chunks_")
        
        for i, text in enumerate(texts):
            logger.info(f"[EdgeTTSBackend] Generating slide {i+1}/{len(texts)}...")
            
            # Check if text needs chunking
            needs_chunking = chunker and chunker.needs_chunking(text)
            
            if needs_chunking:
                logger.info(f"[EdgeTTSBackend] Slide {i+1}: Long text detected ({len(text)} chars), chunking...")
                language = self.config.get("edge_language", "English")
                chunks = chunker.chunk_text(text, language)
                logger.info(f"[EdgeTTSBackend] Slide {i+1}: Split into {len(chunks)} chunks")
                
                chunk_paths = []
                chunk_errors = []
                total_chunks = len(chunks)
                
                for chunk_idx, (chunk_text, estimated_duration) in enumerate(chunks):
                    chunk_path = os.path.join(temp_dir, f"chunk_{i}_{chunk_idx}.mp3")
                    
                    # Report progress
                    self._report_progress(
                        "chunk",
                        chunk_idx + 1,
                        total_chunks,
                        f"Chunk {chunk_idx + 1}/{total_chunks}"
                    )
                    
                    try:
                        # Generate audio for this chunk
                        success = self._generate_single(chunk_text, chunk_path)
                        if success:
                            chunk_paths.append(chunk_path)
                        else:
                            chunk_errors.append(f"Failed to generate chunk {chunk_idx + 1}")
                    except Exception as e:
                        logger.error(f"Failed to generate chunk {chunk_idx + 1}: {e}")
                        chunk_errors.append(f"Chunk {chunk_idx + 1}: {str(e)}")
                
                if len(chunk_paths) == len(chunks):
                    # All chunks generated, concatenate them
                    if self._concatenate_audio_files(chunk_paths, output_paths[i]):
                        success_count += 1
                        # Cleanup chunk files
                        for cp in chunk_paths:
                            try:
                                os.remove(cp)
                            except:
                                pass
                    else:
                        errors.append(f"Failed to concatenate chunks for slide {i+1}")
                else:
                    errors.extend(chunk_errors)
                    
            else:
                # Single text, generate directly
                try:
                    if self._generate_single(text, output_paths[i]):
                        success_count += 1
                    else:
                        errors.append(f"Failed to generate audio for slide {i+1}")
                except Exception as e:
                    logger.error(f"Generation failed for slide {i+1}: {e}")
                    errors.append(str(e))
        
        # Cleanup temp directory
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass
        
        if success_count == len(texts):
            return True, []
        else:
            return False, errors

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
            # Create communication object
            communicate = edge_tts.Communicate(
                text=text.strip(),
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume
            )
            
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
            
            # Generate and save audio - handle async properly
            try:
                loop = asyncio.get_running_loop()
                # We're inside an async context, need to run in a new thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, 
                        communicate.save(output_path)
                    )
                    future.result()
            except RuntimeError:
                # No running loop, safe to use asyncio.run
                asyncio.run(communicate.save(output_path))
            
            # Edge TTS outputs MP3, convert to WAV if needed
            if output_path.endswith('.wav'):
                pass
            elif output_path.endswith('.mp3'):
                wav_path = output_path.replace('.mp3', '.wav')
                if self._convert_mp3_to_wav(output_path, wav_path):
                    os.remove(output_path)
                    os.rename(wav_path, output_path)
            
            logger.info(f"[EdgeTTSBackend] Generated: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"[EdgeTTSBackend] Generation failed: {e}")
            return False

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
        
        if len(audio_files) == 1:
            try:
                import shutil
                shutil.copy(audio_files[0], output_path)
                return True
            except Exception as e:
                logger.error(f"Failed to copy single audio file: {e}")
                return False
        
        logger.info(f"[EdgeTTSBackend] Concatenating {len(audio_files)} audio chunks...")
        
        try:
            import subprocess
            import tempfile
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                for audio_file in audio_files:
                    abs_path = os.path.abspath(audio_file).replace(os.sep, '/')
                    f.write(f"file '{abs_path}'\n")
                concat_file = f.name
            
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