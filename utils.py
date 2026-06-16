# utils.py
"""
Core utilities, data structures, styling, and file import/export logic.

Handles manifest management, FFmpeg detection, silent audio generation,
theme management, and the heavy lifting of importing PDFs, PPTXs, 
and images into HTML/TXT slide pairs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import subprocess
import wave
import contextlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
#  OPTIONAL DEPENDENCY FLAGS
# ═══════════════════════════════════════════════════════════════════════════════

TORCH_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logging(level=logging.INFO) -> None:
    """Configures the root logger for the application."""
    root = logging.getLogger()
    root.setLevel(level)
    
    # Avoid adding duplicate handlers if called multiple times
    if not root.handlers:
        console = logging.StreamHandler()
        console.setLevel(level)
        formatter = logging.Formatter(
            "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s", 
            datefmt="%H:%M:%S"
        )
        console.setFormatter(formatter)
        root.addHandler(console)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES & MESSAGES
# ═══════════════════════════════════════════════════════════════════════════════

class RenderPhase(str, Enum):
    SCANNING = "Scanning"
    PROCESSING_AUDIO = "Processing Audio"
    GENERATING_AUDIO = "Generating Audio"
    BUILDING_TIMELINE = "Building Timeline"
    RENDERING_VIDEO = "Rendering Video"
    FINALIZING = "Finalizing"

class RenderStatus(str, Enum):
    PENDING = "Pending"
    RENDERING = "Rendering"
    READY = "Ready"
    ERROR = "Error"
    OUTDATED = "Outdated"

STATUS_SORT_PRIORITY = {
    RenderStatus.RENDERING: 0,
    RenderStatus.ERROR: 1,
    RenderStatus.OUTDATED: 2,
    RenderStatus.PENDING: 3,
    RenderStatus.READY: 4,
}

@dataclass
class PhaseMessage:
    project_name: str
    phase: str

@dataclass
class SlideProgressMessage:
    project_name: str
    current: int
    total: int

@dataclass
class ChunkProgressMessage:
    project_name: str
    current: int
    total: int
    message: str = ""

@dataclass
class ErrorMessage:
    project_name: str
    error_text: str

@dataclass
class FinalizedMessage:
    project_name: str

@dataclass
class LogMessage:
    project_name: str
    level: str
    text: str


# ═══════════════════════════════════════════════════════════════════════════════
#  CUSTOM EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class BackendGenerationError(Exception): pass
class FFmpegError(Exception): pass
class VideoRenderError(Exception): pass
class BackendInitializationError(Exception): pass


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

FILE_FILTER_IMPORT = "Importable Files (*.pdf *.pptx *.txt *.csv *.json *.png *.jpg *.jpeg *.wav);;All Files (*)"
FILE_FILTER_SCRIPT = "Script Files (*.txt *.md *json *pdf);;Text Files (*.txt);;Markdown Files (*.md);;All Files (*)"
FILE_FILTER_AUDIO = "Audio Files (*.wav *.mp3 *.ogg *.flac);;WAV Files (*.wav);;All Files (*)"
FILE_FILTER_IMAGE = "Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*)"
FILE_FILTER_VIDEO = "Video Files (*.mp4 *.avi *.mov);;All Files (*)"

# ═══════════════════════════════════════════════════════════════════════════════
#  STYLING & PALETTE
# ═══════════════════════════════════════════════════════════════════════════════

class Palette:
    BG = "#1e1e1e"
    BG_LIGHT = "#252526"
    BG_DARK = "#181818"
    TEXT = "#d4d4d4"
    TEXT_DIM = "#808080"
    ACCENT = "#0078d4"
    ACCENT_HOVER = "#1a8ae8"
    BORDER = "#3c3c3c"
    ERROR = "#f44747"
    SUCCESS = "#6a9955"
    WARNING = "#FFA500"
    
    LOG_DEBUG = "#608b4e"
    LOG_INFO = "#d4d4d4"
    LOG_WARN = "#cca700"
    LOG_ERROR = "#f44747"
    LOG_CRITICAL = "#ff0000"

def _base_style() -> str:
    return f"color: {Palette.TEXT}; background-color: {Palette.BG_LIGHT}; border: 1px solid {Palette.BORDER};"

def style_input(padding="6px", radius="4px") -> str:
    return f"{_base_style()} padding: {padding}; border-radius: {radius};"

def style_input_wide(padding="6px", radius="4px") -> str:
    return style_input(padding, radius) + " min-width: 180px;"

def style_button(padding="6px 12px", radius="4px") -> str:
    return (
        f"color: {Palette.TEXT}; background-color: {Palette.BG_DARK}; "
        f"border: 1px solid {Palette.BORDER}; padding: {padding}; border-radius: {radius};"
    )

def style_button_small() -> str:
    return style_button(padding="3px 8px", radius="3px")

def style_primary_button() -> str:
    return (
        f"color: white; background-color: {Palette.ACCENT}; border: none; "
        f"padding: 6px 12px; border-radius: 4px; font-weight: bold;"
    )

def style_blue_button() -> str:
    return style_primary_button()

def style_green_button() -> str:
    return (
        f"color: white; background-color: {Palette.SUCCESS}; border: none; "
        f"padding: 6px 12px; border-radius: 4px; font-weight: bold;"
    )

def style_stop_button() -> str:
    return (
        f"color: white; background-color: {Palette.ERROR}; border: none; "
        f"padding: 6px 12px; border-radius: 4px; font-weight: bold;"
    )

def style_radio() -> str:
    return f"color: {Palette.TEXT};"

def style_checkbox() -> str:
    return f"color: {Palette.TEXT};"

def style_group_box() -> str:
    return (
        f"QGroupBox {{ color: {Palette.TEXT}; border: none; font-weight: bold; margin-top: 10px; }} "
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}"
    )

def style_group_box_bordered() -> str:
    return (
        f"QGroupBox {{ color: {Palette.TEXT}; border: 1px solid {Palette.BORDER}; border-radius: 4px; "
        f"margin-top: 10px; padding-top: 15px; font-weight: bold; }} "
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}"
    )

def style_summary_label() -> str:
    return f"color: {Palette.TEXT_DIM}; font-style: italic;"

def style_log_text() -> str:
    return (
        f"background-color: {Palette.BG_DARK}; color: {Palette.TEXT}; "
        f"border: 1px solid {Palette.BORDER}; font-family: 'Consolas', 'Courier New', monospace;"
    )

def style_context_menu() -> str:
    return (
        f"QMenu {{ background-color: {Palette.BG_LIGHT}; color: {Palette.TEXT}; border: 1px solid {Palette.BORDER}; }} "
        f"QMenu::item {{ padding: 5px 20px; }} "
        f"QMenu::item:selected {{ background-color: {Palette.ACCENT}; }}"
    )

def list_widget_stylesheet_from_theme() -> str:
    return (
        f"QListWidget {{ background-color: {Palette.BG_DARK}; border: 1px solid {Palette.BORDER}; }} "
        f"QListWidget::item {{ padding: 5px; }}"
    )

def project_list_stylesheet() -> str:
    return list_widget_stylesheet_from_theme() + (
        "QListWidget::item { background: transparent; border: none; }"
        "QListWidget::item:selected { background: transparent; border: none; }"
        "QListWidget::item:hover   { background: transparent; border: none; }"
    )

def style_page_widget() -> str:
    """Styles the base container widget for a tab or page."""
    return (
        f"QWidget {{ background-color: {Palette.BG_DARK}; color: {Palette.TEXT}; border: none; }}"
    )

def style_scroll_area() -> str:
    """Styles QScrollArea to match the dark theme."""
    return (
        f"QScrollArea {{ background-color: {Palette.BG_DARK}; border: none; }}"
        f"QScrollBar:vertical {{ background: {Palette.BG_DARK}; width: 10px; margin: 0px; }}"
        f"QScrollBar::handle:vertical {{ background: {Palette.BORDER}; min-height: 20px; border-radius: 5px; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
    )

def style_tab_widget() -> str:
    """Styles QTabWidget panes."""
    return (
        f"QTabWidget::pane {{ border: 1px solid {Palette.BORDER}; background: {Palette.BG_DARK}; }}"
    )

def style_label_header() -> str:
    """Styles large header labels."""
    return f"color: {Palette.TEXT}; font-size: 14px; font-weight: bold;"

def style_label_subtitle() -> str:
    """Styles smaller descriptive labels."""
    return f"color: {Palette.TEXT_DIM}; font-size: 11px;"

def style_list_widget() -> str:
    """Styles generic QListWidgets."""
    return (
        f"QListWidget {{ background-color: {Palette.BG_DARK}; border: 1px solid {Palette.BORDER}; "
        f"color: {Palette.TEXT}; outline: none; }}"
        f"QListWidget::item {{ padding: 5px; }}"
        f"QListWidget::item:selected {{ background-color: {Palette.ACCENT}; }}"
    )

def style_progress_bar() -> str:
    """Styles the QProgressBar."""
    return (
        f"QProgressBar {{ border: 1px solid {Palette.BORDER}; border-radius: 4px; "
        f"background-color: {Palette.BG_DARK}; text-align: center; color: {Palette.TEXT}; }}"
        f"QProgressBar::chunk {{ background-color: {Palette.ACCENT}; border-radius: 3px; }}"
    )

def style_tool_button() -> str:
    """Styles flat tool buttons."""
    return (
        f"QToolButton {{ background: transparent; border: none; color: {Palette.TEXT}; "
        f"padding: 4px; }}"
        f"QToolButton:hover {{ background-color: {Palette.BG_LIGHT}; }}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS-SPECIFIC STYLE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def style_combo(padding="6px 10px", radius="4px") -> str:
    """Styles QComboBox for the settings UI with dropdown arrow."""
    return (
        f"QComboBox {{ color: {Palette.TEXT}; background-color: {Palette.BG_LIGHT}; "
        f"border: 1px solid {Palette.BORDER}; border-radius: {radius}; "
        f"padding: {padding}; min-height: 22px; }}"
        f"QComboBox:hover {{ border-color: {Palette.ACCENT}; }}"
        f"QComboBox::drop-down {{ border: none; width: 24px; }}"
        f"QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; "
        f"border-right: 5px solid transparent; border-top: 6px solid {Palette.TEXT_DIM}; "
        f"margin-right: 8px; }}"
        f"QComboBox QAbstractItemView {{ color: {Palette.TEXT}; "
        f"background-color: {Palette.BG_LIGHT}; border: 1px solid {Palette.BORDER}; "
        f"selection-background-color: {Palette.ACCENT}; selection-color: white; "
        f"outline: none; padding: 4px; }}"
    )

def style_spinbox(padding="4px 8px", radius="4px") -> str:
    """Styles QSpinBox / QDoubleSpinBox with up/down buttons."""
    return (
        f"QSpinBox, QDoubleSpinBox {{ color: {Palette.TEXT}; "
        f"background-color: {Palette.BG_LIGHT}; border: 1px solid {Palette.BORDER}; "
        f"border-radius: {radius}; padding: {padding}; min-height: 22px; }}"
        f"QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {Palette.ACCENT}; }}"
        f"QSpinBox::up-button, QDoubleSpinBox::up-button {{ "
        f"subcontrol-origin: border; subcontrol-position: top right; "
        f"width: 20px; border-left: 1px solid {Palette.BORDER}; border-bottom: 1px solid {Palette.BORDER}; "
        f"border-top-right-radius: {radius}; }}"
        f"QSpinBox::down-button, QDoubleSpinBox::down-button {{ "
        f"subcontrol-origin: border; subcontrol-position: bottom right; "
        f"width: 20px; border-left: 1px solid {Palette.BORDER}; "
        f"border-bottom-right-radius: {radius}; }}"
        f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ "
        f"width: 8px; height: 8px; border-left: 4px solid transparent; "
        f"border-right: 4px solid transparent; border-bottom: 5px solid {Palette.TEXT_DIM}; }}"
        f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ "
        f"width: 8px; height: 8px; border-left: 4px solid transparent; "
        f"border-right: 4px solid transparent; border-top: 5px solid {Palette.TEXT_DIM}; }}"
    )

def style_checkbox_rich() -> str:
    """Full-featured checkbox style for settings pages with indicator theming."""
    return (
        f"QCheckBox {{ color: {Palette.TEXT}; spacing: 8px; }}"
        f"QCheckBox::indicator {{ width: 16px; height: 16px; "
        f"border: 1px solid {Palette.BORDER}; border-radius: 3px; "
        f"background-color: {Palette.BG_LIGHT}; }}"
        f"QCheckBox::indicator:hover {{ border-color: {Palette.ACCENT}; }}"
        f"QCheckBox::indicator:checked {{ background-color: {Palette.ACCENT}; "
        f"border-color: {Palette.ACCENT}; "
        f"image: none; }}"
        f"QCheckBox::indicator:checked:hover {{ background-color: {Palette.ACCENT_HOVER}; "
        f"border-color: {Palette.ACCENT_HOVER}; }}"
    )

def style_settings_sidebar() -> str:
    """Styles the settings sidebar QListWidget with icons and hover/active states."""
    return (
        f"QListWidget {{ background-color: {Palette.BG_LIGHT}; border: none; "
        f"border-right: 1px solid {Palette.BORDER}; font-size: 13px; outline: none; }}"
        f"QListWidget::item {{ padding: 14px 16px; color: {Palette.TEXT_DIM}; "
        f"border: none; border-left: 3px solid transparent; }}"
        f"QListWidget::item:selected {{ background-color: {Palette.BG_DARK}; "
        f"color: {Palette.TEXT}; border-left: 3px solid {Palette.ACCENT}; }}"
        f"QListWidget::item:hover:!selected {{ background-color: {Palette.BG_DARK}; "
        f"color: {Palette.TEXT}; }}"
    )

def style_settings_search() -> str:
    """Styles the settings search bar."""
    return (
        f"QLineEdit {{ padding: 8px 12px; background-color: {Palette.BG_DARK}; "
        f"color: {Palette.TEXT}; border: none; border-bottom: 1px solid {Palette.BORDER}; "
        f"font-size: 13px; }}"
        f"QLineEdit:focus {{ border-bottom: 2px solid {Palette.ACCENT}; }}"
        f"QLineEdit::placeholder {{ color: {Palette.TEXT_DIM}; }}"
    )

def style_settings_page() -> str:
    """Styles a settings content page (QWidget inside the stacked widget)."""
    return (
        f"QWidget {{ background-color: {Palette.BG_DARK}; color: {Palette.TEXT}; }}"
    )

def style_settings_scroll() -> str:
    """Styles scroll areas inside settings pages."""
    return (
        f"QScrollArea {{ background-color: {Palette.BG_DARK}; border: none; }}"
        f"QScrollBar:vertical {{ background: {Palette.BG_DARK}; width: 8px; margin: 0px; }}"
        f"QScrollBar::handle:vertical {{ background: {Palette.BORDER}; min-height: 30px; "
        f"border-radius: 4px; }}"
        f"QScrollBar::handle:vertical:hover {{ background: {Palette.TEXT_DIM}; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
    )

def style_form_label() -> str:
    """Styles QFormLayout label (left column) for settings pages."""
    return (
        f"color: {Palette.TEXT_DIM}; font-size: 12px; padding-top: 4px;"
    )

def style_section_header() -> str:
    """Styles a section header label within settings pages."""
    return (
        f"color: {Palette.TEXT}; font-size: 15px; font-weight: bold; "
        f"padding: 8px 0px 4px 0px;"
    )

def style_section_desc() -> str:
    """Styles a section description label below a header."""
    return (
        f"color: {Palette.TEXT_DIM}; font-size: 12px; padding: 0px 0px 12px 0px;"
    )

def style_group_box_settings() -> str:
    """Styles QGroupBox for settings pages — subtle card-like appearance."""
    return (
        f"QGroupBox {{ color: {Palette.TEXT}; background-color: {Palette.BG_LIGHT}; "
        f"border: 1px solid {Palette.BORDER}; border-radius: 6px; "
        f"margin-top: 14px; padding: 16px; padding-top: 24px; font-weight: bold; font-size: 13px; }}"
        f"QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; "
        f"color: {Palette.TEXT}; }}"
    )

def style_browse_button() -> str:
    """Styles the small 'Browse...' / '...' buttons in settings."""
    return (
        f"QPushButton {{ color: {Palette.TEXT}; background-color: {Palette.BG_LIGHT}; "
        f"border: 1px solid {Palette.BORDER}; border-radius: 4px; "
        f"padding: 5px 12px; min-width: 60px; }}"
        f"QPushButton:hover {{ border-color: {Palette.ACCENT}; color: {Palette.ACCENT}; }}"
        f"QPushButton:pressed {{ background-color: {Palette.ACCENT}; color: white; }}"
    )

def style_diagnostics_text() -> str:
    """Styles the QTextEdit used in the diagnostics page."""
    return (
        f"QTextEdit {{ background-color: {Palette.BG_LIGHT}; color: {Palette.TEXT}; "
        f"border: 1px solid {Palette.BORDER}; border-radius: 6px; "
        f"padding: 12px; font-size: 13px; }}"
        f"QTextEdit h3 {{ color: {Palette.ACCENT}; }}"
    )

def style_copy_button() -> str:
    """Styles the 'Copy to Clipboard' style button for diagnostics."""
    return (
        f"QPushButton {{ color: white; background-color: {Palette.ACCENT}; "
        f"border: none; border-radius: 4px; padding: 8px 16px; font-weight: bold; }}"
        f"QPushButton:hover {{ background-color: {Palette.ACCENT_HOVER}; }}"
        f"QPushButton:pressed {{ background-color: #005a9e; }}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  THEMES
# ═══════════════════════════════════════════════════════════════════════════════

_CURRENT_THEME_NAME = "Dark"

_THEMES = {
    "Dark": Palette,
}

def get_theme() -> dict:
    """Return the current theme as a dictionary with 'name' and 'palette' keys."""
    return {
        "name": _CURRENT_THEME_NAME,
        "palette": _THEMES.get(_CURRENT_THEME_NAME, Palette),
    }

def set_theme(name: str) -> None:
    """Set the active theme by name."""
    global _CURRENT_THEME_NAME
    if name in _THEMES:
        _CURRENT_THEME_NAME = name
        logger.info(f"[UI] Theme switched to '{name}'")
    else:
        logger.warning(f"[UI] Unknown theme '{name}', keeping '{_CURRENT_THEME_NAME}'")

def list_themes() -> list[str]:
    return list(_THEMES.keys())


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS & FILE UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def natural_sort_key(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', str(text))]

def detect_ffmpeg() -> Optional[str]:
    """Detect FFmpeg executable."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    # Check common paths
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None

