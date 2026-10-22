"""Tie the application-level world-position service to executor shutdown."""

from __future__ import annotations

import contextlib

_PATCH_INSTALLED = False


def install_position_service_patch() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.task.TaskExecutor import TaskExecutor

    from src.localization.position_service import shutdown_world_pose_service

    original_destroy = TaskExecutor.destroy

    def destroy(self):
        with contextlib.suppress(Exception):
            shutdown_world_pose_service(self)
        return original_destroy(self)

    TaskExecutor.destroy = destroy
    _PATCH_INSTALLED = True


__all__ = ["install_position_service_patch"]
