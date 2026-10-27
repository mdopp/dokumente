"""dienst.py: Paperless' hook starts the contract helper once a stack has
arrived, the hourly run catches what a hook missed, the helper's messages
become files for laib-notify, and the status says what the last run did."""
import json, os, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dienst  # noqa: E402

scan = dienst.scan

# a stand-in for vertraege.py: counts its runs, says what it was given,
# writes the run record and, when asked, a message for the owner
# (the helper gets only the env the service hands it, so the fake's own
# knobs are files beside its counter)
FAKE = r'''
import json, os, sys
Z = %r
n = int(open(Z).read() or 0) + 1 if os.path.exists(Z) else 1
open(Z, "w").write(str(n))
open(Z + ".env", "w").write(json.dumps({k: os.environ.get(k, "") for k in
    ("PAPERLESS_API", "PAPERLESS_TOKEN", "PAPERLESS_AI_LLM_ENDPOINT", "PAPERLESS_AI_LLM_API_KEY", "PAPERLESS_AI_LLM_MODEL", "VERTRAG_ARTEN", "PAPERLESS_AI_ENABLED")}))
print("vertraege: lief")
if os.path.exists(Z + ".melden"):
    print("meldung " + json.dumps({"an": "", "betreff": "Kündigung prüfen: Kfz", "text": "bis 30.11.", "at": 1}))
    print("meldung kein json")
json.dump({"lauf": {"am": "2026-10-27T20:00:00Z", "vertraege": 2, "rechnungen": 1, "kontakte": 0, "erinnerungen": 1, "pruefen": 1}},
          open(os.environ["VERTRAEGE_FILE"], "w"))
sys.exit(1 if os.path.exists(Z + ".exit") else 0)
'''


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        skript = os.path.join(self.tmp, "fake.py")
        self.zaehler = os.path.join(self.tmp, "laeufe")
        Path(skript).write_text(FAKE % self.zaehler)
        self.env = {"PATH": os.environ["PATH"], "PAPERLESS_TOKEN": "pt", "PAPERLESS_URL": "http://127.0.0.1:8000",
                    "MODEL_URL": "http://127.0.0.1:11435/v1", "MODEL_KEY": "mk", "MODEL_NAME": "household",
                    "VERTRAG_ARTEN": "Vertrag, Versicherung, Bescheid", "VERTRAEGE_SKRIPT": skript,
                    "DOKUMENTE_DATA": self.tmp, "MELDUNGEN": os.path.join(self.tmp, "eingang"),
                    "AUSWERTUNG_RUHE_S": "0.3", "AUSWERTUNG_SPAETESTENS_S": "5", "AUSWERTUNG_STUENDLICH_S": "3600",
                    }
        scan.AUSWERTUNG.clear()

    def laeufe(self):
        try:
            return int(Path(self.zaehler).read_text() or 0)
        except OSError:
            return 0

    def warte(self, bis, s=8):
        ende = time.time() + s
        while time.time() < ende and not bis():
            time.sleep(0.05)
        return bis()


class Hook(Basis):
    def post(self, port, token, path="/hook"):
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="POST", data=b"{}",
                                   headers={"X-Dokumente-Token": token} if token is not None else {})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_a_stack_of_hooks_is_one_run_after_the_quiet(self):
        a = dienst.Auswertung(self.env)
        srv = dienst.lausche(a, {**self.env, "DOKUMENTE_WEBHOOK_TOKEN": "wt", "DOKUMENTE_LISTEN": "127.0.0.1:0"})
        self.addCleanup(srv.shutdown)
        threading.Thread(target=a.schleife, daemon=True).start()
        port = srv.server_port
        for _ in range(5):   # 5 documents of one stack, one after another
            self.assertEqual(self.post(port, "wt"), 202)
            time.sleep(0.05)
        self.assertTrue(self.warte(lambda: self.laeufe() == 1 and scan.AUSWERTUNG.get("zustand") == "fertig"))
        time.sleep(0.8)
        self.assertEqual(self.laeufe(), 1, "one stack, one run")
        st = scan.AUSWERTUNG
        self.assertEqual(st["anlass"], "neue Dokumente")
        self.assertEqual(st["lauf"]["vertraege"], 2)
        self.assertEqual(st["detail"], "Nach neuen Dokumenten ausgewertet. Gelesen: 2 Verträge, 1 Rechnung, 1 Erinnerung. 1 Dokument zum Prüfen markiert.")
        given = json.loads(Path(self.zaehler + ".env").read_text())
        self.assertEqual(given, {"PAPERLESS_API": "http://127.0.0.1:8000", "PAPERLESS_TOKEN": "pt", "PAPERLESS_AI_LLM_ENDPOINT": "http://127.0.0.1:11435/v1",
                                 "PAPERLESS_AI_LLM_API_KEY": "mk", "PAPERLESS_AI_LLM_MODEL": "household",
                                 "VERTRAG_ARTEN": "Vertrag, Versicherung, Bescheid", "PAPERLESS_AI_ENABLED": "true"})

    def test_only_paperless_with_the_token_starts_a_run(self):
        a = dienst.Auswertung(self.env)
        srv = dienst.lausche(a, {**self.env, "DOKUMENTE_WEBHOOK_TOKEN": "wt", "DOKUMENTE_LISTEN": "127.0.0.1:0"})
        self.addCleanup(srv.shutdown)
        port = srv.server_port
        self.assertEqual(self.post(port, "falsch"), 401)
        self.assertEqual(self.post(port, None), 401)
        self.assertEqual(self.post(port, "wt", "/anderes"), 404)
        self.assertFalse(a.erster, "a refused hook is no document")
        self.assertEqual(self.post(port, "wt"), 202)
        self.assertTrue(a.erster)

    def test_no_webhook_token_no_listener(self):
        self.assertIsNone(dienst.lausche(dienst.Auswertung(self.env), self.env))


