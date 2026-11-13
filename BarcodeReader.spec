# -*- mode: python ; coding: utf-8 -*-

from importlib.metadata import distributions
from pathlib import Path


license_datas = []
for distribution in distributions():
    package_name = distribution.metadata.get("Name", "unknown").replace("/", "_")
    for package_file in distribution.files or ():
        relative_path = package_file.as_posix().lower()
        filename = package_file.name.lower()
        if ".dist-info/licenses/" not in relative_path and not (
            ".dist-info/" in relative_path and filename.startswith(("license", "copying"))
        ):
            continue
        source = Path(distribution.locate_file(package_file))
        if source.is_file():
            license_datas.append((str(source), f"licenses/{package_name}"))

project_license_dir = Path(SPECPATH) / "licenses"
for source in project_license_dir.iterdir():
    if source.is_file():
        license_datas.append((str(source), "licenses"))

a = Analysis(
    ['BarcodeReaderUI.py'],
    pathex=[],
    binaries=[],
    datas=license_datas,
    hiddenimports=['pynput.keyboard._win32', 'pynput._util.win32'],
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
    name='BarcodeReader',
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
)
