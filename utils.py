# utils.py
import os
import sys
import json
import logging
import glob
import shutil
import re
import wave
import struct
import datetime
import time
from typing import List, Dict, Optional, Any, Tuple
from collections import OrderedDict

# ==============================================================================
# GLOBAL LOGGER
# ==============================================================================

logger = logging.getLogger("AIRenderer")

def setup_logging(log_widget=None):
    """
    Configures logging to stdout and optionally to a GUI widget.
    """
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers
    if logger.handlers:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# ==============================================================================
# THEME MANAGEMENT
# ==============================================================================

CURRENT_THEME = "Dark"

THEMES = {
    "Dark": {
        "name": "Dark",
        "list_item": {
            "background": "#2d2d30",
            "background_selected": "#007acc",
            "border_bottom": "1px solid #3d3d3d",
            "border_selected": "1px solid #007acc",
            "border_radius": 4,
            "height": 60,
            "margin": "2px 4px 2px 4px"
        },
        "status_strip": {
            "width": 6,
            "border_radius": "4px 0px 0px 4px",
            "colors": {
                "default": "#555555",
                "rendering": "#f1c40f",
                "queued": "#3498db",
                "ready": "#2ecc71",
                "error": "#e74c3c",
                "unknown": "#95a5a6"
            }
        },
        "badge": {
            "width": 50,
            "height": 20,
            "border_radius": 4,
            "font_size": 10,
            "font_weight": "bold",
            "colors": {
                "default_bg": "#444444",
                "default_text": "#cccccc",
                "default_border": "#555555"
            }
        },
        "text": {
            "title": {"color": "#ffffff", "font_size": 13, "font_weight": "600"},
            "meta": {"color": "#aaaaaa", "font_size": 11, "font_weight": "normal", "color_error": "#ff6b6b"}
        },
        "content_margins": (10, 5, 10, 5),
        "content_spacing": 10
    },
    "Light": {
        "name": "Light",
        "list_item": {
            "background": "#ffffff",
            "background_selected": "#cce5ff",
            "border_bottom": "1px solid #dee2e6",
            "border_selected": "1px solid #007acc",
            "border_radius": 4,
            "height": 60,
            "margin": "2px 4px 2px 4px"
        },
        "status_strip": {
            "width": 6,
            "border_radius": "4px 0px 0px 4px",
            "colors": {
                "default": "#ced4da",
                "rendering": "#ffc107",
                "queued": "#17a2b8",
                "ready": "#28a745",
                "error": "#dc3545",
                "unknown": "#6c757d"
            }
        },
        "badge": {
            "width": 50,
            "height": 20,
            "border_radius": 4,
            "font_size": 10,
            "font_weight": "bold",
            "colors": {
                "default_bg": "#e9ecef",
                "default_text": "#495057",
                "default_border": "#ced4da"
            }
        },
        "text": {
            "title": {"color": "#212529", "font_size": 13, "font_weight": "600"},
            "meta": {"color": "#6c757d", "font_size": 11, "font_weight": "normal", "color_error": "#dc3545"}
        },
        "content_margins": (10, 5, 10, 5),
        "content_spacing": 10
    }
}

def get_theme() -> Dict:
    return THEMES.get(CURRENT_THEME, THEMES["Dark"])

def set_theme(theme_name: str):
    global CURRENT_THEME
    if theme_name in THEMES:
        CURRENT_THEME = theme_name
    else:
        logger.warning(f"Theme '{theme_name}' not found, falling back to Dark.")

def list_themes() -> List[str]:
    return list(THEMES.keys())

def list_widget_stylesheet_from_theme() -> str:
    th = get_theme()
    return f"""
        QListWidget {{ background-color: {th['list_item']['background']}; border: none; outline: none; }}
        QListWidget::item {{ border: none; }}
    """

# ==============================================================================
# SYSTEM UTILITIES
# ==============================================================================