def generate_silent_wav(output_path: str, duration: float) -> bool:
    """Generates a silent WAV file."""
    try:
        sample_rate = 44100
        num_channels = 1
        num_frames = int(sample_rate * duration)
        
        with wave.open(output_path, 'w') as w:
            w.setnchannels(num_channels)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(sample_rate)
            # Write silent frames (zeros)
            w.writeframes(struct.pack('<' + 'h' * num_frames, *([0] * num_frames)))
        return True
    except Exception as exc:
        logger.error(f"Failed to generate silent WAV: {exc}")
        return False

def is_image_slide(html_path: str) -> bool:
    """Checks if an HTML slide is just an image wrapper."""
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read().lower()
        # Simple heuristic: contains an img tag with object-fit contain/cover
        return '<img' in content and 'object-fit' in content
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML / SLIDE GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def get_blank_slide_html() -> str:
    return """<!DOCTYPE html>
<html><head><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; background: #000; color: #fff; display: flex; align-items: center; justify-content: center; }
</style></head><body></body></html>"""

def get_default_slide_html() -> str:
    return """<!DOCTYPE html>
<html><head><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; background: #000; color: #fff; display: flex; align-items: center; justify-content: center; font-family: sans-serif; font-size: 4vw; padding: 5vw; }
</style></head><body><div class="content">Slide Content</div></body></html>"""

