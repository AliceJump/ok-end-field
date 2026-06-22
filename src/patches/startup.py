from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from patches.cascade_dropdown import install_cascade_dropdown_patch
    from patches.log_upload import install_log_upload_patch
    from patches.ocr_text_fix import install_ocr_text_fix_patch

    install_cascade_dropdown_patch()
    install_log_upload_patch()
    install_ocr_text_fix_patch()
    _PATCH_INSTALLED = True
