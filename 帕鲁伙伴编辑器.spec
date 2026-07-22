# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[(str(root / "palworld_save_tools" / "lib" / "windows" / "ooz.pyd"), "palworld_save_tools/lib/windows")],
    datas=[
        (str(root / "palworld_pal_edit" / "resources" / "data"), "palworld_pal_edit/resources/data"),
        (str(root / "palcalc_db.json"), "."),
        (str(root / "app.ico"), "."),
    ],
    hiddenimports=["ooz"],
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
    name="帕鲁伙伴编辑器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(root / "app.ico")],
)
