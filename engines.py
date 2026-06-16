# engines.py
"""
Render engine — worker process that drives the full pipeline:
scan → audio → timeline → video → mux → manifest.

Runs in a subprocess; communicates with the main GUI via
typed dataclass messages on a multiprocessing.Queue.

IMPROVED:
- Direct image reading via Pillow for PDF/PPTX slides (bypasses Playwright).
- Relays structured log messages to the GUI via QueueLogHandler.
- Per-phase timing and consistent [WORKER {name}] prefixes.
"""

from __future__ import annotations

import asyncio
import glob                   # FIX #8: Proper top-level import
import hashlib
import io
import json
import logging
import math
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from backends import get_backend, get_all_audio_settings_keys
from text_chunker import TextChunker, ChunkingConfig

from utils import (
    build_manifest_from_timeline, detect_ffmpeg, generate_silent_wav,
    is_image_slide, natural_sort_key, save_project_manifest,
    update_library_manifest, BackendGenerationError, ChunkProgressMessage,
    ErrorMessage, FFmpegError, FinalizedMessage, LogMessage, PhaseMessage,
    RenderPhase, RenderStatus, SlideProgressMessage, VideoRenderError,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SlideTask:
    """Represents one slide's processing state in the render pipeline."""
    index: int
    html_path: str
    txt_path: str
    target_audio_path: str
    text_content: str = ""
    source_audio_path: Optional[str] = None
    needs_tts: bool = False
    is_silent: bool = False
    silent_duration: float = 5.0
    generation_error: Optional[str] = None

    _SLIDE_NUM_RE = re.compile(r"slide(\d+)\.html")

    @property
    def slide_number(self) -> int:
        match = self._SLIDE_NUM_RE.search(Path(self.html_path).name)
        return int(match.group(1)) if match else self.index


@dataclass
class RenderContext:
    """Immutable context carried through the entire pipeline."""
    project_path: str
    temp_dir: str
    config: dict
    ffmpeg_path: str
    render_mode: str


# ═══════════════════════════════════════════════════════════════════════════════
#  QUEUE LOG HANDLER  (relays worker logs to the GUI)
# ═══════════════════════════════════════════════════════════════════════════════

class QueueLogHandler(logging.Handler):
    """Sends structured LogMessage objects through the multiprocessing queue
    so that important worker log entries appear in the GUI log widget."""

    _INFO_WHITELIST: tuple[str, ...] = (
        "Phase", "Partition:", "Smart Cache", "Decision]",
        "Generating", "Playwright", "FFmpeg finished",
        "Manifest saved", "Done.", "Direct image",
    )

    def __init__(
        self, msg_queue, project_name: str, min_level: int = logging.WARNING
    ):
        super().__init__()
        self._queue = msg_queue
        self._project_name = project_name
        self.setLevel(logging.DEBUG)
        self._min_level = min_level

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= self._min_level:
                self._send(record)
                return
            if record.levelno >= logging.INFO:
                msg_text = record.getMessage()
                for pattern in self._INFO_WHITELIST:
                    if pattern in msg_text:
                        self._send(record)
                        return
        except Exception:
            self.handleError(record)

    def _send(self, record: logging.LogRecord) -> None:
        level_name = logging.getLevelName(record.levelno)
        self._queue.put(LogMessage(
            project_name=self._project_name,
            level=level_name,
            text=record.getMessage(),
        ))


# ═══════════════════════════════════════════════════════════════════════════════
#  FFMPEG COMMAND BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class FFmpegCommandBuilder:
    """Builds FFmpeg commands for the video encoding pipeline."""

    _BITRATE_TIERS: list[tuple[int, tuple[str, str, str]]] = [
        (3840, ("16000k", "16000k", "32000k")),
        (2560, ("12000k", "12000k", "24000k")),
        (1920, ("8000k",  "8000k",  "16000k")),
        (1280, ("5000k",  "5000k",  "10000k")),
        (854,  ("3000k",  "3000k",  "6000k")),
        (0,    ("1500k",  "1500k",  "3000k")),
    ]

    @classmethod
    def get_bitrate_settings(cls, width: int) -> tuple[str, str, str]:
        for threshold, rates in cls._BITRATE_TIERS:
            if width >= threshold:
                return rates
        return cls._BITRATE_TIERS[-1][1]

    @staticmethod
    def detect_encoder(ffmpeg_path: str) -> str:
        try:
            r = subprocess.run(
                [ffmpeg_path, "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            if "nvenc" in r.stdout:
                return "h264_nvenc"
        except Exception:
            pass
        return "libx264"

    @staticmethod
    def build_video_encoder(
        ffmpeg_path: str,
        output_path: str,
        width: int,
        height: int,
        fps: int,
        encoder: str = "auto",
        preset: str = "fast",
    ) -> list[str]:
        from config import AppConfig
        width, height = AppConfig.sanitize_resolution(width, height)

        if encoder == "auto":
            encoder = FFmpegCommandBuilder.detect_encoder(ffmpeg_path)

        gop = max(2, int(fps / 2))
        target_rate, max_rate, buf_size = (
            FFmpegCommandBuilder.get_bitrate_settings(width)
        )

        return [
            ffmpeg_path, "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-c:v", encoder, "-preset", preset,
            "-pix_fmt", "yuv420p",
            "-crf", "23",
            "-g", str(gop), "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-b:v", target_rate, "-maxrate", max_rate,
            "-bufsize", buf_size,
            "-movflags", "+faststart",
            output_path,
        ]


# ═══════════════════════════════════════════════════════════════════════════════
#  HASHING UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _get_audio_settings_hash(config: dict) -> str:
    keys_to_hash = get_all_audio_settings_keys()
    data = {k: config.get(k) for k in keys_to_hash}
    for ref_key in ("qwen3_ref_audio", "omnivoice_ref_audio"):
        ref_audio_path = config.get(ref_key, "")
        if ref_audio_path and os.path.exists(ref_audio_path):
            data[f"{ref_key}_mtime"] = os.path.getmtime(ref_audio_path)
    return hashlib.md5(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


def _get_video_settings_hash(config: dict) -> str:
    keys_to_hash = [
        "width", "height", "fps", "encoder",
        "preset", "transition_duration",
    ]
    data = {k: config.get(k) for k in keys_to_hash}
    return hashlib.md5(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
#  PIPELINE STAGES
# ═══════════════════════════════════════════════════════════════════════════════

def prepare_slide_tasks(
    html_files: List[str], ctx: RenderContext
) -> List[SlideTask]:
    """Phase 1: scan HTML files, determine which need TTS regeneration."""
    project_name = os.path.basename(ctx.project_path)
    manifest = _load_manifest(ctx.project_path)

    prev_audio_hash = manifest.get("audio_settings_hash", "")
    current_audio_hash = _get_audio_settings_hash(ctx.config)
    settings_changed = (
        prev_audio_hash != "" and current_audio_hash != prev_audio_hash
    )

    if settings_changed:
        logger.info(
            f"[WORKER {project_name}] Audio settings changed — "
            f"regenerating all audio"
        )
    else:
        logger.info(
            f"[WORKER {project_name}] Audio settings unchanged — "
            f"checking file timestamps"
        )

    enable_chunking = ctx.config.get("enable_text_chunking", True)
    chunk_max_chars = ctx.config.get("chunk_max_chars", 500)
    warn_threshold = ctx.config.get("chunk_warn_threshold", 1000)
    silent_duration = ctx.config.get("silent_slide_duration", 5.0)

    chunker = TextChunker(ChunkingConfig(
        max_chars=chunk_max_chars,
        max_sentences=ctx.config.get("chunk_max_sentences", 5),
        min_chunk_chars=ctx.config.get("chunk_min_chars", 50),
        language=ctx.config.get("qwen3_language", "English"),
    )) if enable_chunking else None

    tasks: list[SlideTask] = []
    long_text_slides: list[tuple[int, int]] = []

    for html_file in html_files:
        p_html = Path(html_file)
        p_txt = p_html.with_suffix(".txt")
        slide_num_match = re.search(r"slide(\d+)\.html", p_html.name)
        slide_num = (
            int(slide_num_match.group(1)) if slide_num_match else 0
        )

        target_audio = Path(ctx.temp_dir) / f"a{len(tasks)}.wav"
        text_content = (
            p_txt.read_text(encoding="utf-8").strip()
            if p_txt.exists() else ""
        )
        _is_image = is_image_slide(str(p_html))

        if not text_content:
            if _is_image:
                _is_silent = True
                logger.debug(
                    f"[WORKER {project_name}] Slide {slide_num}: Image-only "
                    f"(duration: {silent_duration}s)"
                )
            else:
                text_content = (
                    f"This is Slide {slide_num} with no text."
                )
                _is_silent = False
        else:
            _is_silent = False

        if not _is_silent and text_content:
            text_length = len(text_content)
            if text_length > warn_threshold:
                long_text_slides.append((slide_num, text_length))
                if chunker and chunker.needs_chunking(text_content):
                    chunks = chunker.chunk_text(
                        text_content,
                        ctx.config.get("qwen3_language", "English"),
                    )
                    logger.info(
                        f"[WORKER {project_name}] Slide {slide_num}: "
                        f"{text_length} chars → {len(chunks)} chunk(s)"
                    )
                elif not enable_chunking:
                    logger.warning(
                        f"[WORKER {project_name}] Slide {slide_num}: "
                        f"{text_length} chars exceeds threshold but "
                        f"chunking is DISABLED — generation may fail"
                    )

        source_audio = Path(ctx.project_path) / f"slide{slide_num}.wav"
        needs_tts = _determine_needs_tts(
            _is_silent, settings_changed, str(source_audio),
            str(p_txt), slide_num, project_name,
        )

        tasks.append(SlideTask(
            index=len(tasks),
            html_path=str(p_html),
            txt_path=str(p_txt),
            target_audio_path=str(target_audio),
            text_content=text_content,
            source_audio_path=str(source_audio),
            needs_tts=needs_tts,
            is_silent=_is_silent,
            silent_duration=silent_duration,
        ))

    if long_text_slides:
        logger.info(
            f"[WORKER {project_name}] {len(long_text_slides)} slide(s) "
            f"exceed {warn_threshold} chars"
        )
        if not enable_chunking:
            logger.warning(
                f"[WORKER {project_name}] Text chunking is DISABLED — "
                f"long slides may fail"
            )

    logger.info(
        f"[WORKER {project_name}] Phase 1 complete: "
        f"{len(tasks)} slides scanned, "
        f"{sum(1 for t in tasks if t.needs_tts)} need TTS, "
        f"{sum(1 for t in tasks if t.is_silent)} silent/image"
    )
    return tasks


def _determine_needs_tts(
    is_silent: bool,
    settings_changed: bool,
    source_audio_path: str,
    txt_path: str,
    slide_num: int,
    project_name: str = "",
) -> bool:
    if is_silent:
        return False
    if settings_changed:
        return True
    if not os.path.exists(source_audio_path):
        logger.debug(
            f"[WORKER {project_name}] Slide {slide_num}: "
            f"No cached audio — needs TTS"
        )
        return True
    try:
        if os.path.getmtime(txt_path) > os.path.getmtime(source_audio_path):
            logger.info(
                f"[WORKER {project_name}] Slide {slide_num}: "
                f"Text modified — regenerating audio"
            )
            return True
        logger.debug(
            f"[WORKER {project_name}] Slide {slide_num}: "
            f"Cached audio is current — reusing"
        )
        return False
    except Exception as exc:
        logger.warning(
            f"[WORKER {project_name}] Slide {slide_num}: "
            f"mtime check failed ({exc}) — regenerating"
        )
        return True


def _load_manifest(project_path: str) -> dict:
    manifest_path = os.path.join(project_path, "manifest.json")
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning(f"[Smart Cache] Could not load manifest: {exc}")
        return {}


async def process_audio_tasks(
    tasks: List[SlideTask], ctx: RenderContext, msg_queue
) -> None:
    """Phase 2: copy reusable audio, generate silent audio, run TTS backend."""
    project_name = os.path.basename(ctx.project_path)

    tasks_to_copy = [
        t for t in tasks
        if t.source_audio_path
        and os.path.exists(t.source_audio_path)
        and not t.needs_tts
    ]
    tasks_to_generate = [t for t in tasks if t.needs_tts]
    tasks_to_silence = [
        t for t in tasks
        if t.is_silent and not os.path.exists(t.target_audio_path)
    ]

    logger.info(
        f"[WORKER {project_name}] Partition: "
        f"{len(tasks_to_copy)} copy, "
        f"{len(tasks_to_generate)} generate, "
        f"{len(tasks_to_silence)} silent"
    )

    for task in tasks_to_copy:
        try:
            shutil.copy(task.source_audio_path, task.target_audio_path)
        except Exception as exc:
            logger.error(
                f"[WORKER {project_name}] Copy failed for slide "
                f"{task.slide_number}: {exc}"
            )
            task.generation_error = str(exc)

    for task in tasks_to_silence:
        logger.debug(
            f"[WORKER {project_name}] Silent audio ({task.silent_duration}s) "
            f"for slide {task.slide_number}"
        )
        if not generate_silent_wav(
            task.target_audio_path, task.silent_duration
        ):
            logger.error(
                f"[WORKER {project_name}] Silent audio generation failed "
                f"for slide {task.slide_number}"
            )
            task.generation_error = "Failed to generate silent audio"

    if not tasks_to_generate:
        logger.info(
            f"[WORKER {project_name}] No TTS generation needed — "
            f"all audio ready"
        )
        return

    backend = None
    try:
        backend_name = ctx.config.get("active_backend", "qwen3")
        logger.info(
            f"[WORKER {project_name}] Initializing TTS backend: "
            f"{backend_name}"
        )

        backend = get_backend(backend_name, ctx.config)

        def progress_callback(
            stage: str, current: int, total: int, message: str = ""
        ):
            msg_queue.put(ChunkProgressMessage(
                project_name=project_name,
                current=current, total=total, message=message,
            ))

        backend.set_progress_callback(progress_callback)

        texts = [t.text_content for t in tasks_to_generate]
        output_paths = [t.target_audio_path for t in tasks_to_generate]

        total_to_generate = len(tasks_to_generate)
        tts_start = time.time()
        logger.info(
            f"[WORKER {project_name}] Generating "
            f"{total_to_generate} audio clip(s)..."
        )

        success, errors = backend.generate_batch(texts, output_paths)

        tts_elapsed = time.time() - tts_start
        if tts_elapsed >= 60:
            tts_time_str = (
                f"{int(tts_elapsed // 60)}m {int(tts_elapsed % 60)}s"
            )
        else:
            tts_time_str = f"{tts_elapsed:.1f}s"

        if success:
            logger.info(
                f"[WORKER {project_name}] Audio generation complete: "
                f"{total_to_generate} clip(s) in {tts_time_str}"
            )
            for task in tasks_to_generate:
                try:
                    if (
                        task.source_audio_path
                        and os.path.exists(task.target_audio_path)
                    ):
                        shutil.copy(
                            task.target_audio_path, task.source_audio_path
                        )
                except Exception as exc:
                    logger.warning(
                        f"[WORKER {project_name}] Could not cache audio "
                        f"for slide {task.slide_number}: {exc}"
                    )

            msg_queue.put(SlideProgressMessage(
                project_name=project_name,
                current=total_to_generate, total=total_to_generate,
            ))
        else:
            logger.error(
                f"[WORKER {project_name}] Audio generation FAILED "
                f"after {tts_time_str}"
            )
            msg_queue.put(SlideProgressMessage(
                project_name=project_name,
                current=0, total=total_to_generate,
            ))

            error_messages: list[str] = []
            for i, task in enumerate(tasks_to_generate):
                if i < len(errors) and errors[i]:
                    task.generation_error = errors[i]
                    error_messages.append(
                        f"Slide {task.slide_number}: {errors[i]}"
                    )
                elif not os.path.exists(task.target_audio_path):
                    task.generation_error = (
                        "File missing after generation"
                    )
                    error_messages.append(
                        f"Slide {task.slide_number}: output file missing"
                    )

            if error_messages:
                raise BackendGenerationError(
                    f"Audio generation failed: {'; '.join(error_messages)}"
                )

    except BackendGenerationError:
        raise
    except Exception as exc:
        logger.error(
            f"[WORKER {project_name}] Critical TTS backend error: {exc}",
            exc_info=True,
        )
        for task in tasks_to_generate:
            task.generation_error = str(exc)
        msg_queue.put(ErrorMessage(
            project_name=project_name,
            error_text=f"TTS backend error: {exc}",
        ))
        raise
    finally:
        if backend:
            if hasattr(backend, "_cleanup_chunk_temp"):
                try:
                    backend._cleanup_chunk_temp()
                except Exception as exc:
                    logger.warning(
                        f"[WORKER {project_name}] Chunk temp cleanup "
                        f"error: {exc}"
                    )
            logger.debug(
                f"[WORKER {project_name}] Cleaning up backend resources"
            )
            try:
                backend.cleanup()
            except Exception as exc:
                logger.error(
                    f"[WORKER {project_name}] Backend cleanup error: {exc}"
                )


def build_timeline(tasks: List[SlideTask]) -> List[dict]:
    """Build a timeline entry for each slide with computed duration."""
    timeline: list[dict] = []
    for task in tasks:
        duration = _compute_slide_duration(task)

        direct_image_path = None
        if task.is_silent and is_image_slide(task.html_path):
            try:
                with open(task.html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                match = re.search(r'src=["\']([^"\']+)["\']', html_content)
                if match:
                    img_rel = match.group(1)
                    direct_image_path = os.path.normpath(
                        os.path.join(
                            os.path.dirname(task.html_path), img_rel
                        )
                    )
            except Exception as exc:
                logger.warning(
                    f"Could not parse direct image path from HTML: {exc}"
                )

        timeline.append({
            "html": task.html_path,
            "audio": task.target_audio_path,
            "duration": duration,
            "direct_image": direct_image_path,
        })
    return timeline


def _compute_slide_duration(task: SlideTask) -> float:
    if task.is_silent and not Path(task.target_audio_path).exists():
        return task.silent_duration
    if Path(task.target_audio_path).exists():
        try:
            import wave
            with wave.open(task.target_audio_path, "r") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    if task.is_silent:
        return task.silent_duration
    return max(3.0, len(task.text_content.split()) / 2.5)


# ═══════════════════════════════════════════════════════════════════════════════
#  WORKER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _reset_worker_logger(msg_queue, project_name: str) -> None:
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter(
        "[%(process)d] [%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    qh = QueueLogHandler(msg_queue, project_name, min_level=logging.WARNING)
    qh.setLevel(logging.DEBUG)
    root.addHandler(qh)

    root.setLevel(logging.DEBUG)


def render_project_worker(
    project_path: str,
    msg_queue: multiprocessing.Queue,
    config: dict,
) -> str:
    """Entry point for the render subprocess."""

    cache_dir = config.get(
        "hf_cache_dir", os.path.join(os.getcwd(), "models_cache")
    )
    os.environ["HF_HOME"] = cache_dir
    os.environ["HUGGINGFACE_HUB_CACHE"] = cache_dir
    os.environ["TRANSFORMERS_CACHE"] = cache_dir
    os.environ["HF_DATASETS_CACHE"] = os.path.join(cache_dir, "datasets")

    if not config.get("hf_use_symlinks", False):
        os.environ["HF_HUB_SYMLINKS"] = "0"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    project_name = os.path.basename(project_path)

    _reset_worker_logger(msg_queue, project_name)

    logger.info(
        f"[WORKER {project_name}] Render worker started (PID {os.getpid()})"
    )

    from config import AppConfig
    app_root = AppConfig.APP_ROOT
    temp_base_dir = os.path.join(app_root, "temp")
    project_temp_dir = os.path.join(temp_base_dir, project_name)

    if os.path.exists(project_temp_dir):
        logger.warning(
            f"[WORKER {project_name}] Removing stale temp directory "
            f"from previous run"
        )
        shutil.rmtree(project_temp_dir, ignore_errors=True)

    ctx = RenderContext(
        project_path=project_path,
        temp_dir=project_temp_dir,
        config=config,
        ffmpeg_path=config.get("ffmpeg_path"),
        render_mode="full",
    )

    try:
        os.makedirs(ctx.temp_dir, exist_ok=True)
    except Exception as exc:
        logger.error(
            f"[WORKER {project_name}] Cannot create temp directory: {exc}"
        )
        msg_queue.put(ErrorMessage(
            project_name=project_name,
            error_text=f"Cannot create temp directory: {exc}",
        ))
        return project_path

    config["_temp_dir"] = ctx.temp_dir

    w, h = config.get("width", 1280), config.get("height", 720)
    w_orig, h_orig = w, h
    w, h = AppConfig.sanitize_resolution(w, h)
    if w != w_orig or h != h_orig:
        logger.warning(
            f"[WORKER {project_name}] Resolution sanitized: "
            f"{w_orig}×{h_orig} → {w}×{h} "
            f"(even dimensions required by FFmpeg)"
        )
    config["width"] = w
    config["height"] = h
    fps = config.get("fps", 30)

    async def run_async():
        pipeline_start = time.time()
        output_dir = os.path.abspath(config.get("output_dir", "Output"))
        os.makedirs(output_dir, exist_ok=True)
        final_video = os.path.join(output_dir, project_name + ".mp4")
        transition_duration = config.get("transition_duration", 0.5)
        transition_frames = int(transition_duration * fps)

        encoder = config.get("encoder", "auto")
        preset = config.get("preset", "fast")

        cmd = FFmpegCommandBuilder.build_video_encoder(
            ffmpeg_path=ctx.ffmpeg_path,
            output_path=os.path.join(ctx.temp_dir, "video_raw.mp4"),
            width=w, height=h, fps=fps,
            encoder=encoder, preset=preset,
        )

        manifest = _load_manifest(project_path)
        current_video_hash = _get_video_settings_hash(ctx.config)
        prev_video_hash = manifest.get("video_settings_hash", "")

        # ── Phase 1: Scan ────────────────────────────────────────────────
        phase_start = time.time()
        msg_queue.put(PhaseMessage(
            project_name=project_name, phase=RenderPhase.SCANNING
        ))
        logger.info(
            f"[WORKER {project_name}] Phase 1/5: Scanning project files"
        )

        # FIX #8: Use proper top-level `glob` module instead of
        # __import__("glob")
        html_files = sorted(
            glob.glob(os.path.join(project_path, "slide*.html")),
            key=natural_sort_key,
        )
        if not html_files:
            msg_queue.put(ErrorMessage(
                project_name=project_name,
                error_text=(
                    "No slide HTML files found in project directory"
                ),
            ))
            return

        tasks = prepare_slide_tasks(html_files, ctx)
        logger.info(
            f"[WORKER {project_name}] Phase 1 complete in "
            f"{time.time() - phase_start:.2f}s — {len(tasks)} slides found"
        )

        # ── Phase 2: Audio ───────────────────────────────────────────────
        phase_start = time.time()
        msg_queue.put(PhaseMessage(
            project_name=project_name,
            phase=RenderPhase.PROCESSING_AUDIO,
        ))
        logger.info(
            f"[WORKER {project_name}] Phase 2/5: Processing audio"
        )
        await process_audio_tasks(tasks, ctx, msg_queue)
        logger.info(
            f"[WORKER {project_name}] Phase 2 complete in "
            f"{time.time() - phase_start:.2f}s"
        )

        # ── Decision logic ───────────────────────────────────────────────
        needs_video_render = _should_render_video(
            tasks, manifest, current_video_hash, prev_video_hash, ctx
        )

        if not needs_video_render:
            total_elapsed = time.time() - pipeline_start
            logger.info(
                f"[WORKER {project_name}] Video is up to date — "
                f"skipping render (total: {total_elapsed:.1f}s)"
            )
            manifest_data = build_manifest_from_timeline(
                project_name, project_path, config, [],
                output_dir, final_video, "full", {},
            )
            manifest_data["audio_settings_hash"] = (
                _get_audio_settings_hash(ctx.config)
            )
            manifest_data["video_settings_hash"] = current_video_hash
            save_project_manifest(project_path, manifest_data)
            update_library_manifest(project_path)
            msg_queue.put(FinalizedMessage(project_name=project_name))
            return

        # ── Start FFmpeg ─────────────────────────────────────────────────
        ffmpeg_proc, ffmpeg_log_file = _start_ffmpeg(
            cmd, ctx.temp_dir
        )
        if ffmpeg_proc is None:
            msg_queue.put(ErrorMessage(
                project_name=project_name,
                error_text=(
                    "FFmpeg failed to start. Check that FFmpeg is "
                    "installed and the encoder is supported on your "
                    "system."
                ),
            ))
            return

        # ── Phase 3: Timeline ────────────────────────────────────────────
        phase_start = time.time()
        msg_queue.put(PhaseMessage(
            project_name=project_name,
            phase=RenderPhase.BUILDING_TIMELINE,
        ))
        logger.info(
            f"[WORKER {project_name}] Phase 3/5: Building timeline"
        )
        timeline = build_timeline(tasks)
        total_duration = sum(item["duration"] for item in timeline)
        logger.info(
            f"[WORKER {project_name}] Timeline: {len(timeline)} slides, "
            f"total duration {total_duration:.1f}s "
            f"(built in {time.time() - phase_start:.2f}s)"
        )

                # ── Phase 4: Video ───────────────────────────────────────────────
        phase_start = time.time()
        msg_queue.put(PhaseMessage(
            project_name=project_name,
            phase=RenderPhase.RENDERING_VIDEO,
        ))
        logger.info(
            f"[WORKER {project_name}] Phase 4/5: Rendering video "
            f"({w}×{h}, {fps}fps, encoder={encoder})"
        )

        try:
            await _render_video_frames(
                timeline, ffmpeg_proc, w, h, fps,
                transition_frames, transition_duration,
                project_name, msg_queue, ctx,
            )
        except Exception as exc:
            logger.error(
                f"[WORKER {project_name}] Video render failed: {exc}",
                exc_info=True,
            )
            _terminate_ffmpeg(ffmpeg_proc)
            raise
        finally:
            _close_ffmpeg_stdin(ffmpeg_proc)

        video_elapsed = time.time() - phase_start
        logger.info(
            f"[WORKER {project_name}] Phase 4 complete in "
            f"{video_elapsed:.1f}s"
        )

        # ── Phase 5: Finalizing ──────────────────────────────────────────
        phase_start = time.time()
        msg_queue.put(PhaseMessage(
            project_name=project_name, phase=RenderPhase.FINALIZING
        ))
        logger.info(
            f"[WORKER {project_name}] Phase 5/5: Finalizing output"
        )

        # FIX #9: Pass ffmpeg_log_file to _finalize so it gets closed
        # even if _finalize raises an exception (e.g., VideoRenderError).
        _finalize(
            ffmpeg_proc, timeline, ctx, config,
            output_dir, final_video, project_name,
            current_video_hash, msg_queue, ffmpeg_log_file,
        )

        finalize_elapsed = time.time() - phase_start
        total_elapsed = time.time() - pipeline_start

        if total_elapsed >= 60:
            time_str = (
                f"{int(total_elapsed // 60)}m {int(total_elapsed % 60)}s"
            )
        else:
            time_str = f"{total_elapsed:.1f}s"

        logger.info(
            f"[WORKER {project_name}] ✓ Render complete: "
            f"{len(timeline)} slides, {total_duration:.1f}s video, "
            f"{w}×{h} — total time: {time_str}"
        )
        msg_queue.put(FinalizedMessage(project_name=project_name))

    try:
        asyncio.run(run_async())
    except Exception as exc:
        logger.error(
            f"[WORKER {project_name}] Pipeline crashed: {exc}",
            exc_info=True,
        )
        msg_queue.put(ErrorMessage(
            project_name=project_name,
            error_text=f"Render pipeline error: {exc}",
        ))
    finally:
        _cleanup_temp(ctx.temp_dir, project_name)

    return project_path


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _should_render_video(
    tasks: list[SlideTask],
    manifest: dict,
    current_video_hash: str,
    prev_video_hash: str,
    ctx: RenderContext,
) -> bool:
    project_name = os.path.basename(ctx.project_path)
    audio_was_regenerated = any(t.needs_tts for t in tasks)
    video_hash_changed = (current_video_hash != prev_video_hash)

    html_content_changed = False
    assets_meta = manifest.get("assets_meta", {})
    for task in tasks:
        html_name = os.path.basename(task.html_path)
        if html_name in assets_meta:
            prev_mtime = assets_meta[html_name].get("mtime", 0)
            try:
                if os.path.getmtime(task.html_path) > prev_mtime:
                    html_content_changed = True
                    break
            except OSError:
                pass
        else:
            html_content_changed = True
            break

    if audio_was_regenerated:
        logger.info(
            f"[WORKER {project_name}] [Decision] Audio regenerated → "
            f"video render required"
        )
    elif video_hash_changed:
        logger.info(
            f"[WORKER {project_name}] [Decision] Video settings changed → "
            f"video render required"
        )
    elif html_content_changed:
        logger.info(
            f"[WORKER {project_name}] [Decision] HTML content changed → "
            f"video render required"
        )
    else:
        logger.info(
            f"[WORKER {project_name}] [Decision] No changes detected → "
            f"skipping video render"
        )

    return (
        audio_was_regenerated or video_hash_changed or html_content_changed
    )


def _start_ffmpeg(cmd: list[str], temp_dir: str):
    """Launch the FFmpeg encoder subprocess."""
    try:
        ffmpeg_log_path = os.path.join(temp_dir, "ffmpeg_encode.log")
        err_log = open(ffmpeg_log_path, "w")
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=err_log,
        )
        if proc.poll() is not None:
            logger.error(
                f"FFmpeg process exited immediately with code "
                f"{proc.returncode}. See log: {ffmpeg_log_path}"
            )
            err_log.close()
            return None, None
        return proc, err_log
    except FileNotFoundError:
        logger.error(
            "FFmpeg binary not found. Ensure FFmpeg is installed and "
            "in PATH."
        )
        return None, None
    except Exception as exc:
        logger.error(f"FFmpeg failed to start: {exc}")
        return None, None


def _terminate_ffmpeg(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()


def _close_ffmpeg_stdin(proc) -> None:
    if proc and proc.stdin and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except Exception:
            pass


def _write_frame(proc, frame: np.ndarray) -> None:
    if proc.stdin:
        proc.stdin.write(frame.tobytes())


def _write_frames_safe(proc, frames, label: str) -> None:
    try:
        for frame in frames:
            _write_frame(proc, frame)
    except BrokenPipeError:
        logger.error(
            f"FFmpeg pipe broken during {label} — "
            f"encoder may have crashed"
        )
        _terminate_ffmpeg(proc)
        raise FFmpegError(f"Pipe broken during {label}")


async def _render_video_frames(
    timeline: list[dict],
    ffmpeg_proc,
    w: int, h: int, fps: int,
    transition_frames: int,
    transition_duration: float,
    project_name: str,
    msg_queue,
    ctx: RenderContext,
) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: "
            "pip install playwright && playwright install chromium"
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-gpu", "--no-sandbox"]
        )
        page = await browser.new_page()
        await page.set_viewport_size({"width": w, "height": h})

        async def get_slide_image(index: int) -> np.ndarray:
            item = timeline[index]
            direct_image = item.get("direct_image")

            # ── FAST PATH: Direct image read ─────────────────────────
            if direct_image and os.path.exists(direct_image):
                try:
                    img = Image.open(direct_image)
                    img = img.resize((w, h), Image.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    return np.array(img)
                except Exception as exc:
                    logger.error(
                        f"[WORKER {project_name}] Direct image load "
                        f"failed for slide {index}: {exc}. "
                        f"Falling back to Playwright."
                    )

            # ── NORMAL PATH: Playwright HTML Rendering ───────────────
            try:
                file_url = (
                    f"file:///"
                    f"{os.path.abspath(item['html']).replace(os.sep, '/')}"
                )
                await page.goto(
                    file_url, wait_until="domcontentloaded"
                )
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=2000
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.1)
                img_bytes = await page.screenshot(type="png")
                img = Image.open(io.BytesIO(img_bytes))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return np.array(img)
            except Exception as exc:
                logger.error(
                    f"[WORKER {project_name}] Screenshot failed for "
                    f"slide {index}: {exc}"
                )
                return np.zeros((h, w, 3), dtype=np.uint8)

        img_current = await get_slide_image(0)
        total_slides = len(timeline)
        msg_queue.put(SlideProgressMessage(
            project_name=project_name,
            current=1, total=total_slides,
        ))

        for i in range(total_slides):
            img_next = (
                await get_slide_image(i + 1)
                if i < total_slides - 1 else None
            )
            if img_next is not None:
                msg_queue.put(SlideProgressMessage(
                    project_name=project_name,
                    current=i + 2, total=total_slides,
                ))

            duration = timeline[i]["duration"]
            is_last = (i == total_slides - 1)

            if is_last:
                static_count = int(math.ceil(duration * fps))
            else:
                static_count = max(
                    0,
                    int(math.ceil((duration - transition_duration) * fps)),
                )

            if static_count > 0:
                _write_frames_safe(
                    ffmpeg_proc,
                    (img_current for _ in range(static_count)),
                    "static frames",
                )

            if (
                not is_last
                and img_next is not None
                and transition_frames > 0
            ):
                transition_frames_list = [
                    (
                        img_current * (1.0 - alpha) + img_next * alpha
                    ).astype(np.uint8)
                    for alpha in (
                        t / transition_frames
                        for t in range(transition_frames)
                    )
                ]
                _write_frames_safe(
                    ffmpeg_proc, transition_frames_list, "transition",
                )

            img_current = img_next

        await browser.close()
        logger.info(
            f"[WORKER {project_name}] Playwright closed — "
            f"all frames written to FFmpeg"
        )


def _finalize(
    ffmpeg_proc,
    timeline: list[dict],
    ctx: RenderContext,
    config: dict,
    output_dir: str,
    final_video: str,
    project_name: str,
    current_video_hash: str,
    msg_queue,
    ffmpeg_log_file=None,        # FIX #9: Added parameter
) -> None:
    """Wait for FFmpeg, mux audio, save manifest."""

    try:
        if ffmpeg_proc:
            if ffmpeg_proc.stdin and not ffmpeg_proc.stdin.closed:
                ffmpeg_proc.stdin.close()
            ffmpeg_proc.wait()
            if ffmpeg_proc.returncode == 0:
                logger.info(
                    f"[WORKER {project_name}] "
                    f"FFmpeg encoding finished successfully"
                )
            else:
                logger.error(
                    f"[WORKER {project_name}] FFmpeg exited with code "
                    f"{ffmpeg_proc.returncode}"
                )
    except Exception as exc:
        logger.error(
            f"[WORKER {project_name}] Error waiting for FFmpeg: {exc}"
        )
    finally:
        # FIX #9: Guarantee the log file is closed even if wait() or
        # another step above raises an exception.
        if ffmpeg_log_file and not ffmpeg_log_file.closed:
            ffmpeg_log_file.close()

    raw_video = os.path.join(ctx.temp_dir, "video_raw.mp4")
    if not os.path.exists(raw_video):
        raise VideoRenderError(
            f"Raw video file not found after encoding: {raw_video}. "
            f"FFmpeg may have crashed during video capture."
        )

    listfile = os.path.join(ctx.temp_dir, "audio_list.txt")
    audio_full = os.path.join(ctx.temp_dir, "audio_full.wav")

    with open(listfile, "w", encoding="utf-8") as f:
        for item in timeline:
            abs_path = os.path.abspath(item["audio"]).replace(
                os.sep, "/"
            )
            escaped = abs_path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    concat_log = os.path.join(ctx.temp_dir, "ffmpeg_concat.log")
    with open(concat_log, "w") as concat_err:
        res_concat = subprocess.run(
            [
                ctx.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
                "-i", listfile, "-c", "copy", audio_full,
            ],
            stdout=subprocess.DEVNULL, stderr=concat_err,
        )
    if res_concat.returncode != 0:
        raise FFmpegError(
            f"Audio concat failed (exit code "
            f"{res_concat.returncode}). Check log: {concat_log}"
        )

    mux_log = os.path.join(ctx.temp_dir, "ffmpeg_mux.log")
    cmd_mux = [
        ctx.ffmpeg_path, "-y",
        "-i", raw_video, "-i", audio_full,
        "-c:v", "copy", "-c:a", "aac", "-ar", "48000",
        "-ac", "2", "-b:a", "384k",
        "-movflags", "+faststart", "-shortest",
        final_video,
    ]
    with open(mux_log, "w") as mux_err:
        res_mux = subprocess.run(
            cmd_mux, stdout=subprocess.DEVNULL, stderr=mux_err,
        )
    if res_mux.returncode != 0:
        logger.error(
            f"[WORKER {project_name}] Audio muxing failed "
            f"(exit {res_mux.returncode}). "
            f"Saving video WITHOUT audio as fallback."
        )
        try:
            shutil.copy(raw_video, final_video)
            logger.warning(
                f"[WORKER {project_name}] Saved video without audio "
                f"due to mux error. Check log: {mux_log}"
            )
        except Exception as exc:
            logger.error(
                f"[WORKER {project_name}] Fallback video copy also "
                f"failed: {exc}"
            )
    else:
        video_size_mb = os.path.getsize(final_video) / (1024 * 1024)
        logger.info(
            f"[WORKER {project_name}] Video finalized: "
            f"{final_video} ({video_size_mb:.1f} MB)"
        )

    assets_meta = _collect_assets_meta(timeline, ctx.project_path)

    manifest_data = build_manifest_from_timeline(
        project_name, ctx.project_path, config,
        timeline, output_dir, final_video, "full", assets_meta,
    )
    manifest_data["audio_settings_hash"] = (
        _get_audio_settings_hash(ctx.config)
    )
    manifest_data["video_settings_hash"] = current_video_hash
    save_project_manifest(ctx.project_path, manifest_data)
    update_library_manifest(ctx.project_path)
    logger.info(f"[WORKER {project_name}] Manifest saved")


def _collect_assets_meta(timeline: list[dict], project_path: str) -> dict:
    meta: dict = {}
    for item in timeline:
        html_path = item["html"]
        txt_path = os.path.splitext(html_path)[0] + ".txt"
        for path in (html_path, txt_path):
            if os.path.exists(path):
                meta[os.path.basename(path)] = {
                    "mtime": os.path.getmtime(path),
                    "size": os.path.getsize(path),
                }

    images_dir = os.path.join(project_path, "images")
    if os.path.isdir(images_dir):
        for img_name in os.listdir(images_dir):
            img_path = os.path.join(images_dir, img_name)
            if os.path.isfile(img_path):
                meta[f"images/{img_name}"] = {
                    "mtime": os.path.getmtime(img_path),
                    "size": os.path.getsize(img_path),
                }
    return meta


def _cleanup_temp(temp_dir: str, project_name: str) -> None:
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug(
                f"[WORKER {project_name}] Cleaned temp directory"
            )
    except Exception as exc:
        logger.warning(
            f"[WORKER {project_name}] Temp cleanup failed: {exc}"
        )

    parent = os.path.dirname(temp_dir)
    if os.path.isdir(parent):
        try:
            if not os.listdir(parent):
                os.rmdir(parent)
                logger.debug(
                    f"[WORKER {project_name}] Removed empty parent "
                    f"temp directory"
                )
        except OSError as exc:
            logger.debug(
                f"[WORKER {project_name}] Parent temp cleanup skipped: "
                f"{exc}"
            )