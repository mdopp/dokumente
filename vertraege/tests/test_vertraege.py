"""#709: contract data into Paperless' contract fields, and the reminder before a
notice date. paperless-vertraege.py asks the household model for the fields of
a contract's text, keeps only values the text backs up word for word, fills
only empty fields, notes what it filled, and makes one reminder in Haus's
Haushalt per document and notice date. Against a fake Paperless, a fake model
and a fake Haus, never real ones; no real names or addresses."""
import base64, hashlib, json, os, subprocess, sys, tempfile, threading, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
SCRIPT = DIR / "paperless-vertraege.py"
AUTH = "Basic " + base64.b64encode(b"admin:adminpw").decode()
NAMES = ["Anbieter", "Vertragsnummer", "Vertragsbeginn", "Vertragsende", "Kündigungsfrist",
         "Nächster Kündigungstermin", "Betrag", "Zahlungsintervall", "Rechnungsnummer", "Fällig am"]
KINDS = ["string", "string", "date", "date", "string", "date", "monetary", "string", "string", "date"]
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

    def __init__(self, docs, types=None, tags=None, users=None, correspondents=None, names=NAMES):
        self.docs, self.calls, self.notes, self.bulk, self.v3 = docs, [], {}, [], True
        self.users = users if users is not None else [{"id": 1, "username": "admin", "first_name": "", "last_name": ""}]
        self.correspondents = correspondents if correspondents is not None else []
        self.types = types if types is not None else [{"id": 1, "name": "Vertrag"}, {"id": 2, "name": "Rechnung"}]
        self.tags = tags if tags is not None else [{"id": 1, "name": "Versicherung"}]
        self.fields = [{"id": F[n], "name": n, "data_type": k} for n, k in zip(NAMES, KINDS) if n in names] + [
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
                if method == "GET" and path == "/api/users/":
                    if fake.users is None:
                        return reply(self, 403, {"detail": "no"})
                    return reply(self, 200, {"next": None, "results": fake.users})
                if method == "GET" and path == "/api/correspondents/":   # Paperless counts a correspondent's documents
                    return reply(self, 200, {"next": None, "results": [
                        {**c, "document_count": sum(1 for d in fake.docs if d.get("correspondent") == c["id"])} for c in fake.correspondents]})
                if method == "POST" and path == "/api/tags/":
                    t = {**body, "id": 100 + len(fake.tags)}
                    fake.tags.append(t)
                    return reply(self, 201, t)
                if method == "POST" and path == "/api/documents/reprocess/" and fake.v3:   # Paperless 3.0
                    fake.bulk.append({**body, "method": "reprocess"})
                    return reply(self, 200, {"result": "OK"})
                if method == "POST" and path == "/api/documents/bulk_edit/" and not fake.v3:   # 2.x
                    fake.bulk.append(body)
                    return reply(self, 200, {"result": "OK"})
                if method == "GET" and path == "/api/documents/":
                    out = fake.docs
                    if "document_type__id__in" in q:
                        ids = {int(i) for i in q["document_type__id__in"].split(",")}
                        out = [d for d in out if d.get("document_type") in ids]
                    if "tags__id__in" in q:
                        ids = {int(i) for i in q["tags__id__in"].split(",")}
                        out = [d for d in out if ids & set(d.get("tags") or [])]
                    if "title__icontains" in q:
                        w = urllib.parse.unquote(q["title__icontains"]).casefold()
                        out = [d for d in out if w in (d.get("title") or "").casefold()]
                    return reply(self, 200, {"next": None, "results": out})
                parts = path.strip("/").split("/")
                if parts[:2] == ["api", "documents"] and len(parts) >= 3 and parts[2].isdigit():
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

            def do_DELETE(self):
                self.any("DELETE")

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


def vertrag(i=1, fields=None, typ=1, tags=None, text=TEXT, title="Stromvertrag", correspondent=None):
    return {"id": i, "title": title, "content": text, "document_type": typ, "tags": tags or [], "correspondent": correspondent,
            "custom_fields": [{"field": k, "value": v} for k, v in (fields or {}).items()]}


ANSWER = {"Anbieter": {"wert": "Stadtwerke Beispielstadt GmbH", "beleg": "Stadtwerke Beispielstadt GmbH"},
          "Vertragsnummer": {"wert": "SW-12345", "beleg": "Stromvertrag Nr. SW-12345"},
          "Vertragsbeginn": {"wert": "2026-04-01", "beleg": "Vertragsbeginn: 01.04.2026"},
          "Vertragsende": {"wert": "2027-03-31", "beleg": "Mindestlaufzeit endet am 31.03.2027"},
          "Kündigungsfrist": {"wert": "3 Monate", "beleg": "Kündigungsfrist: 3 Monate"},
          "Nächster Kündigungstermin": {"wert": "2027-03-31", "beleg": "endet am 31.03.2027"},
          "Betrag": {"wert": 84.5, "beleg": "84,50 EUR"},
          "Zahlungsintervall": {"wert": "monatlich", "beleg": "84,50 EUR monatlich"}}


class Lauf(unittest.TestCase):
    """one run of the script as the unit feeds it, against the fakes"""

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


class Vertraege(Lauf):
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
        rechnung = {F["Betrag"]: "EUR10.00", F["Rechnungsnummer"]: "R-1", F["Fällig am"]: "2026-01-31"}   # #818: an invoice, its fields full
        p = Paperless([vertrag(1, typ=2, title="Rechnung", fields=rechnung), vertrag(2, typ=None, tags=[1]), vertrag(3, fields=full)])
        m = Model(ANSWER)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1, "the invoice is no contract, the full one has nothing to fill")
        self.assertEqual(p.value(2, F["Vertragsnummer"]), "SW-12345", "tagged Versicherung: a contract")
        self.assertIsNone(p.value(1, F["Vertragsnummer"]))

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
        self.assertIn("paperless-vertraege.sh", exec_line)
        self.assertNotIn("${HAUSHALT_TOKEN}", exec_line)
        self.assertIn("Environment=HAUSHALT_TOKEN=${HAUSHALT_TOKEN}", unit)
        self.assertTrue((DIR / "paperless-vertraege.timer").exists())
        sh = (DIR / "paperless-vertraege.sh").read_text()
        self.assertIn("-e HAUSHALT_TOKEN ", sh)
        self.assertIn("-e HAUSHALT_NAMEN ", sh)
        self.assertIn("Environment=HAUSHALT_NAMEN=${HAUSHALT_NAMEN}", unit)
        self.assertIn("--user paperless", sh)
        self.assertNotIn("$HAUSHALT_TOKEN", sh)

    # #721 (a): a notice date that passed moves on by the renewal the text names
    def test_a_past_notice_date_moves_on_by_the_renewal_the_text_names(self):
        text = TEXT + "\nDer Vertrag verlängert sich jeweils um 12 Monate, wenn er nicht fristgerecht gekündigt wird."
        p, h = Paperless([vertrag(text=text, fields={F["Vertragsende"]: "2026-03-31", F["Kündigungsfrist"]: "3 Monate",
                                                     F["Nächster Kündigungstermin"]: "2026-03-31"})]), Haus()
        r = self.run_job(p, haus=h, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Nächster Kündigungstermin"]), "2027-03-31")
        self.assertEqual(p.value(1, F["Vertragsende"]), "2026-03-31", "only the notice date moves, not a field with the same day")
        self.assertEqual(p.value(1, F["Kündigungsfrist"]), "3 Monate", "the other fields stay")
        [note] = p.notes[1]
        self.assertIn("31.03.2027", note)
        self.assertIn("verlängert sich jeweils um 12 Monate", note, "the note quotes the text it stands on")
        self.assertNotIn(PRUEFEN_ID, p.doc(1)["tags"])
        [rem] = h.reminders()
        self.assertEqual(rem["wann"], "2026-12-03", "the new notice date gets its reminder at once")
        self.run_job(p, haus=h, ai="false")
        self.assertEqual(len(p.notes[1]), 1, "moved once")
        self.assertEqual(len(h.reminders()), 1)

    def test_a_renewal_keeps_the_end_of_the_month_and_skips_terms_already_past(self):
        text = TEXT + "\nDanach verlängert sich der Vertrag stillschweigend um ein weiteres Jahr."
        p = Paperless([vertrag(text=text, fields={F["Kündigungsfrist"]: "1 Monat", F["Nächster Kündigungstermin"]: "2024-02-29"})])
        r = self.run_job(p, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Nächster Kündigungstermin"]), "2027-02-28", "29.02.2024 + 3 Jahre, each a month's end")

    def test_a_past_notice_date_without_a_renewal_in_the_text_is_marked_once(self):
        vague = TEXT + "\nDer Vertrag verlängert sich um 12 Monate oder um 24 Monate, je nach Tarif."
        p = Paperless([vertrag(1, fields={F["Kündigungsfrist"]: "3 Monate", F["Nächster Kündigungstermin"]: "2026-03-31"}),
                       vertrag(2, text=vague, fields={F["Kündigungsfrist"]: "3 Monate", F["Nächster Kündigungstermin"]: "2026-03-31"})])
        r = self.run_job(p, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        tag = next(t for t in p.tags if t["name"] == "prüfen")
        for i in (1, 2):
            self.assertEqual(p.value(i, F["Nächster Kündigungstermin"]), "2026-03-31", "no evidence: no new date")
            self.assertIn(tag["id"], p.doc(i)["tags"])
            self.assertIn("31.03.2026", p.notes[i][0])
        self.run_job(p, ai="false")
        self.assertEqual([len(p.notes[i]) for i in (1, 2)], [1, 1], "said once")

    # #721 (b): "zum Monats-/Quartals-/Jahresende"
    def test_a_notice_period_to_the_end_of_a_month_quarter_or_year(self):
        p, h = Paperless([
            vertrag(1, fields={F["Kündigungsfrist"]: "1 Monat zum Monatsende", F["Nächster Kündigungstermin"]: "2027-02-28"}),
            vertrag(2, fields={F["Kündigungsfrist"]: "3 Monate zum Ende eines Kalendervierteljahres", F["Nächster Kündigungstermin"]: "2027-06-30"}),
            vertrag(3, fields={F["Kündigungsfrist"]: "6 Wochen zum Quartalsende", F["Nächster Kündigungstermin"]: "2027-06-30"}),
            vertrag(4, fields={F["Kündigungsfrist"]: "3 Monate zum Jahresende", F["Nächster Kündigungstermin"]: "2027-12-31"}),
            vertrag(5, fields={F["Kündigungsfrist"]: "3 Monate zum Quartalsende", F["Nächster Kündigungstermin"]: "2027-02-15"})]), Haus()
        r = self.run_job(p, haus=h, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        bis = {int(x["text"].split("/documents/")[1].split("/")[0]): x["text"].split("Kündigen bis ")[1][:10] for x in h.reminders()}
        self.assertEqual(bis[1], "31.01.2027", "28.02. − 1 Monat zum Monatsende: the end of January, not the 28th")
        self.assertEqual(bis[2], "31.03.2027")
        self.assertEqual(bis[3], "19.05.2027", "weeks count back by the day")
        self.assertEqual(bis[4], "30.09.2027")
        self.assertEqual(bis[5], "15.11.2026", "a date that is no quarter's end is never moved later")
        tag = next(t for t in p.tags if t["name"] == "prüfen")
        self.assertIn(tag["id"], p.doc(5)["tags"])
        self.assertIn("Quartal", p.notes[5][0])
        self.assertEqual(sorted(p.notes), [5], "only the contradictory one is marked")

    def test_the_model_keeps_the_end_of_period_rule(self):
        text = TEXT.replace("3 Monate zum Ende der Laufzeit", "3 Monate zum Quartalsende")
        answer = {**ANSWER, "Kündigungsfrist": {"wert": "drei Monate zum Quartalsende", "beleg": "Kündigungsfrist: 3 Monate zum Quartalsende"}}
        p = Paperless([vertrag(text=text)])
        r = self.run_job(p, Model(answer))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Kündigungsfrist"]), "3 Monate zum Quartalsende")

    # #721 (c): a text layer that is letter salad is recognised again, a few a run
    def test_letter_salad_is_recognised_again_once_and_marked_when_it_stays(self):
        docs = [vertrag(i, typ=None, title="Scan %d" % i, text=SALAT) for i in range(1, 6)]
        docs += [vertrag(6, typ=2, title="Brief", text=BRIEF, fields=VOLL), vertrag(7, typ=2, title="Rechnung", text=RECHNUNG, fields=VOLL),
                 vertrag(8, title="Vertrag", text=SALAT)]
        p, m = Paperless(docs), Model(ANSWER)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m.asks, [], "a contract of letter salad is not read")
        ids = [i for b in p.bulk for i in b["documents"]]
        self.assertTrue(all(b["method"] == "reprocess" for b in p.bulk), p.bulk)
        self.assertEqual(len(ids), 3, "three a run: OCR is the box's CPU")
        self.assertNotIn(6, ids)
        self.assertNotIn(7, ids, "an invoice of numbers is no salad")
        self.run_job(p, m)
        ids = [i for b in p.bulk for i in b["documents"]]
        self.assertEqual(sorted(ids), [1, 2, 3, 4, 5, 8], "the next three the next run, none twice")
        # recognised again: one reads now, the others are salad still
        p.doc(1)["content"] = BRIEF
        r = self.run_job(p, m, heute="2026-10-28")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([i for b in p.bulk for i in b["documents"]]), 6, "no second recognition")
        tag = next(t for t in p.tags if t["name"] == "prüfen")
        self.assertNotIn(tag["id"], p.doc(1)["tags"])
        self.assertIn(tag["id"], p.doc(2)["tags"], "still salad after it was recognised again: the family looks")
        self.assertTrue(any("neu scannen" in n for n in p.notes[2]), p.notes)
        self.assertEqual(m.asks, [], "the salad contract stays unread")
        self.run_job(p, m, heute="2026-10-29")
        self.assertEqual(len([n for n in p.notes[2] if "neu scannen" in n]), 1, "said once")

    def test_an_older_paperless_reprocesses_through_bulk_edit(self):
        p = Paperless([vertrag(1, typ=2, text=SALAT)])
        p.v3 = False
        r = self.run_job(p, ai="false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.bulk, [{"documents": [1], "method": "reprocess", "parameters": {}}])

    # #721 (d): the reminder day reaches the owner over the Bote, through laib-notify's eingang
    def test_on_the_reminder_day_a_message_for_the_bote_once(self):
        fields = {F["Anbieter"]: "Stadtwerke Beispielstadt GmbH", F["Kündigungsfrist"]: "3 Monate", F["Nächster Kündigungstermin"]: "2027-03-31"}
        p = Paperless([vertrag(fields=fields)])
        r = self.run_job(p, ai="false", heute="2026-11-20")
        self.assertEqual(meldungen(r.stdout), [], "before the reminder day: nothing")
        r = self.run_job(p, ai="false", heute="2026-12-03")
        [m] = meldungen(r.stdout)
        self.assertEqual(m["betreff"], "Kündigung prüfen: Stromvertrag")
        self.assertIn("Kündigen bis 31.12.2026", m["text"])
        self.assertIn("Stadtwerke Beispielstadt GmbH", m["text"])
        self.assertIn("https://paperless.example.org/documents/1/details", m["text"])
        self.assertEqual(m["an"], "")
        self.assertIsInstance(m["at"], int)
        r = self.run_job(p, ai="false", heute="2026-12-04")
        self.assertEqual(meldungen(r.stdout), [], "once per notice date")
        r = self.run_job(Paperless([vertrag(fields=fields)]), ai="false", heute="2027-01-02")
        self.assertEqual(meldungen(r.stdout), [], "after the notice had to be in: too late, nothing")


# #737: the household's own people are never a document's correspondent by the AI's doing.
# Invented names only (the Duden's Mustermann family), never the box's.
USERS = [{"id": 1, "username": "admin", "first_name": "", "last_name": ""},
         {"id": 2, "username": "erika", "first_name": "Erika", "last_name": "Mustermann"},   # from Authelia's name claim
         {"id": 3, "username": "joerg", "first_name": "Jörg", "last_name": "Beispiel-Weiß"},
         {"id": 4, "username": "kim", "first_name": "", "last_name": ""}]                    # no display name: the login only
KORR = [{"id": 1, "name": "Erika Mustermann"}, {"id": 2, "name": "Stadtwerke Beispielstadt"},
        {"id": 3, "name": "Mustermann, Erika"}, {"id": 4, "name": "Familie Mustermann"}, {"id": 5, "name": "JOERG BEISPIEL-WEISS"},
        {"id": 6, "name": "Kim"}, {"id": 7, "name": "Erika Mustermann Immobilien GmbH"}, {"id": 8, "name": "Erika"},
        {"id": 9, "name": "Frau Lena Mustermann"}, {"id": 10, "name": "Herrn Dr. Jörg Beispiel-Weiß"}]
OK_TAG = 50


# #818: invented invoices, as OCR and mail deliver them
MALER = ("Malerbetrieb Beispiel GmbH\nMusterweg 3, 12345 Beispielstadt\nRechnungsdatum: 04.05.2026\nRech.-\nNr.: 2026/\n0042\n"
         "Leistung: Fassadenanstrich\nNettobetrag 1.037,45 €\nzzgl. 19 % MwSt. 197,11 €\nGesamtbetrag 1.234,56 €\n"
         "zahlbar bis 18.05.2026 ohne Abzug")
SHOP = ("Online-Shop Beispiel\nRechnung Nr. RE-88123\nRechnungsdatum 02.03.2026\nKundennummer K-555\n"
        "Rechnungsbetrag: EUR 87,00\nZahlungsziel 14 Tage")
MALER_ANTWORT = {"Betrag": {"wert": "1.234,56 €", "beleg": "Gesamtbetrag 1.234,56 €"},
                 "Rechnungsnummer": {"wert": "2026/0042", "beleg": "Rech.-Nr.: 2026/0042"},
                 "Fällig am": {"wert": "2026-05-18", "beleg": "zahlbar bis 18.05.2026"}}
SHOP_ANTWORT = {"Betrag": {"wert": 87, "beleg": "Rechnungsbetrag: EUR 87,00"},
                "Rechnungsnummer": {"wert": "RE-88123", "beleg": "Rechnung Nr. RE-88123"},
                "Fällig am": None,
                "Rechnungsdatum": {"wert": "2026-03-02", "beleg": "Rechnungsdatum 02.03.2026"},
                "Zahlungsziel": {"wert": "14 Tage", "beleg": "Zahlungsziel 14 Tage"}}
VOLL = {F["Betrag"]: "EUR57.62", F["Rechnungsnummer"]: "2026-0042", F["Fällig am"]: "2026-05-26"}   # an invoice with nothing to fill


def rechnung(i=1, text=MALER, title="2026-05-04 Malerbetrieb Beispiel - Rechnung Fassade", typ=None, tags=None, fields=None):
    return vertrag(i, typ=typ, tags=tags, text=text, title=title, fields=fields)


class Rechnungen(Lauf):
    """#818: the same reader fills Betrag, Rechnungsnummer and Fällig am of an invoice"""

    def prüfen(self, p, i):
        t = next((t for t in p.tags if t["name"] == "prüfen"), None)
        return t is not None and t["id"] in p.doc(i)["tags"]

    def test_a_scanned_invoice_by_its_title_fills_amount_number_and_due_date(self):
        p, m = Paperless([rechnung()]), Model(MALER_ANTWORT)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Betrag"]), "EUR1234.56", "„1.234,56 €“ is 1234.56")
        self.assertEqual(p.value(1, F["Rechnungsnummer"]), "2026/0042", "the number OCR split across lines, joined")
        self.assertEqual(p.value(1, F["Fällig am"]), "2026-05-18")
        self.assertIsNone(p.value(1, F["Anbieter"]), "an invoice is asked for no contract field")
        self.assertFalse(self.prüfen(p, 1))
        self.assertIn("Rechnungsdaten aus dem Text übernommen (laib): Betrag, Rechnungsnummer, Fällig am.", p.notes[1][0])
        _, headers, body = m.asks[0]
        self.assertEqual(headers.get("X-Laib-Background"), "paperless", "background work, as the contracts")
        ask = body["messages"][-1]["content"]
        self.assertIn("Rechnungsnummer", ask)
        self.assertNotIn("Kündigungsfrist", ask)
        self.run_job(p, m)
        self.assertEqual(len(m.asks), 1, "once per text")

    def test_only_the_scan_services_art_at_the_titles_start_makes_an_invoice(self):
        p = Paperless([rechnung(1, title="Rechnung 2026-0042"), rechnung(2, title="2026-05-04 Mosel - Rechnung Schornsteinfeger"),
                       rechnung(3, title="Mahnung zur Rechnung 4711"), rechnung(4, title="2026-05-04 Mosel - Rechnungskorrektur"),
                       rechnung(5, title="Brief", tags=[7]), rechnung(6, title="Brief", typ=2)],
                      tags=[{"id": 1, "name": "Versicherung"}, {"id": 7, "name": "rechnung"}])
        m = Model(MALER_ANTWORT)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i for i in range(1, 7) if p.value(i, F["Betrag"])], [1, 2, 5, 6],
                         "by title, by tag, by document type; a reminder about an invoice is none")

    def test_a_payment_term_counts_from_the_invoice_date(self):
        p = Paperless([rechnung(text=SHOP, title="Ihre Bestellung", typ=2)])
        r = self.run_job(p, Model(SHOP_ANTWORT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Betrag"]), "EUR87.00", "„EUR 87,00“")
        self.assertEqual(p.value(1, F["Rechnungsnummer"]), "RE-88123")
        self.assertEqual(p.value(1, F["Fällig am"]), "2026-03-16", "02.03.2026 and 14 days")
        self.assertFalse(self.prüfen(p, 1))
        self.assertIn("Fällig am: Rechnungsdatum 02.03.2026 und „Zahlungsziel 14 Tage“", p.notes[1][0])

    def test_a_payment_term_without_the_invoice_date_or_from_receipt_is_left_to_the_owner(self):
        ohne = {**SHOP_ANTWORT, "Rechnungsdatum": None}
        eingang = {**SHOP_ANTWORT, "Zahlungsziel": {"wert": "14 Tage", "beleg": "Zahlungsziel 14 Tage nach Erhalt der Rechnung"}}
        for antwort, text in ((ohne, SHOP), (eingang, SHOP + " nach Erhalt der Rechnung")):
            p = Paperless([rechnung(text=text, typ=2)])
            r = self.run_job(p, Model(antwort))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(p.value(1, F["Fällig am"]), "no day is guessed")
            self.assertEqual(p.value(1, F["Betrag"]), "EUR87.00", "the rest is filled")
            self.assertTrue(self.prüfen(p, 1))
            self.assertIn("Fällig am", p.notes[1][0].split("prüfen", 1)[1])
            os.remove(self.record)

    def test_a_due_date_or_amount_not_in_its_words_is_not_taken(self):
        text = SHOP.replace("Zahlungsziel 14 Tage", "Der Betrag ist fällig am 30.06.2026.")
        antwort = {"Betrag": {"wert": 870, "beleg": "Rechnungsbetrag: EUR 87,00"},
                   "Rechnungsnummer": {"wert": "RE-88124", "beleg": "Rechnung Nr. RE-88123"},
                   "Fällig am": {"wert": "2026-06-03", "beleg": "fällig am 30.06.2026"}}
        p = Paperless([rechnung(text=text, typ=2)])
        r = self.run_job(p, Model(antwort))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.value(1, F[n]) for n in ("Betrag", "Rechnungsnummer", "Fällig am")], [None, None, None])
        self.assertTrue(self.prüfen(p, 1))
        for n in ("Betrag", "Rechnungsnummer", "Fällig am"):
            self.assertIn(n, p.notes[1][0])

    def test_a_credit_note_or_a_negative_sum_is_never_an_invoice_amount(self):
        gutschrift = "Online-Shop Beispiel\nGutschrift Nr. GS-551\nzur Rechnung RE-88123\nWir erstatten Ihnen 49,90 €."
        minus = "Stadtwerke Beispielstadt\nRechnung Nr. 4711\nRechnungsbetrag -25,00 €"
        dollar = "Example Shop\nRechnung Nr. 4712\nGesamtbetrag USD 87.00"
        cases = [(gutschrift, {"wert": 49.9, "beleg": "erstatten Ihnen 49,90 €"}, "Gutschrift"),
                 (minus, {"wert": -25, "beleg": "Rechnungsbetrag -25,00 €"}, "negativ"),
                 (minus, {"wert": "25,00", "beleg": "Rechnungsbetrag -25,00 €"}, "negativ"),
                 (dollar, {"wert": 87, "beleg": "Gesamtbetrag USD 87.00"}, "Euro")]
        for text, betrag, warum in cases:
            p = Paperless([rechnung(text=text, tags=[1])], tags=[{"id": 1, "name": "Rechnung"}])
            r = self.run_job(p, Model({"Betrag": betrag}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(p.value(1, F["Betrag"]), text)
            self.assertTrue(self.prüfen(p, 1), "never silently: %s" % text)
            self.assertIn(warum, p.notes[1][0])
            os.remove(self.record)

    def test_the_owners_value_stays_and_a_contract_keeps_its_regular_amount(self):
        p = Paperless([rechnung(fields={F["Betrag"]: "EUR10.00"}), vertrag(2)])
        r = self.run_job(p, Model({**ANSWER, **MALER_ANTWORT, "Betrag": {"wert": 84.5, "beleg": "84,50 EUR"}}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.value(1, F["Betrag"]), "EUR10.00", "a value the owner set is never changed")
        self.assertFalse(self.prüfen(p, 1), "a value not taken for a field the owner filled is no reason to look")
        self.assertEqual(p.value(1, F["Rechnungsnummer"]), "2026/0042")
        self.assertEqual(p.value(2, F["Betrag"]), "EUR84.50", "the contract's Betrag, read as before")
        self.assertIsNone(p.value(2, F["Rechnungsnummer"]), "a contract is asked for no invoice field")

    def test_at_most_pro_lauf_documents_a_run_older_invoices_bit_by_bit(self):
        p = Paperless([rechnung(i, typ=2) for i in range(1, 13)])
        m = Model(MALER_ANTWORT)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 10)
        self.assertIn("im nächsten Lauf", r.stdout)
        self.assertEqual([i for i in range(1, 13) if p.value(i, F["Betrag"])], list(range(1, 11)), "the oldest first")
        self.run_job(p, m)
        self.assertEqual(len(m.asks), 12)

    def test_a_contract_read_before_is_asked_again_once_when_it_is_also_an_invoice(self):
        d = rechnung(typ=1)   # a contract by its type, an invoice by its title
        sha = hashlib.sha256(d["content"].encode()).hexdigest()[:16]
        with open(self.record, "w") as f:   # the record of a run before #818: no "felder"
            json.dump({"dokumente": {"1": {"text": sha, "gefuellt": [], "verworfen": []}}}, f)
        p, m = Paperless([d]), Model(MALER_ANTWORT)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1)
        self.assertEqual(p.value(1, F["Rechnungsnummer"]), "2026/0042")
        self.run_job(p, m)
        self.assertEqual(len(m.asks), 1)

    def test_without_the_invoice_fields_contracts_are_read_and_it_says_so(self):
        p = Paperless([rechnung(1, typ=2), vertrag(2)], names=NAMES[:8])
        m = Model(ANSWER)
        r = self.run_job(p, m)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1)
        self.assertEqual(p.value(2, F["Vertragsnummer"]), "SW-12345")
        self.assertIn("Rechnungsnummer", r.stdout)
        self.assertIn("laib apply paperless", r.stdout)

    def test_the_unit_hands_the_invoice_kinds_on(self):
        self.assertIn("Environment=RECHNUNG_ARTEN=${RECHNUNG_ARTEN}", (DIR / "paperless-vertraege.service").read_text())
        self.assertIn("-e RECHNUNG_ARTEN ", (DIR / "paperless-vertraege.sh").read_text())
        self.assertIn("RECHNUNG_ARTEN: Rechnung", (DIR / "values.example.yml").read_text())


class Korrespondenten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.record = os.path.join(self.tmp, "laib-vertraege.json")

    def run_job(self, p, **extra):
        return Vertraege.run_job(self, p, ai="false", **extra)

    def brief(self, i, korr, tags=None):
        return vertrag(i, typ=2, title="Brief %d" % i, text=BRIEF, correspondent=korr, tags=tags)

    def test_a_household_name_is_cleared_with_a_note_the_sender_stays_and_a_second_run_does_nothing(self):
        alt = self.brief(1, 1)   # set before the pass first ran: the owner's archive
        p = Paperless([alt], users=USERS, correspondents=KORR, tags=[{"id": OK_TAG, "name": "Korrespondent-OK"}])
        r = self.run_job(p, HAUSHALT_NAMEN="Lena Mustermann")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(p.doc(1)["correspondent"], 1, "a correspondent set before the pass existed stays")
        self.assertEqual(p.notes, {})
        with open(self.record) as f:
            self.assertIn("seit", json.load(f)["korrespondenten"], "the first run is recorded")
        # afterwards the AI files new ones
        p.docs += [self.brief(i, k) for i, k in ((2, 1), (3, 2), (4, 3), (5, 4), (6, 5), (7, 6), (8, 7), (9, 8), (10, 9), (11, 10))]
        p.docs.append(self.brief(12, 1, tags=[OK_TAG]))
        r = self.run_job(p, HAUSHALT_NAMEN="Lena Mustermann")
        self.assertEqual(r.returncode, 0, r.stderr)
        cleared = sorted(d["id"] for d in p.docs if d.get("correspondent") is None)
        self.assertEqual(cleared, [2, 4, 5, 6, 7, 9, 10, 11],
                         "full name either order, Familie + surname, umlauts and case folded, a first name or a login as the whole name, "
                         "a name from HAUSHALT_NAMEN, salutations and titles dropped")
        self.assertEqual(p.doc(3)["correspondent"], 2, "the real sender stays")
        self.assertEqual(p.doc(8)["correspondent"], 7, "a company that carries a person's name is no person")
        self.assertEqual(p.doc(12)["correspondent"], 1, "the skip tag keeps it, whatever the case")
        self.assertEqual(p.doc(1)["correspondent"], 1)
        [note] = p.notes[2]
        self.assertIn("„Erika Mustermann“", note)
        self.assertIn("Haushalt", note)
        self.assertIn("korrespondent-ok", note.casefold(), "the note says how to keep it")
        patches = [c for c in p.calls if c[0] == "PATCH"]
        self.assertTrue(all(c[3] == {"correspondent": None} for c in patches), patches)
        self.assertFalse([c for c in p.calls if c[0] == "DELETE"], "a correspondent is never deleted")
        # a second run does nothing
        n = len(p.calls)
        r = self.run_job(p, HAUSHALT_NAMEN="Lena Mustermann")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in p.calls[n:] if c[0] in ("PATCH", "POST")], p.calls[n:])
        self.assertEqual([len(v) for v in p.notes.values()], [1] * 8)

    def test_the_skip_tag_is_made_for_the_family_and_correspondents_left_empty_are_listed_not_deleted(self):
        p = Paperless([], users=USERS, correspondents=KORR)
        self.run_job(p)
        p.docs += [self.brief(2, 4), self.brief(3, 1), self.brief(4, 1, tags=[]), self.brief(5, 2)]
        r = self.run_job(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        tag = next(t for t in p.tags if t["name"] == "korrespondent-ok")
        self.assertEqual(tag["matching_algorithm"], 0, "the tag never files a document by itself")
        self.assertIsNone(tag["owner"], "the family sees the tag")
        with open(self.record) as f:
            leer = json.load(f)["korrespondenten"]["leer"]
        self.assertEqual(leer, ["Erika Mustermann", "Familie Mustermann"],
                         "household correspondents without documents, for the owner to delete: Paperless marks none as the AI's")
        self.assertNotIn("Mustermann, Erika", leer, "one with no document before is not the pass's doing")
        self.assertIn("Familie Mustermann", r.stdout)
        self.assertFalse([c for c in p.calls if c[0] == "DELETE"])

    def test_without_the_users_api_the_pass_says_so_and_the_rest_runs(self):
        p = Paperless([self.brief(1, 1)], users=USERS, correspondents=KORR)
        p.users = None
        r = self.run_job(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Haushalt", r.stdout)
        self.assertEqual(p.doc(1)["correspondent"], 1)


def meldungen(out):
    return [json.loads(l[len("meldung "):]) for l in out.splitlines() if l.startswith("meldung ")]


PRUEFEN_ID = 101
BRIEF = ("Sehr geehrte Damen und Herren, vielen Dank für Ihr Schreiben vom 12. Mai. Wir bestätigen hiermit die Änderung "
         "Ihres Vertrags zum 31.03.2027. Bitte beachten Sie, dass eine Kündigung schriftlich erfolgen muss. Der Betrag "
         "wird Ihnen auf das bekannte Konto überwiesen. Für Rückfragen sind wir gerne für Sie da. Mit freundlichen Grüßen "
         "Ihre Stadtwerke Beispielstadt GmbH Kundenservice")
RECHNUNG = ("Rechnung Nr. 2026-0042 Datum 12.05.2026 Pos Menge Artikel Einzelpreis Gesamt 1 2 Druckerpatrone schwarz "
            "19,90 39,80 2 1 Papier A4 500 Blatt 4,99 4,99 3 1 Versand 4,90 4,90 Zwischensumme 49,69 MwSt 19% 7,93 "
            "Gesamtbetrag 57,62 EUR Zahlbar bis 26.05.2026 ohne Abzug Lieferdatum entspricht Rechnungsdatum Steuernummer "
            "000/000/00000 Amtsgericht Beispielstadt Geschäftsführung Kundennummer Bestellnummer Zahlungsart Überweisung")
# what Tesseract makes of an upside-down or blurred page (the box, #567)
SALAT = ("ae Wu ZEN NIE aD TER cn Ta PEN ER ne nase Fe ri oa DE HE LE ne EEE Tre En Er ze ae Ze ae Br pr ee Er RE an "
         "ee ee ae ae Sr Sn En Te ne ZZ ae ER ea ri ae ae Er Tue ae i ' Eee ; = NN — = rn DD . ‚ e7 = ae IE ee re")


class Sender(unittest.TestCase):
    """paperless-vertraege.sh, the host's part: runs the script in the container, hands its messages to laib-notify"""

    def test_messages_go_to_eingang_logs_to_the_journal_and_the_exit_code_stays(self):
        tmp = tempfile.mkdtemp()
        podman = os.path.join(tmp, "podman")
        with open(podman, "w") as f:
            f.write("#!/bin/sh\necho \"$@\" >%s/argv\ncat >%s/stdin\necho 'vertraege: eine Zeile'\n"
                    "echo 'meldung {\"an\": \"\", \"betreff\": \"Kündigung prüfen: Strom\", \"text\": \"Kündigen bis 31.12.2026\", \"at\": 1}'\n"
                    "exit 3\n" % (tmp, tmp))
        os.chmod(podman, 0o755)
        skript = os.path.join(tmp, "skript.py")
        with open(skript, "w") as f:
            f.write("print('hallo')")
        env = {"PATH": os.environ["PATH"], "PODMAN": podman, "VERTRAEGE_SKRIPT": skript,
               "NOTIFY_EINGANG": os.path.join(tmp, "eingang"), "HAUSHALT_TOKEN": "haustoken"}
        r = subprocess.run(["sh", str(DIR / "paperless-vertraege.sh")], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 3, "the script's exit code is the unit's")
        self.assertIn("vertraege: eine Zeile", r.stdout)
        self.assertNotIn("meldung {", r.stdout)
        [f] = os.listdir(os.path.join(tmp, "eingang"))
        self.assertTrue(f.startswith("paperless-") and f.endswith(".json"), f)
        with open(os.path.join(tmp, "eingang", f)) as fh:
            self.assertEqual(json.load(fh)["betreff"], "Kündigung prüfen: Strom")
        argv = open(os.path.join(tmp, "argv")).read()
        self.assertNotIn("haustoken", argv)
        self.assertIn("-e HAUSHALT_TOKEN", argv)
        self.assertEqual(open(os.path.join(tmp, "stdin")).read(), "print('hallo')")


if __name__ == "__main__":
    unittest.main()
