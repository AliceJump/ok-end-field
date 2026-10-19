from src.patches.pre_config_patch import install_pre_config_patch
from src.patches.qfluent_mute_promo_patch import install_mute_promo_patch

install_pre_config_patch()
install_mute_promo_patch()

from src.config import config  # noqa: E402
from src.patches.startup_patches import install_startup_patches  # noqa: E402

if __name__ == "__main__":
    install_startup_patches()
    import ok

    ok = ok.OK(config)
    # Config paths become available after OK is initialized. Recover only
    # abandoned temporary launcher parents recorded by this application.
    try:
        import logging

        from src.core.temporary_parent_recovery import recover_owned_parents

        recover_owned_parents(logging.getLogger("ok-end-field.launcher"))
    except Exception:
        import logging

        logging.getLogger("ok-end-field.launcher").exception("Temporary launcher recovery failed")
    ok.start()
