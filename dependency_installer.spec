# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


pip_hiddenimports = collect_submodules('pip')
pip_datas = collect_data_files('pip', include_py_files=False)

a = Analysis(
    ['scripts/arkloop_dependency_installer.py'],
    pathex=['.'],
    binaries=[],
    datas=pip_datas,
    hiddenimports=pip_hiddenimports,
    hookspath=[],
    runtime_hooks=['hook/pyi_rth_pip_distlib.py'],
    excludes=['torch', 'cv2', 'numpy', 'maa', 'webview'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ArkLoopDependencyInstaller',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    runtime_tmpdir='dependencies/.installer-runtime',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
