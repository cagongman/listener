"""PyInstaller 빌드: python build.py  →  dist/Listener/Listener.exe"""
import shutil
import subprocess
import sys
from pathlib import Path

from listener.app import COLORS, State, make_icon

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "build_assets"


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    make_icon(COLORS[State.IDLE], 256).save(ASSETS / "listener.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "listener.spec"], cwd=ROOT, check=True)

    dist = ROOT / "dist" / "Listener"
    shutil.copy(ROOT / "config.json", dist / "config.json")
    print(f"\nBuilt: {dist / 'Listener.exe'}")


if __name__ == "__main__":
    main()
