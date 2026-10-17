from src.patches.customization_contract import validate_customization_contract

validate_customization_contract()

from src.patches.pre_config_patch import install_pre_config_patch  # noqa: E402
from src.patches.qfluent_mute_promo_patch import install_mute_promo_patch  # noqa: E402

install_pre_config_patch()
install_mute_promo_patch()

from src.config import config  # noqa: E402
from src.patches.startup_patches import install_startup_patches  # noqa: E402

if __name__ == "__main__":
    install_startup_patches()
    import ok

    ok = ok.OK(config)
    ok.start()
