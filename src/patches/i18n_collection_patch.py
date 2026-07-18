from __future__ import annotations

from functools import wraps


_PATCH_INSTALLED = False


def install_i18n_collection_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import App

    original_tr = App.tr

    @wraps(original_tr)
    def tr_without_numeric_ids(self, key):
        translated = original_tr(self, key)
        if self.to_translate is not None and isinstance(key, str) and key.strip().isdigit():
            self.to_translate.discard(key)
        return translated

    App.tr = tr_without_numeric_ids
    _PATCH_INSTALLED = True
