# engines.py
import os
import sys
import wave
import math
import asyncio
import io
import logging
import glob
import subprocess
import numpy as np
import shutil
import re
import concurrent.futures
import json
import hashlib
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path
from PIL import Image

# Local imports
from utils import (
    logger, detect_ffmpeg, natural_sort_key, build_manifest_from_timeline, 
    save_project_manifest, update_library_manifest
)
# Import the Backend Factory
from backends import get_backend, get_all_audio_settings_keys

# Import text chunker for validation
from text_chunker import TextChunker, ChunkingConfig

# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

@dataclass
class SlideTask:
    index: int
    html_path: str
    txt_path: str
    target_audio_path: str
    text_content: str = ""
    
    source_audio_path: Optional[str] = None
    needs_tts: bool = False
    generation_error: Optional[str] = None

    @property
    def slide_number(self) -> int:
        match = re.search(r"slide(\d+)\.html", Path(self.html_path).name)
        return int(match.group(1)) if match else self.index

@dataclass
class RenderContext:
    """
    Holds configuration and paths for the current render job.
    """
    project_path: str
    temp_dir: str
    config: dict
    ffmpeg_path: str
    render_mode: str

class FFmpegCommandBuilder:
    @staticmethod
    def build_video_encoder(output_path, width, height, fps, encoder="libx264", preset="fast"):
        return [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-c:v", encoder, "-preset", preset, "-pix_fmt", "yuv420p",
            "-crf", "23", "-movflags", "+faststart",
            output_path
        ]

# =================================================================
# HASHING UTILITIES
# =================================================================

def _get_audio_settings_hash(config: dict) -> str:
    # Collect all audio-affecting keys from every registered backend
    keys_to_hash = get_all_audio_settings_keys()
    data = {k: config.get(k) for k in keys_to_hash}
    
    # Include file modification time for any reference audio files
    for ref_key in ("qwen3_ref_audio", "omnivoice_ref_audio"):
        ref_audio_path = config.get(ref_key, "")
        if ref_audio_path and os.path.exists(ref_audio_path):
            data[f"{ref_key}_mtime"] = os.path.getmtime(ref_audio_path)
    
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()

def _get_video_settings_hash(config: dict) -> str:
    keys_to_hash = [
        "width", "height", "fps", "encoder", "preset", "transition_duration"
    ]
    data = {k: config.get(k) for k in keys_to_hash}
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()

# =================================================================
# WORKFLOW LOGIC
# =================================================================

