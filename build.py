"""PyInstaller 빌드: python build.py  →  dist/Listener/Listener.exe (Windows) / dist/Listener.app (macOS)"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from listener.app import COLORS, State, make_icon

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "build_assets"


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = make_icon(COLORS[State.IDLE], 256)
    if sys.platform == "darwin":
        icon.save(ASSETS / "listener.icns")
    else:
        icon.save(ASSETS / "listener.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "listener.spec"], cwd=ROOT, check=True)

    if sys.platform == "darwin":
        app = ROOT / "dist" / "Listener.app"
        # Ad-hoc signatures change every build, so macOS drops Accessibility/Input Monitoring grants.
        # A real identity (e.g. "Apple Development: ...") keeps them across rebuilds.
        identity = os.environ.get("LISTENER_SIGN_IDENTITY")
        if identity:
            subprocess.run(["codesign", "--force", "--deep", "--sign", identity, str(app)], check=True)
        else:
            print("LISTENER_SIGN_IDENTITY not set: ad-hoc signed, permissions must be re-granted after each build")
        # config/log live in ~/Library/Application Support/Listener (see config.py)
        print(f"\nBuilt: {app}")
        return
    dist = ROOT / "dist" / "Listener"
    shutil.copy(ROOT / "config.json", dist / "config.json")
    print(f"\nBuilt: {dist / 'Listener.exe'}")


if __name__ == "__main__":
    main()
