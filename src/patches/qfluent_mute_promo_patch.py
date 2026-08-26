"""Suppress the qfluentwidgets promo banner printed at first import."""

import sys
from contextlib import ContextDecorator


class _BlackHoleStream:
    """A fake stream that discards all writes to the real *stream*."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return len(text)

    def flush(self):
        self._stream.flush()

    def isatty(self):
        return self._stream.isatty()


class muted_stdout(ContextDecorator):
    """Temporarily route ``sys.stdout`` writes into a black hole."""

    def __enter__(self):
        self._original = sys.stdout
        sys.stdout = _BlackHoleStream(self._original)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self._original
        return False


def install_mute_promo_patch():
    """This function will dry-import qfluentwidgets to suppress
    the promo banner printed at first import.
    """

    with muted_stdout():
        import qfluentwidgets  # noqa: F401
