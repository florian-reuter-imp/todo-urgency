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

from . import autostart, server
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


#: Titel des App-Fensters - entspricht dem <title> der Seite. Ein normaler
#: Browser-Tab hiesse "Todo - Profil - Microsoft Edge" und wird so nicht
#: faelschlich mitgefangen.
WINDOW_TITLE = "Todo"


def focus_existing(title: str = WINDOW_TITLE) -> bool:
    """Vorhandenes App-Fenster nach vorn holen. True, wenn eines da war.

    Ein zweites Fenster auf dieselbe Datei bringt nichts und ist nach
    einem Neustart sogar gefaehrlich, weil es noch mit altem JavaScript
    laeuft. Also verhalten wir uns wie jedes andere Programm: Klick aufs
    Symbol holt das offene Fenster.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _param):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value == title:
                    found.append(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        if not found:
            return False

        handle = found[0]
        user32.ShowWindow(handle, 9)          # SW_RESTORE, falls minimiert

        # SetForegroundWindow allein genuegt nicht: Windows erlaubt es nur
        # Prozessen mit Vordergrundrecht. Der uebliche Weg ist, sich kurz
        # an den Eingabe-Thread des aktuellen Vordergrundfensters zu
        # haengen - dann gilt das Recht auch fuer uns.
        foreground = user32.GetForegroundWindow()
        ours = user32.GetWindowThreadProcessId(handle, None)
        theirs = user32.GetWindowThreadProcessId(foreground, None)
        if theirs and theirs != ours:
            user32.AttachThreadInput(theirs, ours, True)
            user32.SetForegroundWindow(handle)
            user32.AttachThreadInput(theirs, ours, False)
        else:
            user32.SetForegroundWindow(handle)

        if user32.GetForegroundWindow() != handle:
            # Zweiter Weg: das, was die Taskleiste selbst benutzt.
            try:
                user32.SwitchToThisWindow(handle, True)
            except Exception:
                pass
        if user32.GetForegroundWindow() != handle:
            # Letzter Versuch: kurz nach ganz oben und gleich wieder zurueck.
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            NOMOVE_NOSIZE = 0x0002 | 0x0001
            user32.SetWindowPos(handle, HWND_TOPMOST, 0, 0, 0, 0, NOMOVE_NOSIZE)
            user32.SetWindowPos(handle, HWND_NOTOPMOST, 0, 0, 0, 0, NOMOVE_NOSIZE)
            user32.SetForegroundWindow(handle)
        return True
    except Exception:
        return False


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


def _handle_autostart(args) -> int:
    try:
        if args.autostart == "install":
            where = autostart.install(args.with_window)
            print("Autostart eingerichtet:")
            print("  " + where)
            print("  Gestartet wird " + ("Server und Fenster." if args.with_window
                                         else "nur der Server, ohne Fenster."))
            print("Entfernen: python -m app --autostart remove")
            if os.name == "nt":
                print("Oder im Task-Manager unter 'Autostart' abschalten.")
        elif args.autostart == "remove":
            print("Autostart entfernt." if autostart.remove()
                  else "Es war kein Autostart eingerichtet.")
        else:
            info = autostart.status()
            print("Autostart: " + ("eingerichtet" if info["installed"] else "nicht eingerichtet"))
            for key in ("path", "target", "arguments", "working_dir"):
                if info.get(key):
                    print(f"  {key:12} {info[key]}")
    except Exception as exc:
        print(f"Autostart fehlgeschlagen: {type(exc).__name__}: {exc}")
        return 1
    return 0


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
    parser.add_argument("--new-window", action="store_true",
                        help="Zweites Fenster oeffnen, statt das vorhandene zu holen")
    parser.add_argument("--restart", action="store_true",
                        help="Laufende Instanz beenden und neu starten")
    parser.add_argument("--stop", action="store_true",
                        help="Laufende Instanz beenden")
    parser.add_argument("--shortcut", action="store_true",
                        help="Verknuepfung zum Oeffnen auf dem Desktop anlegen")
    parser.add_argument("--autostart", choices=("install", "remove", "status"),
                        help="Beim Anmelden mitstarten einrichten, entfernen oder pruefen")
    parser.add_argument("--with-window", action="store_true",
                        help="Mit --autostart install: beim Anmelden auch das Fenster oeffnen")
    args = parser.parse_args(argv)

    if args.autostart:
        return _handle_autostart(args)
    if args.shortcut:
        try:
            print("Verknuepfung angelegt:")
            print("  " + autostart.desktop_shortcut())
            print("Zum Anheften: Rechtsklick auf das Symbol → "
                  "\"An Taskleiste anheften\".")
        except Exception as exc:
            print(f"Fehlgeschlagen: {exc}")
            return 1
        return 0

    mode = "none" if args.no_browser else args.window
    cfg = config.load()
    size = args.size or cfg["window"]["size"]
    host, port = cfg["server"]["host"], cfg["server"]["port"]
    if args.port:
        port = args.port
    url = f"http://{host}:{port}/"

    if args.stop:
        if not _is_running(host, port):
            print(f"Es laeuft nichts auf Port {port}.")
            return 0
        if _stop_running(url, host, port):
            print("Server beendet.")
            return 0
        print("Der Server reagiert nicht. Prozess 'pythonw -m app' von Hand beenden.")
        return 1
    cfg["server"]["port"] = port

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
                if not args.new_window and focus_existing():
                    print("Vorhandenes Fenster nach vorn geholt.")
                else:
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
            if not args.new_window and focus_existing():
                print("Vorhandenes Fenster nach vorn geholt; es laedt sich selbst neu.")
            else:
                print("Geoeffnet als:", open_window(url, size, mode == "app"))
        threading.Timer(0.8, launch).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
