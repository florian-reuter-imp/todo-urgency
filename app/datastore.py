"""Schicht 1: Rohtext lesen/schreiben.

Kennt weder das Dateiformat (das ist Sache des TaskCodec) noch die
Sync-Mechanik (die passiert komplett ausserhalb der App auf OS-Ebene).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol


class DataStore(Protocol):
    def load(self) -> str: ...
    def save(self, content: str) -> None: ...
    def changed_since_last_load(self) -> bool: ...


class LocalFileStore:
    """Liest/schreibt eine einzelne lokale Datei.

    Der Pfad darf in einem synchronisierten Ordner liegen (OneDrive,
    Syncthing, ...) - der Store weiss davon nichts.
    """

    def __init__(self, path: str | os.PathLike[str], encoding: str = "utf-8") -> None:
        self.path = Path(path)
        self.encoding = encoding
        self._stamp: tuple[int, int] | None = None

    # -- intern ---------------------------------------------------------
    def _current_stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    # -- DataStore ------------------------------------------------------
    def load(self) -> str:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding=self.encoding)
        # Stempel VOR dem Lesen nehmen: schreibt jemand waehrend des Lesens,
        # sieht der naechste changed_since_last_load() das zuverlaessig.
        stamp = self._current_stamp()
        content = self.path.read_text(encoding=self.encoding)
        self._stamp = stamp
        return content

    def save(self, content: str) -> None:
        """Atomar schreiben: erst Temp-Datei im selben Ordner, dann umbenennen.

        Verhindert, dass ein Sync-Client eine halb geschriebene Datei
        hochlaedt. os.replace ist auf Windows und POSIX atomar, sofern
        Quelle und Ziel im selben Dateisystem liegen - daher dir=parent.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding=self.encoding, newline="\n") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self._stamp = self._current_stamp()

    def changed_since_last_load(self) -> bool:
        """True, wenn die Datei seit dem letzten load()/save() fremdveraendert wurde.

        mtime+size reicht als erste Stufe; ein echter Watcher (watchdog)
        kann spaeter ergaenzt werden, ohne die Schnittstelle zu aendern.
        """
        if self._stamp is None:
            return self._current_stamp() is not None
        return self._current_stamp() != self._stamp
