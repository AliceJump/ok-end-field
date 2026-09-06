from __future__ import annotations

_PATCH_INSTALLED = False


def install_startup_patches():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.patches.cascade_dropdown_patch import install_cascade_dropdown_patch
    from src.patches.conditional_rotation_patch import install_conditional_rotation_patch
    from src.patches.dynamic_config_patch import install_dynamic_config_patch
    from src.patches.log_upload_patch import install_log_upload_patch
    from src.patches.ocr_text_fix_patch import install_ocr_text_fix_patch
    from src.patches.process_execute_patch import install_process_execute_patch
    from src.patches.qfluent_navigation_patch import install_qfluent_navigation_patch
    from src.patches.screenshot_sidecar_patch import install_screenshot_sidecar_patch
    from src.patches.startup_window_patch import install_startup_window_patch
    from src.patches.task_config_lock_patch import install_task_config_lock_patch
    from src.patches.win32_gdi_point_patch import install_win32_gdi_point_patch

    install_cascade_dropdown_patch()
    install_conditional_rotation_patch()
    install_dynamic_config_patch()
    install_log_upload_patch()
    install_ocr_text_fix_patch()
    install_process_execute_patch()
    install_screenshot_sidecar_patch()
    install_startup_window_patch()
    install_task_config_lock_patch()
    install_win32_gdi_point_patch()
    install_qfluent_navigation_patch()
    _PATCH_INSTALLED = True
