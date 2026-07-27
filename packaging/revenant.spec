# PyInstaller spec for the `revenant` CLI — a self-contained, single-file executable.
#
# Build (from the repo root, on the target OS):
#   pip install pyinstaller
#   pip install -e packages/nerva-core -e packages/nerva-agent -e packages/revenant-cli
#   pyinstaller packaging/revenant.spec
#   -> dist/revenant   (macOS/Linux)   or   dist/revenant.exe  (Windows)
#
# The CLI is pure standard library, so the bundle is small. It still needs Ollama
# running on the target machine at runtime (the LLM is not bundled).
from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("nerva_core")
    + collect_submodules("nerva_agent")
    + collect_submodules("revenant_cli")
)

import os
_HERE = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(_HERE, "_revenant_entry.py")],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Keep the private companion + heavy/unused libs out of the CLI bundle.
        "aibot_app",
        "tkinter",
        "test",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="revenant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # a CLI — keep the console
    disable_windowed_traceback=False,
    target_arch=None,      # native arch (arm64 on Apple Silicon)
    codesign_identity=None,
    entitlements_file=None,
)
