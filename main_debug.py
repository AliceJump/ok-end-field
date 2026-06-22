import sys
from pathlib import Path

_src_path = str(Path(__file__).resolve().parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from config import config
from patches.startup import install_startup_patches

if __name__ == '__main__':
    config = config
    config['debug'] = True
    install_startup_patches()
    import ok
    ok = ok.OK(config)
    ok.start()
