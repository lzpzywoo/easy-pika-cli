# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Windows GUI release (onedir)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
root = Path(SPECPATH).resolve().parent

ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")

a = Analysis(
    [str(root / "gui.py")],
    pathex=[str(root)],
    binaries=ctk_binaries,
    datas=ctk_datas,
    hiddenimports=[
        "pikpak_downloader",
        "pikpak_downloader.gui",
        "pikpak_downloader.cli",
        "pikpak_downloader.downloader",
        "pikpak_downloader.download_manager",
        "pikpak_downloader.progress",
        "pikpak_downloader.session",
        "pikpak_downloader.token_helpers",
        "pikpak_downloader.api_helpers",
        "pikpakapi",
        "pikpakapi.PikpakException",
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "sniffio",
        "certifi",
        "rich",
        "charset_normalizer",
        "idna",
    ]
    + ctk_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="easy-pika-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="easy-pika-cli",
)
