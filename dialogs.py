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
    logger, get_theme, 
    create_slide_file, get_default_slide_html, get_blank_slide_html
)
from config import AppConfig
import backends 

# =================================================================
# CUSTOM WIDGETS
# =================================================================

class ProjectListItemWidget(QWidget):
    """Custom widget for items in the project library list."""
    def __init__(self, name, path, parent=None):
        super().__init__(parent)
        self.name = name
        self.path = path
        th = get_theme()
        item_th = th["list_item"]
        strip_th = th["status_strip"]
        badge_th = th["badge"]
        text_th = th["text"]
        content_margins = th["content_margins"]
        content_spacing = th["content_spacing"]

        self.setObjectName("projectListItemWidget")
        self.setStyleSheet(f"""
            #projectListItemWidget {{
                background-color: {item_th["background"]};
                border-bottom: {item_th["border_bottom"]};
                border-radius: {item_th["border_radius"]}px;
                margin: {item_th["margin"]};
            }}
        """)
        self.setAutoFillBackground(True)
        self.setFixedHeight(item_th["height"])

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # Status Strip (Left border)
        self.status_strip = QFrame()
        self.status_strip.setFixedWidth(strip_th["width"])
        self.status_strip.setStyleSheet(
            f"background-color: {strip_th['colors']['default']}; "
            f"border-top-left-radius: {strip_th['border_radius'].split()[0]}; "
            f"border-bottom-left-radius: {strip_th['border_radius'].split()[2]};"
        )

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(*content_margins)
        content_layout.setSpacing(content_spacing)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        self.lbl_title = QLabel(name)
        self.lbl_title.setStyleSheet(
            f"color: {text_th['title']['color']}; "
            f"font-size: {text_th['title']['font_size']}px; "
            f"font-weight: {text_th['title']['font_weight']}; "
            f"background: transparent;"
        )
        self.lbl_title.setWordWrap(True)

        self.lbl_meta = QLabel("No Data")
        self.lbl_meta.setStyleSheet(
            f"color: {text_th['meta']['color']}; "
            f"font-size: {text_th['meta']['font_size']}px; "
            f"font-weight: {text_th['meta']['font_weight']}; "
            f"background: transparent;"
        )

        text_layout.addWidget(self.lbl_title)
        text_layout.addWidget(self.lbl_meta)

        # Badge
        self.lbl_status_badge = QLabel("NEW")
        self.lbl_status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status_badge.setFixedWidth(badge_th["width"])
        self.lbl_status_badge.setFixedHeight(badge_th["height"])
        self.lbl_status_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_th['colors']['default_bg']};
                color: {badge_th['colors']['default_text']};
                border-radius: {badge_th['border_radius']}px;
                font-size: {badge_th['font_size']}px;
                font-weight: {badge_th['font_weight']};
                border: 1px solid {badge_th['colors']['default_border']};
            }}
        """)

        content_layout.addLayout(text_layout)
        content_layout.addStretch()
        content_layout.addWidget(self.lbl_status_badge)

        self.layout.addWidget(self.status_strip)
        self.layout.addLayout(content_layout)
        self.update_status("unknown")

    def set_selected_visual(self, selected: bool):
        th = get_theme()
        item_th = th["list_item"]
        bg = item_th["background_selected"] if selected else item_th["background"]
        border = item_th["border_selected"] if selected else item_th["border_bottom"]
        self.setStyleSheet(f"""
            #projectListItemWidget {{
                background-color: {bg};
                border-bottom: {border};
                border-radius: {item_th["border_radius"]}px;
                margin: {item_th["margin"]};
            }}
        """)

    def update_status(self, status, progress_pct=0, detail_text=""):
        th = get_theme()
        strip_th = th["status_strip"]
        badge_th = th["badge"]
        text_th = th["text"]

        strip_color = strip_th["colors"].get("default")
        badge_bg = badge_th["colors"]["default_bg"]
        badge_text = badge_th["colors"]["default_text"]
        badge_border = badge_th["colors"]["default_border"]
        badge_label = "NEW"

        if not detail_text: detail_text = "Not Rendered"

        if status == "rendering":
            strip_color = strip_th["colors"].get("rendering")
            badge_bg = strip_color
            badge_text = "#ffffff"
            badge_label = f"{progress_pct}%"
            if "Not Rendered" in detail_text: detail_text = "Processing..."
        elif status == "queued":
            strip_color = strip_th["colors"].get("queued")
            badge_bg = strip_color
            badge_text = "#ffffff"
            badge_label = "WAIT"
        elif status == "ready":
            strip_color = strip_th["colors"].get("ready")
            badge_bg = strip_color
            badge_text = "#ffffff"
            badge_label = "DONE"
        elif status == "error":
            strip_color = strip_th["colors"].get("error")
            badge_bg = strip_color
            badge_text = "#ffffff"
            badge_label = "ERR"
            detail_text = "Error - Check Logs"
        elif status == "unknown":
            strip_color = strip_th["colors"].get("unknown")
            badge_bg = strip_color
            badge_text = "#ffffff"
            badge_label = "TODO"
            detail_text = "Not Rendered"

        self.status_strip.setStyleSheet(
            f"background-color: {strip_color}; "
            f"border-top-left-radius: {strip_th['border_radius'].split()[0]}; "
            f"border-bottom-left-radius: {strip_th['border_radius'].split()[2]};"
        )
        self.lbl_status_badge.setText(badge_label)
        self.lbl_status_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_bg};
                color: {badge_text};
                border-radius: {badge_th['border_radius']}px;
                font-size: {badge_th['font_size']}px;
                font-weight: {badge_th['font_weight']};
                border: 1px solid {strip_color};
            }}
        """)
        
        if status == "error":
            self.lbl_meta.setStyleSheet(f"color: {text_th['meta']['color_error']}; font-size: {text_th['meta']['font_size']}px; font-weight: {text_th['meta']['font_weight']}; background: transparent;")
        else:
            self.lbl_meta.setStyleSheet(f"color: {text_th['meta']['color']}; font-size: {text_th['meta']['font_size']}px; font-weight: {text_th['meta']['font_weight']}; background: transparent;")
            
        self.lbl_meta.setText(detail_text)


