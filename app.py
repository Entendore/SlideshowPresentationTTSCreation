# app.py
"""
Application Entry Point.

Handles multiprocessing safety guards and environment configuration
BEFORE importing the heavy GUI and ML modules.
"""

import sys
import os
import multiprocessing
import logging


def main():
    # ── CRITICAL: Multiprocessing Guards ──────────────────────────────────
    # Required on Windows. Without this, the child process re-runs the
    # main script, which would otherwise import PySide6 and create a
    # second blank window.
    multiprocessing.freeze_support()

    # If this is a child process spawned by multiprocessing, bail out
    # immediately. The multiprocessing machinery will call the target
    # function (render_project_worker) directly.
    if multiprocessing.parent_process() is not None:
        return

    # ── Early Configuration & Logging ─────────────────────────────────────
    # Must happen BEFORE importing torch, transformers, or PySide6.
    import utils
    utils.setup_logging()
    logger = logging.getLogger(__name__)
    
    # ── Configure Global Cache Directory ───────────────────────────────────
    from config import AppConfig
    config = AppConfig()

    _cache_dir = config.get("hf_cache_dir", os.path.join(os.getcwd(), "models_cache"))
    if not os.path.isabs(_cache_dir):
        _cache_dir = os.path.abspath(_cache_dir)

    os.environ["HF_HOME"]                = _cache_dir
    os.environ["HUGGINGFACE_HUB_CACHE"]  = _cache_dir
    os.environ["TRANSFORMERS_CACHE"]     = _cache_dir
    os.environ["HF_DATASETS_CACHE"]      = os.path.join(_cache_dir, "datasets")
    os.environ["HF_HUB_SYMLINKS"]        = "0"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"]       = "0"

    logger.info(f"[App] Global cache configured to: {_cache_dir}")

    # ── Import GUI & Run Application ──────────────────────────────────────
    from PySide6.QtWidgets import QApplication
    from gui import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    # Backup cleanup if the event loop exits without closeEvent firing
    app.aboutToQuit.connect(window._on_about_to_quit)

    ret = app.exec()
    sys.exit(ret)


if __name__ == "__main__":
    main()