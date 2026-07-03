from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.log_upload_patch import install_log_upload_patch
    from src.ocr_text_fix_patch import install_ocr_text_fix_patch
    from src.cascade_dropdown_patch import install_cascade_dropdown_patch

    install_cascade_dropdown_patch()
    install_log_upload_patch()
    install_ocr_text_fix_patch()
    _PATCH_INSTALLED = True
