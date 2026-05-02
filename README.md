# AI Slideshow Renderer

A PyQt6-based application for generating slideshow videos with AI-powered text-to-speech.

## Project Structure

```
tts_project/
├── main.py              # Main application entry point
├── config.py            # Application configuration management
├── utils.py             # Utility functions
├── text_chunker.py      # Long text chunking for TTS
├── engines.py           # Video rendering engine
├── dialogs.py           # PyQt6 UI dialogs and widgets
├── README.md            # This file
└── backends/
    ├── __init__.py      # Backend factory
    ├── base.py          # Abstract base class for TTS backends
    ├── edge.py          # Microsoft Edge TTS backend (free, online)
    └── qwen3.py         # Qwen3 TTS backend (local, requires GPU)
```

## Requirements

- Python 3.10+
- PyQt6
- edge-tts (for Edge TTS backend)
- torch, soundfile (for Qwen3 backend)
- playwright (for video rendering)
- ffmpeg

## Installation

```bash
pip install PyQt6 edge-tts torch soundfile playwright
playwright install chromium
```

## Usage

```bash
python main.py
```

## TTS Backends

### Edge TTS (Recommended for free usage)
- Free online TTS using Microsoft Edge neural voices
- Multiple languages and voices
- Requires internet connection

### Qwen3 TTS (For local/high-quality usage)
- Local TTS with emotion and style control
- Voice cloning and design capabilities
- Requires GPU with ~4GB VRAM

## Configuration

Settings are stored in `settings.json` and can be modified through the Settings tab in the application.

## License

MIT License