def get_image_slide_html(image_rel_path: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; background: #000; }}
  img {{ width: 100%; height: 100%; object-fit: contain; }}
</style></head><body>
  <img src="{image_rel_path}" />
</body></html>"""

def create_slide_file(project_path: str, slide_num: int, html_content: str, txt_content: str = "") -> None:
    """Creates the HTML and TXT files for a specific slide number."""
    html_path = os.path.join(project_path, f"slide{slide_num}.html")
    txt_path = os.path.join(project_path, f"slide{slide_num}.txt")
    
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content.strip())

def get_next_slide_number(project_path: str) -> int:
    """Finds the next available slide number in a project directory."""
    existing = [0]
    for f in os.listdir(project_path):
        match = re.match(r"slide(\d+)\.html", f)
        if match:
            existing.append(int(match.group(1)))
    return max(existing) + 1


# ═══════════════════════════════════════════════════════════════════════════════
#  PROJECT & MANIFEST MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def get_project_dirs() -> list[str]:
    """Returns a list of valid project directory paths."""
    from config import AppConfig
    config = AppConfig()
    projects_root = config.get("projects_root", "Projects")
    # Always resolve to absolute path — the default "Projects" is relative
    if not os.path.isabs(projects_root):
        projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)

    if not os.path.exists(projects_root):
        return []

    _SLIDE_RE = re.compile(r"slide\d+\.html$", re.IGNORECASE)
    dirs = []
    for entry in os.listdir(projects_root):
        full_path = os.path.join(projects_root, entry)
        if not os.path.isdir(full_path):
            continue
        # Use any() + re.match per filename instead of re.match on a
        # space-joined string — the old approach failed whenever a file
        # sorted before "slide*.html" alphabetically (e.g. "assets/",
        # "manifest.json").
        if any(_SLIDE_RE.match(f) for f in os.listdir(full_path)):
            dirs.append(full_path)
    return sorted(dirs, key=natural_sort_key)

def initialize_project_files(project_path: str, config: dict = None) -> None:
    """Initializes a new project directory with a first blank slide."""
    os.makedirs(project_path, exist_ok=True)
    create_slide_file(project_path, 1, get_default_slide_html(), "")
    # Write a project manifest so the library can immediately display
    # slide count and source info for this brand-new project.
    project_name = os.path.basename(project_path)
    save_project_manifest(project_path, {
        "name": project_name,
        "path": project_path,
        "source": "blank",
        "slide_count": 1,
        "status": "new",
        "created": _now_iso(),
    })
    # Also update the global library manifest
    update_library_manifest(project_path)

def get_library_manifest_path() -> str:
    from config import AppConfig
    return os.path.join(AppConfig.APP_ROOT, "library_manifest.json")

def load_project_manifest(project_path: str) -> dict:
    manifest_path = os.path.join(project_path, "manifest.json")
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"Could not load manifest for {project_path}: {exc}")
        return {}

def save_project_manifest(project_path: str, data: dict) -> None:
    manifest_path = os.path.join(project_path, "manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as exc:
        logger.error(f"Failed to save manifest for {project_path}: {exc}")

def update_library_manifest(project_path: str) -> None:
    """Updates the global library manifest with current project status.

    The library manifest acts as a cache so refresh_library() can display
    rich info without re-scanning every project directory on disk.
    """
    lib_path = get_library_manifest_path()
    data = {}
    if os.path.exists(lib_path):
        try:
            with open(lib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    project_name = os.path.basename(project_path)
    manifest = load_project_manifest(project_path)

    # Count slides from disk for accuracy
    try:
        _SLIDE_RE = re.compile(r"slide\d+\.html$", re.IGNORECASE)
        slide_count = sum(1 for f in os.listdir(project_path) if _SLIDE_RE.match(f))
    except Exception:
        slide_count = manifest.get("slide_count", 0)

    data[project_name] = {
        "path": project_path,
        "source": manifest.get("source", ""),
        "slide_count": slide_count,
        "status": manifest.get("status", "new"),
        "last_rendered": manifest.get("timestamp", 0),
        "created": manifest.get("created", ""),
        "title": manifest.get("title", project_name),
    }

    try:
        with open(lib_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as exc:
        logger.error(f"Failed to update library manifest: {exc}")

def build_manifest_from_timeline(
    project_name: str, project_path: str, config: dict,
    timeline: list, output_dir: str, final_video: str,
    render_mode: str, assets_meta: dict
) -> dict:
    """Constructs the manifest dictionary for a completed render."""
    import time
    return {
        "project_name": project_name,
        "path": project_path,
        "timestamp": time.time(),
        "status": "completed",
        "output": {
            "dir": output_dir,
            "video_filename": os.path.basename(final_video)
        },
        "render_settings": {
            "width": config.get("width"),
            "height": config.get("height"),
            "fps": config.get("fps"),
        },
        "timeline_count": len(timeline),
        "assets_meta": assets_meta
    }

def get_library_status_data(project_dirs: list, active_procs: dict, pending: list) -> list:
    """Generates UI-friendly status data for projects.

    Status values are capitalized strings matching RenderStatus enum /
    StatusTheme keys: "New", "Outdated", "Pending", "Rendering", "Ready", "Error".
    """
    data = []
    active_paths = {p_info["path"] for p_info in active_procs.values()}
    pending_paths = set(pending)

    for path in project_dirs:
        name = os.path.basename(path)
        manifest = load_project_manifest(path)

        if path in active_paths:
            status = RenderStatus.RENDERING
            detail = "Rendering..."
        elif path in pending_paths:
            status = RenderStatus.PENDING
            detail = "Queued"
        elif manifest.get("status") == "completed":
            status = RenderStatus.READY
            detail = "Completed"
        elif manifest.get("status") == "new":
            status = "New"
            detail = "New Project"
        else:
            status = RenderStatus.OUTDATED
            detail = "Needs Render"

        # Enrich with manifest data for better library display
        slide_count = manifest.get("slide_count", 0)
        # If slide_count missing, count from disk
        if not slide_count:
            try:
                _SLIDE_RE = re.compile(r"slide\d+\.html$", re.IGNORECASE)
                slide_count = sum(1 for f in os.listdir(path) if _SLIDE_RE.match(f))
            except Exception:
                pass

        data.append({
            "name": name,
            "path": path,
            "status": status,
            "detail": detail,
            "slide_count": slide_count,
            "source": manifest.get("source", ""),
            "last_rendered": manifest.get("timestamp", 0),
            "created": manifest.get("created", ""),
        })
    return data

def should_skip_render(project_path: str, config: dict) -> bool:
    """Checks if a project is already up-to-date with current settings."""
    manifest = load_project_manifest(project_path)
    if manifest.get("status") != "completed":
        return False
    
    # Simple check: if width/height/fps changed, don't skip
    render_settings = manifest.get("render_settings", {})
    if render_settings.get("width") != config.get("width"): return False
    if render_settings.get("height") != config.get("height"): return False
    if render_settings.get("fps") != config.get("fps"): return False
    
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORT LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _create_import_slide_files(project_path: str, slide_num: int, img_rel_path: str, text_content: str = ""):
    """Helper specifically for image imports to generate the wrapper HTML and TXT."""
    html_content = get_image_slide_html(img_rel_path)
    create_slide_file(project_path, slide_num, html_content, text_content)

def import_image_as_slide(image_path: str, project_path: str, config: dict) -> int:
    """Imports a single image as a new slide at the end of the project."""
    images_dir = os.path.join(project_path, "images")
    os.makedirs(images_dir, exist_ok=True)
    
    slide_num = get_next_slide_number(project_path)
    ext = os.path.splitext(image_path)[1]
    img_name = f"slide{slide_num}{ext}"
    img_path = os.path.join(images_dir, img_name)
    
    shutil.copy2(image_path, img_path)
    _create_import_slide_files(project_path, slide_num, f"images/{img_name}", "")
    
    logger.info(f"[Import] Image imported as slide {slide_num}")
    return 1


def _resolve_on_exists(project_path: str, project_name: str, on_exists: str) -> None:
    """Handle an existing project directory according to the on_exists policy.

    Args:
        on_exists: "error"  – raise ValueError (default, backward-compatible)
                   "overwrite" – delete the existing directory
                   "append"    – keep the directory; caller should use append logic
    """
    if not os.path.exists(project_path):
        return

    if on_exists == "overwrite":
        shutil.rmtree(project_path)
        logger.info(f"[Import] Overwriting existing project: {project_name}")
    elif on_exists == "append":
        logger.info(f"[Import] Appending to existing project: {project_name}")
    else:  # "error" or any unknown value
        raise ValueError(
            f"Project '{project_name}' already exists. "
            f"Choose 'Overwrite' or 'Append' to proceed."
        )


def import_pdf_as_project(
    pdf_path: str,
    config: dict,
    project_name: str = None,
    on_exists: str = "error",
) -> str:
    """Import a PDF file as a new project, converting each page to a slide."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF import. "
            "Install via: pip install PyMuPDF"
        )

    if not os.path.isfile(pdf_path):
        raise ValueError(f"PDF file not found: {pdf_path}")

    # Resolve project name
    if not project_name:
        project_name = os.path.splitext(os.path.basename(pdf_path))[0]

    # Create the project directory
    from config import AppConfig
    projects_root = config.get("projects_root", "Projects")
    if not os.path.isabs(projects_root):
        projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)
    project_path = os.path.join(projects_root, project_name)

    _resolve_on_exists(project_path, project_name, on_exists)

    os.makedirs(project_path, exist_ok=True)

    # Get DPI setting
    dpi = config.get("pdf_import_dpi", 200)
    zoom = dpi / 72.0  # 72 is the default PDF resolution

    # ── Open the PDF and process pages ──────────────────────────────────
    # FIX #1: Suppress MuPDF's noisy C-level stderr warnings about
    # "No common ancestor in structure tree". These are non-fatal
    # structural warnings — the pages are still perfectly readable.
    doc = None
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            doc = fitz.open(pdf_path)

        if doc.is_encrypted:
            doc.close()
            raise ValueError(
                f"PDF '{os.path.basename(pdf_path)}' is encrypted and "
                f"cannot be imported."
            )

        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            raise ValueError(
                f"PDF '{os.path.basename(pdf_path)}' has no pages."
            )

        logger.info(
            f"[Import] PDF has {total_pages} page(s), "
            f"rendering at {dpi} DPI"
        )

        images_dir = os.path.join(project_path, "images")
        os.makedirs(images_dir, exist_ok=True)

        # FIX #2: Process ALL pages inside the `with` block so the
        # document is never accessed after closing.
        for page_num in range(total_pages):
            page = doc[page_num]
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img_filename = f"slide{page_num + 1}.png"
            img_path = os.path.join(images_dir, img_filename)
            # Capture width before freeing the pixmap
            rendered_width = pix.width
            pix.save(img_path)
            pix = None  # Free memory immediately

            # Create the slide HTML
            html_path = os.path.join(project_path, f"slide{page_num + 1}.html")
            _create_image_slide_html(
                html_path, f"images/{img_filename}",
                width=rendered_width,
            )

            # Create empty txt file (PDF slides are silent/image-only)
            txt_path = os.path.join(project_path, f"slide{page_num + 1}.txt")
            if not os.path.exists(txt_path):
                Path(txt_path).write_text("", encoding="utf-8")

            logger.debug(
                f"[Import] Rendered page {page_num + 1}/{total_pages}"
            )

        logger.info(
            f"[Import] Successfully imported {total_pages} page(s) "
            f"from '{os.path.basename(pdf_path)}'"
        )

        # Save a project manifest for consistency with other import paths
        manifest_data = {
            "name": project_name,
            "source": "pdf",
            "source_file": os.path.basename(pdf_path),
            "slide_count": total_pages,
            "pdf_import_dpi": dpi,
            "status": "new",
            "created": _now_iso(),
        }
        save_project_manifest(project_path, manifest_data)
        update_library_manifest(project_path)

    except ImportError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        logger.error(
            f"[Import] Failed to import PDF "
            f"'{os.path.basename(pdf_path)}': {exc}",
            exc_info=True,
        )
        raise ValueError(f"Failed to import PDF: {exc}") from exc
    finally:
        # FIX #2: Guarantee the document is closed, even on error
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    return project_path



