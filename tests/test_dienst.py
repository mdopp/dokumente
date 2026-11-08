"""dienst.py: Paperless' hook starts the contract helper once a stack has
arrived, the hourly run catches what a hook missed, the helper's messages
become files for laib-notify, and the status says what the last run did."""
import json, os, re, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
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
    ("PAPERLESS_API", "PAPERLESS_TOKEN", "PAPERLESS_AI_LLM_ENDPOINT", "PAPERLESS_AI_LLM_API_KEY", "PAPERLESS_AI_LLM_MODEL", "VERTRAG_TYPEN", "VERTRAG_ARTEN", "PAPERLESS_AI_ENABLED")}))
print("vertraege: lief")
if os.path.exists(Z + ".melden"):
    print("meldung " + json.dumps({"an": "", "betreff": "Kündigung prüfen: Kfz", "text": "bis 30.11.", "at": 1}))
    print("meldung kein json")
json.dump({"lauf": {"am": "2026-10-27T20:00:00Z", "vertraege": 2, "rechnungen": 1, "kontakte": 0, "erinnerungen": 1, "pruefen": 1}},
          open(os.environ["VERTRAEGE_FILE"], "w"))
sys.exit(1 if os.path.exists(Z + ".exit") else 0)
'''

# a stand-in for ordnen.py --neue: says it ran (before vertraege's counter moves) and what it filed in order
ORDNEN = r'''
import json, os, sys
Z = %r
assert not os.path.exists(Z), "ordnen runs before the helper"
open(Z + ".reihe", "a").write("ordnen %%s %%s\n" %% (sys.argv[1], os.environ["ORDNEN_DIR"]))
os.makedirs(os.environ["ORDNEN_DIR"], exist_ok=True)
json.dump({"lauf": {"eingeordnet": 3}}, open(os.path.join(os.environ["ORDNEN_DIR"], "ordnen.json"), "w"))
'''


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        skript = os.path.join(self.tmp, "fake.py")
        self.zaehler = os.path.join(self.tmp, "laeufe")
        Path(skript).write_text(FAKE % self.zaehler)
        self.env = {"PATH": os.environ["PATH"], "PAPERLESS_TOKEN": "pt", "PAPERLESS_URL": "http://127.0.0.1:8000",
                    "MODEL_URL": "http://127.0.0.1:11435/v1", "MODEL_KEY": "mk", "MODEL_NAME": "household",
                    "VERTRAG_ARTEN": "Vertrag, Versicherung, Bescheid", "VERTRAG_TYPEN": "Vertrag, Versicherungsschein",
                    "VERTRAEGE_SKRIPT": skript,
                    "DOKUMENTE_DATA": self.tmp, "MELDUNGEN": os.path.join(self.tmp, "eingang"),
                    "AUSWERTUNG_RUHE_S": "0.3", "AUSWERTUNG_SPAETESTENS_S": "5", "AUSWERTUNG_STUENDLICH_S": "3600",
                    "ORDNEN_SKRIPT": "", "DOPPELT_SKRIPT": "", "VERTRAEGE_FILE": os.path.join(self.tmp, "laib-vertraege.json"),
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
                                 "VERTRAG_TYPEN": "Vertrag, Versicherungsschein", "VERTRAG_ARTEN": "",
                                 "PAPERLESS_AI_ENABLED": "true"}, "contracts by type: the old VERTRAG_ARTEN (tags too) is not handed on")

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

    def test_new_documents_are_put_in_order_before_the_helper_reads_them(self):
        ordnen = os.path.join(self.tmp, "ordnen.py")
        Path(ordnen).write_text(ORDNEN % self.zaehler)
        a = dienst.Auswertung({**self.env, "ORDNEN_SKRIPT": ordnen})
        a.lauf("neue Dokumente")
        self.assertEqual(Path(self.zaehler + ".reihe").read_text(), "ordnen --neue %s\n" % os.path.join(self.tmp, "ordnen"))
        self.assertEqual(self.laeufe(), 1, "the helper ran after it")
        self.assertEqual(scan.AUSWERTUNG["lauf"]["eingeordnet"], 3)
        self.assertTrue(scan.AUSWERTUNG["detail"].startswith("Nach neuen Dokumenten ausgewertet. 3 Dokumente eingeordnet. Gelesen: 2 Verträge"),
                        scan.AUSWERTUNG["detail"])

    def test_ordnen_failing_never_stops_the_helper(self):
        ordnen = os.path.join(self.tmp, "ordnen.py")
        Path(ordnen).write_text("import sys; print('kaputt'); sys.exit(3)")
        a = dienst.Auswertung({**self.env, "ORDNEN_SKRIPT": ordnen})
        a.lauf("stündlich")
        self.assertEqual(self.laeufe(), 1)
        self.assertEqual(scan.AUSWERTUNG["zustand"], "fertig")
        self.assertNotIn("eingeordnet", scan.AUSWERTUNG["lauf"])

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


# a stand-in for ordnen.py --neue that cannot do its work, as the real one says it (dokumente, 2026-11-08)
HAENGT = r'''
import json
print("ordnen: die Ordnung ist in Paperless noch nicht eingerichtet")
print("hängt " + json.dumps({"was": "einrichtung", "detail": "Neue Dokumente werden nicht eingeordnet: Bereich-Tag „Gesundheit“ ist für Dokumente in Paperless nicht sichtbar", "fix": "In Paperless nachsehen", "einzelheiten": ""}, ensure_ascii=False))
'''

# a stand-in for doppelt.py --neue: says how it was called, one partial scan to the trash, one pair marked
DOPPELT = r'''
import json, sys
open(%r, "w").write(json.dumps(sys.argv[1:]))
print("doppelt: #438 steckt ganz in #405")
print("hängt " + json.dumps({"was": "doppelt", "detail": "2 doppelte Scans warten auf dein Löschen: in Paperless den Tag „Doppelt“ öffnen und alle löschen.", "fix": "Jedes nennt das vollständige Dokument.", "einzelheiten": ""}, ensure_ascii=False))
print("doppelt " + json.dumps({"zusammengefuehrt": 1, "zum_loeschen": 2, "pruefen": 1}))
'''

# laib's proposal.Technical (internal/proposal/plain.go): a status finding's detail that matches is not what the family reads
TECHNISCH = re.compile(r"\blaib [a-z]|\b[\w@-]+\.(service|timer|socket|container)\b|\bexit (status )?\d|dial tcp|connection refused|"
                       r"no such|deadline exceeded|\b(Get|Post) \"|\b(failed|not found|unknown|cannot|refused|invalid|error)\b|"
                       r"\b\w+\.ya?ml\b|(^|\s)--[a-z]|\{\"")


class Haengt(Basis):
    """„Es muss auf jeden Fall auffallen“: a step that cannot do its work is a finding, yellow, red if it lasts"""

    def finding(self, st, was):
        return next((f for f in st.get("findings") or [] if f.get("topic") == "dokumente-" + was), None)

    def test_ordnen_not_set_up_is_a_finding_that_names_the_piece_and_turns_red(self):
        ordnen = os.path.join(self.tmp, "ordnen.py")
        Path(ordnen).write_text(HAENGT)
        a = dienst.Auswertung({**self.env, "ORDNEN_SKRIPT": ordnen, "HAENGT_ROT_H": "6"})
        a.lauf("neue Dokumente")
        f = self.finding(scan.AUSWERTUNG, "einrichtung")
        self.assertEqual(f["severity"], "gelb", "what never mends itself is yellow at once")
        self.assertIn("Bereich-Tag „Gesundheit“ ist für Dokumente in Paperless nicht sichtbar", f["detail"])
        self.assertEqual(f["fix"], "In Paperless nachsehen")
        # seven hours later it still hangs: red, for the Operator
        st = json.loads(Path(self.tmp, "dokumente-haengt.json").read_text())
        st["einrichtung"]["seit"] -= 7 * 3600
        Path(self.tmp, "dokumente-haengt.json").write_text(json.dumps(st))
        a.lauf("stündlich")
        f = self.finding(scan.AUSWERTUNG, "einrichtung")
        self.assertEqual(f["severity"], "rot")
        self.assertIn("(seit ", f["detail"])
        self.assertTrue(f["fix"].startswith("Das löst sich nicht von selbst; der Operator sollte nachsehen."))
        # set up by hand: the next run says nothing more
        Path(ordnen).write_text(ORDNEN % self.zaehler)
        os.remove(self.zaehler)
        a.lauf("stündlich")
        self.assertIsNone(self.finding(scan.AUSWERTUNG, "einrichtung"))
        self.assertEqual(json.loads(Path(self.tmp, "dokumente-haengt.json").read_text()), {})

    def test_the_finding_reaches_the_status_file_laib_reads(self):
        ordnen = os.path.join(self.tmp, "ordnen.py")
        Path(ordnen).write_text(HAENGT)
        cfg = scan.Config.from_env({})
        cfg.data = self.tmp
        d = scan.Dienst(cfg)
        dienst.Auswertung({**self.env, "ORDNEN_SKRIPT": ordnen}, d).lauf("neue Dokumente")
        d.zeige()
        st = json.loads(Path(self.tmp, "laib-status.json").read_text())
        self.assertEqual(self.finding(st, "einrichtung")["severity"], "gelb")

    def test_a_passing_hitch_is_quiet_until_it_repeats(self):
        w = dienst.Wache(os.path.join(self.tmp, "w.json"), {})
        modell = {"modell": {"detail": "Das Hausmodell antwortet nicht", "fix": "warten"}}
        self.assertEqual(w.lauf(modell, now=1000), [])
        self.assertEqual(w.lauf(modell, now=1100), [])
        [f] = w.lauf(modell, now=1200)
        self.assertEqual(f["severity"], "gelb")
        [f] = w.lauf(modell, now=1000 + 6 * 3600)
        self.assertEqual(f["severity"], "rot")
        self.assertEqual(w.lauf({}, now=1000 + 7 * 3600), [])
        self.assertEqual(w.lauf(modell, now=1000 + 8 * 3600), [], "over once: counted afresh")

    def test_the_helper_failing_runs_in_a_row_is_a_finding(self):
        Path(self.zaehler + ".exit").touch()
        a = dienst.Auswertung(self.env)
        for _ in range(3):
            a.lauf("stündlich")
        f = self.finding(scan.AUSWERTUNG, "vertraege-abbruch")
        self.assertEqual(f["severity"], "gelb")
        self.assertIn("vertraege: lief", f["einzelheiten"])

    def test_without_a_token_it_is_a_finding(self):
        a = dienst.Auswertung({**self.env, "PAPERLESS_TOKEN": ""})
        a.schleife()
        self.assertEqual(scan.AUSWERTUNG["findings"][0]["severity"], "gelb")

    def test_the_family_reads_plain_words(self):
        """laib's Say takes a status finding's detail as it is only when it is not technical"""
        p = {}
        dienst.vertraege_probleme({"modell_weg": True, "fehlt": "Vertragsfelder fehlen"}, 1, ["HTTP Error 401: Unauthorized"], False, p)
        dienst.vertraege_probleme({}, 1, ["Traceback"], True, p)
        fs = dienst.Wache(os.path.join(self.tmp, "w.json"), {"HAENGT_GELB_LAEUFE": "1"}).lauf(p)
        self.assertEqual(len(fs), 4)
        for f in fs:
            self.assertFalse(TECHNISCH.search(f["detail"]), f["detail"])
        self.assertTrue(TECHNISCH.search("Paperless: connection refused"), "the pattern is laib's and can match")


class Doppelt(Basis):
    def doppelt(self):
        skript = os.path.join(self.tmp, "doppelt.py")
        Path(skript).write_text(DOPPELT % os.path.join(self.tmp, "doppelt-argv"))
        return skript

    def argv(self):
        return json.loads(Path(self.tmp, "doppelt-argv").read_text())

    def test_duplicates_go_first_and_the_family_reads_how_many(self):
        a = dienst.Auswertung({**self.env, "DOPPELT_SKRIPT": self.doppelt()})
        a.lauf("neue Dokumente")
        self.assertEqual(self.argv(), ["--neue"])
        self.assertEqual(scan.AUSWERTUNG["lauf"]["doppelt_zusammengefuehrt"], 1)
        self.assertIn("1 doppelt gescanntes Dokument zusammengeführt.", scan.AUSWERTUNG["detail"])
        self.assertIn("2 doppelt gescannte Dokumente zum Löschen markiert (Tag „Doppelt“).", scan.AUSWERTUNG["detail"])
        self.assertIn("1 Dokument vielleicht doppelt gescannt, zum Prüfen markiert.", scan.AUSWERTUNG["detail"])
        [f] = [f for f in scan.AUSWERTUNG["findings"] if f.get("topic") == "dokumente-doppelt"]
        self.assertEqual(f["severity"], "gelb", "a person's duplicates wait on them: yellow at once")
        self.assertIn("2 doppelte Scans warten auf dein Löschen", f["detail"])

    def test_duplicates_waiting_on_a_person_never_turn_red(self):
        w = dienst.Wache(os.path.join(self.tmp, "w.json"), {})
        p = {"doppelt": {"detail": "3 doppelte Scans warten auf dein Löschen", "fix": "Tag „Doppelt“ öffnen"}}
        [f] = w.lauf(p, now=1000)
        self.assertEqual(f["severity"], "gelb")
        [f] = w.lauf(p, now=1000 + 48 * 3600)
        self.assertEqual(f["severity"], "gelb", "two days later still the person's, not the Operator's")
        self.assertEqual(w.lauf({}, now=1000 + 49 * 3600), [], "deleted: over")

    def test_probe_is_a_dry_run_and_aus_is_off(self):
        dienst.Auswertung({**self.env, "DOPPELT_SKRIPT": self.doppelt(), "DOPPELT": "probe"}).lauf("stündlich")
        self.assertEqual(self.argv(), ["--neue", "--dry-run"])
        os.remove(os.path.join(self.tmp, "doppelt-argv"))
        dienst.Auswertung({**self.env, "DOPPELT_SKRIPT": self.doppelt(), "DOPPELT": "aus"}).lauf("stündlich")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "doppelt-argv")))
