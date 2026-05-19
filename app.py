# main.py
import sys
import os
import subprocess
import multiprocessing
import queue 
import shutil

# ==============================================================================
# EARLY CONFIGURATION LOAD
# ==============================================================================
import logging

from config import AppConfig

# Initialize the Singleton instance early.
# This loads settings.json immediately and sets up environment variables.
config = AppConfig()

_cache_dir = config.get("hf_cache_dir", os.path.join(os.getcwd(), "models_cache"))

if not os.path.isabs(_cache_dir):
    _cache_dir = os.path.abspath(_cache_dir)

os.environ['HF_HOME'] = _cache_dir
os.environ['HUGGINGFACE_HUB_CACHE'] = _cache_dir
os.environ['TRANSFORMERS_CACHE'] = _cache_dir
os.environ['HF_DATASETS_CACHE'] = os.path.join(_cache_dir, 'datasets')
os.environ['HF_HUB_SYMLINKS'] = '0'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '0'

print(f"[App] Global Cache configured to: {_cache_dir}")

# ==============================================================================
# HEAVY IMPORTS
# ==============================================================================

try:
    import torch
except ImportError:
    raise Exception("Missing torch dependency. Run: pip install torch torchaudio")

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QMenu, QCompleter,
                             QHBoxLayout, QListWidget, QPushButton, QLabel, QLineEdit,
                             QTextEdit, QProgressBar, QSplitter, QMessageBox, QComboBox,
                             QFileDialog, QListWidgetItem, QGroupBox, QFormLayout, QInputDialog, QTabWidget, QFrame)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QShortcut, QKeySequence  

