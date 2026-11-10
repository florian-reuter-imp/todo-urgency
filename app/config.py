"""Laedt config.toml (Python 3.11+ bringt tomllib in der Stdlib mit)."""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"

DEFAULTS: dict = {
    "data": {"file": "todo.md"},
    "server": {"host": "127.0.0.1", "port": 8777, "idle_shutdown_minutes": 0},
    # Sollgroesse des App-Fensters beim Start. Darunter wird die
    # Oberflaeche kompakter, statt Scrollbalken zu zeigen.
    "window": {"size": "1240,860"},
    # Jedes Feature ist einzeln abschaltbar. Aus bedeutet: das Feld wird
    # weiter geparst und beim Speichern erhalten, aber vom Scorer ignoriert
    # und in der Oberflaeche versteckt - der einfache Weg bleibt bestehen.
    "features": {"effort": True, "grouping": True, "dependencies": False},
    "urgency": {},
}


def _merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_ASSIGN_RE = re.compile(
    r"^(?P<lead>\s*)(?P<key>[A-Za-z0-9_]+)(?P<mid>\s*=\s*)(?P<value>[^#\n]*?)(?P<tail>\s*(?:#.*)?)$"
)
_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")


def _format(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def update_file(updates: dict[str, dict], path: Path | None = None) -> None:
    """Werte in der config.toml ersetzen, ohne Kommentare zu verlieren.

    `updates` ist nach Abschnitt geschluesselt, z.B.
    {"urgency.due": {"coefficient": 10.0}}. Vorhandene Zuweisungen werden
    an ihrem Platz ueberschrieben (Einrueckung und nachgestellter
    Kommentar bleiben stehen), fehlende Schluessel kommen ans Ende ihres
    Abschnitts, fehlende Abschnitte ans Ende der Datei. Deshalb kein
    Neugenerieren: die handgeschriebenen Kommentare sind Teil des Werts
    dieser Datei.
    """
    path = path or CONFIG_PATH
    lines = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
    pending = {sec: dict(vals) for sec, vals in updates.items()}

    section = ""
    section_end: dict[str, int] = {}
    for i, line in enumerate(lines):
        header = _SECTION_RE.match(line)
        if header:
            section = header.group("name").strip()
            section_end[section] = i
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            if line.strip() and section:
                section_end[section] = i
            continue
        if section:
            section_end[section] = i
        key = m.group("key")
        if section in pending and key in pending[section]:
            value = pending[section].pop(key)
            lines[i] = f"{m.group('lead')}{key}{m.group('mid')}{_format(value)}{m.group('tail')}"

    # Fehlende Schluessel einsetzen - von hinten, damit die Indizes halten.
    inserts: list[tuple[int, str]] = []
    tail_sections: list[str] = []
    for sec, vals in pending.items():
        if not vals:
            continue
        if sec in section_end:
            for key, value in vals.items():
                inserts.append((section_end[sec] + 1, f"{key} = {_format(value)}"))
        else:
            tail_sections.append(sec)
    for at, text in sorted(inserts, key=lambda p: -p[0]):
        lines.insert(at, text)
    for sec in tail_sections:
        lines.extend(["", f"[{sec}]"] + [f"{k} = {_format(v)}" for k, v in pending[sec].items()])

    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    # Atomar schreiben, wie bei der Datendatei: die config.toml liegt
    # ebenfalls im synchronisierten Ordner.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config.toml.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fingerprint() -> str:
    """Kennzeichen des Codes auf der Platte (Quelldateien + config.toml).

    Der Starter vergleicht ihn mit dem der laufenden Instanz. Weicht er ab,
    wird neu gestartet, statt stillschweigend die alte Fassung weiterzubenutzen.
    """
    newest = 0
    count = 0
    for pattern in ("app/*.py", "app/web/*", "config.toml"):
        for item in sorted(ROOT.glob(pattern)):
            if item.is_file():
                newest = max(newest, item.stat().st_mtime_ns)
                count += 1
    return f"{count}-{newest}"


def load(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    raw: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    cfg = _merge(DEFAULTS, raw)
    # Relative Pfade beziehen sich auf den Projektordner.
    data_file = Path(cfg["data"]["file"]).expanduser()
    if not data_file.is_absolute():
        data_file = ROOT / data_file
    cfg["data"]["path"] = data_file
    return cfg