def detect_ffmpeg():
    """Detects FFmpeg executable."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    
    # Common paths
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg"
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            return path
            
    return None

def natural_sort_key(s):
    """
    Returns a key for natural sorting (e.g., slide1, slide2, slide10).
    """
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]

# ==============================================================================
# PROJECT MANAGEMENT
# ==============================================================================

def get_project_dirs() -> List[str]:
    """Returns a list of valid project directories."""
    # Assuming projects are stored in a 'Projects' folder by default or configured path
    # For this implementation, we scan 'Projects' in CWD
    root = "Projects"
    if not os.path.exists(root):
        try:
            os.makedirs(root)
        except:
            pass
        return []
    
    dirs = []
    for d in os.listdir(root):
        full_path = os.path.join(root, d)
        if os.path.isdir(full_path):
            # Basic validation: does it contain at least one HTML file?
            if glob.glob(os.path.join(full_path, "*.html")):
                dirs.append(full_path)
    return sorted(dirs, key=natural_sort_key)

def execute_bulk_delete(paths: List[str], active_procs: Dict, pending_procs: List[str]) -> Tuple[int, List[Tuple[str, str]]]:
    """
    Deletes project folders and removes them from the library manifest.
    Returns (deleted_count, list_of_failed_tuples).
    """
    deleted = 0
    failed = []
    
    # Load the central library manifest to clean it up
    lib_manifest_path = get_library_manifest_path()
    lib_data = {}
    if os.path.exists(lib_manifest_path):
        try:
            with open(lib_manifest_path, 'r', encoding='utf-8') as f:
                lib_data = json.load(f)
        except Exception:
            lib_data = {}

    manifest_changed = False

    for path in paths:
        project_name = os.path.basename(path)
        
        # Double check safety
        if any(p['path'] == path for p in active_procs.values()):
            failed.append((project_name, "Currently rendering"))
            continue
        if path in pending_procs:
            failed.append((project_name, "Currently queued"))
            continue
            
        try:
            # Delete the folder
            shutil.rmtree(path)
            deleted += 1
            
            # Remove from library manifest dictionary
            if project_name in lib_data:
                del lib_data[project_name]
                manifest_changed = True
                
        except Exception as e:
            failed.append((project_name, str(e)))
    
    # Save the cleaned manifest if changes occurred
    if manifest_changed:
        try:
            with open(lib_manifest_path, 'w', encoding='utf-8') as f:
                json.dump(lib_data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to update library manifest during bulk delete: {e}")
            
    return deleted, failed

def initialize_project_files(project_path: str):
    """
    Ensures the project directory exists and contains basic files.
    Useful for repair or initialization logic.
    """
    if not os.path.exists(project_path):
        os.makedirs(project_path)
    
    # Check for at least one slide
    html_files = glob.glob(os.path.join(project_path, "*.html"))
    if not html_files:
        create_slide_file(os.path.join(project_path, "slide1.html"), get_blank_slide_html("#000000"))
        with open(os.path.join(project_path, "slide1.txt"), "w", encoding="utf-8") as f:
            f.write("")

# ==============================================================================
# MANIFEST HANDLING
# ==============================================================================

LIBRARY_MANIFEST_FILE = "library.json"

def get_library_manifest_path() -> str:
    return LIBRARY_MANIFEST_FILE

def load_project_manifest(project_path: str) -> Dict:
    path = os.path.join(project_path, "manifest.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_project_manifest(project_path: str, data: Dict):
    path = os.path.join(project_path, "manifest.json")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving manifest for {project_path}: {e}")

def build_manifest_from_timeline(project_name, project_path, config, timeline, output_dir, video_path, mode, assets_meta):
    return {
        "project_name": project_name,
        "last_rendered": datetime.datetime.now().isoformat(),
        "settings_snapshot": {
            "resolution": f"{config.get('width')}x{config.get('height')}",
            "fps": config.get('fps')
        },
        "output": {
            "dir": output_dir,
            "video_filename": os.path.basename(video_path)
        },
        "assets_meta": assets_meta
    }

def update_library_manifest(project_path: str):
    """
    Updates the central library.json with the status of a specific project.
    """
    lib_path = get_library_manifest_path()
    data = {}
    if os.path.exists(lib_path):
        try:
            with open(lib_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            data = {}

    project_name = os.path.basename(project_path)
    manifest = load_project_manifest(project_path)
    
    status = "unknown"
    last_render = 0
    
    # Determine status
    output_info = manifest.get('output', {})
    video_file = output_info.get('video_filename')
    if video_file:
        video_path = os.path.join(output_info.get('dir', ''), video_file)
        if os.path.exists(video_path):
            status = "ready"
            last_render = os.path.getmtime(video_path)
    
    data[project_name] = {
        "path": project_path,
        "status": status,
        "last_render": last_render
    }
    
    try:
        with open(lib_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error updating library manifest: {e}")

def get_library_status_data(project_dirs: List[str], active_procs: Dict, pending_procs: List[str]) -> List[Dict]:
    """Gathers status data for the UI list."""
    data = []
    for path in project_dirs:
        name = os.path.basename(path)
        
        # Determine status
        status = "unknown"
        detail = "No Data"
        last_render = 0
        
        # Check active
        is_active = any(p['path'] == path for p in active_procs.values())
        is_pending = path in pending_procs
        
        if is_active:
            status = "rendering"
            detail = "Processing..."
        elif is_pending:
            status = "queued"
            detail = "Waiting..."
        else:
            # Check manifest
            manifest = load_project_manifest(path)
            out_info = manifest.get('output', {})
            vid_name = out_info.get('video_filename')
            out_dir = out_info.get('dir')
            
            if vid_name and out_dir:
                vid_path = os.path.join(out_dir, vid_name)
                if os.path.exists(vid_path):
                    status = "ready"
                    detail = f"Rendered: {time.ctime(os.path.getmtime(vid_path))}"
                    last_render = os.path.getmtime(vid_path)
                else:
                    status = "error"
                    detail = "Output missing"
            else:
                status = "unknown"
                detail = "Not Rendered"

        data.append({
            "name": name,
            "path": path,
            "status": status,
            "detail": detail,
            "last_rendered": last_render
        })
        
    return data

def should_skip_render(project_path: str, config: dict) -> bool:
    """
    Heuristic check to see if we can skip rendering entirely.
    The worker process does the definitive check, this is just an optimization for the UI queue.
    """
    manifest = load_project_manifest(project_path)
    
    # If no manifest, definitely render
    if not manifest:
        return False
        
    # If output video missing, render
    out_info = manifest.get('output', {})
    vid_name = out_info.get('video_filename')
    out_dir = out_info.get('dir')
    if not vid_name or not out_dir:
        return False
        
    vid_path = os.path.join(out_dir, vid_name)
    if not os.path.exists(vid_path):
        return False
        
    # If source files are newer than video, render
    vid_mtime = os.path.getmtime(vid_path)
    for f in os.listdir(project_path):
        if f.endswith('.html') or f.endswith('.txt'):
            if os.path.getmtime(os.path.join(project_path, f)) > vid_mtime:
                return False
                
    return True

# ==============================================================================
# FILE GENERATION
# ==============================================================================

def create_slide_file(path: str, content: str):
    """Utility to create a slide HTML file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Failed to create slide file: {e}")

