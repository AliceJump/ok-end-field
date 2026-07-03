from src.config import config
from src.startup_patches import install_startup_patches

if __name__ == '__main__':
    config = config
    config['debug'] = True
    install_startup_patches()
    import ok
    ok = ok.OK(config)
    ok.start()
