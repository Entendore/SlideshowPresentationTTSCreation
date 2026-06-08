# dialogs.py
import os
import sys
import re
import shutil
import tempfile
import logging
import html
import threading
import platform
import subprocess
import glob
import weakref

from PySide6.QtWidgets import (
    QWidget, QFormLayout, QSpinBox, QDoubleSpinBox, QFrame,
    QApplication, QComboBox, QPushButton, QHBoxLayout, QVBoxLayout,
    QSplitter, QMessageBox, QSizePolicy, QTextEdit, QGroupBox,
    QCheckBox, QLabel, QLineEdit, QFileDialog, QTabWidget,
    QPlainTextEdit, QStackedWidget, QListWidget, QListWidgetItem, QScrollArea,
    QInputDialog
)
from PySide6.QtCore import Qt, QTimer, QMetaObject, QSize, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QShortcut, QKeySequence, QFont, QColor, QPalette

# Local imports
from utils import (
    logger, get_theme, natural_sort_key,
    create_slide_file, get_default_slide_html, get_blank_slide_html
)
from config import AppConfig
import backends


# =================================================================
# ILLEGAL FILENAME CHARACTERS (cross-platform)
# =================================================================

_ILLEGAL_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_project_name(name: str) -> str:
    """Remove characters that are illegal in directory names on
    Windows, macOS, or Linux.  Also strips leading/trailing whitespace
    and dots (trailing dots are invalid on Windows)."""
    name = name.strip()
    name = _ILLEGAL_NAME_RE.sub('_', name)
    name = name.rstrip('.')
    return name


# =================================================================
# CUSTOM WIDGETS
# =================================================================

# =================================================================
# MODULAR SUB-COMPONENTS
# =================================================================

class StatusStrip(QFrame):
    """
    Left-edge colored strip indicating project status.
    Self-contained: accepts a color and renders itself.
    """
    STRIP_WIDTH = 5
    STRIP_RADIUS_TOP = "3px"
    STRIP_RADIUS_BOT = "3px"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.STRIP_WIDTH)
        self._color = "#555555"
        self._apply_style()

    def set_color(self, hex_color: str):
        self._color = hex_color
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            f"background-color: {self._color}; "
            f"border-top-left-radius: {self.STRIP_RADIUS_TOP}; "
            f"border-bottom-left-radius: {self.STRIP_RADIUS_BOT};"
        )


class StatusBadge(QLabel):
    """
    Compact status indicator badge (e.g. "DONE", "WAIT", "ERR").
    Self-contained: accepts label text, bg, text, and border colors.
    """
    BADGE_W = 52
    BADGE_H = 22
    BADGE_RADIUS = 11
    BADGE_FONT_SIZE = 10
    BADGE_FONT_WEIGHT = "bold"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(self.BADGE_W, self.BADGE_H)
        self._bg = "#444444"
        self._fg = "#999999"
        self._border = "#555555"
        self._apply_style()

    def set_appearance(self, text: str, bg: str, fg: str, border: str):
        self.setText(text)
        self._bg = bg
        self._fg = fg
        self._border = border
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {self._bg};
                color: {self._fg};
                border-radius: {self.BADGE_RADIUS}px;
                font-size: {self.BADGE_FONT_SIZE}px;
                font-weight: {self.BADGE_FONT_WEIGHT};
                border: 1px solid {self._border};
            }}
        """)


class MetaLabel(QLabel):
    """
    Two-line content block: title (bold, larger) + detail (muted, smaller).
    Self-contained with its own layout.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setStyleSheet("background: transparent;")

    def set_title(self, text: str, color: str = "#e0e0e0",
                  size: int = 14, weight: str = "bold"):
        self.setText(text)
        self.setStyleSheet(
            f"color: {color}; font-size: {size}px; "
            f"font-weight: {weight}; background: transparent;"
        )