class QTextEditLogger(logging.Handler):
    """Custom logging handler that writes to a QTextEdit."""
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record):
        try:
            if not self.text_edit: return
            msg = self.format(record)
            msg = html.escape(msg)
            msg = msg.replace("\n", "<br>")
            color = "#d4d4d4" 
            if record.levelno >= logging.ERROR: color = "#ff6b6b"
            elif record.levelno >= logging.WARNING: color = "#f1c40f"
            elif record.levelname == "INFO": color = "#3498db"
            html_msg = f'<span style="color:{color}">{msg}</span>'
            
            # PySide6 invokeMethod passes arguments directly (no Q_ARG needed)
            QMetaObject.invokeMethod(self.text_edit, "append", Qt.ConnectionType.QueuedConnection, html_msg)
        except Exception: self.handleError(record)


# =================================================================
# SETTINGS TAB WIDGET
# =================================================================

class SettingsTabWidget(QWidget):
    """Widget to be embedded in a QTabWidget for settings."""
    
    log_signal = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        
        self.backend_descriptions = {
            "qwen3": "Qwen-Audio: High quality speech synthesis. Supports emotion and style. Requires moderate VRAM.",
            "bark": "Bark: Transformer-based text-to-audio model capable of generating highly realistic multilingual speech and sound effects.",
            "tortoise": "Tortoise-TTS: Multi-voice TTS known for very high quality prosody, though slower generation speed.",
            "default": "Generic backend implementation."
        }
        
        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Search Bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search settings...")
        self.search_bar.setStyleSheet("padding: 5px; background: #333; color: white; border-radius: 0px; border-bottom: 1px solid #444;")
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
        
        # Sidebar Items
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

        # Connect signal to slot
        self.log_signal.connect(self._update_test_console_ui)

        # Initialize
        self.refresh_ui_values()
        self.refresh_backend_settings_ui()

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
        # General
        self.width_spin.setValue(self.config.get('width', 1280))
        self.height_spin.setValue(self.config.get('height', 720))
        self.fps_spin.setValue(self.config.get('fps', 30))
        self.enc_combo.setCurrentText(self.config.get('encoder', 'auto'))
        self.worker_spin.setValue(self.config.get('render_workers', 3))
        self.out_dir_edit.setText(self.config.get('output_dir', 'Output'))
        self.chk_zoom.setChecked(self.config.get('enable_zoom', False))
        self.spin_zoom.setValue(self.config.get('zoom_factor', 1.1))
        self.trans_spin.setValue(self.config.get('transition_duration', 0.5))

        # Backend Settings
        current_backend = self.config.get('active_backend', 'qwen3')
        index = self.backend_combo.findData(current_backend)
        if index >= 0: self.backend_combo.setCurrentIndex(index)

        # Hardware
        self.qwen3_device_combo.setCurrentText(self.config.get('qwen3_device_map', 'cuda:0'))
        self.qwen3_dtype_combo.setCurrentText(self.config.get('qwen3_dtype', 'bfloat16'))
        self.chk_flash_attn.setChecked(self.config.get('qwen3_attn_implementation') == "flash_attention_2")
        self.qwen3_size_combo.setCurrentText(self.config.get('qwen3_size', '1.7B'))

        # Text Chunking
        self.chk_enable_chunking.setChecked(self.config.get('enable_text_chunking', True))
        self.spin_chunk_max_chars.setValue(self.config.get('chunk_max_chars', 500))
        self.spin_chunk_max_sentences.setValue(self.config.get('chunk_max_sentences', 5))
        self.spin_chunk_min_chars.setValue(self.config.get('chunk_min_chars', 50))
        self.spin_warn_threshold.setValue(self.config.get('chunk_warn_threshold', 1000))

    # ==========================================================
    # PAGE: GENERAL & VIDEO
    # ==========================================================
    def _create_general_page(self):
        page = QWidget()
        layout = QFormLayout(page)

        profile_group = QGroupBox("Quick Profiles")
        p_layout = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["Custom", "Fast Draft (CPU)", "High Quality (GPU)"])
        self.profile_combo.currentIndexChanged.connect(self.apply_profile)
        p_layout.addWidget(self.profile_combo)
        profile_group.setLayout(p_layout)
        layout.addRow(profile_group)

        res_layout = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 3840)
        self.width_spin.valueChanged.connect(lambda v: self.config.set('width', v))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(480, 2160)
        self.height_spin.valueChanged.connect(lambda v: self.config.set('height', v))
        res_layout.addWidget(self.width_spin)
        res_layout.addWidget(QLabel("x"))
        res_layout.addWidget(self.height_spin)
        layout.addRow("Resolution:", res_layout)
        
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 60)
        self.fps_spin.valueChanged.connect(lambda v: self.config.set('fps', v))
        layout.addRow("FPS:", self.fps_spin)
        
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["auto", "libx264", "h264_nvenc"])
        self.enc_combo.currentIndexChanged.connect(lambda i: self.config.set('encoder', self.enc_combo.itemText(i)))
        layout.addRow("Encoder:", self.enc_combo)
        
        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(1, 8)
        self.worker_spin.valueChanged.connect(lambda v: self.config.set('render_workers', v))
        layout.addRow("Render Workers:", self.worker_spin)
        
        out_layout = QHBoxLayout()
        self.out_dir_edit = QLineEdit()
        self.out_dir_edit.textChanged.connect(lambda t: self.config.set('output_dir', t))
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse_output_dir)
        out_layout.addWidget(self.out_dir_edit)
        out_layout.addWidget(btn_browse)
        layout.addRow("Output Dir:", out_layout)

        self.trans_spin = QDoubleSpinBox()
        self.trans_spin.setRange(0.0, 10.0)
        self.trans_spin.setSingleStep(0.1)
        self.trans_spin.setSuffix(" s")
        self.trans_spin.valueChanged.connect(lambda v: self.config.set('transition_duration', v))
        layout.addRow("Transition Duration:", self.trans_spin)

        self.chk_zoom = QCheckBox("Enable Ken Burns (Slow Zoom)")
        self.chk_zoom.toggled.connect(lambda c: self.config.set('enable_zoom', c))
        layout.addRow(self.chk_zoom)
        
        self.spin_zoom = QDoubleSpinBox()
        self.spin_zoom.setRange(1.01, 2.0)
        self.spin_zoom.setSingleStep(0.05)
        self.spin_zoom.setSuffix("x")
        self.spin_zoom.valueChanged.connect(lambda v: self.config.set('zoom_factor', v))
        layout.addRow("Max Zoom:", self.spin_zoom)
        return page

    def apply_profile(self, index):
        if index == 0: return
        if index == 1: # Draft
            self.config.set('qwen3_size', '0.6B')
            self.config.set('qwen3_device_map', 'cpu')
            self.config.set('fps', 15)
            self.config.set('enable_zoom', False)
        elif index == 2: # Cinema
            self.config.set('qwen3_size', '1.7B')
            self.config.set('qwen3_device_map', 'cuda:0')
            self.config.set('qwen3_attn_implementation', 'flash_attention_2')
            self.config.set('fps', 30)
            self.config.set('enable_zoom', True)
            self.config.set('zoom_factor', 1.1)
        self.profile_combo.setCurrentIndex(0)
        self.refresh_ui_values()
        QMessageBox.information(self, "Profile Applied", "Settings updated. Please review Backend tabs.")

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
        
        self.backend_combo.currentIndexChanged.connect(self.on_backend_selection_changed)
        selector_layout.addWidget(self.backend_combo, 1)
        
        # Mode Selector
        self.mode_label = QLabel("Mode:")
        self.mode_combo = QComboBox()
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
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
        
        # Console Output
        self.test_console = QPlainTextEdit()
        self.test_console.setReadOnly(True)
        self.test_console.setPlaceholderText("Test output will appear here...")
        self.test_console.setMaximumHeight(100)
        
        # Quick Voice Test
        voice_test_layout = QHBoxLayout()
        self.test_preview_text = QLineEdit()
        self.test_preview_text.setPlaceholderText("Enter text for quick audio test...")
        self.test_preview_text.setText("Hello world, this is a test.")
        
        btn_test_voice = QPushButton("Test Audio")
        btn_test_voice.setToolTip("Generate and play a short audio clip")
        btn_test_voice.clicked.connect(self.on_test_voice)
        
        voice_test_layout.addWidget(QLabel("Input:"))
        voice_test_layout.addWidget(self.test_preview_text, 1)
        voice_test_layout.addWidget(btn_test_voice)
        
        diag_layout.addWidget(QLabel("<b>Test Console:</b>"))
        diag_layout.addWidget(self.test_console)
        diag_layout.addLayout(voice_test_layout)
        diag_group.setLayout(diag_layout)
        
        self.scroll_layout.addWidget(diag_group)
        self.scroll_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)
        
        return page

    def on_backend_selection_changed(self, index):
        backend_name = self.backend_combo.currentData()
        self.config.set('active_backend', backend_name)
        self.refresh_backend_settings_ui()

    def on_mode_changed(self, index):
        backend_name = self.config.get('active_backend', 'qwen3')
        mode_key = self.mode_combo.currentData()
        
        self.config.set(f"{backend_name}_mode", mode_key)
        self.load_backend_widget()

    def refresh_backend_settings_ui(self):
        backend_name = self.config.get('active_backend', 'qwen3')
        
        desc = self.backend_descriptions.get(backend_name, self.backend_descriptions.get("default"))
        self.backend_desc_label.setText(f"<b>{backend_name.upper()}</b>: {desc}")

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
            if widget: widget.deleteLater()
            
        backend_name = self.config.get('active_backend', 'qwen3')
        
        try:
            backend_class = backends.BACKEND_MAP.get(backend_name)
            if backend_class:
                if hasattr(backend_class, 'get_settings_widget'):
                    if self.mode_combo.isVisible():
                        mode = self.mode_combo.currentData()
                    else:
                        mode = self.config.get(f"{backend_name}_mode", "default")
                        
                    backend_widget = backend_class.get_settings_widget(mode, self.config, self)
                    self.backend_params_layout.addWidget(backend_widget)
                else:
                    self.backend_params_layout.addWidget(QLabel(f"{backend_name} has no configurable parameters."))
            else:
                self.backend_params_layout.addWidget(QLabel("Backend implementation not found."))
        except Exception as e:
            self.backend_params_layout.addWidget(QLabel(f"<span style='color:red'>Error loading UI: {e}</span>"))
            logger.error(f"Error loading backend settings: {e}")

    def _update_test_console_ui(self, message):
        self.test_console.appendPlainText(message)
        sb = self.test_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def log_test(self, message):
        self.log_signal.emit(f">> {message}")

    def on_test_voice(self):
        btn = self.sender()
        btn.setEnabled(False)
        original_text = btn.text()
        btn.setText("Running...")
        self.log_test("Starting Audio Test...")
        
        def _run():
            tmp_path = ""
            backend = None
            try:
                # 1. Validation
                text = self.test_preview_text.text().strip()
                if not text:
                    self.log_test("ERROR: No text to generate.")
                    return

                # 2. Create Temp File
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                
                # 3. Initialize Backend
                backend_name = self.config.get('active_backend', 'qwen3')
                self.log_test(f"Initializing backend: {backend_name}...")
                
                # 4. Run Generation
                backend = backends.get_backend(backend_name, self.config.settings)
                self.log_test(f"Generating audio for: '{text}'")
                
                success, errors = backend.generate_batch([text], [tmp_path])
                
                # 5. Handle Result
                if success and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    self.log_test("SUCCESS: Audio generated.")
                    try:
                        import platform
                        if platform.system() == "Windows": os.startfile(tmp_path)
                        elif platform.system() == "Darwin": subprocess.call(["open", tmp_path])
                        else: subprocess.call(["xdg-open", tmp_path])
                    except Exception as e:
                        self.log_test(f"Could not auto-play file: {e}")
                        self.log_test(f"File saved at: {tmp_path}")
                else:
                    self.log_test("FAILED: Backend returned failure.")
                    if errors:
                        err_msg = str(errors[0]).split('\n')[0]
                        self.log_test(f"Reason: {err_msg}")

            except Exception as e:
                import traceback
                self.log_test(f"CRASH: {str(e)}")
                logger.error(traceback.format_exc())
            finally:
                # 6. Cleanup Backend
                if backend:
                    self.log_test("Cleaning up backend...")
                    try:
                        backend.cleanup()
                    except Exception as e:
                        logger.error(f"Cleanup failed: {e}")
                
                # 7. Reset UI
                QTimer.singleShot(100, lambda: btn.setEnabled(True))
                QTimer.singleShot(100, lambda: btn.setText(original_text))

        import threading
        t = threading.Thread(target=_run)
        t.start()

    # ==========================================================
    # PAGE: HARDWARE
    # ==========================================================
    def _create_hardware_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        
        dev_row = QHBoxLayout()
        self.qwen3_device_combo = QComboBox()
        self.qwen3_device_combo.addItems(["cpu", "cuda:0", "cuda:1"])
        self.qwen3_device_combo.currentTextChanged.connect(lambda t: self.config.set('qwen3_device_map', t))
        
        btn_detect = QPushButton("Auto-Detect GPU")
        btn_detect.clicked.connect(self.auto_detect_hardware)
        
        dev_row.addWidget(self.qwen3_device_combo)
        dev_row.addWidget(btn_detect)
        layout.addRow("Compute Device:", dev_row)

        self.qwen3_dtype_combo = QComboBox()
        self.qwen3_dtype_combo.addItems(["bfloat16", "float16"])
        self.qwen3_dtype_combo.currentTextChanged.connect(lambda t: self.config.set('qwen3_dtype', t))
        layout.addRow("Data Type:", self.qwen3_dtype_combo)

        self.chk_flash_attn = QCheckBox("Flash Attention 2")
        self.chk_flash_attn.setToolTip("Faster, lower VRAM. Requires RTX 30/40 series.")
        self.chk_flash_attn.toggled.connect(lambda c: self.config.set('qwen3_attn_implementation', "flash_attention_2" if c else "eager"))
        layout.addRow(self.chk_flash_attn)

        # Model Size Selector
        self.qwen3_size_combo = QComboBox()
        self.qwen3_size_combo.addItems(["1.7B", "0.6B"])
        self.qwen3_size_combo.setCurrentText(self.config.get('qwen3_size', '1.7B'))
        self.qwen3_size_combo.currentTextChanged.connect(lambda t: self.config.set('qwen3_size', t))
        layout.addRow("Model Size:", self.qwen3_size_combo)
        
        return page

    def auto_detect_hardware(self):
        try:
            import torch
            if torch.cuda.is_available():
                count = torch.cuda.device_count()
                self.qwen3_device_combo.clear()
                self.qwen3_device_combo.addItem("cpu")
                for i in range(count):
                    name = torch.cuda.get_device_name(i)
                    self.qwen3_device_combo.addItem(f"cuda:{i} ({name})")
                self.qwen3_device_combo.setCurrentIndex(1)
                self.config.set('qwen3_device_map', "cuda:0")
                QMessageBox.information(self, "Success", f"Found {count} GPU(s).")
            else:
                QMessageBox.warning(self, "No GPU", "No CUDA-compatible GPU found.")
                self.qwen3_device_combo.setCurrentText("cpu")
        except ImportError:
            QMessageBox.critical(self, "Error", "PyTorch not installed.")

    # ==========================================================
    # PAGE: TEXT CHUNKING (Long Text Handling)
    # ==========================================================
    def _create_chunking_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # Info Header
        info_group = QGroupBox("Long Text Handling")
        info_layout = QVBoxLayout()
        info_label = QLabel(
            "<b>Automatic Text Chunking for TTS</b><br><br>"
            "When enabled, long text inputs are automatically split into smaller chunks "
            "that the TTS model can process reliably. Each chunk is generated separately "
            "and then concatenated to produce the final audio output.<br><br>"
            "<i>This prevents audio generation failures for long paragraphs and ensures "
            "consistent voice quality across chunked segments.</i>"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #cccccc; padding: 10px;")
        info_layout.addWidget(info_label)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # Settings Form
        settings_group = QGroupBox("Chunking Settings")
        form_layout = QFormLayout()
        
        # Enable Chunking
        self.chk_enable_chunking = QCheckBox("Enable Automatic Text Chunking")
        self.chk_enable_chunking.setToolTip("When disabled, long texts may cause TTS failures")
        self.chk_enable_chunking.setChecked(True)
        self.chk_enable_chunking.toggled.connect(lambda c: self.config.set('enable_text_chunking', c))
        form_layout.addRow(self.chk_enable_chunking)
        
        # Max Characters per Chunk
        self.spin_chunk_max_chars = QSpinBox()
        self.spin_chunk_max_chars.setRange(100, 2000)
        self.spin_chunk_max_chars.setSingleStep(50)
        self.spin_chunk_max_chars.setSuffix(" chars")
        self.spin_chunk_max_chars.setToolTip("Maximum characters per audio chunk. Lower values = more chunks but more reliable.")
        self.spin_chunk_max_chars.valueChanged.connect(lambda v: self.config.set('chunk_max_chars', v))
        form_layout.addRow("Max Chars per Chunk:", self.spin_chunk_max_chars)
        
        # Max Sentences per Chunk
        self.spin_chunk_max_sentences = QSpinBox()
        self.spin_chunk_max_sentences.setRange(1, 10)
        self.spin_chunk_max_sentences.setToolTip("Maximum sentences per chunk. Helps preserve natural speech patterns.")
        self.spin_chunk_max_sentences.valueChanged.connect(lambda v: self.config.set('chunk_max_sentences', v))
        form_layout.addRow("Max Sentences per Chunk:", self.spin_chunk_max_sentences)
        
        # Min Chunk Characters
        self.spin_chunk_min_chars = QSpinBox()
        self.spin_chunk_min_chars.setRange(10, 200)
        self.spin_chunk_min_chars.setSingleStep(10)
        self.spin_chunk_min_chars.setSuffix(" chars")
        self.spin_chunk_min_chars.setToolTip("Minimum characters to form a valid chunk. Prevents tiny fragments.")
        self.spin_chunk_min_chars.valueChanged.connect(lambda v: self.config.set('chunk_min_chars', v))
        form_layout.addRow("Min Chunk Size:", self.spin_chunk_min_chars)
        
        # Warning Threshold
        self.spin_warn_threshold = QSpinBox()
        self.spin_warn_threshold.setRange(500, 5000)
        self.spin_warn_threshold.setSingleStep(100)
        self.spin_warn_threshold.setSuffix(" chars")
        self.spin_warn_threshold.setToolTip("Log a warning when text exceeds this length")
        self.spin_warn_threshold.valueChanged.connect(lambda v: self.config.set('chunk_warn_threshold', v))
        form_layout.addRow("Warning Threshold:", self.spin_warn_threshold)
        
        settings_group.setLayout(form_layout)
        layout.addWidget(settings_group)
        
        # Estimated Impact Info
        impact_group = QGroupBox("Estimated Impact")
        impact_layout = QVBoxLayout()
        impact_label = QLabel(
            "<b>Recommended Settings by Use Case:</b><br><br>"
            "• <b>Short slides (~200 chars):</b> Chunking rarely needed, defaults work fine<br>"
            "• <b>Medium slides (~500 chars):</b> Default settings (500 chars/chunk) recommended<br>"
            "• <b>Long narratives (~1000+ chars):</b> Consider 400-500 chars/chunk for best quality<br>"
            "• <b>Very long text (~2000+ chars):</b> Reduce to 300-400 chars if quality issues occur<br><br>"
            "<i>Note: Smaller chunks = more API calls but more reliable output. "
            "Larger chunks = fewer calls but may hit model limits.</i>"
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
                self.config.set(key, text)
                
                exists = os.path.isdir(text) if is_dir else os.path.exists(text)
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
            btn.clicked.connect(lambda: self.browse_generic(edit, is_dir))
            
            row.addWidget(QLabel(label))
            row.addWidget(edit)
            row.addWidget(btn)
            return row

        layout.addRow(make_validated_row("HF Model Cache:", "hf_cache_dir"))
        layout.addRow(make_validated_row("HF Datasets:", "hf_datasets_dir"))
        layout.addRow(make_validated_row("Voice References:", "voice_references_root"))
        layout.addRow(make_validated_row("Voice Design Cache:", "instruction_folder_root"))
        
        self.chk_symlinks = QCheckBox("Use Symlinks (Linux/Mac only)")
        self.chk_symlinks.setChecked(self.config.get("hf_use_symlinks", False))
        self.chk_symlinks.toggled.connect(lambda c: self.config.set("hf_use_symlinks", c))
        layout.addRow(self.chk_symlinks)
        
        return page

    def browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.out_dir_edit.text())
        if d: self.out_dir_edit.setText(d)

    def browse_generic(self, line_edit, is_dir):
        if is_dir:
            d = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
            if d: line_edit.setText(d)
        else:
            f, _ = QFileDialog.getOpenFileName(self, "Select File", line_edit.text())
            if f: line_edit.setText(f)

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
            cuda_ver = torch.version.cuda
            gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "No GPU"
            torch_ver = torch.__version__
        except:
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
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(info_text.toPlainText()))
        
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
        self.type_combo.addItems(["Text Slide", "Image Slide", "Blank Slide"])
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        
        self.content_input = QLineEdit()
        self.content_input.setPlaceholderText("Enter text or select image...")
        
        self.btn_browse = QPushButton("...")
        self.btn_browse.setMaximumWidth(40)
        self.btn_browse.clicked.connect(self.browse_content)
        self.btn_browse.setVisible(False) 
        
        content_layout = QHBoxLayout()
        content_layout.addWidget(self.content_input)
        content_layout.addWidget(self.btn_browse)

        self.btn_create = QPushButton("Create New Project")
        self.btn_create.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 8px;")
        self.btn_create.clicked.connect(self.create_project)

        layout.addRow("Project Name:", self.name_input)
        layout.addRow("Initial Slide Type:", self.type_combo)
        layout.addRow("Content:", content_layout)
        layout.addRow(self.btn_create)
        layout.addRow(QLabel("<i>Note: Project will be created and added to the library.</i>"))

    def on_type_changed(self, index):
        if self.type_combo.currentText() == "Image Slide":
            self.content_input.setPlaceholderText("Path to image...")
            self.content_input.setEnabled(True)
            self.btn_browse.setVisible(True)
        elif self.type_combo.currentText() == "Text Slide":
            self.content_input.setPlaceholderText("Enter text for slide...")
            self.content_input.setEnabled(True)
            self.btn_browse.setVisible(False)
        else:
            self.content_input.clear()
            self.content_input.setEnabled(False)
            self.btn_browse.setVisible(False)

    def browse_content(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg)")
        if f:
            self.content_input.setText(f)

    def create_project(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Input Error", "Please enter a project name.")
            return

        root_dir = self.config.get('projects_root', os.path.join(os.getcwd(), "Projects"))
        if not os.path.exists(root_dir):
            try:
                os.makedirs(root_dir)
            except OSError as e:
                QMessageBox.critical(self, "Error", f"Could not create projects directory: {e}")
                return

        project_path = os.path.join(root_dir, name)
        if os.path.exists(project_path):
            QMessageBox.warning(self, "Exists", f"A project folder named '{name}' already exists.")
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
                        create_slide_file(html_path, get_image_slide_html(content))
                    except ImportError:
                        create_slide_file(html_path, f"<html><body><img src='file:///{content}'></body></html>")
                    
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write("Image slide.")
                else:
                    create_slide_file(html_path, get_blank_slide_html("#000000"))
                    with open(txt_path, "w", encoding="utf-8") as f: f.write("")
            elif slide_type == "Blank Slide":
                create_slide_file(html_path, get_blank_slide_html("#000000"))
                with open(txt_path, "w", encoding="utf-8") as f: f.write("")

            if self.on_project_created:
                self.on_project_created(project_path)
            
            self.name_input.clear()
            self.content_input.clear()
            self.name_input.setFocus()
            
            QMessageBox.information(self, "Success", f"Project '{name}' created successfully!")

        except Exception as e:
            logger.exception("Failed to create project")
            QMessageBox.critical(self, "Error", f"Failed to create project: {e}")


# =================================================================
# SLIDE EDITOR TAB WIDGET
# =================================================================

class SlideEditorTabWidget(QWidget):
    def __init__(self, project_path, config, parent=None):
        super().__init__(parent)
        self.project_path = project_path
        self.project_name = os.path.basename(project_path)
        self.config = config
        
        self.vid_width = int(self.config.get('width', 1280))
        self.vid_height = int(self.config.get('height', 720))
        self.slide_files = [] 
        self.current_index = 0
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        # Navigation Bar
        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(5, 5, 5, 5)
        
        self.btn_prev = QPushButton("◀ Previous")
        self.btn_prev.clicked.connect(self.prev_slide)
        self.lbl_slide_info = QLabel("Slide 1 / 1")
        self.lbl_slide_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_slide_info.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.btn_next = QPushButton("Next Slide ▶")
        self.btn_next.clicked.connect(self.next_slide)
        
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.lbl_slide_info, 1)
        nav_layout.addWidget(self.btn_next)
        self.layout.addLayout(nav_layout)
        
        # Editor & Preview Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.tabs = QTabWidget()
        self.html_editor = QPlainTextEdit()
        self.html_editor.setStyleSheet("font-family: Consolas; font-size: 12px; background-color: #1e1e1e; color: #dcdcaa;")
        self.tabs.addTab(self.html_editor, "HTML Source")
        
        self.txt_editor = QPlainTextEdit()
        self.txt_editor.setStyleSheet("font-family: Consolas; font-size: 12px; background-color: #1e1e1e; color: #ce9178;")
        self.tabs.addTab(self.txt_editor, "Voiceover Text")
        
        splitter.addWidget(self.tabs)
        
        self.preview_container = QFrame()
        self.preview_container.setStyleSheet("background-color: #ffffff; border-radius: 4px;")
        self.preview_container_layout = QVBoxLayout(self.preview_container)
        self.preview_container_layout.setContentsMargins(0,0,0,0)
        self.preview_container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.preview = QWebEngineView()
        self.preview.setStyleSheet("background-color: #ffffff; border: none;")
        self.preview_container_layout.addWidget(self.preview)
        
        splitter.addWidget(self.preview_container)
        splitter.setSizes([600, 600])
        self.layout.addWidget(splitter)
        
        # Action Bar
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(5, 5, 5, 5)
        
        self.btn_add_blank = QPushButton("Add Blank Slide")
        self.btn_add_blank.clicked.connect(self.add_blank_slide_quick)
        
        self.btn_add_custom = QPushButton("Add Custom Slide")
        self.btn_add_custom.clicked.connect(self.add_new_slide_inline)

        self.btn_delete_slide = QPushButton("Delete Current Slide")
        self.btn_delete_slide.setStyleSheet("color: #ff6b6b; font-weight: bold; padding: 5px;")
        self.btn_delete_slide.clicked.connect(self.delete_current_slide) 
        
        self.btn_save = QPushButton("Save Changes")
        self.btn_save.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; padding: 5px;")
        self.btn_save.clicked.connect(self.save_current_slide)
        
        action_layout.addWidget(self.btn_add_blank)
        action_layout.addWidget(self.btn_add_custom)
        action_layout.addWidget(self.btn_delete_slide)
        action_layout.addStretch()
        action_layout.addWidget(self.btn_save)
        self.layout.addLayout(action_layout)
        
        self.load_project()

    def load_project(self):
        self.slide_files = []
        files = sorted(
            [f for f in os.listdir(self.project_path) if f.endswith(".html") and f.startswith("slide")],
            key=lambda x: int(re.search(r'\d+', x).group())
        )
        self.slide_files = files
        if not self.slide_files:
            self.add_blank_slide_quick()
            
        self.current_index = 0
        self.load_slide()

    def load_slide(self):
        if not self.slide_files: return
        
        filename = self.slide_files[self.current_index]
        html_path = os.path.join(self.project_path, filename)
        txt_path = os.path.join(self.project_path, filename.replace(".html", ".txt"))
        
        # Load HTML
        if os.path.exists(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                self.html_editor.setPlainText(f.read())
        
        # Load Text
        if os.path.exists(txt_path):
            with open(txt_path, 'r', encoding='utf-8') as f:
                self.txt_editor.setPlainText(f.read())
        else:
            self.txt_editor.setPlainText("")
            
        self.update_preview()
        self.update_nav_label()

    def update_preview(self):
        html_content = self.html_editor.toPlainText()
        self.preview.setHtml(html_content, baseUrl=QUrl.fromLocalFile(self.project_path + os.sep))

    def save_current_slide(self):
        if not self.slide_files: return
        
        filename = self.slide_files[self.current_index]
        html_path = os.path.join(self.project_path, filename)
        txt_path = os.path.join(self.project_path, filename.replace(".html", ".txt"))
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(self.html_editor.toPlainText())
            
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(self.txt_editor.toPlainText())
            
        logger.info(f"Saved {filename}")
        self.update_preview()

    def next_slide(self):
        if self.current_index < len(self.slide_files) - 1:
            self.current_index += 1
            self.load_slide()

    def prev_slide(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_slide()

    def update_nav_label(self):
        total = len(self.slide_files)
        current = self.current_index + 1
        self.lbl_slide_info.setText(f"Slide {current} / {total}")

    def add_blank_slide_quick(self):
        self.add_slide("blank")

    def add_new_slide_inline(self):
        self.add_slide("text")

    def add_slide(self, slide_type="blank"):
        next_num = len(self.slide_files) + 1
        html_name = f"slide{next_num}.html"
        html_path = os.path.join(self.project_path, html_name)
        
        if slide_type == "blank":
            create_slide_file(html_path, get_blank_slide_html("#000000"))
        else:
            create_slide_file(html_path, get_default_slide_html("New Slide"))
            
        # Create empty txt
        txt_path = html_path.replace(".html", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("")
            
        self.slide_files.append(html_name)
        self.current_index = len(self.slide_files) - 1
        self.load_slide()

    def delete_current_slide(self):
        if len(self.slide_files) <= 1:
            QMessageBox.warning(self, "Error", "Cannot delete the last slide.")
            return
            
        reply = QMessageBox.question(self, "Confirm Delete", "Delete this slide?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            filename = self.slide_files[self.current_index]
            html_path = os.path.join(self.project_path, filename)
            txt_path = html_path.replace(".html", ".txt")
            
            try:
                os.remove(html_path)
                if os.path.exists(txt_path): os.remove(txt_path)
            except Exception as e:
                logger.error(f"Failed to delete slide: {e}")
                return
                
            self.slide_files.pop(self.current_index)
            if self.current_index >= len(self.slide_files):
                self.current_index = len(self.slide_files) - 1
            self.load_slide()