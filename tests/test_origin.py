"""Herkunftspruefung des Servers.

Ein Server auf 127.0.0.1 ist nicht privat: jede Webseite im Browser darf
ihn anfunken. Der Browser verweigert ihr nur die Antwort, nicht die
Wirkung - eine fremde Seite konnte deshalb Aufgaben anlegen und den
Server beenden. Dieser Test ist der nachgestellte Angriff.

Echte HTTP-Anfragen ueber http.client, weil es hier genau um Kopfzeilen
geht (Host, Origin, Sec-Fetch-Site) - die kann urllib nicht frei setzen.
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, server  # noqa: E402

FOREIGN = "https://boese.example"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class OriginTestCase(unittest.TestCase):
    """Startet fuer jeden Test einen echten Server auf einer Wegwerfdatei."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "todo.md"
        self.path.write_text("- [ ] vorhanden\n", encoding="utf-8")
        self.port = _free_port()

        cfg = json.loads(json.dumps(config.DEFAULTS))
        cfg["data"]["path"] = str(self.path)
        cfg["server"]["port"] = self.port
        cfg["server"]["idle_shutdown_minutes"] = 0

        self.httpd, self.activity = server.build(cfg)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    # -- Helfer ---------------------------------------------------------
    def send(self, method: str, path: str, body=None, host: str | None = None,
             **headers: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            if payload is not None:
                # Genau der Trick aus dem Angriff: text/plain gilt als
                # harmlos, der Browser fragt deshalb nicht vorher nach.
                conn.putheader("Content-Type", "text/plain;charset=UTF-8")
                conn.putheader("Content-Length", str(len(payload)))
            for name, value in headers.items():
                conn.putheader(name.replace("_", "-"), value)
            conn.endheaders()
            if payload is not None:
                conn.send(payload)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def add(self, text: str, **headers: str) -> tuple[int, bytes]:
        return self.send("POST", "/api/tasks", {"description": text}, **headers)

    @property
    def file_text(self) -> str:
        return self.path.read_text(encoding="utf-8")


class TestOwnOriginWorks(OriginTestCase):
    """Die eigene Oberflaeche darf alles - sonst waere der Fix wertlos."""

    def test_own_origin_may_add(self):
        status, _ = self.add("von der eigenen Seite",
                             Origin=f"http://127.0.0.1:{self.port}",
                             Sec_Fetch_Site="same-origin")
        self.assertEqual(status, 201)
        self.assertIn("von der eigenen Seite", self.file_text)

    def test_localhost_counts_as_own_origin(self):
        status, _ = self.add("per localhost geoeffnet",
                             host=f"localhost:{self.port}",
                             Origin=f"http://localhost:{self.port}")
        self.assertEqual(status, 201)

    def test_without_origin_is_allowed(self):
        """curl, Python, das eigene --stop: kein Browser, kein Origin.

        Ein Browser KANN den Origin bei fremder Herkunft nicht weglassen,
        darum ist das keine Luecke.
        """
        status, _ = self.add("von der Kommandozeile")
        self.assertEqual(status, 201)

    def test_own_page_may_read(self):
        status, body = self.send("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertIn("vorhanden", body.decode("utf-8"))


class TestForeignOriginRejected(OriginTestCase):
    """Der nachgestellte Angriff."""

    def test_foreign_origin_cannot_add(self):
        status, _ = self.add("EINGESCHMUGGELT", Origin=FOREIGN)
        self.assertEqual(status, 403)
        self.assertNotIn("EINGESCHMUGGELT", self.file_text)

    def test_other_port_is_foreign(self):
        """Gleiche Adresse, anderer Port = andere Herkunft.

        Genau dieser Fall liess sich vorher ausnutzen: eine Seite auf
        127.0.0.1:8898 durfte dem Server auf 8899 schreiben.
        """
        status, _ = self.add("EINGESCHMUGGELT",
                             Origin=f"http://127.0.0.1:{self.port + 1}")
        self.assertEqual(status, 403)
        self.assertNotIn("EINGESCHMUGGELT", self.file_text)

    def test_cross_site_marker_rejected_without_origin(self):
        """Zweites Netz: der Browser verraet die Herkunft auch so."""
        status, _ = self.add("EINGESCHMUGGELT", Sec_Fetch_Site="cross-site")
        self.assertEqual(status, 403)
        self.assertNotIn("EINGESCHMUGGELT", self.file_text)

    def test_sandboxed_frame_rejected(self):
        """Origin 'null' kommt aus einem sandboxed iframe oder file://."""
        status, _ = self.add("EINGESCHMUGGELT", Origin="null")
        self.assertEqual(status, 403)

    def test_foreign_origin_cannot_change_or_delete(self):
        uid = json.loads(self.send("GET", "/api/tasks")[1])["tasks"][0]["uid"]
        for method in ("PATCH", "DELETE"):
            with self.subTest(method=method):
                status, _ = self.send(method, f"/api/tasks/{uid}",
                                      {"done": True}, Origin=FOREIGN)
                self.assertEqual(status, 403)
        self.assertIn("- [ ] vorhanden", self.file_text)

    def test_foreign_origin_cannot_change_weights(self):
        status, _ = self.send("PUT", "/api/config",
                              {"urgency": {"due": {"coefficient": 99.0}}},
                              Origin=FOREIGN)
        self.assertEqual(status, 403)

    def test_foreign_origin_cannot_shutdown(self):
        status, _ = self.send("POST", "/api/shutdown", {}, Origin=FOREIGN)
        self.assertEqual(status, 403)
        # Und der Server lebt noch.
        self.assertEqual(self.send("GET", "/api/version")[0], 200)

    def test_foreign_origin_cannot_restart(self):
        status, _ = self.send("POST", "/api/restart", {}, Origin=FOREIGN)
        self.assertEqual(status, 403)


class TestForeignHostRejected(OriginTestCase):
    """DNS-Rebinding: fremder Name, der auf 127.0.0.1 zeigt.

    Dann haelt der Browser seine Seite fuer gleicher Herkunft und gibt
    ihr auch die Antworten - erst damit waere die Liste LESBAR.
    """

    def test_foreign_host_cannot_read_tasks(self):
        status, body = self.send("GET", "/api/tasks",
                                 host=f"boese.example:{self.port}")
        self.assertEqual(status, 403)
        self.assertNotIn("vorhanden", body.decode("utf-8"))

    def test_foreign_host_cannot_read_config(self):
        self.assertEqual(
            self.send("GET", "/api/config", host="boese.example")[0], 403)

    def test_foreign_host_cannot_load_the_page(self):
        self.assertEqual(
            self.send("GET", "/", host=f"boese.example:{self.port}")[0], 403)

    def test_foreign_host_blocks_writing_too(self):
        status, _ = self.add("EINGESCHMUGGELT", host="boese.example")
        self.assertEqual(status, 403)
        self.assertNotIn("EINGESCHMUGGELT", self.file_text)


class TestHeaderParsing(unittest.TestCase):
    """Die Zerlegung der Kopfzeilen selbst, ohne Server."""

    def test_loopback_names(self):
        self.assertIn("127.0.0.1", server.LOOPBACK_NAMES)
        self.assertIn("localhost", server.LOOPBACK_NAMES)
        self.assertIn("::1", server.LOOPBACK_NAMES)

    def test_writing_methods_are_the_changing_ones(self):
        self.assertEqual(server.WRITING_METHODS,
                         {"POST", "PUT", "PATCH", "DELETE"})
        self.assertNotIn("GET", server.WRITING_METHODS)


if __name__ == "__main__":
    unittest.main()