class Lauf(Basis):
    def test_hourly_without_a_hook(self):
        a = dienst.Auswertung({**self.env, "AUSWERTUNG_STUENDLICH_S": "0.3"})
        threading.Thread(target=a.schleife, daemon=True).start()
        self.assertTrue(self.warte(lambda: scan.AUSWERTUNG.get("zustand") == "fertig"))
        self.assertEqual(scan.AUSWERTUNG["anlass"], "stündlich")
        self.assertTrue(scan.AUSWERTUNG["detail"].startswith("Ausgewertet. Gelesen:"))

    def test_messages_go_to_laib_notify_whole(self):
        a = dienst.Auswertung(self.env)
        Path(self.zaehler + ".melden").touch()
        a.lauf("stündlich")
        files = sorted(os.listdir(self.env["MELDUNGEN"]))
        self.assertEqual(len(files), 1, files)   # the line that was no JSON is dropped
        self.assertTrue(files[0].startswith("dokumente-") and files[0].endswith(".json"))
        msg = json.loads(Path(self.env["MELDUNGEN"], files[0]).read_text())
        self.assertEqual(msg["betreff"], "Kündigung prüfen: Kfz")

    def test_a_failed_run_says_so_and_the_next_tries_again(self):
        Path(self.zaehler + ".exit").touch()
        a = dienst.Auswertung(self.env)
        a.lauf("neue Dokumente")
        self.assertEqual(scan.AUSWERTUNG["zustand"], "fehler")
        self.assertEqual(scan.AUSWERTUNG["detail"], "Die Auswertung brach ab; der nächste Lauf versucht es wieder.")
        self.assertIn("vertraege: lief", scan.AUSWERTUNG["einzelheiten"])

    def test_without_paperless_access_it_says_so_and_waits(self):
        a = dienst.Auswertung({**self.env, "PAPERLESS_TOKEN": ""})
        a.schleife()   # returns at once
        self.assertEqual(scan.AUSWERTUNG["zustand"], "aus")
        self.assertEqual(self.laeufe(), 0)

    def test_the_scan_status_carries_the_evaluation(self):
        cfg = scan.Config.from_env({"DATA": self.tmp})
        cfg.data = self.tmp
        d = scan.Dienst(cfg)
        a = dienst.Auswertung(self.env, d)
        a.lauf("stündlich")
        self.assertTrue(d.neu_zeigen)
        d.zeige()
        st = json.loads(Path(self.tmp, "laib-status.json").read_text())
        self.assertEqual(st["auswertung"]["zustand"], "fertig")
        self.assertIn("zustand", st)   # the scan's own still there


if __name__ == "__main__":
    unittest.main()


class Absender(Basis):
    """laib#737, dokumente#5: a household correspondent left without documents is a yellow finding"""

    def test_left_household_correspondents_are_a_yellow_finding_in_the_status(self):
        cfg = scan.Config.from_env({})
        cfg.data = self.tmp
        d = scan.Dienst(cfg)
        a = dienst.Auswertung(self.env, d)
        a.lauf("stündlich")   # the fake writes a record without them
        d.zeige()
        st = json.loads(Path(self.tmp, "laib-status.json").read_text())
        self.assertFalse([f for f in st.get("findings", []) if "Haushalt" in f["detail"]], "none: nothing to say")
        self.assertEqual(dienst.leere_absender({"korrespondenten": {"leer": ["Erika Mustermann", "Familie Mustermann"]}})[0]["severity"], "gelb")
        rec = os.path.join(self.tmp, "laib-vertraege.json")
        orig = Path(self.zaehler).parent / "fake.py"
        # the next run's record names two (the fake keeps korrespondenten when told to)
        Path(orig).write_text(Path(orig).read_text().replace(
            '"pruefen": 1}}', '"pruefen": 1}, "korrespondenten": {"leer": ["Erika Mustermann", "Familie Mustermann"]}}'))
        a.lauf("stündlich")
        d.zeige()
        st = json.loads(Path(self.tmp, "laib-status.json").read_text())
        [f] = [f for f in st["findings"] if "Haushalt" in f["detail"]]
        self.assertEqual(f["severity"], "gelb")
        self.assertIn("„Erika Mustermann“, „Familie Mustermann“", f["detail"])
        self.assertIn("Korrespondenten", f["fix"])
        self.assertTrue(os.path.exists(rec))
