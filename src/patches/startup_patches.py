from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.patches.log_upload_patch import install_log_upload_patch
    from src.patches.ocr_text_fix_patch import install_ocr_text_fix_patch
    from src.patches.cascade_dropdown_patch import install_cascade_dropdown_patch
    from src.patches.i18n_collection_patch import install_i18n_collection_patch

    install_cascade_dropdown_patch()
    install_i18n_collection_patch()
    install_log_upload_patch()
    install_ocr_text_fix_patch()
    _PATCH_INSTALLED = True