class DetailRow(QWidget):
    """
    Row below the title showing status detail and the badge.
    [detail_text ─────── stretch ─────── BADGE]
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.detail = QLabel()
        self.detail.setStyleSheet("background: transparent;")
        self.detail.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Preferred)

        self.badge = StatusBadge()

        layout.addWidget(self.detail, stretch=1)
        layout.addWidget(self.badge)

    def set_detail(self, text: str, color: str = "#888888",
                   size: int = 11, weight: str = "normal"):
        self.detail.setText(text)
        self.detail.setStyleSheet(
            f"color: {color}; font-size: {size}px; "
            f"font-weight: {weight}; background: transparent;"
        )

    def set_badge(self, text: str, bg: str, fg: str, border: str):
        self.badge.set_appearance(text, bg, fg, border)


# =================================================================
# STATUS THEME RESOLVER (Centralized Status → Color Mapping)
# =================================================================

class StatusTheme:
    """
    Single source of truth for status → color mapping.
    All visual components query this instead of hardcoding colors.
    """
    # Strip colors
    STRIP = {
        "default":  "#555555",
        "unknown":  "#666666",
        "queued":   "#f0ad4e",
        "rendering":"#0078d4",
        "ready":    "#28a745",
        "error":    "#dc3545",
    }

    # Badge colors: (bg, text, border)
    BADGE = {
        "default":  ("#444444", "#999999", "#555555"),
        "unknown":  ("#3a3a3a", "#888888", "#4a4a4a"),
        "queued":   ("#f0ad4e", "#1a1a1a", "#d99a3e"),
        "rendering":("#0078d4", "#ffffff", "#006abc"),
        "ready":    ("#28a745", "#ffffff", "#1e7e34"),
        "error":    ("#dc3545", "#ffffff", "#bd2130"),
    }

    # Badge labels
    LABEL = {
        "default":  "NEW",
        "unknown":  "TODO",
        "queued":   "WAIT",
        "rendering":"...",
        "ready":    "DONE",
        "error":    "ERR",
    }

    # Detail text defaults
    DETAIL_COLOR = {
        "default":  "#888888",
        "unknown":  "#888888",
        "queued":   "#c8a460",
        "rendering":"#66a3d2",
        "ready":    "#6abf7b",
        "error":    "#e87878",
    }

    @classmethod
    def strip_color(cls, status: str) -> str:
        return cls.STRIP.get(status, cls.STRIP["default"])

    @classmethod
    def badge_colors(cls, status: str) -> tuple:
        return cls.BADGE.get(status, cls.BADGE["default"])

    @classmethod
    def badge_label(cls, status: str) -> str:
        return cls.LABEL.get(status, cls.LABEL["default"])

    @classmethod
    def detail_color(cls, status: str) -> str:
        return cls.DETAIL_COLOR.get(status, cls.DETAIL_COLOR["default"])


# =================================================================
# SELECTION STATE (Decoupled from visual)
# =================================================================

class SelectionState:
    """
    Tracks which items are selected in the list.
    Single-select by default; can be extended for multi-select.
    """
    def __init__(self):
        self._selected_path: str = ""

    @property
    def selected_path(self) -> str:
        return self._selected_path

    def select(self, path: str):
        self._selected_path = path

    def deselect_all(self):
        self._selected_path = ""

    def is_selected(self, path: str) -> bool:
        return self._selected_path == path


# =================================================================
# PROJECT LIST ITEM WIDGET (Modular Composition)
# =================================================================

class ProjectListItemWidget(QWidget):
    """
    A single project row in the library list.

    Architecture:
        ┌────┬──────────────────────────────┐
        │    │  Title                        │
        │ S  │  Detail text ─────── [BADGE] │
        │    │                               │
        └────┴──────────────────────────────┘
         Strip   Content (MetaLabel + DetailRow)

    Selection is self-managed via set_selected() — the widget
    updates its own background/border without external help.
    """

    ITEM_HEIGHT = 62
    ITEM_RADIUS = 6
    ITEM_MARGIN = "2px 4px 2px 4px"

    # Background states
    BG_NORMAL    = "#2d2d2d"
    BG_HOVER     = "#353535"
    BG_SELECTED  = "#1f3a5f"
    BG_SEL_HOVER = "#264a73"

    # Border states
    BORDER_NORMAL   = "1px solid #3a3a3a"
    BORDER_SELECTED = "1px solid #0078d4"

    # Title color states
    TITLE_NORMAL   = "#e0e0e0"
    TITLE_SELECTED = "#ffffff"

    def __init__(self, name: str, path: str,
                 selection_state: SelectionState, parent=None):
        super().__init__(parent)
        self.name = name
        self.path = path
        self._sel_state = selection_state
        self._hovered = False
        self._current_status = "unknown"
        self._progress_pct = 0
        self._detail_text = "Not Rendered"

        self.setObjectName("projectItem")
        self.setFixedHeight(self.ITEM_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._build_ui()
        self._apply_visual_state()

    # ── Construction ─────────────────────────────────────────────

    def _build_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Strip
        self.strip = StatusStrip()
        outer.addWidget(self.strip)

        # Content
        content = QVBoxLayout()
        content.setContentsMargins(12, 8, 12, 8)
        content.setSpacing(4)

        self.title_label = MetaLabel()
        self.title_label.set_title(self.name)

        self.detail_row = DetailRow()
        self.detail_row.set_detail("Not Rendered")
        self.detail_row.set_badge("TODO", "#3a3a3a", "#888888", "#4a4a4a")

        content.addWidget(self.title_label)
        content.addWidget(self.detail_row)

        outer.addLayout(content, stretch=1)

    # ── Public API ───────────────────────────────────────────────

    def set_selected(self, selected: bool):
        """Set selection state and update visuals immediately."""
        if selected:
            self._sel_state.select(self.path)
        self._apply_visual_state()

    def is_selected(self) -> bool:
        return self._sel_state.is_selected(self.path)

    def update_status(self, status: str, progress_pct: int = 0,
                      detail_text: str = ""):
        """Update the project's rendering status and refresh all sub-components."""
        self._current_status = status
        self._progress_pct = progress_pct
        self._detail_text = detail_text or self._default_detail(status)

        # Strip
        self.strip.set_color(StatusTheme.strip_color(status))

        # Badge
        bg, fg, border = StatusTheme.badge_colors(status)
        label = self._badge_label(status, progress_pct)
        self.detail_row.set_badge(label, bg, fg, border)

        # Detail text
        det_color = StatusTheme.detail_color(status)
        self.detail_row.set_detail(self._detail_text, color=det_color)

    def current_status(self) -> str:
        return self._current_status

    # ── Visual State Engine ──────────────────────────────────────

    def _apply_visual_state(self):
        """Compute and apply background, border, and title color
        based on (selected, hovered) combination."""
        sel = self.is_selected()
        hov = self._hovered

        if sel and hov:
            bg = self.BG_SEL_HOVER
        elif sel:
            bg = self.BG_SELECTED
        elif hov:
            bg = self.BG_HOVER
        else:
            bg = self.BG_NORMAL

        border = self.BORDER_SELECTED if sel else self.BORDER_NORMAL
        title_color = self.TITLE_SELECTED if sel else self.TITLE_NORMAL

        self.setStyleSheet(f"""
            #projectItem {{
                background-color: {bg};
                border: {border};
                border-radius: {self.ITEM_RADIUS}px;
                margin: {self.ITEM_MARGIN};
            }}
        """)

        self.title_label.set_title(self.name, color=title_color)

    # ── Mouse Events ─────────────────────────────────────────────

    def enterEvent(self, event):
        self._hovered = True
        self._apply_visual_state()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_visual_state()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """Click self-selects and also notifies the QListWidget."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(True)
            list_widget = self._find_parent_list()
            if list_widget:
                for i in range(list_widget.count()):
                    item = list_widget.item(i)
                    w = list_widget.itemWidget(item)
                    if w is self:
                        list_widget.setCurrentItem(item)
                        break
        super().mousePressEvent(event)

    def _find_parent_list(self) -> QListWidget | None:
        """Walk up the widget hierarchy to find the parent QListWidget."""
        p = self.parent()
        while p is not None:
            if isinstance(p, QListWidget):
                return p
            p = p.parent()
        return None

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _default_detail(status: str) -> str:
        defaults = {
            "unknown": "Not Rendered",
            "queued": "Waiting in queue...",
            "rendering": "Processing...",
            "ready": "Completed",
            "error": "Failed — Check Logs",
        }
        return defaults.get(status, "Not Rendered")

    @staticmethod
    def _badge_label(status: str, pct: int) -> str:
        if status == "rendering" and pct > 0:
            return f"{pct}%"
        return StatusTheme.badge_label(status)


# =================================================================
# SELECTION MANAGER (Decoupled from MainWindow)
# =================================================================

class ProjectSelectionManager:
    """
    Manages selection across all ProjectListItemWidget instances
    in a QListWidget. Replaces the manual loops in MainWindow.

    Usage:
        manager = ProjectSelectionManager(list_widget)
        manager.refresh()          # sync from QListWidget selection
        manager.select_path(path)  # programmatic selection
    """

    def __init__(self, list_widget: QListWidget):
        self._list = list_widget
        self._state = SelectionState()

    @property
    def state(self) -> SelectionState:
        return self._state

    def refresh(self):
        """Sync selection state from the QListWidget's current selection."""
        selected_items = self._list.selectedItems()
        if selected_items:
            path = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self._state.select(path)
        else:
            self._state.deselect_all()
        self._update_all_widgets()

    def select_path(self, path: str):
        """Programmatically select the item with the given path."""
        self._state.select(path)
        self._update_all_widgets()

        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                self._list.setCurrentItem(item)
                break

    def selected_path(self) -> str:
        return self._state.selected_path

    def _update_all_widgets(self):
        """Push current selection state to every item widget."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if isinstance(widget, ProjectListItemWidget):
                widget._apply_visual_state()


class QTextEditLogger(logging.Handler):
    """Custom logging handler that writes to a QTextEdit.

    Uses a weak reference to the QTextEdit so that if the widget is
    destroyed by Qt before the handler is removed, we won't crash
    trying to call invokeMethod on a dangling C++ pointer.
    """
    def __init__(self, text_edit):
        super().__init__()
        self._text_edit_ref = weakref.ref(text_edit)
        self.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'
        ))

    def emit(self, record):
        try:
            text_edit = self._text_edit_ref()
            if text_edit is None:
                return
            msg = self.format(record)
            msg = html.escape(msg)
            msg = msg.replace("\n", "<br>")
            color = "#d4d4d4"
            if record.levelno >= logging.ERROR:
                color = "#ff6b6b"
            elif record.levelno >= logging.WARNING:
                color = "#f1c40f"
            elif record.levelname == "INFO":
                color = "#3498db"
            html_msg = f'<span style="color:{color}">{msg}</span>'

            QMetaObject.invokeMethod(
                text_edit, "append",
                Qt.ConnectionType.QueuedConnection, html_msg
            )
        except Exception:
            self.handleError(record)


# =================================================================
# SETTINGS TAB WIDGET
# =================================================================

class SettingsTabWidget(QWidget):
    """Widget to be embedded in a QTabWidget for settings."""

    log_signal = Signal(str)
    settings_changed = Signal(str, object)  # (key, value)

    AUTO_SAVE_DELAY_MS = 600

    # Resolution presets: (display_name, landscape_width, landscape_height)
    RESOLUTION_PRESETS = [
        ("4K UHD (2160p)",  3840, 2160),
        ("1440p QHD",       2560, 1440),
        ("1080p FHD",       1920, 1080),
        ("720p HD",         1280, 720),
        ("540p qHD",        960,  540),
        ("480p SD",         854,  480),
        ("360p",            640,  360),
    ]

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

        # Debounce timer for settings that change rapidly
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(self.AUTO_SAVE_DELAY_MS)
        self._settings_timer.timeout.connect(self._flush_pending_settings)
        self._pending_settings: dict = {}

        self.backend_descriptions = backends.get_backend_descriptions()

        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search settings...")
        self.search_bar.setStyleSheet(
            "padding: 5px; background: #333; color: white; "
            "border-radius: 0px; border-bottom: 1px solid #444;"
        )
        self.search_bar.textChanged.connect(self.filter_sidebar)
        main_layout.addWidget(self.search_bar)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT: SIDEBAR ---
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("""
            QListWidget { background-color: #252526; border: none; font-size: 14px; }
            QListWidget::item { padding: 12px; color: #cccccc; border-bottom: 1px solid #333; }
            QListWidget::item:selected { background-color: #007acc; color: white; }
            QListWidget::item:hover { background-color: #2d2d30; }
        """)

        self.sidebar.addItem("General & Video")
        self.sidebar.addItem("Backend Settings")
        self.sidebar.addItem("Hardware & Performance")
        self.sidebar.addItem("Text Chunking")
        self.sidebar.addItem("Paths & Cache")
        self.sidebar.addItem("Diagnostics")

        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self.change_page)
        splitter.addWidget(self.sidebar)

        # --- RIGHT: CONTENT STACK ---
        self.stack = QStackedWidget()
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 4)

        # Build Pages
        self.page_general = self._create_general_page()
        self.page_backend_settings = self._create_backend_settings_page()
        self.page_hardware = self._create_hardware_page()
        self.page_chunking = self._create_chunking_page()
        self.page_paths = self._create_paths_page()
        self.page_diagnostics = self._create_diagnostics_page()

        self.stack.addWidget(self.page_general)
        self.stack.addWidget(self.page_backend_settings)
        self.stack.addWidget(self.page_hardware)
        self.stack.addWidget(self.page_chunking)
        self.stack.addWidget(self.page_paths)
        self.stack.addWidget(self.page_diagnostics)

        # Ctrl+S shortcut for settings
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._flush_pending_settings)

        # Initialize — block signals so programmatic setValue/setText
        # calls don't cascade through _debounce_set during init.
        self.blockSignals(True)
        try:
            self.refresh_ui_values()
            self.refresh_backend_settings_ui()
        finally:
            self.blockSignals(False)

    # ==========================================================
    # DEBOUNCED SETTINGS HELPER
    # ==========================================================
    def _debounce_set(self, key, value):
        """
        Queue a settings change.  Skips work entirely when the new
        value is identical to the current in-memory value, preventing
        spurious writes during refresh_ui_values() or widget init.
        """
        current = self.config.settings.get(key)
        if current == value and key not in self._pending_settings:
            return  # nothing to do

        self._pending_settings[key] = value
        # Apply to in-memory settings immediately so reads are consistent
        self.config.settings[key] = value
        self._settings_timer.start()  # restart the debounce window
        self.settings_changed.emit(key, value)

    def _flush_pending_settings(self):
        """Write all pending settings to config (and disk)."""
        if not self._pending_settings:
            return
        for key, value in self._pending_settings.items():
            self.config.set(key, value)
        self._pending_settings.clear()
        logger.debug("[Settings] Flushed pending settings to disk.")

    # ==========================================================
    # RESOLUTION HELPERS
    # ==========================================================
    def _apply_resolution_to_config(self):
        """Read current combo values and write width/height to config."""
        data = self.resolution_combo.currentData()
        orientation = self.orientation_combo.currentData()
        if data:
            base_w, base_h = data
            if orientation == "portrait":
                w, h = base_h, base_w
            else:
                w, h = base_w, base_h
            self._debounce_set('width', w)
            self._debounce_set('height', h)
        self._update_resolution_preview()

    def _update_resolution_preview(self):
        """Update the small label showing the actual pixel dimensions."""
        data = self.resolution_combo.currentData()
        orientation = self.orientation_combo.currentData()
        if data:
            base_w, base_h = data
            if orientation == "portrait":
                w, h = base_h, base_w
            else:
                w, h = base_w, base_h
            self.resolution_preview.setText(f"Output: {w}x{h} pixels")
        else:
            self.resolution_preview.setText("")

    def _sync_resolution_combos(self):
        """Set resolution and orientation combos from current config width/height."""
        w = self.config.get('width', 1280)
        h = self.config.get('height', 720)

        # Determine orientation
        if h > w:
            orientation = "portrait"
            base_w, base_h = h, w  # swap to landscape reference
        else:
            orientation = "landscape"
            base_w, base_h = w, h

        # Find matching preset
        for i in range(self.resolution_combo.count()):
            data = self.resolution_combo.itemData(i)
            if data and data[0] == base_w and data[1] == base_h:
                self.resolution_combo.blockSignals(True)
                self.resolution_combo.setCurrentIndex(i)
                self.resolution_combo.blockSignals(False)
                break

        # Set orientation
        orient_idx = self.orientation_combo.findData(orientation)
        if orient_idx >= 0:
            self.orientation_combo.blockSignals(True)
            self.orientation_combo.setCurrentIndex(orient_idx)
            self.orientation_combo.blockSignals(False)

        # Update preview label
        self._update_resolution_preview()

    # ==========================================================
    # NAVIGATION
    # ==========================================================
    def change_page(self, index):
        self.stack.setCurrentIndex(index)

    def filter_sidebar(self, text):
        search = text.lower()
        for i in range(self.sidebar.count()):
            item = self.sidebar.item(i)
            item.setHidden(search not in item.text().lower())

    def refresh_ui_values(self):
        """Reloads UI from config."""
        # General — Resolution
        self._sync_resolution_combos()
        self.fps_spin.setValue(self.config.get('fps', 30))
        self.enc_combo.setCurrentText(self.config.get('encoder', 'auto'))
        self.worker_spin.setValue(self.config.get('render_workers', 3))
        self.out_dir_edit.setText(self.config.get('output_dir', 'Output'))
        self.proj_root_edit.setText(self.config.get('projects_root', 'Projects'))
        self.trans_spin.setValue(self.config.get('transition_duration', 0.5))

        # Backend Settings
        current_backend = self.config.get('active_backend', 'qwen3')
        index = self.backend_combo.findData(current_backend)
        if index >= 0:
            self.backend_combo.setCurrentIndex(index)

        # Hardware — fixed fallback to match config default
        self.qwen3_device_combo.setCurrentText(
            self.config.get('qwen3_device_map', 'cuda:0'))
        self.qwen3_dtype_combo.setCurrentText(
            self.config.get('qwen3_dtype', 'float16'))
        self.chk_flash_attn.setChecked(
            self.config.get('qwen3_attn_implementation') == "flash_attention_2")
        self.qwen3_size_combo.setCurrentText(
            self.config.get('qwen3_size', '1.7B'))

        # Text Chunking
        self.chk_enable_chunking.setChecked(
            self.config.get('enable_text_chunking', True))
        self.spin_chunk_max_chars.setValue(
            self.config.get('chunk_max_chars', 500))
        self.spin_chunk_max_sentences.setValue(
            self.config.get('chunk_max_sentences', 5))
        self.spin_chunk_min_chars.setValue(
            self.config.get('chunk_min_chars', 50))
        self.spin_warn_threshold.setValue(
            self.config.get('chunk_warn_threshold', 1000))

    # ==========================================================
    # PAGE: GENERAL & VIDEO
    # ==========================================================
    def _create_general_page(self):
        page = QWidget()
        layout = QFormLayout(page)

        # Create preview label BEFORE populating combos so that
        # currentIndexChanged signals fired during addItem can safely
        # reference self.resolution_preview.
        self.resolution_preview = QLabel("")
        self.resolution_preview.setStyleSheet(
            "color: #888888; font-size: 11px; padding-left: 2px;")

        res_layout = QHBoxLayout()
        self.resolution_combo = QComboBox()
        self.resolution_combo.blockSignals(True)
        for name, w, h in SettingsTabWidget.RESOLUTION_PRESETS:
            self.resolution_combo.addItem(name, (w, h))
        self.resolution_combo.blockSignals(False)

        self.orientation_combo = QComboBox()
        self.orientation_combo.blockSignals(True)
        self.orientation_combo.addItem("Landscape (Horizontal)", "landscape")
        self.orientation_combo.addItem("Portrait (Vertical)", "portrait")
        self.orientation_combo.blockSignals(False)

        self.resolution_combo.currentIndexChanged.connect(
            self._apply_resolution_to_config)
        self.orientation_combo.currentIndexChanged.connect(
            self._apply_resolution_to_config)

        res_layout.addWidget(self.resolution_combo, stretch=3)
        res_layout.addWidget(self.orientation_combo, stretch=2)
        layout.addRow("Resolution:", res_layout)
        layout.addRow("", self.resolution_preview)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 60)
        self.fps_spin.valueChanged.connect(
            lambda v: self._debounce_set('fps', v))
        layout.addRow("FPS:", self.fps_spin)

        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["auto", "libx264", "h264_nvenc"])
        self.enc_combo.currentIndexChanged.connect(
            lambda i: self._debounce_set('encoder', self.enc_combo.itemText(i))
        )
        layout.addRow("Encoder:", self.enc_combo)

        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(1, 8)
        self.worker_spin.valueChanged.connect(
            lambda v: self._debounce_set('render_workers', v))
        layout.addRow("Render Workers:", self.worker_spin)

        # Output Directory
        out_layout = QHBoxLayout()
        self.out_dir_edit = QLineEdit()
        self.out_dir_edit.setText(self.config.get('output_dir', 'Output'))
        self.out_dir_edit.editingFinished.connect(
            lambda: self._debounce_set('output_dir', self.out_dir_edit.text())
        )
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse_output_dir)
        out_layout.addWidget(self.out_dir_edit)
        out_layout.addWidget(btn_browse)
        layout.addRow("Output Dir:", out_layout)

        # Projects Root — now passes config_key for reliable persistence
        proj_layout = QHBoxLayout()
        self.proj_root_edit = QLineEdit()
        self.proj_root_edit.setText(self.config.get('projects_root', 'Projects'))
        self.proj_root_edit.editingFinished.connect(
            lambda: self._debounce_set('projects_root',
                                       self.proj_root_edit.text())
        )
        btn_browse_proj = QPushButton("Browse...")
        btn_browse_proj.clicked.connect(
            lambda: self.browse_generic(
                self.proj_root_edit, True, config_key='projects_root')
        )
        proj_layout.addWidget(self.proj_root_edit)
        proj_layout.addWidget(btn_browse_proj)
        layout.addRow("Projects Root:", proj_layout)

        self.trans_spin = QDoubleSpinBox()
        self.trans_spin.setRange(0.0, 10.0)
        self.trans_spin.setSingleStep(0.1)
        self.trans_spin.setSuffix(" s")
        self.trans_spin.valueChanged.connect(
            lambda v: self._debounce_set('transition_duration', v))
        layout.addRow("Transition Duration:", self.trans_spin)

        return page

    # ==========================================================
    # PAGE: BACKEND SETTINGS
    # ==========================================================
    def _create_backend_settings_page(self):
        page = QWidget()
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 1. Header & Selector
        header_layout = QVBoxLayout()
        title = QLabel("<h2>Backend Configuration</h2>")
        header_layout.addWidget(title)

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("<b>Active Engine:</b>"))

        self.backend_combo = QComboBox()
        for name in backends.BACKEND_MAP.keys():
            display_name = name.replace("_", " ").title()
            self.backend_combo.addItem(display_name, name)

        self.backend_combo.currentIndexChanged.connect(
            self.on_backend_selection_changed)
        selector_layout.addWidget(self.backend_combo, 1)

        # Mode Selector
        self.mode_label = QLabel("Mode:")
        self.mode_combo = QComboBox()
        self.mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        selector_layout.addWidget(self.mode_label)
        selector_layout.addWidget(self.mode_combo)

        selector_layout.addStretch()
        header_layout.addLayout(selector_layout)

        # Description Area
        self.backend_desc_label = QLabel("Select a backend to view details.")
        self.backend_desc_label.setWordWrap(True)
        header_layout.addWidget(self.backend_desc_label)

        main_layout.addLayout(header_layout)

        # 2. Scroll Area for Content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Group: Backend Parameters
        self.backend_params_group = QGroupBox("Backend Specific Parameters")
        self.backend_params_layout = QVBoxLayout()
        self.backend_params_group.setLayout(self.backend_params_layout)
        self.scroll_layout.addWidget(self.backend_params_group)

        # Group: Diagnostics & Testing
        diag_group = QGroupBox("Diagnostics & Testing")
        diag_layout = QVBoxLayout()
        diag_layout.addWidget(
            QLabel("Run backend diagnostics from the Diagnostics sidebar page."))
        diag_group.setLayout(diag_layout)
        self.scroll_layout.addWidget(diag_group)

        self.scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

        return page

    def on_backend_selection_changed(self, index):
        backend_name = self.backend_combo.currentData()
        self._debounce_set('active_backend', backend_name)
        self.refresh_backend_settings_ui()

    def on_mode_changed(self, index):
        backend_name = self.config.get('active_backend', 'qwen3')
        mode_key = self.mode_combo.currentData()
        self._debounce_set(f"{backend_name}_mode", mode_key)
        self.load_backend_widget()

    def refresh_backend_settings_ui(self):
        backend_name = self.config.get('active_backend', 'qwen3')

        desc = self.backend_descriptions.get(
            backend_name, self.backend_descriptions.get("default"))
        self.backend_desc_label.setText(
            f"<b>{backend_name.upper()}</b>: {desc}")

        backend_class = backends.BACKEND_MAP.get(backend_name)
        has_modes = False

        if backend_class and hasattr(backend_class, 'get_ui_options'):
            options = backend_class.get_ui_options()
            modes = options.get("modes", [])

            if modes:
                has_modes = True
                self.mode_combo.blockSignals(True)
                self.mode_combo.clear()
                for key, display_name in modes:
                    self.mode_combo.addItem(display_name, key)

                current_mode = self.config.get(f"{backend_name}_mode")
                idx = self.mode_combo.findData(current_mode)
                if idx >= 0:
                    self.mode_combo.setCurrentIndex(idx)
                self.mode_combo.blockSignals(False)

        self.mode_label.setVisible(has_modes)
        self.mode_combo.setVisible(has_modes)

        self.load_backend_widget()

    def load_backend_widget(self):
        while self.backend_params_layout.count():
            item = self.backend_params_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        backend_name = self.config.get('active_backend', 'qwen3')

        try:
            backend_class = backends.BACKEND_MAP.get(backend_name)
            if backend_class:
                if hasattr(backend_class, 'get_settings_widget'):
                    if self.mode_combo.isVisible():
                        mode = self.mode_combo.currentData()
                    else:
                        mode = self.config.get(
                            f"{backend_name}_mode", "default")

                    backend_widget = backend_class.get_settings_widget(
                        mode, self.config, self,
                        save_callback=self._debounce_set
                    )
                    self.backend_params_layout.addWidget(backend_widget)
                else:
                    self.backend_params_layout.addWidget(
                        QLabel(
                            f"{backend_name} has no configurable parameters."))
            else:
                self.backend_params_layout.addWidget(
                    QLabel("Backend implementation not found."))
        except Exception as e:
            self.backend_params_layout.addWidget(
                QLabel(f"<span style='color:red'>Error loading UI: {e}</span>"))
            logger.error(f"Error loading backend settings: {e}")

    # ==========================================================
    # PAGE: HARDWARE
    # ==========================================================
    def _create_hardware_page(self):
        page = QWidget()
        layout = QFormLayout(page)

        dev_row = QHBoxLayout()
        self.qwen3_device_combo = QComboBox()
        self.qwen3_device_combo.addItem("cpu", "cpu")
        self.qwen3_device_combo.addItem("cuda:0", "cuda:0")
        self.qwen3_device_combo.addItem("cuda:1", "cuda:1")
        self.qwen3_device_combo.currentIndexChanged.connect(
            lambda i: self._debounce_set(
                'qwen3_device_map',
                self.qwen3_device_combo.itemData(i) or "cpu")
        )

        btn_detect = QPushButton("Auto-Detect GPU")
        btn_detect.clicked.connect(self.auto_detect_hardware)

        dev_row.addWidget(self.qwen3_device_combo)
        dev_row.addWidget(btn_detect)
        layout.addRow("Compute Device:", dev_row)

        self.qwen3_dtype_combo = QComboBox()
        self.qwen3_dtype_combo.addItems(["bfloat16", "float16"])
        self.qwen3_dtype_combo.currentTextChanged.connect(
            lambda t: self._debounce_set('qwen3_dtype', t))
        layout.addRow("Data Type:", self.qwen3_dtype_combo)

        self.chk_flash_attn = QCheckBox("Flash Attention 2")
        self.chk_flash_attn.setToolTip(
            "Faster, lower VRAM. Requires RTX 30/40 series.")
        self.chk_flash_attn.toggled.connect(
            lambda c: self._debounce_set(
                'qwen3_attn_implementation',
                "flash_attention_2" if c else "eager")
        )
        layout.addRow(self.chk_flash_attn)

        self.qwen3_size_combo = QComboBox()
        self.qwen3_size_combo.addItems(["1.7B", "0.6B"])
        self.qwen3_size_combo.setCurrentText(
            self.config.get('qwen3_size', '1.7B'))
        self.qwen3_size_combo.currentTextChanged.connect(
            lambda t: self._debounce_set('qwen3_size', t))
        layout.addRow("Model Size:", self.qwen3_size_combo)

        return page

    def auto_detect_hardware(self):
        """Auto-detect GPU hardware. Block signals on the device combo
        during repopulation to prevent spurious config writes to "cpu"
        before we settle on the correct CUDA device."""
        try:
            import torch
            if torch.cuda.is_available():
                count = torch.cuda.device_count()
                # Block signals while repopulating to avoid writing
                # intermediate "cpu" state to config
                self.qwen3_device_combo.blockSignals(True)
                self.qwen3_device_combo.clear()
                self.qwen3_device_combo.addItem("cpu", "cpu")
                for i in range(count):
                    name = torch.cuda.get_device_name(i)
                    self.qwen3_device_combo.addItem(
                        f"cuda:{i} ({name})", f"cuda:{i}")
                self.qwen3_device_combo.setCurrentIndex(1)
                self.qwen3_device_combo.blockSignals(False)

                # Now explicitly persist the chosen device
                selected = self.qwen3_device_combo.currentData() or "cuda:0"
                self._debounce_set('qwen3_device_map', selected)

                QMessageBox.information(
                    self, "Success", f"Found {count} GPU(s).")
            else:
                QMessageBox.warning(
                    self, "No GPU", "No CUDA-compatible GPU found.")
                self.qwen3_device_combo.setCurrentText("cpu")
        except ImportError:
            QMessageBox.critical(self, "Error", "PyTorch not installed.")

    # ==========================================================
    # PAGE: TEXT CHUNKING (Long Text Handling)
    # ==========================================================
    def _create_chunking_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        info_group = QGroupBox("Long Text Handling")
        info_layout = QVBoxLayout()
        info_label = QLabel(
            "<b>Automatic Text Chunking for TTS</b><br><br>"
            "When enabled, long text inputs are automatically split into "
            "smaller chunks that the TTS model can process reliably. Each "
            "chunk is generated separately and then concatenated to produce "
            "the final audio output.<br><br>"
            "<i>This prevents audio generation failures for long paragraphs "
            "and ensures consistent voice quality across chunked segments.</i>"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #cccccc; padding: 10px;")
        info_layout.addWidget(info_label)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        settings_group = QGroupBox("Chunking Settings")
        form_layout = QFormLayout()

        self.chk_enable_chunking = QCheckBox("Enable Automatic Text Chunking")
        self.chk_enable_chunking.setToolTip(
            "When disabled, long texts may cause TTS failures")
        self.chk_enable_chunking.setChecked(True)
        self.chk_enable_chunking.toggled.connect(
            lambda c: self._debounce_set('enable_text_chunking', c))
        form_layout.addRow(self.chk_enable_chunking)

        self.spin_chunk_max_chars = QSpinBox()
        self.spin_chunk_max_chars.setRange(100, 2000)
        self.spin_chunk_max_chars.setSingleStep(50)
        self.spin_chunk_max_chars.setSuffix(" chars")
        self.spin_chunk_max_chars.setToolTip(
            "Maximum characters per audio chunk.")
        self.spin_chunk_max_chars.valueChanged.connect(
            lambda v: self._debounce_set('chunk_max_chars', v))
        form_layout.addRow("Max Chars per Chunk:", self.spin_chunk_max_chars)

        self.spin_chunk_max_sentences = QSpinBox()
        self.spin_chunk_max_sentences.setRange(1, 10)
        self.spin_chunk_max_sentences.setToolTip(
            "Maximum sentences per chunk.")
        self.spin_chunk_max_sentences.valueChanged.connect(
            lambda v: self._debounce_set('chunk_max_sentences', v))
        form_layout.addRow("Max Sentences per Chunk:",
                           self.spin_chunk_max_sentences)

        self.spin_chunk_min_chars = QSpinBox()
        self.spin_chunk_min_chars.setRange(10, 200)
        self.spin_chunk_min_chars.setSingleStep(10)
        self.spin_chunk_min_chars.setSuffix(" chars")
        self.spin_chunk_min_chars.setToolTip(
            "Minimum characters to form a valid chunk.")
        self.spin_chunk_min_chars.valueChanged.connect(
            lambda v: self._debounce_set('chunk_min_chars', v))
        form_layout.addRow("Min Chunk Size:", self.spin_chunk_min_chars)

        self.spin_warn_threshold = QSpinBox()
        self.spin_warn_threshold.setRange(500, 5000)
        self.spin_warn_threshold.setSingleStep(100)
        self.spin_warn_threshold.setSuffix(" chars")
        self.spin_warn_threshold.setToolTip(
            "Log a warning when text exceeds this length")
        self.spin_warn_threshold.valueChanged.connect(
            lambda v: self._debounce_set('chunk_warn_threshold', v))
        form_layout.addRow("Warning Threshold:", self.spin_warn_threshold)

        settings_group.setLayout(form_layout)
        layout.addWidget(settings_group)

        impact_group = QGroupBox("Estimated Impact")
        impact_layout = QVBoxLayout()
        impact_label = QLabel(
            "<b>Recommended Settings by Use Case:</b><br><br>"
            "• <b>Short slides (~200 chars):</b> Chunking rarely needed, "
            "defaults work fine<br>"
            "• <b>Medium slides (~500 chars):</b> Default settings "
            "(500 chars/chunk) recommended<br>"
            "• <b>Long narratives (~1000+ chars):</b> Consider 400-500 "
            "chars/chunk for best quality<br>"
            "• <b>Very long text (~2000+ chars):</b> Reduce to 300-400 "
            "chars if quality issues occur<br><br>"
            "<i>Note: Smaller chunks = more API calls but more reliable "
            "output. Larger chunks = fewer calls but may hit model limits.</i>"
        )
        impact_label.setWordWrap(True)
        impact_label.setStyleSheet("color: #aaaaaa; padding: 10px;")
        impact_layout.addWidget(impact_label)
        impact_group.setLayout(impact_layout)
        layout.addWidget(impact_group)

        layout.addStretch()
        return page

    # ==========================================================
    # PAGE: PATHS & CACHE
    # ==========================================================
    def _create_paths_page(self):
        page = QWidget()
        layout = QFormLayout(page)

        def make_validated_row(label, key, is_dir=True):
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setText(self.config.get(key, ""))

            def check_path():
                text = edit.text()
                self._debounce_set(key, text)

                exists = (os.path.isdir(text) if is_dir
                          else os.path.exists(text))
                p = edit.palette()
                if exists:
                    p.setColor(QPalette.ColorRole.Base, QColor("#1e2f23"))
                    p.setColor(QPalette.ColorRole.Text, QColor("#a5d6a7"))
                else:
                    p.setColor(QPalette.ColorRole.Base, QColor("#3d2220"))
                    p.setColor(QPalette.ColorRole.Text, QColor("#ef9a9a"))
                edit.setPalette(p)

            edit.editingFinished.connect(check_path)
            check_path()

            btn = QPushButton("...")
            btn.setMaximumWidth(30)
            btn.clicked.connect(
                lambda: self.browse_generic(edit, is_dir, config_key=key))

            row.addWidget(QLabel(label))
            row.addWidget(edit)
            row.addWidget(btn)
            return row

        layout.addRow(make_validated_row("HF Model Cache:", "hf_cache_dir"))
        layout.addRow(make_validated_row("HF Datasets:", "hf_datasets_dir"))
        layout.addRow(
            make_validated_row("Voice References:", "voice_references_root"))
        layout.addRow(make_validated_row(
            "Voice Design Cache:", "instruction_folder_root"))

        self.chk_symlinks = QCheckBox("Use Symlinks (Linux/Mac only)")
        self.chk_symlinks.setChecked(
            self.config.get("hf_use_symlinks", False))
        self.chk_symlinks.toggled.connect(
            lambda c: self._debounce_set("hf_use_symlinks", c))
        layout.addRow(self.chk_symlinks)

        return page

    def browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.out_dir_edit.text())
        if d:
            self.out_dir_edit.setText(d)
            # Emit editingFinished so the persisted path is consistent
            # with the browse_generic pattern
            self.out_dir_edit.editingFinished.emit()

    def browse_generic(self, line_edit, is_dir, config_key=None):
        result = None
        if is_dir:
            result = QFileDialog.getExistingDirectory(
                self, "Select Directory", line_edit.text())
        else:
            result, _ = QFileDialog.getOpenFileName(
                self, "Select File", line_edit.text())

        if result:
            line_edit.setText(result)
            # setText() does NOT trigger editingFinished, so we must
            # save explicitly to avoid the "displayed but not persisted" bug.
            if config_key:
                self._debounce_set(config_key, result)
            # Also trigger the editingFinished handlers if any
            line_edit.editingFinished.emit()

    # ==========================================================
    # PAGE: DIAGNOSTICS
    # ==========================================================
    def _create_diagnostics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        info_text = QTextEdit()
        info_text.setReadOnly(True)

        try:
            import torch
            cuda_ver = torch.version.cuda or "N/A"
            gpu_name = (torch.cuda.get_device_name(0)
                        if torch.cuda.is_available() else "No GPU")
            torch_ver = torch.__version__
        except ImportError:
            cuda_ver = "N/A"
            gpu_name = "N/A"
            torch_ver = "Not Installed"

        report = f"""
    <h3>System Information</h3>
    <b>OS:</b> {platform.system()} {platform.release()}<br>
    <b>Python:</b> {sys.version.split()[0]}<br>
    <b>PyTorch:</b> {torch_ver}<br>
    <b>CUDA:</b> {cuda_ver}<br>
    <b>GPU:</b> {gpu_name}<br>
    <h3>Configuration</h3>
    <b>Active Backend:</b> {self.config.get('active_backend', 'qwen3')}<br>
    <b>Cache Dir:</b> {self.config.get('hf_cache_dir')}
        """
        info_text.setHtml(report)

        btn_copy = QPushButton("Copy Report to Clipboard")
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(info_text.toPlainText()))

        layout.addWidget(info_text)
        layout.addWidget(btn_copy)
        return page


# =================================================================
# NEW PROJECT TAB WIDGET
# =================================================================

class NewProjectTabWidget(QWidget):
    def __init__(self, config, on_project_created_callback, parent=None):
        super().__init__(parent)
        self.config = config
        self.on_project_created = on_project_created_callback
        self.init_ui()

    def init_ui(self):
        layout = QFormLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My Awesome Video")

        self.type_combo = QComboBox()
        self.type_combo.addItems(["Blank Slide", "Text Slide", "Image Slide"])
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)

        self.content_input = QLineEdit()
        self.content_input.setPlaceholderText("Enter text or select image...")
        self.content_input.setEnabled(False)  # Disabled by default for "Blank Slide"

        self.btn_browse = QPushButton("...")
        self.btn_browse.setMaximumWidth(40)
        self.btn_browse.clicked.connect(self.browse_content)
        self.btn_browse.setVisible(False)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.content_input)
        content_layout.addWidget(self.btn_browse)

        self.btn_create = QPushButton("Create New Project")
        self.btn_create.setStyleSheet(
            "background-color: #28a745; color: white; "
            "font-weight: bold; padding: 8px;")
        self.btn_create.clicked.connect(self.create_project)

        layout.addRow("Project Name:", self.name_input)
        layout.addRow("Initial Slide Type:", self.type_combo)
        layout.addRow("Content:", content_layout)
        layout.addRow(self.btn_create)
        layout.addRow(QLabel(
            "<i>Note: Project will be created and added to the library.</i>"))

    def on_type_changed(self, index):
        slide_type = self.type_combo.currentText()
        if slide_type == "Image Slide":
            self.content_input.setPlaceholderText("Path to image...")
            self.content_input.setEnabled(True)
            self.btn_browse.setVisible(True)
        elif slide_type == "Text Slide":
            self.content_input.setPlaceholderText("Enter text for slide...")
            self.content_input.setEnabled(True)
            self.btn_browse.setVisible(False)
        else:  # Blank Slide
            self.content_input.clear()
            self.content_input.setEnabled(False)
            self.btn_browse.setVisible(False)

    def browse_content(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "", "Images (*.png *.jpg *.jpeg)")
        if f:
            self.content_input.setText(f)

    def create_project(self):
        raw_name = self.name_input.text().strip()
        if not raw_name:
            QMessageBox.warning(
                self, "Input Error", "Please enter a project name.")
            return

        # Sanitize the name to remove illegal filesystem characters
        name = _sanitize_project_name(raw_name)
        if not name:
            QMessageBox.warning(
                self, "Input Error",
                "Project name contains only invalid characters.")
            return

        root_dir = self.config.get(
            'projects_root', os.path.join(os.getcwd(), "Projects"))
        if not os.path.exists(root_dir):
            try:
                os.makedirs(root_dir)
            except OSError as e:
                QMessageBox.critical(
                    self, "Error",
                    f"Could not create projects directory: {e}")
                return

        project_path = os.path.join(root_dir, name)
        if os.path.exists(project_path):
            QMessageBox.warning(
                self, "Exists",
                f"A project folder named '{name}' already exists.")
            return

        try:
            os.makedirs(project_path)

            slide_type = self.type_combo.currentText()
            content = self.content_input.text()

            html_path = os.path.join(project_path, "slide1.html")
            txt_path = os.path.join(project_path, "slide1.txt")

            if slide_type == "Text Slide":
                create_slide_file(html_path, get_default_slide_html(content))
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(content)
            elif slide_type == "Image Slide":
                if content and os.path.exists(content):
                    try:
                        from utils import get_image_slide_html
                        create_slide_file(
                            html_path, get_image_slide_html(content))
                    except ImportError:
                        create_slide_file(
                            html_path,
                            f"<html><body><img src='file:///{content}'></body></html>")

                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write("Image slide.")
                else:
                    create_slide_file(
                        html_path, get_blank_slide_html("#000000"))
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write("")
            elif slide_type == "Blank Slide":
                create_slide_file(
                    html_path, get_blank_slide_html("#000000"))
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write("")

            if self.on_project_created:
                self.on_project_created(project_path)

            self.name_input.clear()
            self.content_input.clear()
            self.name_input.setFocus()
            self.type_combo.setCurrentIndex(0)

            QMessageBox.information(
                self, "Success",
                f"Project '{name}' created successfully!")

        except Exception as e:
            logger.exception("Failed to create project")
            QMessageBox.critical(
                self, "Error", f"Failed to create project: {e}")


# =================================================================
# SLIDE LIST PANEL (Modular Sub-Component)
# =================================================================

class SlideListPanel(QWidget):
    """Left panel: slide navigation, ordering, and management controls."""

    slide_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QLabel("Slides")
        header.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("color: #ddd; padding: 4px;")
        layout.addWidget(header)

        self.slide_list = QListWidget()
        self.slide_list.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                color: #cccccc;
                border: 1px solid #333;
                border-radius: 4px;
                font-size: 13px;
                outline: none;
            }
            QListWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid #2a2a2a;
            }
            QListWidget::item:selected {
                background-color: #007acc;
                color: white;
            }
            QListWidget::item:hover {
                background-color: #2a2d2e;
            }
        """)
        self.slide_list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.slide_list, stretch=1)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(3)

        self.btn_add = QPushButton("+")
        self.btn_add.setToolTip("Add blank slide after current")
        self.btn_remove = QPushButton("−")
        self.btn_remove.setToolTip("Remove current slide")
        self.btn_duplicate = QPushButton("⧉")
        self.btn_duplicate.setToolTip("Duplicate current slide")
        self.btn_move_up = QPushButton("▲")
        self.btn_move_up.setToolTip("Move slide up")
        self.btn_move_down = QPushButton("▼")
        self.btn_move_down.setToolTip("Move slide down")

        for btn in [self.btn_add, self.btn_remove, self.btn_duplicate,
                     self.btn_move_up, self.btn_move_down]:
            btn.setFixedSize(34, 28)
            btn.setStyleSheet("""
                QPushButton {
                    background: #3c3c3c; color: #ddd; border: 1px solid #555;
                    border-radius: 3px; font-size: 14px; font-weight: bold;
                }
                QPushButton:hover { background: #505050; }
                QPushButton:pressed { background: #007acc; }
            """)
            btn_layout.addWidget(btn)

        layout.addLayout(btn_layout)

    def _on_row_changed(self, row):
        if row >= 0:
            self.slide_selected.emit(row)

    def load_slides(self, slide_files):
        self.slide_list.blockSignals(True)
        self.slide_list.clear()
        for i, path in enumerate(slide_files):
            item = QListWidgetItem(f"  Slide {i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setSizeHint(QSize(0, 38))
            self.slide_list.addItem(item)
        if slide_files:
            self.slide_list.setCurrentRow(0)
        self.slide_list.blockSignals(False)

    def current_index(self):
        return self.slide_list.currentRow()

    def set_current_index(self, index):
        if 0 <= index < self.slide_list.count():
            self.slide_list.setCurrentRow(index)

    def count(self):
        """Return the total number of slides in the list."""
        return self.slide_list.count()


# =================================================================
# SLIDE PREVIEW PANEL (Modular Sub-Component)
# =================================================================

class SlidePreviewPanel(QWidget):
    """
    Center panel: live HTML preview with zoom/scale controls.

    Uses QWebEngineView to render slides with full CSS support
    (Tailwind, Google Fonts, Material Icons, etc.).
    Scaling is applied via CSS zoom on the page body.
    """

    SLIDE_W = 1280
    SLIDE_H = 720
    PRESET_SCALES = ["Fit", "25%", "50%", "75%", "100%", "125%", "150%", "200%"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scale_percent = 100
        self._fit_mode = False
        self._base_dir = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Zoom Toolbar ---
        self._toolbar = QFrame()
        self._toolbar.setFixedHeight(40)
        self._toolbar.setObjectName("previewToolbar")
        self._toolbar.setStyleSheet("""
            #previewToolbar {
                background: #252526;
                border-bottom: 1px solid #3c3c3c;
            }
            QPushButton {
                background: #3c3c3c; color: #ddd; border: 1px solid #555;
                border-radius: 3px; padding: 3px 10px; font-size: 12px;
                min-height: 24px;
            }
            QPushButton:hover { background: #505050; }
            QPushButton:pressed { background: #007acc; }
            QComboBox {
                background: #3c3c3c; color: #ddd; border: 1px solid #555;
                border-radius: 3px; padding: 3px 8px; font-size: 12px;
                min-height: 24px; min-width: 75px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: #2d2d2d; color: #ddd;
                selection-background-color: #007acc; outline: none;
            }
            QComboBox QLineEdit {
                background: #3c3c3c; color: #ddd; border: none;
            }
            QLabel { color: #777; font-size: 11px; background: transparent; }
        """)

        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(10, 4, 10, 4)

        self.btn_fit = QPushButton("⊞ Fit to Screen")
        self.btn_fit.setToolTip("Scale slide to fit the preview area")
        self.btn_fit.clicked.connect(self.fit_to_screen)

        self.btn_zoom_out = QPushButton("-")
        self.btn_zoom_out.setFixedSize(28, 28)
        self.btn_zoom_out.setToolTip("Zoom out (-25%)")
        self.btn_zoom_out.clicked.connect(lambda: self._step_scale(-25))

        self.scale_combo = QComboBox()
        self.scale_combo.setEditable(True)
        self.scale_combo.setFixedWidth(85)
        for s in self.PRESET_SCALES:
            self.scale_combo.addItem(s)
        self.scale_combo.setCurrentIndex(4)  # "100%"
        self.scale_combo.currentTextChanged.connect(self._on_scale_text_changed)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedSize(28, 28)
        self.btn_zoom_in.setToolTip("Zoom in (+25%)")
        self.btn_zoom_in.clicked.connect(lambda: self._step_scale(25))

        self.lbl_info = QLabel(f"{self.SLIDE_W} x {self.SLIDE_H}")

        tb.addWidget(self.btn_fit)
        tb.addStretch()
        tb.addWidget(self.btn_zoom_out)
        tb.addWidget(self.scale_combo)
        tb.addWidget(self.btn_zoom_in)
        tb.addStretch()
        tb.addWidget(self.lbl_info)

        layout.addWidget(self._toolbar)

        # --- Web View ---
        self.webview = QWebEngineView()
        self.webview.loadFinished.connect(self._on_load_finished)
        self.webview.setStyleSheet("background-color: #1a1a1a;")

        layout.addWidget(self.webview, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, html_path: str):
        """Load an HTML slide file from disk into the preview."""
        self._base_dir = os.path.dirname(os.path.abspath(html_path))
        url = QUrl.fromLocalFile(os.path.abspath(html_path))
        self.webview.load(url)

    def load_html(self, html_content: str, base_dir: str):
        """Load an HTML string into the preview with a base URL for relative paths."""
        self._base_dir = base_dir
        base_url = QUrl.fromLocalFile(os.path.abspath(base_dir) + os.sep)
        self.webview.setHtml(html_content, base_url)

    def refresh_scale(self):
        """Re-apply current scale (call after content change or resize)."""
        if self._fit_mode:
            QTimer.singleShot(30, self.fit_to_screen)
        else:
            self._apply_zoom(self._scale_percent)

    def fit_to_screen(self):
        """Calculate the scale that fits the slide inside the preview area."""
        vw = self.webview.width()
        vh = self.webview.height()
        if vw <= 0 or vh <= 0:
            return
        scale_w = vw / self.SLIDE_W
        scale_h = vh / self.SLIDE_H
        factor = min(scale_w, scale_h)
        percent = max(10, int(factor * 100))
        self._fit_mode = True
        self._scale_percent = percent
        self._apply_zoom(percent)
        self._update_combo_text("Fit")

    def set_scale(self, percent: int):
        """Set an exact scale percentage and exit fit mode."""
        percent = max(10, min(400, percent))
        self._fit_mode = False
        self._scale_percent = percent
        self._apply_zoom(percent)
        self._update_combo_text(f"{percent}%")

    def current_scale(self) -> int:
        return self._scale_percent

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_zoom(self, percent: int):
        """Apply CSS zoom to the page body via JavaScript."""
        js = f"""
        (function() {{
            document.body.style.zoom = '{percent}%';
            document.body.style.overflow = 'hidden';
            document.documentElement.style.overflow = 'hidden';
        }})();
        """
        self.webview.page().runJavaScript(js)

    def _step_scale(self, delta: int):
        new_pct = max(10, min(400, self._scale_percent + delta))
        self.set_scale(new_pct)

    def _on_scale_text_changed(self, text: str):
        text = text.strip()
        if text.lower() == "fit":
            self.fit_to_screen()
            return
        clean = text.replace('%', '').strip()
        try:
            val = int(clean)
            self.set_scale(val)
        except ValueError:
            pass

    def _update_combo_text(self, text: str):
        self.scale_combo.blockSignals(True)
        self.scale_combo.setCurrentText(text)
        self.scale_combo.blockSignals(False)

    def _on_load_finished(self, ok):
        if ok:
            if self._fit_mode:
                QTimer.singleShot(80, self.fit_to_screen)
            else:
                self._apply_zoom(self._scale_percent)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_to_screen()


# =================================================================
# SLIDE EDIT PANEL (Modular Sub-Component)
# =================================================================

class SlideEditPanel(QWidget):
    """Right panel: text narration editor and HTML source editor."""

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_loading = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #333; border-radius: 4px;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #2d2d2d; color: #aaa; padding: 6px 14px;
                border: 1px solid #333; border-bottom: none;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #1e1e1e; color: #fff;
                border-bottom: 2px solid #007acc;
            }
            QTabBar::tab:hover { background: #383838; }
        """)

        # --- Text Tab ---
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(4, 4, 4, 4)

        text_hint = QLabel("Narration text — sent to TTS for audio generation")
        text_hint.setStyleSheet("color: #888; font-size: 11px; padding: 2px 4px;")
        text_hint.setWordWrap(True)
        text_layout.addWidget(text_hint)

        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText(
            "Enter narration text for TTS...\n\n"
            "This text will be converted to speech during rendering."
        )
        self.text_editor.setStyleSheet("""
            QTextEdit {
                background: #1e1e1e; color: #d4d4d4;
                border: 1px solid #333; border-radius: 4px;
                font-size: 14px; padding: 8px;
            }
        """)
        self.text_editor.textChanged.connect(self._on_text_changed)
        text_layout.addWidget(self.text_editor)

        self.tabs.addTab(text_widget, "📝 Text")

        # --- HTML Tab ---
        html_widget = QWidget()
        html_layout = QVBoxLayout(html_widget)
        html_layout.setContentsMargins(4, 4, 4, 4)

        html_hint = QLabel("Slide HTML source — controls visual appearance")
        html_hint.setStyleSheet("color: #888; font-size: 11px; padding: 2px 4px;")
        html_hint.setWordWrap(True)
        html_layout.addWidget(html_hint)

        self.html_editor = QPlainTextEdit()
        self.html_editor.setPlaceholderText("Edit HTML content...")
        self.html_editor.setFont(QFont("Consolas", 10))
        self.html_editor.setStyleSheet("""
            QPlainTextEdit {
                background: #1e1e1e; color: #d4d4d4;
                border: 1px solid #333; border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 12px; padding: 8px;
            }
        """)
        self.html_editor.textChanged.connect(self._on_html_changed)
        html_layout.addWidget(self.html_editor)

        self.tabs.addTab(html_widget, "💻 HTML")

        layout.addWidget(self.tabs)

    # --- Suppress signals during programmatic content load ---

    def _on_text_changed(self):
        if not self._is_loading:
            self.content_changed.emit()

    def _on_html_changed(self):
        if not self._is_loading:
            self.content_changed.emit()

    def set_loading(self, loading: bool):
        self._is_loading = loading

    # --- Getters / Setters ---

    def get_text(self) -> str:
        return self.text_editor.toPlainText()

    def set_text(self, text: str):
        self._is_loading = True
        self.text_editor.setPlainText(text)
        self._is_loading = False

    def get_html(self) -> str:
        return self.html_editor.toPlainText()

    def set_html(self, html: str):
        self._is_loading = True
        self.html_editor.setPlainText(html)
        self._is_loading = False

    def clear(self):
        self._is_loading = True
        self.text_editor.clear()
        self.html_editor.clear()
        self._is_loading = False


# =================================================================
# SLIDE EDITOR TAB WIDGET (Main Orchestrator)
# =================================================================

class SlideEditorTabWidget(QWidget):
    """
    Modular slide editor with live preview.

    Architecture:
        ┌─────────────┬────────────────────────┬──────────────────┐
        │ SlideList   │  SlidePreviewPanel     │  SlideEditPanel  │
        │ Panel       │  (QWebEngineView +     │  (Text + HTML    │
        │ (left nav)  │   zoom controls)       │   editors)       │
        └─────────────┴────────────────────────┴──────────────────┘

    Features:
        - Debounced auto-save (800 ms after last keystroke)
        - Debounced preview refresh (500 ms after last HTML change)
        - Fit to Screen button with auto-refit on resize
        - Scale percentage control (combo + / - buttons)
        - Full CSS rendering (Tailwind CDN, Google Fonts, Material Icons)
        - Ctrl+S for immediate save
    """

    content_saved = Signal(str)  # project_path

    AUTO_SAVE_DELAY_MS = 800
    PREVIEW_DELAY_MS = 500

    def __init__(self, project_path, config, parent=None):
        super().__init__(parent)
        self.project_path = project_path
        self.project_name = os.path.basename(project_path)
        self.config = config

        self.vid_width = int(self.config.get('width', 1280))
        self.vid_height = int(self.config.get('height', 720))
        self.slide_files = []
        self.current_index = -1
        self._dirty = False

        # ── Debounce Timers ──────────────────────────────────────
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(self.AUTO_SAVE_DELAY_MS)
        self._auto_save_timer.timeout.connect(self._on_auto_save_timeout)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(self.PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._setup_ui()
        self._connect_signals()
        self._load_project()

        # Ctrl+S shortcut
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self.save_current_slide)

        # Next/Previous Slide
        self._shortcut_next_slide = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_next_slide.activated.connect(self._next_slide)

        self._shortcut_prev_slide = QShortcut(QKeySequence("Ctrl+G"), self)
        self._shortcut_prev_slide.activated.connect(self._prev_slide)

        # Add New Slide
        self._shortcut_add_slide = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        self._shortcut_add_slide.activated.connect(self._add_slide)

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header Bar ───────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(34)
        header.setObjectName("editorHeader")
        header.setStyleSheet("""
            #editorHeader {
                background: #252526;
                border-bottom: 1px solid #3c3c3c;
            }
        """)
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(10, 0, 10, 0)

        lbl = QLabel(f"📝 {self.project_name}")
        lbl.setStyleSheet(
            "color: #ddd; font-weight: bold; font-size: 13px; "
            "background: transparent;")
        hdr.addWidget(lbl)
        hdr.addStretch()

        self.lbl_dirty = QLabel("✓ Saved")
        self.lbl_dirty.setStyleSheet(
            "color: #6a9955; font-size: 11px; background: transparent;")
        hdr.addWidget(self.lbl_dirty)

        layout.addWidget(header)

        # ── Main Splitter ────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #3c3c3c; width: 2px; }")

        self.list_panel = SlideListPanel()
        self.list_panel.setMinimumWidth(130)
        self.list_panel.setMaximumWidth(260)

        self.preview_panel = SlidePreviewPanel()
        self.preview_panel.setMinimumWidth(350)

        self.edit_panel = SlideEditPanel()
        self.edit_panel.setMinimumWidth(280)

        splitter.addWidget(self.list_panel)
        splitter.addWidget(self.preview_panel)
        splitter.addWidget(self.edit_panel)
        splitter.setSizes([150, 620, 380])

        layout.addWidget(splitter, stretch=1)

    def _connect_signals(self):
        # Slide list
        self.list_panel.slide_selected.connect(self._on_slide_selected)
        self.list_panel.btn_add.clicked.connect(self._add_slide)
        self.list_panel.btn_remove.clicked.connect(self._remove_slide)
        self.list_panel.btn_duplicate.clicked.connect(self._duplicate_slide)
        self.list_panel.btn_move_up.clicked.connect(self._move_slide_up)
        self.list_panel.btn_move_down.clicked.connect(self._move_slide_down)
        # Editor
        self.edit_panel.content_changed.connect(self._on_content_changed)

    # ------------------------------------------------------------------
    # Project Loading
    # ------------------------------------------------------------------

    def _load_project(self):
        """Scan project directory and populate the slide list."""
        import glob as _glob
        pattern = os.path.join(self.project_path, "slide*.html")
        files = sorted(_glob.glob(pattern), key=natural_sort_key)
        self.slide_files = files
        self.list_panel.load_slides(files)
        if files:
            self._on_slide_selected(0)

    # ------------------------------------------------------------------
    # Slide Selection
    # ------------------------------------------------------------------

    def _on_slide_selected(self, index):
        """Load the selected slide into editors and preview."""
        if index < 0 or index >= len(self.slide_files):
            return

        # Save previous if dirty
        if self._dirty and self.current_index >= 0 and self.current_index != index:
            self.save_current_slide()

        self.current_index = index
        html_path = self.slide_files[index]
        txt_path = os.path.splitext(html_path)[0] + ".txt"

        # Read text
        text_content = ""
        if os.path.exists(txt_path):
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    text_content = f.read()
            except Exception as e:
                logger.warning(f"Could not read {txt_path}: {e}")

        # Read HTML
        html_content = ""
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            logger.warning(f"Could not read {html_path}: {e}")

        # Load into editors (signals suppressed)
        self.edit_panel.set_loading(True)
        self.edit_panel.set_text(text_content)
        self.edit_panel.set_html(html_content)
        self.edit_panel.set_loading(False)

        # Load into preview from file (ensures CDN resources load correctly)
        self.preview_panel.load_file(html_path)

        self._dirty = False
        self._update_dirty_indicator()

    # ------------------------------------------------------------------
    # Content Change & Auto-Save
    # ------------------------------------------------------------------

    def _on_content_changed(self):
        """Fired when text or HTML editor content changes."""
        self._dirty = True
        self._update_dirty_indicator()
        self._auto_save_timer.start()   # restart debounce
        self._preview_timer.start()     # restart preview debounce

    def _update_dirty_indicator(self):
        if self._dirty:
            self.lbl_dirty.setText("● Unsaved")
            self.lbl_dirty.setStyleSheet(
                "color: #f1c40f; font-size: 11px; background: transparent;")
        else:
            self.lbl_dirty.setText("✓ Saved")
            self.lbl_dirty.setStyleSheet(
                "color: #6a9955; font-size: 11px; background: transparent;")

    def _on_auto_save_timeout(self):
        if self._dirty:
            self.save_current_slide()

    def save_current_slide(self):
        """Save the current slide's text and HTML to disk."""
        if self.current_index < 0 or self.current_index >= len(self.slide_files):
            return

        html_path = self.slide_files[self.current_index]
        txt_path = os.path.splitext(html_path)[0] + ".txt"

        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(self.edit_panel.get_text())
        except Exception as e:
            logger.error(f"Failed to save text: {e}")

        try:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(self.edit_panel.get_html())
        except Exception as e:
            logger.error(f"Failed to save HTML: {e}")

        self._dirty = False
        self._update_dirty_indicator()
        self.content_saved.emit(self.project_path)

    # ------------------------------------------------------------------
    # Preview Refresh
    # ------------------------------------------------------------------

    def _refresh_preview(self):
        """Reload preview with the current HTML editor content (live preview)."""
        if self.current_index < 0:
            return
        html = self.edit_panel.get_html()
        if html.strip():
            self.preview_panel.load_html(html, self.project_path)

    # ------------------------------------------------------------------
    # Cleanup (called before tab close)
    # ------------------------------------------------------------------

    def save_and_cleanup(self):
        """Flush pending timers and save before tab removal."""
        self._auto_save_timer.stop()
        self._preview_timer.stop()
        if self._dirty:
            self.save_current_slide()

    # ------------------------------------------------------------------
    # Slide Management
    # ------------------------------------------------------------------

    def _next_slide(self):
        """Navigate to the next slide in the list."""
        idx = self.list_panel.current_index()
        if 0 <= idx < len(self.slide_files) - 1:
            self.list_panel.set_current_index(idx + 1)

    def _prev_slide(self):
        """Navigate to the previous slide in the list."""
        idx = self.list_panel.current_index()
        if idx > 0:
            self.list_panel.set_current_index(idx - 1)

    def _add_slide(self):
        next_num = len(self.slide_files) + 1

        # Find the first unused number to avoid collisions
        existing = set()
        for f in self.slide_files:
            m = re.search(r"slide(\d+)\.html", os.path.basename(f))
            if m:
                existing.add(int(m.group(1)))
        for n in range(1, next_num + 1):
            if n not in existing:
                next_num = n
                break

        html_path = os.path.join(self.project_path, f"slide{next_num}.html")
        txt_path = os.path.join(self.project_path, f"slide{next_num}.txt")

        try:
            create_slide_file(html_path, get_blank_slide_html("#1a1a2e"))
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write("")
            self._load_project()
            if html_path in self.slide_files:
                self.list_panel.set_current_index(
                    self.slide_files.index(html_path))
        except Exception as e:
            logger.error(f"Failed to add slide: {e}")

    def _remove_slide(self):
        if len(self.slide_files) <= 1:
            QMessageBox.information(
                self, "Cannot Remove",
                "Project must have at least one slide.")
            return

        idx = self.list_panel.current_index()
        if idx < 0:
            return

        reply = QMessageBox.question(
            self, "Remove Slide",
            f"Remove Slide {idx + 1}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        html_path = self.slide_files[idx]
        txt_path = os.path.splitext(html_path)[0] + ".txt"
        wav_path = os.path.splitext(html_path)[0] + ".wav"

        try:
            for p in [html_path, txt_path, wav_path]:
                if os.path.exists(p):
                    os.remove(p)
            self._load_project()
        except Exception as e:
            logger.error(f"Failed to remove slide: {e}")

    def _duplicate_slide(self):
        idx = self.list_panel.current_index()
        if idx < 0:
            return

        src_html = self.slide_files[idx]
        src_txt = os.path.splitext(src_html)[0] + ".txt"

        # Find next available number
        next_num = len(self.slide_files) + 1
        existing = set()
        for f in self.slide_files:
            m = re.search(r"slide(\d+)\.html", os.path.basename(f))
            if m:
                existing.add(int(m.group(1)))
        for n in range(1, next_num + 1):
            if n not in existing:
                next_num = n
                break

        dst_html = os.path.join(self.project_path, f"slide{next_num}.html")
        dst_txt = os.path.join(self.project_path, f"slide{next_num}.txt")

        try:
            shutil.copy(src_html, dst_html)
            if os.path.exists(src_txt):
                shutil.copy(src_txt, dst_txt)
            else:
                with open(dst_txt, 'w', encoding='utf-8') as f:
                    f.write("")
            self._load_project()
            if dst_html in self.slide_files:
                self.list_panel.set_current_index(
                    self.slide_files.index(dst_html))
        except Exception as e:
            logger.error(f"Failed to duplicate slide: {e}")

    def _move_slide_up(self):
        idx = self.list_panel.current_index()
        if idx <= 0:
            return
        self._swap_slide_content(idx, idx - 1)

    def _move_slide_down(self):
        idx = self.list_panel.current_index()
        if idx < 0 or idx >= len(self.slide_files) - 1:
            return
        self._swap_slide_content(idx, idx + 1)

    def _swap_slide_content(self, a, b):
        """Swap the file contents of two slides (preserves filename order).

        Swaps HTML, text (.txt), AND audio (.wav) files so that
        reordering slides keeps narration in sync with visuals.
        """
        ha = self.slide_files[a]
        hb = self.slide_files[b]
        ta = os.path.splitext(ha)[0] + ".txt"
        tb = os.path.splitext(hb)[0] + ".txt"
        wa = os.path.splitext(ha)[0] + ".wav"
        wb = os.path.splitext(hb)[0] + ".wav"

        try:
            # ── Swap HTML ──
            with open(ha, 'r', encoding='utf-8') as f:
                ca = f.read()
            with open(hb, 'r', encoding='utf-8') as f:
                cb = f.read()
            with open(ha, 'w', encoding='utf-8') as f:
                f.write(cb)
            with open(hb, 'w', encoding='utf-8') as f:
                f.write(ca)

            # ── Swap text ──
            tca = ""
            tcb = ""
            if os.path.exists(ta):
                with open(ta, 'r', encoding='utf-8') as f:
                    tca = f.read()
            if os.path.exists(tb):
                with open(tb, 'r', encoding='utf-8') as f:
                    tcb = f.read()
            with open(ta, 'w', encoding='utf-8') as f:
                f.write(tcb)
            with open(tb, 'w', encoding='utf-8') as f:
                f.write(tca)

            # ── Swap audio (.wav) ──
            wca = None
            wcb = None
            if os.path.exists(wa):
                with open(wa, 'rb') as f:
                    wca = f.read()
            if os.path.exists(wb):
                with open(wb, 'rb') as f:
                    wcb = f.read()
            if wca is not None and wcb is not None:
                with open(wa, 'wb') as f:
                    f.write(wcb)
                with open(wb, 'wb') as f:
                    f.write(wca)
            elif wca is not None:
                # Only slide A had audio: move it to B, delete A's
                with open(wb, 'wb') as f:
                    f.write(wca)
                os.remove(wa)
            elif wcb is not None:
                # Only slide B had audio: move it to A, delete B's
                with open(wa, 'wb') as f:
                    f.write(wcb)
                os.remove(wb)

            self._load_project()
            self.list_panel.set_current_index(b)
        except Exception as e:
            logger.error(f"Failed to swap slides: {e}")