def get_default_slide_html(text: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
    body {{ margin: 0; background-color: #1e1e1e; color: white; font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; text-align: center; }}
    .container {{ padding: 40px; }}
    h1 {{ font-size: 4em; margin-bottom: 0.2em; }}
    p {{ font-size: 2em; color: #cccccc; }}
</style>
</head>
<body>
    <div class="container">
        <h1>Slide</h1>
        <p>{text}</p>
    </div>
</body>
</html>
"""

def get_image_slide_html(image_path: str) -> str:
    # Convert path to URL format for HTML
    file_url = f"file:///{os.path.abspath(image_path).replace(os.sep, '/')}"
    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
    body {{ margin: 0; background-color: #000; height: 100vh; display: flex; justify-content: center; align-items: center; }}
    img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
</style>
</head>
<body>
    <img src="{file_url}" alt="Slide Image">
</body>
</html>
"""

def get_blank_slide_html(color: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
    body {{ margin: 0; background-color: {color}; height: 100vh; }}
</style>
</head>
<body>
</body>
</html>
"""

# ==============================================================================
# AUDIO UTILITIES
# ==============================================================================

def generate_silent_wav(path: str, duration: float):
    """
    Generates a silent WAV file.
    """
    try:
        sample_rate = 22050
        n_channels = 1
        sampwidth = 2
        n_frames = int(duration * sample_rate)
        
        with wave.open(path, 'w') as wav_file:
            wav_file.setparams((n_channels, sampwidth, sample_rate, n_frames, 'NONE', 'not compressed'))
            # Write silent frames (zeros)
            wav_file.writeframes(struct.pack('<' + 'h' * n_frames, *([0] * n_frames)))
    except Exception as e:
        logger.error(f"Failed to generate silent wav: {e}")