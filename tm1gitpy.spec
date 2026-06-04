# -*- mode: python ; coding: utf-8 -*-
# Authoritative PyInstaller spec for standalone tm1gitpy binaries (onefile).

hiddenimports = [
    "_socket",
    "socket",
    "select",
    "_multiprocessing",
    "multiprocessing",
    "multiprocessing.context",
    "multiprocessing.reduction",
    "multiprocessing.resource_tracker",
    "multiprocessing.popen_spawn_posix",
    "multiprocessing.popen_spawn_win32",
]

a = Analysis(
    ["tm1_git_py/main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tm1gitpy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
