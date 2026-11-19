# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files
from pathlib import Path

root = Path(SPECPATH)

datas, binaries, hiddenimports = [], [], []
for pkg in ("ctranslate2", "onnxruntime", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("faster_whisper")
hiddenimports += ["pystray._win32", "PIL._tkinter_finder"]

a = Analysis(
    ["run.py", "run_speech.py", "run_hermes.py"],
    pathex=[str(root), str(root / "listener-hermes")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

entrypoints = {"run", "run_speech", "run_hermes"}
runtime_hooks = [entry for entry in a.scripts if entry[0] not in entrypoints]


def executable(name, entrypoint, admin=False):
    scripts = [entry for entry in a.scripts if entry[0] == entrypoint]
    assert len(scripts) == 1, f"Missing entry point: {entrypoint}"
    return EXE(pyz, runtime_hooks + scripts, [], exclude_binaries=True,
               name=name, debug=False, strip=False, upx=False, console=False,
               uac_admin=admin, icon=str(root / "build_assets" / "listener.ico"))


exe = executable("Listener", "run", admin=True)
speech = executable("ListenerSpeech", "run_speech")
hermes = executable("ListenerHermes", "run_hermes")
coll = COLLECT(
    exe,
    speech,
    hermes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ListenerSuite",
)
