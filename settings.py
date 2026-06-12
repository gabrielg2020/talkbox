"""Persisted user settings for talkbox (engine + voice)."""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Curated Kokoro voices. Prefix decides accent: 'a' = American, 'b' = British.
KOKORO_VOICES = [
    "af_heart",   # American female — Kokoro's best-rated voice
    "af_bella",   # American female
    "am_michael",  # American male
    "bf_emma",    # British female
    "bf_isabella",  # British female
    "bm_george",  # British male
    "bm_lewis",   # British male
]

DEFAULTS = {
    "engine": "kokoro",
    "voice": "af_heart",
    "resume": True,
    "speed": 1.0,    # read-along starting speed
    "volume": 100,   # read-along starting volume (%)
    "seek_step": 10,  # ←/→ seek amount (seconds)
    "scroll_pause": False,  # pause audio while scrolling (free look)
}


def load_settings():
    """Return saved settings merged over defaults; falls back cleanly if absent or corrupt."""
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULTS)
        return {**DEFAULTS, **saved}
    return dict(DEFAULTS)


def save_settings(settings):
    CONFIG_PATH.write_text(json.dumps(settings, indent=2) + "\n")
