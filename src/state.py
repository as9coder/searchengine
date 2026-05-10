import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path("worker_state.json")


def load_state() -> dict:
    """Load persisted crawler state."""
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load state file: {e}")
        return {}


def save_state(state: dict):
    """Persist crawler state to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save state file: {e}")