def _create_image_slide_html(html_path: str, img_src: str, width: int = 1280):
    """Create a minimal HTML file that displays an image full-screen."""
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{
    margin: 0;
    padding: 0;
    background: #000;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    overflow: hidden;
  }}
  img {{
    max-width: 100%;
    max-height: 100vh;
    object-fit: contain;
  }}
</style>
</head>
<body>
  <img src="{img_src}" alt="Slide">
</body>
</html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

def import_pdf_append(pdf_path: str, project_path: str, config: dict) -> int:
    """Appends PDF pages as image slides to an existing project."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required to import PDF files. "
            "Install via: pip install PyMuPDF"
        )

    if not os.path.isfile(pdf_path):
        raise ValueError(f"PDF file not found: {pdf_path}")

    images_dir = os.path.join(project_path, "images")
    os.makedirs(images_dir, exist_ok=True)

    dpi = config.get("pdf_import_dpi", 200)
    zoom = dpi / 72.0

    doc = None
    try:
        # Suppress MuPDF C-level stderr warnings (non-fatal structural warnings)
        with contextlib.redirect_stderr(io.StringIO()):
            doc = fitz.open(pdf_path)

        if doc.is_encrypted:
            doc.close()
            raise ValueError(
                f"PDF '{os.path.basename(pdf_path)}' is encrypted and "
                f"cannot be imported."
            )

        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            raise ValueError(
                f"PDF '{os.path.basename(pdf_path)}' has no pages."
            )

        start_slide = get_next_slide_number(project_path)

        for page_num in range(total_pages):
            slide_num = start_slide + page_num
            page = doc.load_page(page_num)

            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img_name = f"slide{slide_num}.png"
            img_path = os.path.join(images_dir, img_name)
            pix.save(img_path)
            pix = None  # Free memory immediately

            text = page.get_text("text")
            _create_import_slide_files(
                project_path, slide_num, f"images/{img_name}", text
            )

            logger.debug(
                f"[Import] Appended page {page_num + 1}/{total_pages} "
                f"as slide {slide_num}"
            )

        logger.info(
            f"[Import] PDF appended: {total_pages} slide(s) to "
            f"'{os.path.basename(project_path)}'"
        )
        return total_pages

    except ImportError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        logger.error(
            f"[Import] Failed to append PDF "
            f"'{os.path.basename(pdf_path)}': {exc}",
            exc_info=True,
        )
        raise ValueError(f"Failed to append PDF: {exc}") from exc
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

def _extract_pptx_text(pptx_path: str) -> list[dict]:
    """Uses python-pptx to natively extract text and speaker notes."""
    from pptx import Presentation
    prs = Presentation(pptx_path)
    slides_data = []
    
    for slide in prs.slides:
        slide_text = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        slide_text.append(text)
                        
        notes_text = ""
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            notes_text = notes_frame.text.strip()
            
        slides_data.append({
            "body_text": "\n".join(slide_text),
            "notes_text": notes_text
        })
    return slides_data

def import_pptx_as_project(pptx_path: str, config: dict, project_name: str = None, on_exists: str = "error") -> str:
    """Converts PPTX to project, using python-pptx for text and LibreOffice for visuals."""
    try:
        from pptx import Presentation
    except ImportError:
        raise ImportError("python-pptx is required. Run: pip install python-pptx")

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ImportError(
            "LibreOffice is required to render PPTX visuals. "
            "Please install it and ensure 'soffice' is in your system PATH."
        )
        
    if not project_name:
        project_name = os.path.splitext(os.path.basename(pptx_path))[0]
        
    from config import AppConfig
    projects_root = AppConfig().get("projects_root", "Projects")
    project_path = os.path.join(projects_root, project_name)
    _resolve_on_exists(project_path, project_name, on_exists)
    os.makedirs(project_path, exist_ok=True)
    images_dir = os.path.join(project_path, "images")
    os.makedirs(images_dir, exist_ok=True)
    
    # Track 1: Text
    logger.info("[Import] Extracting text from PPTX via python-pptx...")
    pptx_data = _extract_pptx_text(pptx_path)
    
    # Track 2: Visuals
    temp_dir = os.path.join(project_path, "_temp_conversion")
    os.makedirs(temp_dir, exist_ok=True)
    
    cmd = [soffice, "--headless", "--convert-to", "pdf", "--outdir", temp_dir, pptx_path]
    logger.info("[Import] Converting PPTX to PDF via LibreOffice for visuals...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    if result.returncode != 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")
        
    pdf_files = list(Path(temp_dir).glob("*.pdf"))
    if not pdf_files:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise FileNotFoundError("LibreOffice did not produce a PDF file.")
        
    temp_pdf_path = str(pdf_files[0])
    
    import fitz
    dpi = config.get("pdf_import_dpi", 200)
    zoom = dpi / 72.0
    
    doc = fitz.open(temp_pdf_path)
    
    if len(doc) != len(pptx_data):
        logger.warning(
            f"[Import] Slide count mismatch! python-pptx found {len(pptx_data)} slides, "
            f"LibreOffice rendered {len(doc)} pages. Aligning by minimum."
        )
        
    for page_num in range(len(doc)):
        slide_num = page_num + 1
        page = doc.load_page(page_num)
        
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img_name = f"slide{slide_num}.png"
        img_path = os.path.join(images_dir, img_name)
        pix.save(img_path)
        
        text_content = ""
        if page_num < len(pptx_data):
            data = pptx_data[page_num]
            if data["notes_text"]:
                text_content = data["notes_text"]
            else:
                text_content = data["body_text"]
                
        _create_import_slide_files(project_path, slide_num, f"images/{img_name}", text_content)
        
    doc.close()
    shutil.rmtree(temp_dir, ignore_errors=True)

    # Write project manifest
    manifest_data = {
        "name": project_name,
        "source": "pptx",
        "source_file": os.path.basename(pptx_path),
        "slide_count": len(pptx_data),
        "status": "new",
        "created": _now_iso(),
    }
    save_project_manifest(project_path, manifest_data)
    update_library_manifest(project_path)

    logger.info(f"[Import] PPTX imported: {len(pptx_data)} slides -> {project_path}")
    return project_path

def import_pptx_append(pptx_path: str, project_path: str, config: dict) -> int:
    """Appends PPTX slides to an existing project."""
    # Not natively supported with perfect numbering in this simplified helper,
    # so we instantiate a temporary project and copy files over.
    temp_project_name = f"_temp_{os.path.basename(pptx_path)}"
    temp_project_path = import_pptx_as_project(pptx_path, config, project_name=temp_project_name)
    
    start_slide = get_next_slide_number(project_path)
    count = 0
    
    for f in sorted(os.listdir(temp_project_path), key=natural_sort_key):
        if f.startswith("slide") and f.endswith(".html"):
            match = re.match(r"slide(\d+)\.html", f)
            if match:
                old_num = int(match.group(1))
                new_num = start_slide + count
                
                # Copy HTML
                old_html = os.path.join(temp_project_path, f)
                new_html = os.path.join(project_path, f"slide{new_num}.html")
                # Update image path inside html
                with open(old_html, "r", encoding="utf-8") as fh:
                    content = fh.read()
                content = content.replace(f"images/slide{old_num}.png", f"images/slide{new_num}.png")
                with open(new_html, "w", encoding="utf-8") as fh:
                    fh.write(content)
                
                # Copy TXT
                old_txt = os.path.join(temp_project_path, f"slide{old_num}.txt")
                new_txt = os.path.join(project_path, f"slide{new_num}.txt")
                if os.path.exists(old_txt):
                    shutil.copy2(old_txt, new_txt)
                    
                # Copy Image
                old_img = os.path.join(temp_project_path, "images", f"slide{old_num}.png")
                new_img = os.path.join(project_path, "images", f"slide{new_num}.png")
                if os.path.exists(old_img):
                    shutil.copy2(old_img, new_img)
                    
                count += 1
                
    shutil.rmtree(temp_project_path, ignore_errors=True)
    logger.info(f"[Import] PPTX appended: {count} slides to {project_path}")
    return count

def import_file_as_project(file_path: str, config: dict, project_name: str = None, on_exists: str = "error") -> str:
    """Dispatcher to create a new project from a given file.

    Args:
        on_exists: "error"  – raise ValueError if project exists (default)
                   "overwrite" – delete existing project and re-create
                   "append"    – append slides to the existing project
    """
    ext = os.path.splitext(file_path)[1].lower()

    # For "append" mode, delegate to the append dispatcher directly
    if on_exists == "append":
        # We need a project_path to append to
        if not project_name:
            project_name = os.path.splitext(os.path.basename(file_path))[0]
        from config import AppConfig
        projects_root = AppConfig().get("projects_root", "Projects")
        if not os.path.isabs(projects_root):
            projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)
        project_path = os.path.join(projects_root, project_name)
        if not os.path.isdir(project_path):
            # Project doesn't exist yet — fall through to create it
            on_exists = "error"
        else:
            count = import_file_append(file_path, project_path, config)
            update_library_manifest(project_path)
            logger.info(
                f"[Import] Appended {count} slide(s) to '{project_name}'"
            )
            return project_path

    if ext == ".pdf":
        return import_pdf_as_project(file_path, config, project_name, on_exists=on_exists)
    elif ext == ".pptx":
        return import_pptx_as_project(file_path, config, project_name, on_exists=on_exists)
    elif ext == ".json":
        return import_json_speaker_script_as_project(file_path, config, project_name, on_exists=on_exists)
    elif ext in (".txt", ".csv"):
        # Basic text import
        if not project_name:
            project_name = os.path.splitext(os.path.basename(file_path))[0]
        from config import AppConfig
        projects_root = AppConfig().get("projects_root", "Projects")
        if not os.path.isabs(projects_root):
            projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)
        project_path = os.path.join(projects_root, project_name)
        _resolve_on_exists(project_path, project_name, on_exists)
        os.makedirs(project_path, exist_ok=True)

        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read().strip()

        create_slide_file(project_path, 1, get_default_slide_html(), text)
        save_project_manifest(project_path, {
            "name": project_name,
            "source": ext.lstrip("."),
            "source_file": os.path.basename(file_path),
            "slide_count": 1,
            "status": "new",
            "created": _now_iso(),
        })
        update_library_manifest(project_path)
        return project_path
    elif ext in (".png", ".jpg", ".jpeg"):
        if not project_name:
            project_name = os.path.splitext(os.path.basename(file_path))[0]
        from config import AppConfig
        projects_root = AppConfig().get("projects_root", "Projects")
        if not os.path.isabs(projects_root):
            projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)
        project_path = os.path.join(projects_root, project_name)
        _resolve_on_exists(project_path, project_name, on_exists)
        os.makedirs(project_path, exist_ok=True)
        import_image_as_slide(file_path, project_path, config)
        save_project_manifest(project_path, {
            "name": project_name,
            "source": "image",
            "source_file": os.path.basename(file_path),
            "slide_count": 1,
            "status": "new",
            "created": _now_iso(),
        })
        update_library_manifest(project_path)
        return project_path
    else:
        raise ValueError(f"Unsupported file type for import: {ext}")


def import_file_append(file_path: str, project_path: str, config: dict) -> int:
    """Dispatcher to append a file to an existing project."""
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".pdf":
        return import_pdf_append(file_path, project_path, config)
    elif ext == ".pptx":
        return import_pptx_append(file_path, project_path, config)  
    elif ext == ".json":
        return import_json_speaker_script_append(file_path, project_path, config)
    elif ext in (".txt", ".csv"):
        slide_num = get_next_slide_number(project_path)
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        create_slide_file(project_path, slide_num, get_default_slide_html(), text)
        return 1
    elif ext in (".png", ".jpg", ".jpeg"):
        return import_image_as_slide(file_path, project_path, config)
    else:
        raise ValueError(f"Unsupported file type for append: {ext}")

# ═══════════════════════════════════════════════════════════════════════════════
#  JSON SPEAKER SCRIPT IMPORT
# ═══════════════════════════════════════════════════════════════════════════════

def parse_speaker_script_json(file_path: str) -> dict:
    """Parse a JSON speaker script file.
    
    Handles multiple formats:
    
    Format 1 — Object with slides array:
    {
        "presentation": "Rules of Chess",
        "slides": [
            {"slide_number": 1, "title": "(untitled)", "speaker_script": "Welcome..."}
        ]
    }
    
    Format 2 — Flat array of slide objects or strings:
    [
        {"speaker_script": "Welcome..."},
        "Second slide narration text"
    ]
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both top-level array and top-level object
    if isinstance(data, list):
        # Flat array: each element is a slide
        slides_raw = data
        title = ""
    elif isinstance(data, dict):
        # 1. Extract presentation title (supports multiple key names)
        title = str(
            data.get("presentation")
            or data.get("title")
            or data.get("name")
            or ""
        ).strip()
        
        # 2. Extract slides array (supports multiple key names)
        slides_raw = (
            data.get("slides")
            or data.get("slide_data")
            or data.get("scenes")
            or []
        )
        if not isinstance(slides_raw, list):
            raise ValueError(
                "JSON 'slides' (or 'slide_data'/'scenes') must be an array"
            )
    else:
        raise ValueError(
            f"JSON root must be an object or array, "
            f"got {type(data).__name__}"
        )

    parsed_slides = []
    for i, entry in enumerate(slides_raw):
        # Handle string entries directly — each string is one slide's narration
        if isinstance(entry, str):
            script_text = entry.strip()
            if script_text:
                parsed_slides.append({
                    "slide_number": i + 1,
                    "content": script_text,
                })
            continue

        if not isinstance(entry, dict):
            continue
            
        # Specifically target "speaker_script", fallback to other common keys
        script_text = str(
            entry.get("speaker_script")
            or entry.get("content")
            or entry.get("text")
            or entry.get("narration")
            or entry.get("script")
            or ""
        ).strip()
        
        if script_text:
            parsed_slides.append({
                "slide_number": i + 1,
                "content": script_text,
            })

    if not parsed_slides:
        raise ValueError("No slide text found in the JSON file.")

    return {"title": title, "slides": parsed_slides}



