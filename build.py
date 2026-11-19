"""Build Windows ListenerSuite or the existing macOS Listener.app."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from listener.ui import make_icon

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "build_assets"


def main() -> None:
    mac = sys.platform == "darwin"
    dist = ROOT / "dist" / "ListenerSuite"
    saved_configs = {} if mac else {
        name: (dist / name).read_bytes() for name in ("config.json", "hermes.json")
        if (dist / name).is_file()}
    ASSETS.mkdir(exist_ok=True)
    icon = make_icon((60, 160, 80), 256)
    if mac:
        icon.save(ASSETS / "listener.icns")
    else:
        icon.save(ASSETS / "listener.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "listener.spec"],
                       cwd=ROOT, check=True)
    finally:
        # Preserve user settings even if COLLECT replaced the output before a failure.
        if saved_configs:
            dist.mkdir(parents=True, exist_ok=True)
            for name, data in saved_configs.items():
                (dist / name).write_bytes(data)

    if mac:
        app = ROOT / "dist" / "Listener.app"
        identity = os.environ.get("LISTENER_SIGN_IDENTITY")
        if identity:
            subprocess.run(["codesign", "--force", "--deep", "--sign", identity, str(app)], check=True)
        else:
            print("LISTENER_SIGN_IDENTITY not set: ad-hoc signed, permissions must be re-granted after each build")
        print(f"Built: {app}")
        return
    if not (dist / "config.json").exists():
        shutil.copy(ROOT / "config.json", dist / "config.json")
    shutil.copy(ROOT / "listener-hermes" / "config.example.json", dist / "hermes.example.json")
    print(f"Built: {dist / 'Listener.exe'}")


if __name__ == "__main__":
    main()