def prepare_slide_tasks(html_files: List[str], ctx: RenderContext) -> List[SlideTask]:
    """
    Phase 1: Scan files, read text, and determine what needs to be done.
    
    Includes validation and logging for long text that will be chunked.
    """
    # 1. Load previous manifest
    manifest = {}
    manifest_path = os.path.join(ctx.project_path, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"[Smart Cache] Could not load existing manifest: {e}")

    prev_audio_hash = manifest.get('audio_settings_hash', "")
    current_audio_hash = _get_audio_settings_hash(ctx.config)
    
    settings_changed = (prev_audio_hash != "" and current_audio_hash != prev_audio_hash)

    if settings_changed:
        logger.info(f"[Smart Cache] Audio settings changed. Audio will be regenerated.")
    else:
        logger.info(f"[Smart Cache] Audio settings unchanged. Checking modification times...")

    # Get chunking config for validation
    enable_chunking = ctx.config.get("enable_text_chunking", True)
    chunk_max_chars = ctx.config.get("chunk_max_chars", 500)
    warn_threshold = ctx.config.get("chunk_warn_threshold", 1000)
    
    # Create chunker for validation
    if enable_chunking:
        chunker_config = ChunkingConfig(
            max_chars=chunk_max_chars,
            max_sentences=ctx.config.get("chunk_max_sentences", 5),
            min_chunk_chars=ctx.config.get("chunk_min_chars", 50),
            language=ctx.config.get("qwen3_language", "English")
        )
        chunker = TextChunker(chunker_config)
    else:
        chunker = None

    tasks = []
    long_text_slides = []
    
    for html_file in html_files:
        p_html = Path(html_file)
        p_txt = p_html.with_suffix(".txt")
        slide_num = re.search(r"slide(\d+)\.html", p_html.name)
        slide_num = int(slide_num.group(1)) if slide_num else 0
        
        target_audio = Path(ctx.temp_dir) / f"a{len(tasks)}.wav"
        text_content = ""
        if p_txt.exists():
            text_content = p_txt.read_text(encoding="utf-8").strip()
        if not text_content:
            text_content = f"This is Slide {slide_num} with no text."

        # Define cache path regardless of existence
        source_audio = Path(ctx.project_path) / f"slide{slide_num}.wav"
        
        # Validate text length and log warnings
        text_length = len(text_content)
        if text_length > warn_threshold:
            long_text_slides.append((slide_num, text_length))
            if chunker and chunker.needs_chunking(text_content):
                chunks = chunker.chunk_text(text_content, ctx.config.get("qwen3_language", "English"))
                logger.info(f"[Validation] Slide {slide_num}: Long text ({text_length} chars) "
                           f"will be split into {len(chunks)} chunks")
            elif not enable_chunking:
                logger.warning(f"[Validation] Slide {slide_num}: Long text ({text_length} chars) "
                              f"exceeds recommended limit but chunking is disabled!")

        # Decision Logic
        needs_tts = False
        
        if settings_changed:
            needs_tts = True
        elif source_audio.exists():
            try:
                txt_mtime = os.path.getmtime(p_txt)
                wav_mtime = os.path.getmtime(source_audio)
                
                if txt_mtime > wav_mtime:
                    logger.info(f"[Smart Cache] Slide {slide_num}: Text modified. Regenerating.")
                    needs_tts = True
                else:
                    logger.info(f"[Smart Cache] Slide {slide_num}: Text unchanged. Reusing.")
                    needs_tts = False
            except Exception as e:
                logger.warning(f"[Smart Cache] Error checking mtimes for slide {slide_num}: {e}. Regenerating.")
                needs_tts = True
        else:
            needs_tts = True

        task = SlideTask(
            index=len(tasks),
            html_path=str(p_html),
            txt_path=str(p_txt),
            target_audio_path=str(target_audio),
            text_content=text_content,
            source_audio_path=str(source_audio),
            needs_tts=needs_tts
        )
        tasks.append(task)
    
    # Summary log for long text
    if long_text_slides:
        logger.info(f"[Validation] {len(long_text_slides)} slide(s) have text exceeding {warn_threshold} chars")
        if enable_chunking:
            logger.info(f"[Validation] Long text chunking is ENABLED (max {chunk_max_chars} chars/chunk)")
        else:
            logger.warning(f"[Validation] Long text chunking is DISABLED - audio generation may fail!")
    
    return tasks