# Local imports
from dialogs import QTextEditLogger, SlideEditorTabWidget, ProjectListItemWidget, NewProjectTabWidget, SettingsTabWidget
from engines import render_project_worker
from utils import (logger, detect_ffmpeg, get_project_dirs, 
                   get_library_manifest_path, load_project_manifest, 
                   should_skip_render, natural_sort_key,
                   create_slide_file, get_default_slide_html, get_image_slide_html, get_blank_slide_html,
                   get_theme, set_theme, list_themes, list_widget_stylesheet_from_theme,
                   setup_logging, initialize_project_files, get_library_status_data)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Since AppConfig is a Singleton, this returns the same instance created above
        self.config = AppConfig()
        
        self.project_dirs = get_project_dirs()
        
        self.max_workers = self.config.get('render_workers', 3)

        self.active_processes = {}  
        self.pending_projects = []  
        self.render_queue = multiprocessing.Queue()
        
        self.init_ui()
        setup_logging(self.log_text)
        self._cleanup_stale_files()
        
        self.queue_timer = QTimer()
        self.queue_timer.timeout.connect(self.process_queue)
        self.queue_timer.start(100)
        
        self.scheduler_timer = QTimer()
        self.scheduler_timer.timeout.connect(self.schedule_jobs)
        
        self.refresh_library()

    def init_ui(self):
        self.setWindowTitle("AI Slideshow Renderer (Multi-Process)")
        self.resize(1400, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # --- Left Panel: Library ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        lbl_lib = QLabel("Project Library")
        lbl_lib.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        left_layout.addWidget(lbl_lib)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.setStyleSheet("background-color: #2d2d2d; color: #ffffff; border: 1px solid #555555; padding: 6px; border-radius: 4px;")
        self.search_edit.textChanged.connect(self.filter_library)
        left_layout.addWidget(self.search_edit)

        sort_layout = QHBoxLayout()
        sort_layout.setContentsMargins(0, 0, 0, 0)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Name (A->Z)", "Name (Z->A)", "Status", "LastRendered"])
        self.sort_combo.setStyleSheet("background-color: #2d2d2d; color: #ffffff; border: 1px solid #555555; padding: 6px; border-radius: 4px;")
        self.sort_combo.currentIndexChanged.connect(self.refresh_library)

        sort_layout.addWidget(QLabel("Sort:"))
        sort_layout.addWidget(self.sort_combo)
        left_layout.addLayout(sort_layout)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme:")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list_themes())
        self.theme_combo.setCurrentText(get_theme()["name"])
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo)
        left_layout.addLayout(theme_row)

        self.project_list = QListWidget()
        self.project_list.itemDoubleClicked.connect(self.on_project_double_click)

        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.show_library_context_menu)
        self.project_list.itemSelectionChanged.connect(self.on_library_selection_changed)

        self.project_list.setStyleSheet(list_widget_stylesheet_from_theme())

        left_layout.addWidget(self.project_list)
        
        lib_btn_layout = QHBoxLayout()
        btn_new = QPushButton("New Project")
        btn_new.clicked.connect(self.create_new_project)
        btn_edit = QPushButton("Edit Project")
        btn_edit.clicked.connect(self.open_editor)
        btn_delete = QPushButton("Delete Project")
        btn_delete.clicked.connect(self.delete_project)

        lib_btn_layout.addWidget(btn_new)
        lib_btn_layout.addWidget(btn_edit)
        lib_btn_layout.addWidget(btn_delete)
        left_layout.addLayout(lib_btn_layout)
        
        splitter.addWidget(left_panel)
        
        # --- Right Panel: Tabs & Logs ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.setDocumentMode(True)
        
        self.open_settings_tab(initial=True)
        
        right_layout.addWidget(self.tabs, stretch=2)
        
        grp_controls = QGroupBox("Render Controls")
        ctrl_layout = QFormLayout()
        
        self.lbl_workers = QLabel(f"Max Parallel Jobs: {self.max_workers}")
        self.lbl_active_jobs = QLabel("Active: 0")
        self.lbl_queue_count = QLabel("Queue: 0")
        self.lbl_status = QLabel("Idle")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        
        btn_render_sel = QPushButton("Render Selected")
        btn_render_sel.setObjectName("btnBlue")
        btn_render_sel.clicked.connect(self.render_selected)
        
        btn_render_all = QPushButton("Render All (Outdated)")
        btn_render_all.setObjectName("btnGreen")
        btn_render_all.clicked.connect(self.render_all_outdated)
        
        self.btn_stop = QPushButton("Stop All")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.clicked.connect(self.stop_all_renders)
        self.btn_stop.setEnabled(False)

        ctrl_layout.addRow(self.lbl_workers)
        ctrl_layout.addRow(self.lbl_active_jobs)
        ctrl_layout.addRow(self.lbl_queue_count)
        ctrl_layout.addRow("Status:", self.lbl_status)
        ctrl_layout.addRow(self.progress_bar)
        
        btn_row1 = QHBoxLayout()
        btn_row1.addWidget(btn_render_sel)
        btn_row1.addWidget(btn_render_all)
        
        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self.btn_stop)
        
        ctrl_layout.addRow(btn_row1)
        ctrl_layout.addRow(btn_row2)
        
        grp_controls.setLayout(ctrl_layout)
        right_layout.addWidget(grp_controls)
        
        grp_logs = QGroupBox("Application Logs")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #2b2b2b; color: #d4d4d4; font-family: Consolas;")
        log_layout.addWidget(self.log_text)
        grp_logs.setLayout(log_layout)
        right_layout.addWidget(grp_logs)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 1100])

    # --- TAB MANAGEMENT ---

    def open_settings_tab(self, initial=False):
        """Opens or switches to the Settings tab."""
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SettingsTabWidget):
                self.tabs.setCurrentIndex(i)
                return
        
        self.settings_widget = SettingsTabWidget(self.config)
        index = self.tabs.addTab(self.settings_widget, "⚙ Settings")
        if initial:
            self.tabs.setCurrentIndex(index)
        else:
            self.tabs.setCurrentIndex(index)

    def open_project_tab(self, path):
        """Opens or switches to a Project Editor tab."""
        project_name = os.path.basename(path)
        
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, SlideEditorTabWidget) and hasattr(widget, 'project_path') and widget.project_path == path:
                self.tabs.setCurrentIndex(i)
                return
        
        editor = SlideEditorTabWidget(path, self.config, self)
        index = self.tabs.addTab(editor, f"📝 {project_name}")
        self.tabs.setCurrentIndex(index)

    def close_tab(self, index):
        """Handles closing a tab. Prevents closing Settings."""
        widget = self.tabs.widget(index)
        
        if isinstance(widget, SettingsTabWidget):
            return  # Don't close settings

        # Flush any pending auto-save before removing the editor tab
        if isinstance(widget, SlideEditorTabWidget):
            widget.save_and_cleanup()
        
        # Flush any pending debounced settings
        if isinstance(widget, SettingsTabWidget):
            widget._flush_pending_settings()

        self.tabs.removeTab(index)

    # --- LIBRARY & PROJECT MANAGEMENT ---

    def on_library_selection_changed(self):
        """Updates the visual borders when selection changes."""
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            widget = self.project_list.itemWidget(item)
            if widget:
                is_sel = item.isSelected()
                widget.set_selected_visual(is_sel)
            
    def on_theme_changed(self, theme_name: str):
        try:
            set_theme(theme_name)
            logger.info(f"Switched to theme: {theme_name}")
            self.project_list.setStyleSheet(list_widget_stylesheet_from_theme())
            self.refresh_library()
        except Exception as e:
            logger.error(f"Failed to change theme: {e}")

    def refresh_library(self):
        """Reloads library with sorting logic using data from utils."""
        current_selection = self.get_selected_project_path()
        self.project_dirs = get_project_dirs()
        
        self.project_list.clear()
        
        project_data = get_library_status_data(self.project_dirs, self.active_processes, self.pending_projects)
        
        sort_mode = self.sort_combo.currentText()
        if sort_mode == "Name (A->Z)":
            project_data.sort(key=lambda x: natural_sort_key(x["name"]))
        elif sort_mode == "Name (Z->A)":
            project_data.sort(key=lambda x: natural_sort_key(x["name"]), reverse=True)
        elif sort_mode == "Status":
            status_priority = {"error": 0, "rendering": 1, "queued": 2, "unknown": 3, "ready": 4}
            project_data.sort(key=lambda x: status_priority.get(x["status"], 99))
        elif sort_mode == "Last Rendered":
            project_data.sort(key=lambda x: x["last_rendered"], reverse=True)

        for pd in project_data:
            list_widget = ProjectListItemWidget(pd["name"], pd["path"])
            list_widget.update_status(pd["status"], 0, pd["detail"])
            
            item = QListWidgetItem()
            item.setSizeHint(list_widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, pd["path"])
            
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, list_widget)
            
            if pd["path"] == current_selection:
                item.setSelected(True)
            else:
                list_widget.set_selected_visual(False)

        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            widget = self.project_list.itemWidget(item)
            if widget:
                widget.set_selected_visual(item.isSelected())

        self.update_stats()

    def get_selected_project_path(self):
        items = self.project_list.selectedItems()
        if items:
            return items[0].data(Qt.ItemDataRole.UserRole)
        return None

    def get_selected_projects(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.project_list.selectedItems()
        ]

    def create_new_project(self):
        """Opens a New Project Tab. Creates it if it doesn't exist."""
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, NewProjectTabWidget):
                self.tabs.setCurrentIndex(i)
                return

        np_tab = NewProjectTabWidget(self.config, self.on_new_project_created)
        index = self.tabs.addTab(np_tab, "+ New Project")
        self.tabs.setCurrentIndex(index)

    def on_new_project_created(self, project_path):
        """Callback when a project is created via the tab."""
        self.refresh_library()
        self.open_project_tab(project_path)

    def filter_library(self):
        query = self.search_edit.text().strip().lower()
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            name = os.path.basename(path)
            if query in name.lower():
                item.setHidden(False)
            else:
                item.setHidden(True)

    def show_library_context_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        name = os.path.basename(path)

        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #2d2d2d; color: white; } QMenu::item { padding: 5px 20px; } QMenu::item:selected { background-color: #0078d4; }")

        action_open = menu.addAction("Open Folder")
        action_edit = menu.addAction("Edit Project")
        action_render = menu.addAction("Render Now")
        action_output = menu.addAction("Show Output Video")
        menu.addSeparator()
        action_delete = menu.addAction("Delete Project")

        action = menu.exec(self.project_list.mapToGlobal(pos))

        if action == action_open:
            self.open_project_path(path)
        elif action == action_edit:
            self.open_project_tab(path)
        elif action == action_render:
            self.add_to_queue(path)
        elif action == action_output:
            self.show_output_video(path)
        elif action == action_delete:
            self.delete_project_for(path)

    def open_project_path(self, path):
        import platform
        if platform.system() == "Windows": os.startfile(path)
        elif platform.system() == "Darwin": subprocess.call(["open", path])
        else: subprocess.call(["xdg-open", path])

    def open_editor(self):
        path = self.get_selected_project_path()
        if path:
            self.open_project_tab(path)
        else:
            QMessageBox.information(self, "Info", "No project selected.")

    def delete_project(self):
        path = self.get_selected_project_path()
        if path:
            self.delete_project_for(path)

    def show_output_video(self, path):
        manifest = load_project_manifest(path)
        output_info = manifest.get("output", {})
        video_filename = output_info.get("video_filename")
        output_dir = output_info.get("dir")
        if not video_filename or not output_dir:
            QMessageBox.information(self, "Info", "No output video found.")
            return
        video_path = os.path.join(output_dir, video_filename)
        if os.path.exists(video_path):
            self.open_project_path(video_path)
        else:
            QMessageBox.information(self, "Info", "Video file does not exist.")

    def delete_project_for(self, path):
        if path in self.pending_projects:
            self.pending_projects.remove(path)
            logger.info(f" removed {path} from queue.")
            self.refresh_library()
            return
        for pid, p_info in self.active_processes.items():
            if p_info['path'] == path:
                QMessageBox.warning(self, "Busy", "Cannot delete project currently rendering.")
                return

        confirm = QMessageBox.question(self, "Delete Project", 
                                       f"Delete {os.path.basename(path)}?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                if os.path.exists(path):
                    shutil.rmtree(path)
                    logger.info(f"Deleted project: {path}")
                self.refresh_library()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete project: {e}")

    def on_project_double_click(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        self.open_project_path(path)

    # --- SCHEDULER & RENDER LOGIC ---

    def render_selected(self):
        """Render selected projects. Supports multi-select batch."""
        paths = self.get_selected_projects()
        
        if not paths:
            QMessageBox.information(self, "Info", "No projects selected.")
            return
        
        if len(paths) == 1:
            self.add_to_queue(paths[0])
            return
        
        busy_paths = []
        ready_paths = []
        
        for path in paths:
            is_active = any(p['path'] == path for p in self.active_processes.values())
            is_queued = path in self.pending_projects
            
            if is_active or is_queued:
                busy_paths.append(os.path.basename(path))
            else:
                ready_paths.append(path)
        
        msg_parts = [f"Add {len(ready_paths)} project(s) to render queue?"]
        
        if busy_paths:
            msg_parts.append(f"\n\nSkipped (already active/queued): {len(busy_paths)}")
            msg_parts.extend([f"  • {name}" for name in busy_paths[:5]])
            if len(busy_paths) > 5:
                msg_parts.append(f"  ... and {len(busy_paths) - 5} more")
        
        if not ready_paths:
            QMessageBox.information(
                self, "All Busy",
                "All selected projects are already rendering or queued."
            )
            return
        
        msg_parts.append(f"\n\nReady to render:")
        msg_parts.extend([f"  • {os.path.basename(p)}" for p in ready_paths[:10]])
        if len(ready_paths) > 10:
            msg_parts.append(f"  ... and {len(ready_paths) - 10} more")
        
        reply = QMessageBox.question(
            self,
            "Batch Render",
            "\n".join(msg_parts),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            added = 0
            for path in ready_paths:
                self.add_to_queue(path)
                added += 1
            
            logger.info(f"Batch render: added {added} projects to queue")
            self.lbl_status.setText(f"Added {added} projects to queue")

    def render_all_outdated(self):
        paths = []
        for i in range(self.project_list.count()):
            paths.append(self.project_list.item(i).data(Qt.ItemDataRole.UserRole))
        
        count = 0
        for path in paths:
            needs_render = not should_skip_render(path, self.config.settings)
            if needs_render:
                self.add_to_queue(path)
                count += 1
        
        if count == 0:
            QMessageBox.information(self, "Up to Date", "All projects are already up to date based on current settings.")
        else:
            logger.info(f"Added {count} outdated projects to render queue.")

    def add_to_queue(self, project_path):
        if project_path in self.pending_projects: return
        for p_info in self.active_processes.values():
            if p_info['path'] == project_path: return

        self.pending_projects.append(project_path)
        self.update_stats()
        self.refresh_library() 
            
        if not self.scheduler_timer.isActive():
            self.scheduler_timer.start(500)

    def schedule_jobs(self):
        self.max_workers = self.config.get('render_workers', 3)
        
        if not self.pending_projects and not self.active_processes:
            self.scheduler_timer.stop()
            self.lbl_status.setText("All Tasks Completed")
            self.progress_bar.setValue(0)
            self.btn_stop.setEnabled(False)
            logger.info("All rendering tasks finished.")
            self.refresh_library()
            return

        self.btn_stop.setEnabled(True)

        active_count = len(self.active_processes)
        if active_count < self.max_workers and self.pending_projects:
            next_project = self.pending_projects.pop(0)
            self.start_worker(next_project)
            self.update_stats()
            self.refresh_library()

    def start_worker(self, project_path):
        ffmpeg = detect_ffmpeg()
        if not ffmpeg:
            logger.error("FFmpeg missing. Cannot start worker.")
            self.update_item_status(os.path.basename(project_path), "error")
            return

        worker_config = self.config.settings.copy()
        worker_config['ffmpeg_path'] = ffmpeg
        
        proc = multiprocessing.Process(
            target=render_project_worker, 
            args=(project_path, self.render_queue, worker_config)
        )
        proc.start()
        
        self.active_processes[proc.pid] = {'proc': proc, 'path': project_path}
        logger.info(f"Started worker [PID {proc.pid}] for {os.path.basename(project_path)}")
        self.lbl_status.setText(f"Active Jobs: {len(self.active_processes)}")

    def _cleanup_stale_temp(self):
        """Remove any leftover temp directories from previous crashed runs."""
        temp_base = os.path.join(os.getcwd(), "temp")
        if not os.path.isdir(temp_base):
            return
        
        try:
            entries = os.listdir(temp_base)
            if entries:
                logger.info(f"[Startup] Cleaning up {len(entries)} stale temp director(ies) from previous runs")
                for entry in entries:
                    entry_path = os.path.join(temp_base, entry)
                    try:
                        if os.path.isdir(entry_path):
                            shutil.rmtree(entry_path, ignore_errors=True)
                            logger.info(f"[Startup] Removed stale temp: {entry}")
                    except Exception as e:
                        logger.warning(f"[Startup] Could not remove stale temp '{entry}': {e}")
                
                # Remove the base temp dir if now empty
                try:
                    if not os.listdir(temp_base):
                        os.rmdir(temp_base)
                except OSError:
                    pass
            else:
                # Empty temp dir, remove it
                try:
                    os.rmdir(temp_base)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"[Startup] Error during stale temp cleanup: {e}")

    def stop_all_renders(self):
        logger.info("Stopping all renders...")
        self.pending_projects.clear()
        
        for pid, proc_data in list(self.active_processes.items()):
            project_path = proc_data['path']
            project_name = os.path.basename(project_path)
            
            try:
                proc_data['proc'].terminate()
                proc_data['proc'].join(timeout=1.0)
                if proc_data['proc'].is_alive():
                    proc_data['proc'].kill()
            except Exception as e:
                logger.error(f"Error stopping process {pid}: {e}")
            
            # Force-clean this project's local temp dir since the worker's 
            # finally block may not execute after kill()
            temp_dir = os.path.join(os.getcwd(), "temp", project_name)
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"Forced cleanup of temp dir for stopped project: {project_name}")
                except Exception as e:
                    logger.warning(f"Could not clean temp for {project_name}: {e}")
        
        self.active_processes.clear()
        self.scheduler_timer.stop()
        self.update_stats()
        self.refresh_library()
        self.lbl_status.setText("Stopped by user")
        self.btn_stop.setEnabled(False)

    def process_queue(self):
        try:
            while True:
                try:
                    msg = self.render_queue.get_nowait()
                except queue.Empty:
                    break
                
                if isinstance(msg, tuple) and len(msg) > 0 and msg[0] == "LOG":
                    self.log_text.append(msg[1])
                    continue

                self.handle_message(msg)
                
        except Exception as e:
            logger.error(f"Queue processing error: {e}")

        active_pids = list(self.active_processes.keys())
        for pid in active_pids:
            proc_data = self.active_processes[pid]
            proc = proc_data['proc']
            
            if not proc.is_alive():
                project_name = os.path.basename(proc_data['path'])
                exit_code = proc.exitcode
                
                logger.info(f"Worker [PID {pid}] finished with code {exit_code}")
                
                if exit_code == 0:
                    self.update_item_status(project_name, "ready")
                else:
                    self.update_item_status(project_name, "error")
                    logger.error(f"Worker {pid} crashed.")

                del self.active_processes[pid]
                self.update_stats()
                
                if self.scheduler_timer.isActive():
                    self.schedule_jobs()
                else:
                    self.schedule_jobs()

    def handle_message(self, msg):
        if not isinstance(msg, tuple): return

        if len(msg) >= 4 and msg[0] == "CHUNK_PROGRESS":
            _, project_name, current, total, message = msg[:5]
            self.lbl_status.setText(f"{project_name}: {message}")
            self.update_item_status(project_name, "rendering", f"Audio: {message}")
            return

        if len(msg) > 0 and msg[0] == "ERROR":
            error_str = str(msg[1]) if len(msg) > 1 else "Unknown error"
            project_name = "Unknown"
            
            if len(msg) == 3 and isinstance(msg[1], str):
                project_name = msg[1]
                error_str = msg[2]
            elif len(msg) == 2:
                error_str = msg[1]
                if self.active_processes:
                    if len(self.active_processes) == 1:
                        project_name = os.path.basename(list(self.active_processes.values())[0]['path'])
            
            logger.error(f"Render Error [{project_name}]: {error_str}")
            self.update_item_status(project_name, "error")
            return

        try:
            project_name, status, data = msg
        except ValueError:
            return
            
        self.update_item_status(project_name, "rendering")
        
        if isinstance(status, int) and isinstance(data, int):
            if data > 0:
                pct = int((status / data) * 100)
                self.lbl_status.setText(f"Rendering {project_name}: Slide {status}/{data}")
                self.progress_bar.setValue(pct)
                
                progress_str = f"Slide {status}/{data} ({pct}%)"
                self.update_item_status(project_name, "rendering", progress_str)
                
        elif isinstance(status, str):
            if status == "PREPARING AUDIO":
                self.lbl_status.setText(f"{project_name}: Preparing Audio...")
                self.progress_bar.setValue(10)
                self.update_item_status(project_name, "rendering", "Generating Audio...")
            elif status == "RENDERING VIDEO":
                self.lbl_status.setText(f"{project_name}: Capturing Video...")
                self.progress_bar.setValue(30)
                self.update_item_status(project_name, "rendering", "Capturing Video...")
            elif status == "SCANNING SLIDES":
                self.lbl_status.setText(f"{project_name}: Scanning project files...")
                self.progress_bar.setValue(5)
                self.update_item_status(project_name, "rendering", "Scanning...")
            elif status == "GENERATING AUDIO":
                self.lbl_status.setText(f"{project_name}: Generating audio batch...")
                self.progress_bar.setValue(20)
                self.update_item_status(project_name, "rendering", "Processing Audio...")
            elif status == "BUILDING TIMELINE":
                self.lbl_status.setText(f"{project_name}: Assembling timeline...")
                self.progress_bar.setValue(30)
                self.update_item_status(project_name, "rendering", "Finalizing Prep...")
            elif status == "FINALIZING":
                self.lbl_status.setText(f"{project_name}: Finalizing...")
                self.progress_bar.setValue(90)
                self.update_item_status(project_name, "rendering", "Finalizing Video...")

    def update_item_status(self, project_name, status, progress_text=""):
        """Updates the widget during rendering."""
        for i in range(self.project_list.count()):
            item = self.project_list.item(i)

            path = item.data(Qt.ItemDataRole.UserRole)
            
            if os.path.basename(path) == project_name:
                widget = self.project_list.itemWidget(item)
                if widget:
                    pct = 0
                    detail = progress_text if progress_text else "Updating..."
                    
                    if status == "rendering":
                        if "%" in progress_text:
                            try:
                                pct = int(progress_text.split("(")[-1].replace("%",""))
                                detail = "Rendering..." 
                            except: 
                                detail = "Processing..."
                    elif status == "ready":
                        detail = "Completed"
                    elif status == "error":
                        detail = "Failed"

                    widget.update_status(status, pct, detail)
                break

    def update_stats(self):
        self.lbl_active_jobs.setText(f"Active: {len(self.active_processes)}")
        self.lbl_queue_count.setText(f"Queue: {len(self.pending_projects)}")

    def closeEvent(self, event):
        """Handle application close: ensure all workers are stopped."""
        if self.active_processes or self.pending_projects:
            reply = QMessageBox.question(
                self, 
                "Confirm Exit",
                "Renders are still in progress. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

            logger.info("Stopping all workers for application exit...")
            self.stop_all_renders()
            
            QTimer.singleShot(500, lambda: event.accept())
        else:
            event.accept()

if __name__ == "__main__":
    # PySide6 supports AA_EnableHighDpiScaling directly
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())