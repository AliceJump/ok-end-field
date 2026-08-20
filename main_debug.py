from src.patches.pre_config_patch import install_pre_config_patch

install_pre_config_patch()

from src.config import config  # noqa: E402
from src.patches.startup_patches import install_startup_patches  # noqa: E402

if __name__ == "__main__":
    config = config
    config["debug"] = True
    install_startup_patches()
    import ok

    ok = ok.OK(config)
    ok.start()