async def process_audio_tasks(tasks: List[SlideTask], ctx: RenderContext, msg_queue):
    """
    Phase 2: Execute the work using the Backend Plugin.
    """
    tasks_to_copy = [t for t in tasks if t.source_audio_path and os.path.exists(t.source_audio_path) and not t.needs_tts]
    tasks_to_generate = [t for t in tasks if t.needs_tts]
    total_to_generate = len(tasks_to_generate)
    completed_count = 0
    
    logger.info(f"[Worker] Partition: {len(tasks_to_copy)} to copy, {total_to_generate} to generate.")
    
    # --- Sub-Phase 2a: Copy Existing Files ---
    for task in tasks_to_copy:
        try:
            shutil.copy(task.source_audio_path, task.target_audio_path)
        except Exception as e:
            logger.error(f"Failed to copy {task.source_audio_path}: {e}")
            task.generation_error = str(e)

    # --- Sub-Phase 2b: TTS Generation via Backend ---
    if not tasks_to_generate:
        return

    # Backend Logic
    backend = None
    try:
        # Get backend from config (default to qwen3 for backward compatibility)
        backend_name = ctx.config.get("active_backend", "qwen3")
        logger.info(f"[Worker] Initializing Backend: {backend_name}...")
        
        backend = get_backend(backend_name, ctx.config)
        
        # Set up progress callback for chunk-level reporting
        def progress_callback(stage: str, current: int, total: int, message: str = ""):
            """Forward backend progress to the main process via message queue."""
            project_name = os.path.basename(ctx.project_path)
            if stage == "chunk":
                msg_queue.put(("CHUNK_PROGRESS", project_name, current, total, message))
        
        backend.set_progress_callback(progress_callback)
        
        texts = [t.text_content for t in tasks_to_generate]
        output_paths = [t.target_audio_path for t in tasks_to_generate]
        
        logger.info(f"[Worker] Generating {total_to_generate} clips...")
        
        success, errors = backend.generate_batch(texts, output_paths)
        
        if success:
            for task in tasks_to_generate:
                try:
                    if task.source_audio_path and os.path.exists(task.target_audio_path):
                        shutil.copy(task.target_audio_path, task.source_audio_path)
                except Exception as e:
                    logger.warning(f"Could not cache audio file: {e}")
            
            msg_queue.put((os.path.basename(ctx.project_path), total_to_generate, total_to_generate))
        else:
            # Handle partial or full failures - DO NOT generate silent WAV
            logger.error(f"[Worker] Backend reported errors.")
            msg_queue.put((os.path.basename(ctx.project_path), 0, total_to_generate))
            
            error_messages = []
            for i, task in enumerate(tasks_to_generate):
                # Only mark tasks that actually have a corresponding error
                if i < len(errors) and errors[i]:
                    err_msg = errors[i]
                    logger.error(f"Failed to generate slide {task.slide_number}: {err_msg}")
                    task.generation_error = err_msg
                    error_messages.append(f"Slide {task.slide_number}: {err_msg}")
                elif not os.path.exists(task.target_audio_path):
                    # No specific error reported, but the file is missing
                    err_msg = "File missing after generation"
                    task.generation_error = err_msg
                    error_messages.append(f"Slide {task.slide_number}: {err_msg}")
            
            if error_messages:
                raise RuntimeError(f"Audio generation failed: {'; '.join(error_messages)}")

    except Exception as e:
        logger.exception(f"[Worker] Critical error in backend execution: {e}")
        # Critical failure: propagate error, do NOT generate silent audio
        for task in tasks_to_generate:
            task.generation_error = str(e)
        # Send error message to queue and re-raise
        msg_queue.put(("ERROR", os.path.basename(ctx.project_path), str(e)))
        raise
        
    finally:
        # Clean up backend chunk temp dirs before backend cleanup
        if backend and hasattr(backend, '_cleanup_chunk_temp'):
            try:
                backend._cleanup_chunk_temp()
            except Exception as e:
                logger.warning(f"[Worker] Error cleaning backend chunk temp: {e}")
        
        if backend:
            logger.info("[Worker] Cleaning up backend resources...")
            try:
                backend.cleanup()
            except Exception as e:
                logger.error(f"[Worker] Error during backend cleanup: {e}")

def build_timeline(tasks: List[SlideTask]) -> List[dict]:
    """
    Phase 3: Assemble the final timeline for the video renderer.
    """
    timeline = []
    for task in tasks:
        duration = 3.0
        if Path(task.target_audio_path).exists():
            try:
                with wave.open(task.target_audio_path, 'r') as w:
                    duration = w.getnframes() / float(w.getframerate())
            except Exception: pass
        else:
            duration = max(3.0, len(task.text_content.split()) / 2.5)
        timeline.append({'html': task.html_path, 'audio': task.target_audio_path, 'duration': duration})
    return timeline

# =================================================================
# MAIN WORKER
# =================================================================

