"""#709: contract data into Paperless' contract fields, and the reminder before a
notice date. paperless-vertraege.py asks the household model for the fields of
a contract's text, keeps only values the text backs up word for word, fills
only empty fields, notes what it filled, and makes one reminder in Haus's
Haushalt per document and notice date. Against a fake Paperless, a fake model
and a fake Haus, never real ones; no real names or addresses."""
import base64, json, os, subprocess, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
SCRIPT = DIR / "paperless-vertraege.py"
AUTH = "Basic " + base64.b64encode(b"admin:adminpw").decode()
NAMES = ["Anbieter", "Vertragsnummer", "Vertragsbeginn", "Vertragsende", "Kündigungsfrist",
         "Nächster Kündigungstermin", "Betrag", "Zahlungsintervall"]
KINDS = ["string", "string", "date", "date", "string", "date", "monetary", "string"]
F = {n: i + 1 for i, n in enumerate(NAMES)}   # field ids
OTHER = 20                                    # a custom field of the owner's, not a contract field
TEXT = ("Stadtwerke Beispielstadt GmbH\nIhr Stromvertrag Nr. SW-12345\nVertragsbeginn: 01.04.2026\n"
        "Die Mindestlaufzeit endet am 31.03.2027. Kündigungsfrist: 3 Monate zum Ende der Laufzeit.\n"
        "Abschlag: 84,50 EUR monatlich")


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_port


def reply(h, code, body):
    b = json.dumps(body).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(b)))
    h.end_headers()
    h.wfile.write(b)


def body_of(h):
    n = int(h.headers.get("Content-Length") or 0)
    return json.loads(h.rfile.read(n)) if n else None


class Paperless:
    """documents with custom fields and tags, document types, tags, notes: in memory"""

    def __init__(self, docs, types=None, tags=None):
        self.docs, self.calls, self.notes = docs, [], {}
        self.types = types if types is not None else [{"id": 1, "name": "Vertrag"}, {"id": 2, "name": "Rechnung"}]
        self.tags = tags if tags is not None else [{"id": 1, "name": "Versicherung"}]
        self.fields = [{"id": F[n], "name": n, "data_type": k} for n, k in zip(NAMES, KINDS)] + [
            {"id": OTHER, "name": "Ablageort", "data_type": "string"}]
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def any(self, method):
                if self.headers.get("Authorization") != AUTH:
                    return reply(self, 401, {"detail": "no"})
                body = body_of(self)
                path, _, query = self.path.partition("?")
                fake.calls.append((method, path, query, body))
                q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                if method == "GET" and path == "/api/custom_fields/":
                    return reply(self, 200, {"next": None, "results": fake.fields})
                if method == "GET" and path == "/api/document_types/":
                    return reply(self, 200, {"next": None, "results": fake.types})
                if method == "GET" and path == "/api/tags/":
                    return reply(self, 200, {"next": None, "results": fake.tags})
                if method == "POST" and path == "/api/tags/":
                    t = {**body, "id": 100 + len(fake.tags)}
                    fake.tags.append(t)
                    return reply(self, 201, t)
                if method == "GET" and path == "/api/documents/":
                    out = fake.docs
                    if "document_type__id__in" in q:
                        ids = {int(i) for i in q["document_type__id__in"].split(",")}
                        out = [d for d in out if d.get("document_type") in ids]
                    if "tags__id__in" in q:
                        ids = {int(i) for i in q["tags__id__in"].split(",")}
                        out = [d for d in out if ids & set(d.get("tags") or [])]
                    return reply(self, 200, {"next": None, "results": out})
                parts = path.strip("/").split("/")
                if parts[:2] == ["api", "documents"] and len(parts) >= 3:
                    d = next(d for d in fake.docs if d["id"] == int(parts[2]))
                    if len(parts) == 4 and parts[3] == "notes" and method == "POST":
                        fake.notes.setdefault(d["id"], []).append(body["note"])
                        return reply(self, 200, [])
                    if method == "GET":
                        return reply(self, 200, d)
                    if method == "PATCH":   # Paperless: the list replaces the document's custom fields
                        d.update(body)
                        return reply(self, 200, d)
                reply(self, 404, {})

            def do_GET(self):
                self.any("GET")

            def do_POST(self):
                self.any("POST")

            def do_PATCH(self):
                self.any("PATCH")

        self.srv, self.url = serve(H)

    def doc(self, i):
        return next(d for d in self.docs if d["id"] == i)

    def value(self, i, field):
        return next((c["value"] for c in self.doc(i)["custom_fields"] if c["field"] == field), None)


