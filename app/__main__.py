"""Einstiegspunkt: python -m app

Startet den lokalen Server, oeffnet die Oberflaeche und beendet sich wieder,
sobald laengere Zeit kein Fenster mehr offen ist.

Standardmaessig wird ein Chromium-Fenster im App-Modus geoeffnet
(`--app=<url>`): keine Adressleiste, keine Tabs, eigenes Fenster und
eigener Taskleisten-Eintrag. Ist kein Chromium zu finden, faellt es auf
den Standardbrowser zurueck.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import server
from .config import fingerprint
from . import config

#: Kandidaten fuer den App-Modus, in dieser Reihenfolge. Unter Windows 11
#: ist Edge immer vorhanden, daher zuerst.
_WINDOWS_BROWSERS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)
_POSIX_BROWSERS = (
    "microsoft-edge", "microsoft-edge-stable",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser", "vivaldi",
)


def _find_chromium() -> str | None:
    if os.name == "nt":
        for candidate in _WINDOWS_BROWSERS:
            path = Path(os.path.expandvars(candidate))
            if path.is_file():
                return str(path)
        return shutil.which("msedge") or shutil.which("chrome")
    for name in _POSIX_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def open_window(url: str, size: str = "1280,900", app_mode: bool = True) -> str:
    """Oeffnet die Oberflaeche. Gibt zurueck, wie es geklappt hat."""
    if app_mode:
        exe = _find_chromium()
        if exe:
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", f"--window-size={size}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return f"App-Fenster ({Path(exe).stem})"
            except OSError:
                pass  # unten faellt es auf den Standardbrowser zurueck
    webbrowser.open(url)
    return "Standardbrowser"


def _is_running(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _running_fingerprint(url: str) -> str | None:
    """Fingerabdruck des laufenden Servers. None, wenn er ihn nicht kennt
    (dann ist er aelter als dieses Feature und damit sicher veraltet)."""
    try:
        with urllib.request.urlopen(url + "api/version", timeout=1.0) as resp:
            return json.loads(resp.read()).get("fingerprint")
    except Exception:
        return None


def _stop_running(url: str, host: str, port: int, timeout: float = 8.0) -> bool:
    """Den laufenden Server bitten, sich zu beenden, und darauf warten."""
    try:
        req = urllib.request.Request(url + "api/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2.0).read()
    except Exception:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_running(host, port):
            return True
        time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todo", description="Persoenliches Todo-Tool")
    parser.add_argument(
        "--window", choices=("app", "tab", "none"), default="app",
        help="app = rahmenloses Chromium-Fenster (Default), tab = Standardbrowser, none = nichts",
    )
    parser.add_argument("--no-browser", action="store_true",
                        help="Kurzform fuer --window none")
    parser.add_argument("--size", help="Fenstergroesse, z.B. 1280,900 (Default aus config.toml)")
    parser.add_argument("--port", type=int, help="Port ueberschreiben")
    parser.add_argument("--restart", action="store_true",
                        help="Laufende Instanz beenden und neu starten")
    args = parser.parse_args(argv)

    mode = "none" if args.no_browser else args.window
    cfg = config.load()
    size = args.size or cfg["window"]["size"]
    if args.port:
        cfg["server"]["port"] = args.port
    host, port = cfg["server"]["host"], cfg["server"]["port"]
    url = f"http://{host}:{port}/"

    # Laeuft schon eine Instanz? Dann nur ein Fenster oeffnen, statt am
    # belegten Port zu scheitern - ausser der laufende Server arbeitet mit
    # aelterem Code als dem auf der Platte. Sonst sitzt man stundenlang auf
    # einem veralteten Prozess, weil das offene Fenster ihn am Leben haelt
    # und ein zweiter Doppelklick ihn nie ersetzt.
    if _is_running(host, port):
        mine = fingerprint()
        theirs = _running_fingerprint(url)
        stale = args.restart or theirs != mine
        if not stale:
            print(f"Laeuft bereits: {url}")
            if mode != "none":
                open_window(url, size, mode == "app")
            return 0

        grund = "angefordert" if args.restart else "Code hat sich geaendert"
        print(f"Neustart ({grund}) – beende die laufende Instanz …")
        if not _stop_running(url, host, port):
            print(f"Die laufende Instanz auf Port {port} reagiert nicht.")
            print("Bitte den Prozess 'pythonw -m app' beenden und erneut starten.")
            return 1
        print("Hinweis: Der Verlauf fuer Rueckgaengig beginnt damit neu.")

    httpd, _activity = server.build(cfg)
    print(f"Datendatei:  {cfg['data']['path']}")
    print(f"Oberflaeche: {url}")
    print(f"Auto-Stop nach {cfg['server']['idle_shutdown_minutes']} min ohne offenes "
          f"Fenster, Strg+C beendet sofort.")

    if mode != "none":
        def launch() -> None:
            print("Geoeffnet als:", open_window(url, size, mode == "app"))
        threading.Timer(0.4, launch).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
