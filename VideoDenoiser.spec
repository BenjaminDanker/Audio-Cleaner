# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# Collect data files from the 'df' package
df_datas = collect_all('df')[0]

# Specify the models directory to be included
# The first element is the source path (relative to the .spec file)
# The second element is the destination path inside the bundled app
model_datas = [('models', 'models')]

a = Analysis(
    ['deep.py'],
    pathex=[],
    binaries=[],
    datas=df_datas + model_datas, # Combine the df data and the models data
    hiddenimports=[],
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
    name='VideoDenoiser',
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