def import_json_speaker_script_as_project(
    file_path: str, config: dict, project_name: str = None, on_exists: str = "error"
) -> str:
    """Import a JSON speaker script as a new project.

    Each entry in the JSON 'slides' array becomes one slide.  Slide
    numbers are assigned sequentially (1, 2, 3, ...) regardless of the
    'slide_number' values in the JSON, so there are never gaps.

    Args:
        on_exists: "error"  – raise ValueError if project exists (default)
                   "overwrite" – delete existing project and re-create
                   "append"    – append slides to the existing project
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"JSON file not found: {file_path}")

    parsed = parse_speaker_script_json(file_path)

    if not project_name:
        project_name = parsed["title"] or os.path.splitext(os.path.basename(file_path))[0]

    # Sanitize name
    project_name = re.sub(r'[\\/*?:"<>|]', "", project_name).strip()
    if not project_name:
        project_name = "Untitled_Script"

    projects_root = config.get("projects_root", "Projects")
    if not os.path.isabs(projects_root):
        from config import AppConfig
        projects_root = os.path.join(AppConfig.APP_ROOT, projects_root)
    project_path = os.path.join(projects_root, project_name)

    # Handle append mode separately — delegate to the append function
    if on_exists == "append" and os.path.isdir(project_path):
        count = import_json_speaker_script_append(file_path, project_path, config)
        logger.info(
            f"[JSON Import] Appended {count} slide(s) to existing project '{project_name}'"
        )
        return project_path

    _resolve_on_exists(project_path, project_name, on_exists)

    os.makedirs(project_path, exist_ok=True)

    # Use sequential slide numbering to avoid gaps
    for idx, slide_info in enumerate(parsed["slides"], start=1):
        script_text = slide_info["content"]

        html_path = os.path.join(project_path, f"slide{idx}.html")
        txt_path = os.path.join(project_path, f"slide{idx}.txt")

        # 1. Write ONLY the speaker_script text to the .txt file for TTS
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(script_text)

        # 2. Write HTML file (inject the text so the visual slide isn't blank)
        html_content = get_default_slide_html()
        # Escape HTML characters and convert newlines to <br> for the visual slide
        safe_text = script_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        html_content = html_content.replace("Slide Content", safe_text)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    manifest_data = {
        "name": project_name,
        "source": "json_speaker_script",
        "title": parsed["title"],
        "slide_count": len(parsed["slides"]),
        "status": "new",
        "created": _now_iso(),
    }
    save_project_manifest(project_path, manifest_data)
    update_library_manifest(project_path)

    logger.info(
        f"[JSON Import] Created project '{project_name}' with "
        f"{len(parsed['slides'])} slide(s) from speaker script"
    )
    return project_path

def import_json_speaker_script_append(
    file_path: str, project_path: str, config: dict
) -> int:
    """Append slides from a JSON speaker script to an existing project.

    Slides are appended sequentially starting from the next available
    slide number in the project, ignoring the 'slide_number' values
    from the JSON.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"JSON file not found: {file_path}")

    if not os.path.isdir(project_path):
        raise ValueError(f"Project directory not found: {project_path}")

    parsed = parse_speaker_script_json(file_path)
    next_slide = get_next_slide_number(project_path)

    count = 0
    for slide_info in parsed["slides"]:
        slide_num = next_slide + count
        script_text = slide_info["content"]  # The extracted speaker_script text
        
        html_path = os.path.join(project_path, f"slide{slide_num}.html")
        txt_path = os.path.join(project_path, f"slide{slide_num}.txt")
        
        # 1. Write ONLY the speaker_script text to the .txt file
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(script_text)
            
        # 2. Write HTML file
        html_content = get_default_slide_html()
        safe_text = script_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        html_content = html_content.replace("Slide Content", safe_text)
        
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        count += 1

    logger.info(
        f"[JSON Import] Appended {count} slide(s) from speaker script "
        f"to '{os.path.basename(project_path)}'"
    )
    return count