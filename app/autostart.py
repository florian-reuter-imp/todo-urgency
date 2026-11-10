"""Beim Anmelden mitstarten.

Eingerichtet wird bewusst nur der SERVER, ohne Fenster: dann steht das
Tool wie ein Kalender den ganzen Tag bereit und oeffnet sich auf Klick
sofort, statt beim Anmelden ungefragt ein Fenster aufzureissen.

Windows: eine Verknuepfung im Autostart-Ordner. Sie ist im Explorer
sichtbar und laesst sich im Task-Manager unter "Autostart" ab- und
wieder anschalten - also jederzeit ohne dieses Werkzeug rueckgaengig zu
machen.

Linux: eine systemd-Unit im Benutzerkontext.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import icon
from .config import ROOT

NAME = "Todo"


def _python() -> str:
    """pythonw.exe startet ohne Konsolenfenster; sonst der normale Interpreter."""
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.is_file() else exe)


def _arguments(with_window: bool) -> str:
    return "-m app" + ("" if with_window else " --window none")


# --- Windows ---------------------------------------------------------
def _desktop_dir() -> Path:
    """Den ECHTEN Desktop-Ordner erfragen, nicht %USERPROFILE%\\Desktop raten.

    Windows erlaubt es, bekannte Ordner umzuleiten; bei eingerichtetem
    OneDrive-Backup liegt der Desktop typischerweise unter
    %OneDrive%\\Desktop. Wer den Pfad fest verdrahtet, legt Verknuepfungen
    in einem Ordner ab, den der Nutzer nie sieht.
    """
    try:
        found = _powershell("[Environment]::GetFolderPath('Desktop')").strip()
        if found:
            return Path(found)
    except Exception:
        pass
    return Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))


def _startup_dir() -> Path:
    return Path(os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))


def _link_path() -> Path:
    return _startup_dir() / f"{NAME}.lnk"


def _powershell(script: str) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "PowerShell meldete einen Fehler.")
    return done.stdout.strip()


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _windows_shortcut(link: Path, args: str, description: str, icon: Path | None) -> str:
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)});"
        f"$s.TargetPath = {_ps_quote(_python())};"
        f"$s.Arguments = {_ps_quote(args)};"
        f"$s.WorkingDirectory = {_ps_quote(ROOT)};"
        "$s.WindowStyle = 7;"
        f"$s.Description = {_ps_quote(description)};"
    )
    if icon is not None:
        script += f"$s.IconLocation = {_ps_quote(str(icon) + ',0')};"
    _powershell(script + "$s.Save()")
    return str(link)


def _windows_install(with_window: bool) -> str:
    return _windows_shortcut(
        _link_path(), _arguments(with_window), "Todo-Server im Hintergrund", icon.ensure()
    )


def _windows_remove() -> bool:
    link = _link_path()
    if link.is_file():
        link.unlink()
        return True
    return False


def _windows_status() -> dict:
    link = _link_path()
    if not link.is_file():
        return {"installed": False, "path": str(link)}
    info = _powershell(
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)});"
        "Write-Output $s.TargetPath; Write-Output $s.Arguments; Write-Output $s.WorkingDirectory"
    ).split("\n")
    info += ["", "", ""]
    return {
        "installed": True, "path": str(link),
        "target": info[0].strip(), "arguments": info[1].strip(),
        "working_dir": info[2].strip(),
    }


# --- Linux (systemd im Benutzerkontext) ------------------------------
def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "todo.service"


def _unit_text(with_window: bool) -> str:
    return (
        "[Unit]\n"
        "Description=Todo-Server\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={ROOT}\n"
        f"ExecStart={sys.executable} {_arguments(with_window)}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _linux_install(with_window: bool) -> str:
    unit = _unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(_unit_text(with_window), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "todo.service"], check=False)
    return str(unit)


def _linux_remove() -> bool:
    unit = _unit_path()
    subprocess.run(["systemctl", "--user", "disable", "--now", "todo.service"], check=False)
    if unit.is_file():
        unit.unlink()
        return True
    return False


def _linux_status() -> dict:
    unit = _unit_path()
    return {"installed": unit.is_file(), "path": str(unit)}


# --- gemeinsame Oberflaeche -------------------------------------------
def install(with_window: bool = False) -> str:
    return _windows_install(with_window) if os.name == "nt" else _linux_install(with_window)


def remove() -> bool:
    return _windows_remove() if os.name == "nt" else _linux_remove()


def status() -> dict:
    return _windows_status() if os.name == "nt" else _linux_status()


def desktop_shortcut() -> str:
    """Verknuepfung zum Oeffnen - mit richtigem Icon, zum Anheften.

    Zeigt auf `-m app` (mit Fenster): laeuft der Server schon, geht nur ein
    Fenster auf; laeuft er nicht, wird er vorher gestartet. Damit funktioniert
    das Symbol in beiden Faellen, anders als ein angehefteter PWA-Eintrag.
    """
    if os.name != "nt":
        raise RuntimeError("Nur unter Windows; unter Linux legt der Desktop "
                           "selbst eine .desktop-Datei an.")
    return _windows_shortcut(
        _desktop_dir() / f"{NAME}.lnk", "-m app", "Todo oeffnen", icon.ensure()
    )
