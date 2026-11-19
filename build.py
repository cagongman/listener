"""Build three executables with one shared runtime in dist/ListenerSuite/."""
import shutil
import subprocess
import sys
from pathlib import Path

from listener.ui import make_icon

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "build_assets"


def main() -> None:
    dist = ROOT / "dist" / "ListenerSuite"
    # COLLECT replaces the output directory. Preserve user-edited configs first.
    saved_configs = {name: (dist / name).read_bytes() for name in ("config.json", "hermes.json")
                     if (dist / name).is_file()}
    ASSETS.mkdir(exist_ok=True)
    make_icon((60, 160, 80), 256).save(ASSETS / "listener.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "listener.spec"], cwd=ROOT, check=True)

    for name, data in saved_configs.items():
        (dist / name).write_bytes(data)
    if not (dist / "config.json").exists():
        shutil.copy(ROOT / "config.json", dist / "config.json")
    shutil.copy(ROOT / "listener-hermes" / "config.example.json", dist / "hermes.example.json")
    print(f"\nBuilt: {dist / 'Listener.exe'}")


if __name__ == "__main__":
    main()
