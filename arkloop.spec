# -*- mode: python ; coding: utf-8 -*-
import os, maa
from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_data_files,
    collect_dynamic_libs,
)

maa_pkg_dir = os.path.dirname(maa.__file__)
# Bundle the maa package directory wholesale.  PyInstaller picks up the .py
# files into PYZ on its own, but the top-level DLLs (MaaFramework.dll,
# MaaAdbControlUnit.dll, DirectML.dll, ...) and bin/agent subdirs need to be
# copied as datas — there's no built-in hook for maa.
maa_datas = [(maa_pkg_dir, 'maa')]
maa_hiddenimports = collect_submodules('maa')

hiddenimports = []
hiddenimports += collect_submodules('webview')
hiddenimports += collect_submodules('clr_loader')
hiddenimports += ['pythonnet']
hiddenimports += maa_hiddenimports
# websocket-client (the `websocket` module) loads many submodules lazily
# (websocket._app, websocket._core, websocket._abnf, ...); collect them so the
# WS time source works in the frozen bundle.
hiddenimports += collect_submodules('websocket')
# NumPy 2.x 需要显式收集 _core 子模块，否则打包后会报
# No module named 'numpy._core._exceptions' 等 C-ext 导入错误。
hiddenimports += collect_submodules('numpy._core')
hiddenimports += collect_submodules('numpy.lib')

# PyTorch (optional GPU avatar matching).  Collect submodules + data + dynamic
# libs (CUDA runtime DLLs) so the batched conv2d works frozen.  Heavy: adds
# ~1.5-2.5 GB.  If torch isn't installed, skip silently — the matcher falls
# back to its CPU loop, so the bundle still works.
try:
    import torch as _torch  # noqa: F401
    torch_hidden = collect_submodules('torch')
    torch_datas = collect_data_files('torch', include_py_files=False)
    torch_bins = collect_dynamic_libs('torch')
    _torch_version = getattr(_torch, '__version__', '?')
    print(f"[spec] collecting torch {_torch_version}: "
          f"{len(torch_hidden)} submodules, {len(torch_datas)} data, "
          f"{len(torch_bins)} libs")
except Exception as _exc:
    torch_hidden = []
    torch_datas = []
    torch_bins = []
    print(f"[spec] torch not collected: {_exc}")
hiddenimports += torch_hidden

# Tesseract-OCR is optional: tesserocr has no Python 3.12 wheel and the live
# pipeline degrades gracefully without it (pause-detection OCR only).  Only
# bundle it + the tessdata runtime hook when the folder is staged at root.
_has_tesseract = os.path.isdir(os.path.join(SPECPATH, 'Tesseract-OCR'))

datas = [
    ('ui/dist',             'ui/dist'),
    ('resource',            'resource'),
    ('calibration',         'calibration'),
    ('hook',                'hook'),
    ('config.example.json', '.'),
    ('src/maa/nodes',                  'src/maa/nodes'),    # MAA pipeline + OCR model weights
    ('src/maa/prts_plus_override.json', 'src/maa'),         # project-specific ROI overrides
] + maa_datas + torch_datas
binaries = list(torch_bins)
if _has_tesseract:
    datas.append(('Tesseract-OCR', 'Tesseract-OCR'))

runtime_hooks = ['pyi_rth_tessdata.py'] if _has_tesseract else []

a = Analysis(
    ['scripts/arkloop_webview.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='ArkLoop',
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ArkLoop',
)

# Copy user-facing helper files to the package root (next to ArkLoop.exe).
# datas with dest '.' land in _internal/ in PyInstaller 6.x, so these must
# be copied separately after COLLECT.
import shutil as _shutil
_dist_root = os.path.join(DISTPATH, 'ArkLoop')
for _f in ['list_mumu_windows.cmd', 'HOWTOUSE.md', 'PRTS+键鼠方案示例.json']:
    if os.path.isfile(_f):
        _shutil.copy(_f, os.path.join(_dist_root, _f))

# Copy sample timeline to timelines/ so it's available on first launch.
import pathlib as _pathlib
_tl_dir = _pathlib.Path(_dist_root) / 'timelines'
_tl_dir.mkdir(exist_ok=True)
if os.path.isfile('sample1-7.json'):
    _shutil.copy('sample1-7.json', _tl_dir / 'sample1-7.json')