class Model:
    """the household model's /v1/chat/completions: answers what the test gives, records the asks"""

    def __init__(self, answer, code=200):
        self.answer, self.code, self.asks = answer, code, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                fake.asks.append((self.path, dict(self.headers), body_of(self)))
                if fake.code != 200:
                    return reply(self, fake.code, {"error": "held"})
                reply(self, 200, {"choices": [{"message": {"content": json.dumps(fake.answer, ensure_ascii=False)}}]})

        self.srv, self.url = serve(H)


class Haus:
    """Haus's /mcp/haushalt: tools/call erinnerung_anlegen"""

    def __init__(self):
        self.calls = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer haustoken":
                    self.send_response(401)
                    self.end_headers()
                    return
                b = body_of(self)
                fake.calls.append(b)
                reply(self, 200, {"jsonrpc": "2.0", "id": b["id"], "result": {"content": [{"type": "text", "text": "Erinnerung angelegt"}], "isError": False}})

        self.srv, self.url = serve(H)

    def reminders(self):
        return [c["params"]["arguments"] for c in self.calls if c["params"]["name"] == "erinnerung_anlegen"]


def vertrag(i=1, fields=None, typ=1, tags=None, text=TEXT, title="Stromvertrag"):
    return {"id": i, "title": title, "content": text, "document_type": typ, "tags": tags or [],
            "custom_fields": [{"field": k, "value": v} for k, v in (fields or {}).items()]}


ANSWER = {"Anbieter": {"wert": "Stadtwerke Beispielstadt GmbH", "beleg": "Stadtwerke Beispielstadt GmbH"},
          "Vertragsnummer": {"wert": "SW-12345", "beleg": "Stromvertrag Nr. SW-12345"},
          "Vertragsbeginn": {"wert": "2026-04-01", "beleg": "Vertragsbeginn: 01.04.2026"},
          "Vertragsende": {"wert": "2027-03-31", "beleg": "Mindestlaufzeit endet am 31.03.2027"},
          "Kündigungsfrist": {"wert": "3 Monate", "beleg": "Kündigungsfrist: 3 Monate"},
          "Nächster Kündigungstermin": {"wert": "2027-03-31", "beleg": "endet am 31.03.2027"},
          "Betrag": {"wert": 84.5, "beleg": "84,50 EUR"},
          "Zahlungsintervall": {"wert": "monatlich", "beleg": "84,50 EUR monatlich"}}


