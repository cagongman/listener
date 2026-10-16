# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files

MAC = sys.platform == "darwin"

datas, binaries, hiddenimports = [], [], []
pkgs = ("ctranslate2", "onnxruntime")
pkgs += ("pynput",) if MAC else ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")
for pkg in pkgs:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("faster_whisper")
hiddenimports += ["pystray._darwin"] if MAC else ["pystray._win32", "PIL._tkinter_finder"]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Listener",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=not MAC,
    icon="build_assets/listener.icns" if MAC else "build_assets/listener.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Listener",
)
if MAC:
    app = BUNDLE(
        coll,
        name="Listener.app",
        icon="build_assets/listener.icns",
        bundle_identifier="com.cagongman.listener",
        info_plist={
            "LSUIElement": True,  # menu bar only, no Dock icon
            "NSMicrophoneUsageDescription": "음성을 녹음해 텍스트로 변환합니다.",
        },
    )
