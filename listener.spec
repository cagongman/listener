# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

MAC = sys.platform == "darwin"
root = Path(SPECPATH)
datas, binaries, hiddenimports = [], [], []
pkgs = ("ctranslate2", "onnxruntime")
pkgs += ("pynput", "mlx", "mlx_whisper") if MAC else ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")
for pkg in pkgs:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("faster_whisper")
datas += collect_data_files("listener", includes=["assets/sounds/*"])
hiddenimports += ["pystray._darwin"] if MAC else ["pystray._win32", "PIL._tkinter_finder"]

a = Analysis(
    ["run.py"] if MAC else ["run.py", "run_speech.py", "run_hermes.py"],
    pathex=[str(root), str(root / "listener-hermes")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], runtime_hooks=[], excludes=["torch", "tkinter", "matplotlib"], noarchive=False,
)
pyz = PYZ(a.pure)
entrypoints = {"run", "run_speech", "run_hermes"}
runtime_hooks = [entry for entry in a.scripts if entry[0] not in entrypoints]


def executable(name, entrypoint, admin=False):
    scripts = [entry for entry in a.scripts if entry[0] == entrypoint]
    assert len(scripts) == 1, f"Missing entry point: {entrypoint}"
    return EXE(pyz, runtime_hooks + scripts, [], exclude_binaries=True,
               name=name, debug=False, strip=False, upx=False, console=False,
               uac_admin=admin and not MAC,
               icon=str(root / "build_assets" / ("listener.icns" if MAC else "listener.ico")))


executables = [executable("Listener", "run", admin=True)]
if not MAC:
    executables += [executable("ListenerSpeech", "run_speech"), executable("ListenerHermes", "run_hermes")]
coll = COLLECT(*executables, a.binaries, a.datas, strip=False, upx=False,
               name="Listener" if MAC else "ListenerSuite")
if MAC:
    app = BUNDLE(coll, name="Listener.app", icon=str(root / "build_assets" / "listener.icns"),
                 bundle_identifier="com.cagongman.listener",
                 info_plist={"LSUIElement": True,
                             "NSMicrophoneUsageDescription": "음성을 녹음해 텍스트로 변환합니다."})