class Vertraege(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.record = os.path.join(self.tmp, "laib-vertraege.json")

    def run_job(self, paperless, model=None, haus=None, heute="2026-10-26", ai="true", **extra):
        env = {"PATH": os.environ["PATH"], "PAPERLESS_API": paperless.url, "PAPERLESS_ADMIN_USER": "admin",
               "PAPERLESS_ADMIN_PASSWORD": "adminpw", "PAPERLESS_URL": "https://paperless.example.org",
               "PAPERLESS_AI_ENABLED": ai, "PAPERLESS_AI_LLM_ENDPOINT": (model.url + "/v1") if model else "",
               "PAPERLESS_AI_LLM_API_KEY": "modelkey", "PAPERLESS_AI_LLM_MODEL": "household",
               "VERTRAG_ARTEN": "Vertrag, Versicherung", "VERTRAG_VORLAUF_TAGE": "28",
               "HAUSHALT_URL": (haus.url + "/mcp/haushalt") if haus else "", "HAUSHALT_TOKEN": "haustoken" if haus else "",
               "VERTRAEGE_FILE": self.record, "HEUTE": heute, **extra}
        # as the unit feeds it: the script on stdin
        return subprocess.run([sys.executable, "-"], input=SCRIPT.read_text(), env=env, capture_output=True, text=True, timeout=60)

    def test_fills_only_empty_fields_keeps_the_owners_and_notes_what_it_filled(self):
        p = Paperless([vertrag(fields={F["Anbieter"]: "Meine Stadtwerke", F["Betrag"]: None, OTHER: "Ordner 3"})])
        m = Model(ANSWER)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Anbieter"]), "Meine Stadtwerke", "a value the owner set is never overwritten")
        self.assertEqual(p.value(1, OTHER), "Ordner 3", "a field that is not a contract field stays")
        self.assertEqual(p.value(1, F["Vertragsnummer"]), "SW-12345")
        self.assertEqual(p.value(1, F["Vertragsbeginn"]), "2026-04-01")
        self.assertEqual(p.value(1, F["Kündigungsfrist"]), "3 Monate")
        self.assertEqual(p.value(1, F["Nächster Kündigungstermin"]), "2027-03-31")
        self.assertEqual(p.value(1, F["Betrag"]), "EUR84.50", "an empty instance is empty: filled")
        self.assertEqual(p.value(1, F["Zahlungsintervall"]), "monatlich")
        note = p.notes[1][0]
        self.assertIn("Vertragsnummer", note)
        self.assertNotIn("Anbieter", note.split("prüfen")[0], "the note names only what was filled")
        # the model: the household one, through the proxy, as background work, asked in JSON
        path, headers, body = m.asks[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers.get("Authorization"), "Bearer modelkey")
        self.assertEqual(headers.get("X-Laib-Background"), "paperless")
        self.assertEqual(body["model"], "household")
        self.assertEqual(body["response_format"]["type"], "json_object")
        self.assertIn("Stromvertrag Nr. SW-12345", body["messages"][-1]["content"])
        with open(self.record) as f:
            rec = json.load(f)
        self.assertIn("Vertragsnummer", rec["dokumente"]["1"]["gefuellt"])
        # once per document's text: a second run asks the model nothing
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1)
        self.assertEqual(len(p.notes[1]), 1)

    def test_a_value_the_text_does_not_back_up_is_not_filled_but_marked(self):
        answer = {**ANSWER, "Vertragsnummer": {"wert": "SW-99999", "beleg": "Vertrag Nr. SW-99999"},
                  "Betrag": {"wert": "viel", "beleg": "84,50 EUR"}, "Vertragsbeginn": None}
        p = Paperless([vertrag(tags=[5])])
        r = self.run_job(p, Model(answer))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(p.value(1, F["Vertragsnummer"]), "a number not in the text is a guess")
        self.assertIsNone(p.value(1, F["Betrag"]), "not an amount")
        self.assertIsNone(p.value(1, F["Vertragsbeginn"]))
        self.assertEqual(p.value(1, F["Anbieter"]), "Stadtwerke Beispielstadt GmbH")
        tag = next(t for t in p.tags if t["name"] == "prüfen")
        self.assertEqual(tag["matching_algorithm"], 0, "the tag never files a document by itself")
        self.assertIsNone(tag["owner"], "the family sees the tag")
        self.assertEqual(set(p.doc(1)["tags"]), {5, tag["id"]}, "the document's tags kept, prüfen added")
        self.assertIn("Vertragsnummer", p.notes[1][0].split("prüfen", 1)[1])

    def test_only_contracts_by_type_or_tag_and_only_with_an_empty_field(self):
        full = {F[n]: "x" for n in NAMES}
        p = Paperless([vertrag(1, typ=2, title="Rechnung"), vertrag(2, typ=None, tags=[1]), vertrag(3, fields=full)])
        m = Model(ANSWER)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1, "the invoice is no contract, the full one has nothing to fill")
        self.assertEqual(p.value(2, F["Vertragsnummer"]), "SW-12345", "tagged Versicherung: a contract")
        self.assertEqual(p.doc(1)["custom_fields"], [])

    def test_model_away_changes_nothing_and_the_next_run_asks_again(self):
        p = Paperless([vertrag()])
        m = Model(ANSWER, code=409)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Modell", r.stdout)
        self.assertEqual(p.doc(1)["custom_fields"], [])
        self.assertFalse([c for c in p.calls if c[0] == "PATCH"])
        m.code = 200
        self.run_job(p, m)
        self.assertEqual(p.value(1, F["Vertragsnummer"]), "SW-12345")

    def test_ai_off_asks_no_model(self):
        p = Paperless([vertrag()])
        m = Model(ANSWER)
        r = self.run_job(p, m, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m.asks, [])
        self.assertEqual(p.doc(1)["custom_fields"], [])

    def test_a_reminder_four_weeks_before_the_notice_must_be_in_once(self):
        fields = {F["Anbieter"]: "Stadtwerke Beispielstadt GmbH", F["Kündigungsfrist"]: "drei Monate zum Ende der Laufzeit",
                  F["Nächster Kündigungstermin"]: "2027-03-31"}
        p, h = Paperless([vertrag(fields=fields)]), Haus()
        r = self.run_job(p, haus=h, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        [rem] = h.reminders()
        self.assertEqual(rem["wann"], "2026-12-03", "31.03.2027 − 3 Monate = 31.12.2026, − 28 Tage")
        self.assertIn("31.12.2026", rem["text"])
        self.assertIn("Stadtwerke Beispielstadt GmbH", rem["text"])
        self.assertIn("https://paperless.example.org/documents/1/details", rem["text"])
        self.assertLessEqual(len(rem["text"]), 500, "Haus's cap on a reminder's text")
        self.run_job(p, haus=h, ai="false")
        self.assertEqual(len(h.reminders()), 1, "one reminder per document and notice date")
        p.doc(1)["custom_fields"] = [{"field": F["Kündigungsfrist"], "value": "1 Monat"},
                                     {"field": F["Nächster Kündigungstermin"], "value": "2028-03-31"}]
        self.run_job(p, haus=h, ai="false")
        self.assertEqual(h.reminders()[-1]["wann"], "2028-02-01", "a new notice date: a new reminder")

    def test_a_reminder_already_due_is_today_one_too_late_is_none(self):
        due = vertrag(1, fields={F["Kündigungsfrist"]: "4 Wochen", F["Nächster Kündigungstermin"]: "2026-12-15"})
        late = vertrag(2, fields={F["Kündigungsfrist"]: "3 Monate", F["Nächster Kündigungstermin"]: "2026-12-31"})
        vague = vertrag(3, fields={F["Kündigungsfrist"]: "rechtzeitig", F["Nächster Kündigungstermin"]: "2027-12-31"})
        p, h = Paperless([due, late, vague]), Haus()
        r = self.run_job(p, haus=h, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["wann"] for x in h.reminders()], ["2026-10-26"], "17.11. − 28 Tage is past: today")
        self.assertIn("rechtzeitig", r.stdout, "a notice period it cannot read is said, not guessed")

    def test_without_haus_no_reminder_and_it_says_so(self):
        p = Paperless([vertrag(fields={F["Kündigungsfrist"]: "3 Monate", F["Nächster Kündigungstermin"]: "2027-03-31"})])
        r = self.run_job(p, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Haus", r.stdout)
        self.run_job(p, haus=(h := Haus()), ai="false")
        self.assertEqual(len(h.reminders()), 1, "not recorded while there was no Haus")

    def test_the_unit_hands_the_token_by_name_never_on_argv(self):
        unit = (DIR / "paperless-vertraege.service").read_text()
        exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        self.assertIn("-e HAUSHALT_TOKEN ", exec_line)
        self.assertNotIn("${HAUSHALT_TOKEN}", exec_line)
        self.assertIn("--user paperless", exec_line)
        self.assertIn("Environment=HAUSHALT_TOKEN=${HAUSHALT_TOKEN}", unit)
        self.assertTrue((DIR / "paperless-vertraege.timer").exists())


if __name__ == "__main__":
    unittest.main()