def reset_worker_logger():
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]: root_logger.removeHandler(handler)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(process)d] %(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
    ch.setFormatter(formatter)
    root_logger.addHandler(ch)
    root_logger.setLevel(logging.INFO)


def render_project_worker(project_path, msg_queue, config):
    # Apply Cache Settings from config passed from main process
    cache_dir = config.get('hf_cache_dir', os.path.join(os.getcwd(), "models_cache"))
    os.environ['HF_HOME'] = cache_dir
    os.environ['HUGGINGFACE_HUB_CACHE'] = cache_dir
    os.environ['TRANSFORMERS_CACHE'] = cache_dir
    os.environ['HF_DATASETS_CACHE'] = os.path.join(cache_dir, 'datasets')
    
    if not config.get('hf_use_symlinks', False):
        os.environ['HF_HUB_SYMLINKS'] = '0'
        os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'

    reset_worker_logger()
    project_name = os.path.basename(project_path)
    logger.info(f"[WORKER {project_name}] Starting render worker")
    
    # =================================================================
    # TEMP DIR CONFIGURATION
    # =================================================================
    # Create temp folder in current working directory
    from config import AppConfig
    app_root = AppConfig.APP_ROOT 
    temp_base_dir = os.path.join(app_root, "temp")
    project_temp_dir = os.path.join(temp_base_dir, project_name)

    # Clean up any stale temp from a previous crashed run
    if os.path.exists(project_temp_dir):
        logger.warning(f"[WORKER {project_name}] Removing stale temp dir from previous run: {project_temp_dir}")
        shutil.rmtree(project_temp_dir, ignore_errors=True)
    
    ctx = RenderContext(
        project_path=project_path,
        temp_dir=project_temp_dir,
        config=config,
        ffmpeg_path=config.get('ffmpeg_path'),
        render_mode='full'
    )
    
    # Ensure base temp dir exists
    try:
        os.makedirs(ctx.temp_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"[WORKER {project_name}] Cannot create temp dir: {e}")
        msg_queue.put(("ERROR", project_name, f"Cannot create temp dir: {e}"))
        return

    # Make temp_dir available to backends so they use LOCAL temp instead of system temp
    config['_temp_dir'] = ctx.temp_dir

    # Async Runner
    async def run_async():
        output_dir = os.path.abspath(config.get('output_dir', 'Output'))
        os.makedirs(output_dir, exist_ok=True)
        final_video = os.path.join(output_dir, project_name + ".mp4")
        transition_duration = config.get('transition_duration', 0.5)
        transition_frames = int(transition_duration * config['fps'])
        
        w = config['width']
        h = config['height']
        fps = config.get('fps', 30)
        gop = max(2, int(fps / 2))

        if w >= 1920:
            target_rate, max_rate, buf_size = "8000k", "8000k", "16000k"
        elif w >= 1280:
            target_rate, max_rate, buf_size = "5000k", "5000k", "10000k"
        else:
            target_rate, max_rate, buf_size = "2500k", "2500k", "5000k"

        encoder = config.get('encoder', 'auto')
        if encoder == "auto":
            try:
                r = subprocess.run([ctx.ffmpeg_path, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if "nvenc" in r.stdout: encoder = "h264_nvenc"
                else: encoder = "libx264"
            except: encoder = "libx264"

        cmd = [
            ctx.ffmpeg_path, "-y", "-f", "rawvideo", "-vcodec", "rawvideo", 
            "-pix_fmt", "rgb24", "-s", f"{config['width']}x{config['height']}", 
            "-r", str(config['fps']), "-i", "-", 
            "-c:v", encoder, "-preset", "fast", "-pix_fmt", "yuv420p", 
            "-crf", "23", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-b:v", target_rate, "-maxrate", max_rate, "-bufsize", buf_size,
            "-movflags", "+faststart",
            os.path.join(ctx.temp_dir, "video_raw.mp4")
        ]

        # =================================================================
        # LOAD MANIFEST
        # =================================================================
        manifest_path = os.path.join(project_path, "manifest.json")
        manifest = {}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
            except Exception as e:
                logger.error(f"[WORKER {project_name}] Error loading manifest: {e}")

        current_video_hash = _get_video_settings_hash(ctx.config)
        prev_video_hash = manifest.get('video_settings_hash', "")

        # =================================================================
        # PHASE 1: PREPARE TASKS
        # =================================================================
        msg_queue.put((project_name, "SCANNING SLIDES", 0))
        logger.info(f"[WORKER {project_name}] Phase 1: Scanning files")
        
        html_files = sorted(glob.glob(os.path.join(project_path, "slide*.html")), key=natural_sort_key)
        if not html_files:
            msg_queue.put(("ERROR", "No slides found."))
            return

        tasks = prepare_slide_tasks(html_files, ctx)

        # =================================================================
        # PHASE 2: PROCESS AUDIO (Backend)
        # =================================================================
        msg_queue.put((project_name, "PROCESSING AUDIO", 0))
        logger.info(f"[WORKER {project_name}] Phase 2: Processing Audio")
        await process_audio_tasks(tasks, ctx, msg_queue)

        # =================================================================
        # DECISION LOGIC
        # =================================================================
        audio_was_regenerated = any(t.needs_tts for t in tasks)
        video_hash_changed = (current_video_hash != prev_video_hash)
        
        # Check if HTML content changed
        html_content_changed = False
        if manifest:
            assets_meta = manifest.get('assets_meta', {})
            for task in tasks:
                html_name = os.path.basename(task.html_path)
                if html_name in assets_meta:
                    prev_mtime = assets_meta[html_name].get('mtime', 0)
                    try:
                        curr_mtime = os.path.getmtime(task.html_path)
                        if curr_mtime > prev_mtime:
                            logger.info(f"[Decision] Slide {task.slide_number}: HTML modified.")
                            html_content_changed = True
                            break
                    except OSError:
                        pass
                else:
                    html_content_changed = True
                    break
        
        needs_video_render = False
        
        if audio_was_regenerated:
            logger.info(f"[Decision] Audio was regenerated. Video must be rendered.")
            needs_video_render = True
        elif video_hash_changed:
            logger.info(f"[Decision] Video settings changed. Video must be rendered.")
            needs_video_render = True
        elif html_content_changed:
            logger.info(f"[Decision] HTML content changed. Video must be rendered.")
            needs_video_render = True
        else:
            logger.info(f"[Decision] Audio and Video hashes unchanged. Skipping render.")
            needs_video_render = False

        # =================================================================
        # EARLY EXIT
        # =================================================================
        if not needs_video_render:
            logger.info(f"[WORKER {project_name}] Skipping render pipeline. Finalizing manifest.")
            manifest_data = build_manifest_from_timeline(project_name, project_path, config, [], output_dir, final_video, 'full', {})
            manifest_data['audio_settings_hash'] = _get_audio_settings_hash(ctx.config)
            manifest_data['video_settings_hash'] = current_video_hash
            save_project_manifest(project_path, manifest_data)
            update_library_manifest(project_path)
            msg_queue.put((project_name, "FINALIZED", 0))
            return

        # =================================================================
        # START FFMPEG
        # =================================================================
        ffmpeg_proc = None
        try:
            ffmpeg_log_path = os.path.join(ctx.temp_dir, "ffmpeg_encode.log")
            with open(ffmpeg_log_path, 'w') as err_log:
                ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err_log)
                if ffmpeg_proc.poll() is not None:
                    raise Exception("FFmpeg process failed to start")
        except Exception as e:
            logger.error(f"[WORKER {project_name}] Error starting FFmpeg: {e}")
            msg_queue.put(("ERROR", f"Error starting FFmpeg: {e}"))
            return

        # =================================================================
        # PHASE 3: BUILD TIMELINE
        # =================================================================
        msg_queue.put((project_name, "BUILDING TIMELINE", 0))
        timeline = build_timeline(tasks)
        
        # =================================================================
        # PHASE 4: RENDER VIDEO
        # =================================================================
        msg_queue.put((project_name, "RENDERING VIDEO", 0))
        logger.info(f"[WORKER {project_name}] Phase 4: Rendering Video")
            
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise Exception("Playwright not installed")

        async with async_playwright() as p:
            try:
                    browser = await p.chromium.launch(headless=True, args=['--disable-gpu', '--no-sandbox'])
                    page = await browser.new_page()
                    await page.set_viewport_size({"width": config['width'], "height": config['height']})
                        
                    async def get_slide_image(index):
                        try:
                            item = timeline[index]
                            file_url = f"file:///{os.path.abspath(item['html']).replace(os.sep, '/')}"
                            await page.goto(file_url, wait_until="domcontentloaded")
                            
                            try:
                                await page.wait_for_load_state("networkidle", timeout=2000)
                            except:
                                pass
                            
                            await asyncio.sleep(0.1) # Small buffer
                            
                            img_bytes = await page.screenshot(type="jpeg", quality=85)
                            img = Image.open(io.BytesIO(img_bytes))
                            if img.mode != 'RGB': img = img.convert('RGB')
                            return np.array(img)
                        except Exception as e:
                            logger.error(f"[WORKER] Capture failed slide {index}: {e}")
                            return np.zeros((config['height'], config['width'], 3), dtype=np.uint8)

                    img_current = await get_slide_image(0)
                    msg_queue.put((project_name, 1, len(timeline)))

                    total_slides = len(timeline)
                        
                    for i in range(total_slides):
                        if i < total_slides - 1:
                            img_next = await get_slide_image(i + 1)
                            msg_queue.put((project_name, i + 2, len(timeline)))
                        else:
                            img_next = None

                        duration = timeline[i]['duration']
                        is_last = (i == total_slides - 1)
                        static_frames = max(0, int(math.ceil((duration - transition_duration) * config['fps']))) if not is_last else int(math.ceil(duration * config['fps']))
                            
                        if static_frames > 0:
                            for f_idx in range(static_frames):
                                frame_to_write = img_current
                                try:
                                    if ffmpeg_proc.stdin:
                                        ffmpeg_proc.stdin.write(frame_to_write.tobytes())
                                except BrokenPipeError:
                                    logger.error("[WORKER] FFmpeg pipe broken during static frame write.")
                                    # Log FFmpeg's error output for diagnosis
                                    if ffmpeg_proc and ffmpeg_proc.poll() is None:
                                        ffmpeg_proc.terminate()
                                    ffmpeg_log_path = os.path.join(ctx.temp_dir, "ffmpeg_encode.log")
                                    if os.path.exists(ffmpeg_log_path):
                                        try:
                                            with open(ffmpeg_log_path, "r") as log_f:
                                                last_lines = log_f.readlines()[-20:]
                                                logger.error(f"[WORKER] FFmpeg log (last 20 lines):\n{''.join(last_lines)}")
                                        except Exception:
                                            pass
                                    raise RuntimeError("[WORKER] FFmpeg pipe broken during static frame write.")
                            
                        if not is_last and img_next is not None:
                            start_frame = img_current
                            end_frame = img_next
                            try:
                                for t in range(transition_frames):
                                    alpha = t / transition_frames
                                    blended = (start_frame * (1.0 - alpha) + end_frame * alpha).astype(np.uint8)
                                    if ffmpeg_proc.stdin:
                                        ffmpeg_proc.stdin.write(blended.tobytes())
                            except BrokenPipeError:
                                logger.error("[WORKER] FFmpeg pipe broken during transition.")
                                if ffmpeg_proc and ffmpeg_proc.poll() is None:
                                    ffmpeg_proc.terminate()
                                raise RuntimeError("[WORKER] FFmpeg pipe broken during transition.")

                        img_current = img_next

                    await browser.close()
                    logger.info(f"[WORKER {project_name}] Playwright closed. Writing frames.")
            except Exception as playwright_err:
                logger.error(f"[WORKER {project_name}] Playwright/Capture error: {playwright_err}")
                # Ensure FFmpeg is killed if rendering crashes mid-stream
                if ffmpeg_proc and ffmpeg_proc.poll() is None:
                    ffmpeg_proc.terminate()
                raise
            finally:
                # Always close stdin if it's still open to prevent FFmpeg hangs
                if ffmpeg_proc and ffmpeg_proc.stdin and not ffmpeg_proc.stdin.closed:
                    try:
                        ffmpeg_proc.stdin.close()
                    except Exception:
                        pass

        # =================================================================
        # FINALIZING
        # =================================================================
        msg_queue.put((project_name, "FINALIZING", 0))
        
        try:
            if ffmpeg_proc:
                try:
                    if ffmpeg_proc.stdin: ffmpeg_proc.stdin.close()
                    ffmpeg_proc.wait()
                    logger.info(f"[WORKER {project_name}] FFmpeg process finished.")
                except Exception as e:
                    logger.error(f"[WORKER {project_name}] Error waiting for FFmpeg process: {e}")

            logger.info(f"[WORKER {project_name}] Finalizing...")
            raw_video = os.path.join(ctx.temp_dir, "video_raw.mp4")
            if not os.path.exists(raw_video): raise FileNotFoundError("Video not found")
            
            # Always mux
            listfile = os.path.join(ctx.temp_dir, "audio_list.txt")
            with open(listfile, "w") as f:
                for item in timeline:
                    # FIX: Use forward slashes for FFmpeg concat demuxer safety
                    abs_path = os.path.abspath(item['audio']).replace(os.sep, '/')
                    # Escape single quotes for FFmpeg concat demuxer: replace ' with '\''
                    escaped_path = abs_path.replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")
                    
            audio_full = os.path.join(ctx.temp_dir, "audio_full.wav")
            
            concat_log = os.path.join(ctx.temp_dir, "ffmpeg_concat.log")
            
            cmd_concat = [ctx.ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", audio_full]
            with open(concat_log, "w") as concat_err:
                res_concat = subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=concat_err)
            
            if res_concat.returncode != 0:
                logger.error(f"FFmpeg Audio Concat FAILED (Exit {res_concat.returncode}). Check log: {concat_log}")
                raise RuntimeError(f"FFmpeg failed to concatenate audio tracks. Check log: {concat_log}")
            
            mux_log = os.path.join(ctx.temp_dir, "ffmpeg_mux.log")
            cmd_mux = [
                ctx.ffmpeg_path, "-y", "-i", raw_video, "-i", audio_full,
                "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-b:a", "384k", "-movflags", "+faststart", "-shortest", final_video
            ]
            with open(mux_log, "w") as mux_err:
                res_mux = subprocess.run(cmd_mux, stdout=subprocess.DEVNULL, stderr=mux_err)
            
            if res_mux.returncode != 0:
                logger.error(f"FFmpeg Muxing FAILED (Exit {res_mux.returncode}). Check log: {mux_log}")
                # Fallback: Copy video without audio
                try:
                    shutil.copy(raw_video, final_video)
                    logger.warning("Saved video WITHOUT audio due to muxing error.")
                except Exception as copy_e:
                    logger.error(f"Failed to copy raw video: {copy_e}")
            else:
                logger.info(f"Video finalized successfully: {final_video}")

            assets_meta = {}
            for item in timeline:
                html_path = item['html']
                txt_path = os.path.splitext(html_path)[0] + ".txt"
                if os.path.exists(html_path): assets_meta[os.path.basename(html_path)] = {"mtime": os.path.getmtime(html_path), "size": os.path.getsize(html_path)}
                if os.path.exists(txt_path): assets_meta[os.path.basename(txt_path)] = {"mtime": os.path.getmtime(txt_path), "size": os.path.getsize(txt_path)}

            manifest_data = build_manifest_from_timeline(project_name, project_path, config, timeline, output_dir, final_video, 'full', assets_meta)
            manifest_data['audio_settings_hash'] = _get_audio_settings_hash(ctx.config)
            manifest_data['video_settings_hash'] = current_video_hash
            save_project_manifest(project_path, manifest_data)
            update_library_manifest(project_path)
            logger.info(f"[WORKER {project_name}] Project manifest saved.")
            
        except Exception as e:
            logger.error(f"[WORKER {project_name}] Finalize failed: {e}")
            msg_queue.put(("ERROR", str(e)))

        logger.info(f"[WORKER {project_name}] Done.")
        msg_queue.put((project_name, "FINALIZED", 0))
    
    # Execute async loop
    try:
        asyncio.run(run_async())
    except Exception as e:
        logger.exception(f"[WORKER] Async wrapper crash: {e}")
    finally:
        # =================================================================
        # TEMP CLEANUP — ALWAYS runs, even on crash
        # =================================================================
        try:
            if os.path.exists(ctx.temp_dir):
                shutil.rmtree(ctx.temp_dir)
                logger.info(f"[WORKER {project_name}] Cleaned up local temp directory: {ctx.temp_dir}")
            
            # Remove the base temp/ directory if it's now empty (no other projects running)
            parent_temp = os.path.dirname(ctx.temp_dir)
            if os.path.isdir(parent_temp):
                try:
                    remaining = os.listdir(parent_temp)
                    if not remaining:
                        os.rmdir(parent_temp)
                        logger.info(f"[WORKER {project_name}] Removed empty parent temp directory: {parent_temp}")
                except OSError: 
                    logger.warning(f"[WORKER {project_name}] Failed to remove parent temp directory: {parent_temp}")
        except Exception as clean_e:
            logger.warning(f"[WORKER {project_name}] Failed to clean temp dir: {clean_e}")

    return project_path