# PyInstaller spec for Sara's desktop-evaluation build (onedir).
#
# Build: pyinstaller sara-wallet.spec
# Output: dist/sara-wallet/ — a folder containing the sara-wallet
# executable plus its bundled runtime; zip that whole folder for release.
#
# See docs/architecture.md and backend/desktop_launcher.py for how a
# frozen build resolves its bundled resources and user data directory
# differently from a source run.
from PyInstaller.utils.hooks import collect_all, copy_metadata

block_cipher = None

datas = [
    ("index.html", "."),
    (".env", "."),
    ("backend/images", "images"),
    ("backend/app/tools/names/registry_abi.json", "app/tools/names"),
    ("backend/app/tools/tokens/templates", "app/tools/tokens/templates"),
]
binaries = []
hiddenimports = [
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.loops",
    "uvicorn.loops.auto",
]

# Packages with dynamic-import patterns PyInstaller's static analysis
# routinely misses (provider plugins, lazy backends, native extensions).
# Expect to extend this list from real "ModuleNotFoundError" failures
# surfaced by the first CI build — normal for PyInstaller, not a sign
# the approach is wrong.
for pkg in (
    "litellm",
    "openai",
    "web3",
    "eth_account",
    "solana",
    "solders",
    "tronpy",
    "coincurve",
    "cryptography",
    "pydantic",
    "tiktoken",
    # tiktoken_ext is a *separate* top-level namespace package (not a
    # tiktoken submodule) that registers encodings like cl100k_base via
    # dynamic plugin scanning — invisible to PyInstaller's static import
    # analysis, so tiktoken.get_encoding() fails with "Unknown encoding"
    # unless this is pulled in explicitly. Confirmed by a real local build.
    "tiktoken_ext",
    "tokenizers",
    "huggingface_hub",
):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# Transitive dependencies that call importlib.metadata.version(...) at
# import time (a common pattern for optional/version-gated behavior) need
# their distribution metadata copied explicitly — collect_all() above only
# covers the packages named there, not *their* transitive deps. Confirmed
# by a real local build failing on py_ecc (pulled in via eth_account ->
# eth_keyfile) with "No package metadata was found". Wrapped since a wrong
# or unpublished distribution name must not fail the whole build.
for pkg in (
    "eth_abi", "eth_account", "eth_hash", "eth_keyfile", "eth_keys",
    "eth_rlp", "eth_typing", "eth_utils", "hexbytes", "rlp", "py_ecc",
    "web3", "pydantic", "litellm", "huggingface_hub", "jsonschema",
    "cytoolz", "toolz", "sqlalchemy", "tqdm", "pytz", "urllib3",
    "websockets", "zipp", "markupsafe", "fsspec", "click", "numpy",
    "pandas", "tiktoken", "tokenizers", "openai", "attrs", "coincurve",
    "cryptography",
):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis(
    ["backend/desktop_launcher.py"],
    pathex=["backend"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sara-wallet",
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
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="sara-wallet",
)
