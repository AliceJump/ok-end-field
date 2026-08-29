from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_window_patch():
    """Require the framework to wait for the configured game resolution."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.gui.StartController import StartController

    from src.config import config

    min_size = config.get("supported_resolution", {}).get("min_size")
    if isinstance(min_size, (tuple, list)) and len(min_size) == 2:
        try:
            StartController.STARTED_WINDOW_MIN_SIZE = (int(min_size[0]), int(min_size[1]))
        except (TypeError, ValueError):
            pass

    _PATCH_INSTALLED = True
