"""ordnen.py: the household's Paperless in one order. Types, Bereich tags and
fields are made, near-duplicate types renamed (never Gesundheit's), the tags
that grew move into correspondents, types, Bereiche and Produkte and go once
empty, each document is read by the model and only what the text backs is
filled into what is empty; a backup comes first and puts everything back.
Against a fake Paperless and a fake model; no real names or documents."""
import contextlib, copy, importlib.util, io, json, os, re, tempfile, unittest, urllib.error
from pathlib import Path
from types import SimpleNamespace

# by its path: from the repo's root a bare `import ordnen` finds the ordnen/ folder
_spec = importlib.util.spec_from_file_location("ordnen_skript", Path(__file__).resolve().parent.parent / "ordnen.py")
ordnen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ordnen)


class Fake:
    """Paperless' API in memory: what ordnen.API answers, and every write"""

    def __init__(self, docs, tags=(), typen=(), korr=(), felder=(), users=None, korr_403=False):
        self.docs = {d["id"]: {"document_type": None, "correspondent": None, "tags": [], "custom_fields": [], "content": "", **d} for d in docs}
        self.tags = [dict(t) for t in tags]
        self.typen = [dict(t) for t in typen]
        self.korr = [dict(c) for c in korr]
        self.felder = [dict(f) for f in felder]
        self.views, self.notes, self.writes, self.trash = [], {}, [], []
        self.users = users if users is not None else [{"id": 1, "username": "admin"}, {"id": 4, "username": "anna", "first_name": "Anna", "last_name": "Muster"}]
        self.korr_403 = korr_403
        self.next = 500

    def _id(self):
        self.next += 1
        return self.next

    def all(self, path):
        p, _, q = path.partition("?")
        if p == "/api/documents/":
            ds = [dict(d) for d in self.docs.values()]
            if "ordering=-added" in q:
                ds.sort(key=lambda d: -d["id"])
            return ds
        return [dict(x) for x in {"/api/tags/": self.tags, "/api/document_types/": self.typen, "/api/correspondents/": self.korr,
                                  "/api/custom_fields/": self.felder, "/api/saved_views/": self.views, "/api/users/": self.users,
                                  "/api/trash/": self.trash}[p]]

    def __call__(self, method, path, body=None):
        p, _, q = path.partition("?")
        if method != "GET":
            self.writes.append((method, p, body))
        m = re.fullmatch(r"/api/documents/(\d+)/", p)
        if m and method == "GET":
            return dict(self.docs[int(m.group(1))])
        if m and method == "PATCH":
            self.docs[int(m.group(1))].update(json.loads(json.dumps(body)))
            return dict(self.docs[int(m.group(1))])
        m = re.fullmatch(r"/api/documents/(\d+)/notes/", p)
        if m:
            self.notes.setdefault(int(m.group(1)), []).append(body["note"])
            return {}
        if p == "/api/documents/" and method == "GET":
            t = int(re.search(r"tags__id__all=(\d+)", q).group(1))
            return {"count": sum(1 for d in self.docs.values() if t in d["tags"]), "results": []}
        liste = {"/api/tags/": self.tags, "/api/document_types/": self.typen, "/api/correspondents/": self.korr,
                 "/api/custom_fields/": self.felder, "/api/saved_views/": self.views}
        if method == "POST" and p in liste:
            if p == "/api/correspondents/" and self.korr_403:
                raise urllib.error.HTTPError(p, 403, "Forbidden", {}, None)
            obj = {**json.loads(json.dumps(body)), "id": self._id()}
            if p == "/api/custom_fields/" and obj.get("data_type") == "select":
                for i, o in enumerate(obj["extra_data"]["select_options"]):
                    o.setdefault("id", "opt%d" % i)
            liste[p].append(obj)
            return dict(obj)
        m = re.fullmatch(r"(/api/\w+/)(\d+)/", p)
        if m and m.group(1) in liste:
            lst, i = liste[m.group(1)], int(m.group(2))
            obj = next(x for x in lst if x["id"] == i)
            if method == "PATCH":
                obj.update(json.loads(json.dumps(body)))
                if m.group(1) == "/api/custom_fields/":
                    for k, o in enumerate(obj["extra_data"]["select_options"]):
                        o.setdefault("id", "optn%d" % k)
                return dict(obj)
            if method == "DELETE":
                lst.remove(obj)
                for d in self.docs.values():
                    d["tags"] = [t for t in d["tags"] if t != i]
                return None
        raise AssertionError("unexpected %s %s" % (method, path))


INVOICE = ("Stadtwerke Beispielstadt GmbH\nKundennummer: 4711-0815\nRechnung für Strom und Gas\n"
           "Frau Anna Muster, Beispielweg 1\nTarif: Ökostrom Plus\n")
LAB = "Praxis Dr. Beispiel\nPatient: Anna Muster\nLaborbefund Blutbild\n"


def answer_invoice():
    return {"typ": "Rechnung", "bereiche": ["Haus"], "absender": {"wert": "Stadtwerke Beispielstadt GmbH", "beleg": "Stadtwerke Beispielstadt GmbH"},
            "person": {"wert": "Anna", "beleg": "Frau Anna Muster"}, "kundennummer": {"wert": "4711-0815", "beleg": "Kundennummer: 4711-0815"},
            "versicherungsnummer": {"wert": "VS-999", "beleg": "Versicherung VS-999"},   # not in the text: never taken
            "produkte": {"wert": "Ökostrom Plus", "beleg": "Tarif: Ökostrom Plus"}, "garantie_bis": None, "steuerjahr": None, "sicher": True}


def base(docs=(), **kw):
    return Fake(docs,
                tags=kw.pop("tags", [{"id": 1, "name": "Stapel"}, {"id": 2, "name": "prüfen"}, {"id": 3, "name": "gesundheit", "owner": 6},
                                     {"id": 10, "name": "ERGO"}, {"id": 11, "name": "Glasfaser"}, {"id": 12, "name": "Eigenes Thema"},
                                     {"id": 13, "name": "Rechnung"}, {"id": 14, "name": "Konzert"}]),
                typen=kw.pop("typen", [{"id": 1, "name": "Rechnung"}, {"id": 2, "name": "Befund"}, {"id": 3, "name": "Beitragsrechnung"},
                                       {"id": 4, "name": "Auskunft"}, {"id": 5, "name": "Labor"}]),
                korr=kw.pop("korr", [{"id": 1, "name": "ERGO Versicherung AG"}]), **kw)


def args(**kw):
    a = {"dry_run": False, "personen": "Anna,Ben", "eigentuemer": "anna", "max": None, "zeit": 600.0}
    a.update(kw)
    return SimpleNamespace(**a)


class Struktur(unittest.TestCase):
    def test_types_bereiche_and_fields_are_made_and_duplicates_renamed(self):
        f = base()
        o = ordnen.Ordnung(f, log=lambda *_: None)
        ordnen.struktur(o, ["Anna", "Ben"], log=lambda *_: None)
        namen = {t["name"] for t in f.typen}
        self.assertTrue(set(ordnen.TYPEN) <= namen)
        self.assertNotIn("Beitragsrechnung", namen)
        self.assertEqual(next(t for t in f.typen if t["id"] == 3)["name"], "Beitrag/Bescheid", "renamed in place: its id and documents stay")
        self.assertEqual(next(t for t in f.typen if t["id"] == 4)["name"], "Brief")
        self.assertIn("Befund", namen)
        self.assertIn("Labor", namen)
        self.assertFalse(any(m == "DELETE" for m, _, _ in f.writes), "a type is never deleted")
        tags = {t["name"] for t in f.tags}
        self.assertTrue({"Haus", "Auto", "Versicherung", "Finanzen", "Steuer", "Arbeit & Rente", "Freizeit"} <= tags)
        self.assertNotIn("Gesundheit", tags, "Gesundheit's own tag gesundheit is the Bereich, its name kept")
        felder = {x["name"]: x for x in f.felder}
        self.assertEqual(felder["Produkte"]["data_type"], "longtext")
        self.assertEqual(felder["Steuerjahr"]["data_type"], "integer")
        self.assertEqual([o["label"] for o in felder["Person"]["extra_data"]["select_options"]], ["Anna", "Ben"])

    def test_a_second_run_writes_nothing_and_a_new_person_is_added(self):
        f = base()
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        n = len(f.writes)
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        self.assertEqual(len(f.writes), n)
        ordnen.struktur(ordnen.Ordnung(f), ["Anna", "Ben"], log=lambda *_: None)
        person = next(x for x in f.felder if x["name"] == "Person")
        self.assertEqual([o["label"] for o in person["extra_data"]["select_options"]], ["Anna", "Ben"])
        self.assertEqual(person["extra_data"]["select_options"][0]["id"], "opt0", "Anna's option keeps its id")

    def test_a_medical_type_is_never_merged(self):
        f = base(typen=[{"id": 1, "name": "Auskunft"}])
        old = dict(ordnen.ZUSAMMENLEGEN)
        ordnen.ZUSAMMENLEGEN["Labor"] = "Brief"
        try:
            f.typen.append({"id": 9, "name": "Labor"})
            ordnen.struktur(ordnen.Ordnung(f), [], log=lambda *_: None)
        finally:
            ordnen.ZUSAMMENLEGEN.clear()
            ordnen.ZUSAMMENLEGEN.update(old)
        self.assertEqual(next(t for t in f.typen if t["id"] == 9)["name"], "Labor")

    def test_dry_run_writes_nothing(self):
        f = base()
        ordnen.struktur(ordnen.Ordnung(f, dry=True), ["Anna"], log=lambda *_: None)
        self.assertEqual(f.writes, [])


class Tags(unittest.TestCase):
    def test_company_product_and_type_tags_move_and_go_unknown_and_workflow_tags_stay(self):
        f = base([{"id": 1, "tags": [10, 1]}, {"id": 2, "tags": [11, 12]}, {"id": 3, "tags": [13]},
                  {"id": 4, "tags": [11], "custom_fields": []}])
        o = ordnen.Ordnung(f)
        ordnen.struktur(o, ["Anna"], log=lambda *_: None)
        ordnen.tags_umziehen(o, list(f.all("/api/documents/")), log=lambda *_: None)
        names = {t["name"] for t in f.tags}
        self.assertFalse({"ERGO", "Glasfaser", "Rechnung", "Konzert"} & names)
        self.assertTrue({"Stapel", "prüfen", "gesundheit", "Eigenes Thema"} <= names)
        self.assertEqual(f.docs[1]["correspondent"], 1, "ERGO → the correspondent ERGO Versicherung AG")
        self.assertIn(1, f.docs[1]["tags"])
        haus = o.tag("Haus")["id"]
        produkte = o.feld("Produkte")["id"]
        self.assertIn(haus, f.docs[2]["tags"])
        self.assertIn(12, f.docs[2]["tags"], "a tag without a rule is never touched")
        self.assertEqual({c["field"]: c["value"] for c in f.docs[2]["custom_fields"]}[produkte], "Glasfaser")
        self.assertEqual(f.docs[3]["document_type"], 1)

    def test_a_product_is_added_to_what_is_there(self):
        f = base([{"id": 1, "tags": [11, 14]}])
        o = ordnen.Ordnung(f)
        ordnen.struktur(o, [], log=lambda *_: None)
        ordnen.tags_umziehen(o, list(f.all("/api/documents/")), log=lambda *_: None)
        produkte = o.feld("Produkte")["id"]
        self.assertEqual({c["field"]: c["value"] for c in f.docs[1]["custom_fields"]}[produkte], "Glasfaser, Konzert")
        self.assertTrue({o.tag("Haus")["id"], o.tag("Freizeit")["id"]} <= set(f.docs[1]["tags"]))

    def test_a_tag_named_in_the_contract_kinds_stays(self):
        f = base([{"id": 1, "tags": [13]}])
        os.environ["RECHNUNG_ARTEN"] = "Rechnung"
        try:
            o = ordnen.Ordnung(f)
            ordnen.struktur(o, [], log=lambda *_: None)
            ordnen.tags_umziehen(o, list(f.all("/api/documents/")), log=lambda *_: None)
        finally:
            del os.environ["RECHNUNG_ARTEN"]
        self.assertIn(13, f.docs[1]["tags"])


class Auswerten(unittest.TestCase):
    def setUp(self):
        self.doc = {"id": 7, "title": "Stadtwerke - Rechnung", "content": INVOICE}

    def plan(self, a, korr=()):
        return ordnen.auswerten(a, self.doc, list(ordnen.TYPEN) + ["Befund"], ["Anna", "Ben"], ["Anna Muster", "Ben Muster"], list(korr))

    def test_only_what_the_text_backs(self):
        p = self.plan(answer_invoice())
        self.assertEqual(p["typ"], "Rechnung")
        self.assertEqual(p["bereiche"], ["Haus"])
        self.assertEqual(p["absender"], "Stadtwerke Beispielstadt GmbH")
        self.assertEqual(p["person"], "Anna")
        self.assertEqual(p["felder"], {"Kundennummer": "4711-0815", "Produkte": "Ökostrom Plus"})
        self.assertTrue(any("Versicherungsnummer" in w for w in p["weg"]))
        self.assertEqual(p["pruefen"], [])

    def test_a_known_sender_is_matched(self):
        p = self.plan({**answer_invoice(), "absender": {"wert": "Stadtwerke Beispielstadt", "beleg": "Stadtwerke Beispielstadt"}},
                      ["Stadtwerke Beispielstadt GmbH"])
        self.assertEqual(p["absender"], "Stadtwerke Beispielstadt GmbH")

    def test_ocr_noise_is_no_sender_and_a_hyphen_is_the_same_sender(self):
        self.doc["content"] += "KKKKK\nReha-Zentrum Beispiel\n"
        p = self.plan({**answer_invoice(), "absender": {"wert": "KKKKK", "beleg": "KKKKK"}})
        self.assertIsNone(p["absender"])
        p = self.plan({**answer_invoice(), "absender": {"wert": "Reha-Zentrum Beispiel", "beleg": "Reha-Zentrum Beispiel"}}, ["Rehazentrum Beispiel"])
        self.assertEqual(p["absender"], "Rehazentrum Beispiel")

    def test_a_person_by_full_name_is_the_persons_option(self):
        p = self.plan({**answer_invoice(), "person": {"wert": "Anna Muster", "beleg": "Frau Anna Muster"}})
        self.assertEqual(p["person"], "Anna")
        p = self.plan({**answer_invoice(), "person": {"wert": "Muster Anna", "beleg": "Frau Anna Muster"}})
        self.assertIsNone(p["person"], "only the first name picks the option")
        p = self.plan({**answer_invoice(), "person": {"wert": "Anna und Ben Muster", "beleg": "Frau Anna Muster"}})
        self.assertIsNone(p["person"], "two people: the household, no one")

    def test_an_alias_is_one_sender_with_the_fuller_name(self):
        self.doc["content"] += "DRV Bund\nGemeinschaftslabor\n"
        p = self.plan({**answer_invoice(), "absender": {"wert": "DRV Bund", "beleg": "DRV Bund"}})
        self.assertEqual(p["absender"], "Deutsche Rentenversicherung Bund", "made under the full name")
        p = self.plan({**answer_invoice(), "absender": {"wert": "DRV Bund", "beleg": "DRV Bund"}}, ["Deutsche Rentenversicherung Bund"])
        self.assertEqual(p["absender"], "Deutsche Rentenversicherung Bund")
        p = self.plan({**answer_invoice(), "absender": {"wert": "Gemeinschaftslabor", "beleg": "Gemeinschaftslabor"}})
        self.assertEqual(p["absender"], "LCM Gemeinschaftslabor")

    def test_a_device_or_software_maker_is_never_the_sender(self):
        self.doc["content"] += "Zimmer MedizinSysteme GmbH\nCompuGroup Medical Deutschland AG\nSchiller Apotheke\n"
        for name in ("Zimmer MedizinSysteme", "Zimmer MedizinSysteme GmbH", "CompuGroup Medical Deutschland AG"):
            p = self.plan({**answer_invoice(), "absender": {"wert": name, "beleg": name}})
            self.assertIsNone(p["absender"], name)
            self.assertTrue(any("Hersteller" in w for w in p["weg"]))
            self.assertEqual(p["pruefen"], [], "no sender is no doubt")
        p = self.plan({**answer_invoice(), "absender": {"wert": "Schiller Apotheke", "beleg": "Schiller Apotheke"}})
        self.assertEqual(p["absender"], "Schiller Apotheke")

    def test_the_household_is_never_the_sender_and_a_person_must_be_named(self):
        p = self.plan({**answer_invoice(), "absender": {"wert": "Anna Muster", "beleg": "Frau Anna Muster"},
                       "person": {"wert": "Ben", "beleg": "Frau Anna Muster"}})
        self.assertIsNone(p["absender"])
        self.assertIsNone(p["person"])

    def test_unsure_or_unknown_kind_is_pruefen(self):
        p = self.plan({**answer_invoice(), "typ": "Postkarte", "bereiche": ["Mond"], "sicher": False})
        self.assertIsNone(p["typ"])
        self.assertEqual(p["pruefen"], ["Art nicht erkannt", "Bereich nicht erkannt", "das Modell ist unsicher"])

    def test_tax_year_and_warranty_need_their_words(self):
        self.doc["content"] = "Einkommensteuerbescheid für 2023\nGarantie bis 31.12.2027\n"
        p = self.plan({"typ": "Steuer", "bereiche": ["Steuer"], "sicher": True, "steuerjahr": {"wert": 2023, "beleg": "für 2023"},
                       "garantie_bis": {"wert": "2027-12-31", "beleg": "Garantie bis 31.12.2027"}})
        self.assertEqual(p["felder"], {"Steuerjahr": 2023, "Garantie bis": "2027-12-31"})
        p = self.plan({"typ": "Steuer", "bereiche": ["Steuer"], "sicher": True, "steuerjahr": {"wert": 2022, "beleg": "für 2023"}})
        self.assertEqual(p["felder"], {})


class Lauf(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.f = base([{"id": 1, "title": "Stadtwerke - Rechnung", "content": INVOICE, "tags": [1]},
                       {"id": 2, "title": "Labor", "content": LAB, "tags": [1, 3], "document_type": 5},
                       {"id": 3, "title": "Unklar", "content": "Lorem ipsum", "tags": [1, 10]}])
        self.asked = []

    def ask(self, messages):
        text = messages[1]["content"]
        self.asked.append(text)
        if "Stadtwerke" in text:
            return answer_invoice()
        if "Laborbefund" in text:
            return {"typ": "Brief", "bereiche": ["Finanzen"], "person": {"wert": "Anna", "beleg": "Patient: Anna Muster"}, "sicher": True}
        return {"typ": None, "bereiche": [], "sicher": False}

    def test_dry_run_changes_nothing_and_says_what_would(self):
        text = ordnen.alles(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        self.assertEqual(self.f.writes, [])
        self.assertIn("PROBELAUF", Path(self.dir, "zusammenfassung.txt").read_text())
        self.assertIn("Rechnung", text)
        self.assertIn("ERGO (1 Dok.) → Korrespondent „ERGO Versicherung AG“", text)
        plan = json.loads(Path(self.dir, "plan.json").read_text())
        self.assertEqual(plan["dokumente"]["1"]["setzt"]["typ"], "Rechnung")
        self.assertFalse(list(Path(self.dir).glob("sicherung-*.json")), "a dry run needs no backup")

    def test_a_sample_names_a_renamed_type_by_its_new_name(self):
        text = ordnen.alles(self.f, self.dir, args(dry_run=True), log=lambda *_: None,
                            ask=lambda m: {"typ": "Brief", "bereiche": ["Haus"], "sicher": True})
        self.assertIn('"typ": "Brief"', text)
        self.assertNotIn('"typ": "Auskunft"', text)

    def test_apply_reuses_the_answers_backs_up_first_and_fills_only_what_is_empty(self):
        ordnen.alles(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        n = len(self.asked)
        ordnen.alles(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual(len(self.asked), n, "the dry run's answers are used, the model is not asked again")
        self.assertEqual(len(list(Path(self.dir).glob("sicherung-*.json"))), 1)
        o = ordnen.Ordnung(self.f)
        d1, d2, d3 = self.f.docs[1], self.f.docs[2], self.f.docs[3]
        self.assertEqual(d1["document_type"], o.typ("Rechnung")["id"])
        self.assertEqual(o.korrespondent("Stadtwerke Beispielstadt GmbH")["id"], d1["correspondent"])
        self.assertIn(o.tag("Haus")["id"], d1["tags"])
        felder = {c["field"]: c["value"] for c in d1["custom_fields"]}
        self.assertEqual(felder[o.feld("Person")["id"]], "opt0")
        self.assertEqual(felder[o.feld("Kundennummer")["id"]], "4711-0815")
        self.assertEqual(d2["document_type"], 5, "the type someone set stays")
        self.assertNotIn(o.tag("Finanzen")["id"], d2["tags"], "it has a Bereich (gesundheit): none added")
        self.assertNotIn(2, d2["tags"])
        self.assertIn(2, d3["tags"], "unsure: prüfen")
        self.assertIn("Bitte prüfen", self.f.notes[3][0])
        self.assertEqual(d3["correspondent"], 1)
        self.assertEqual(sorted(v["name"] for v in self.f.views),
                         sorted(list(ordnen.BEREICHE) + ["Anna", "Ben"]))
        self.assertTrue(all(v["owner"] == 4 for v in self.f.views))
        gesundheit = next(v for v in self.f.views if v["name"] == "Gesundheit")
        self.assertEqual(gesundheit["filter_rules"], [{"rule_type": 6, "value": "3"}])
        before = len(self.f.writes)
        ordnen.alles(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual([w for w in self.f.writes[before:] if w[1] != "/api/documents/" ], [], "a second run changes nothing")

    def test_the_backup_puts_everything_back(self):
        before = copy.deepcopy(self.f.docs)
        ordnen.alles(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertNotEqual(before, self.f.docs)
        backup = str(next(Path(self.dir).glob("sicherung-*.json")))
        self.assertEqual(ordnen.wiederherstellen(self.f, backup, log=lambda *_: None), 0)
        ergo = next(t for t in self.f.tags if t["name"] == "ERGO")
        for i, d in before.items():
            now = self.f.docs[i]
            self.assertEqual(now["document_type"], d["document_type"])
            self.assertEqual(now["correspondent"], d["correspondent"])
            self.assertEqual(sorted(now["tags"]), sorted(ergo["id"] if t == 10 else t for t in d["tags"]))
            self.assertEqual(now["custom_fields"], d["custom_fields"])
        self.assertEqual(next(t for t in self.f.typen if t["id"] == 3)["name"], "Beitragsrechnung")

    def test_the_model_away_keeps_what_it_answered(self):
        calls = []

        def flaky(m):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("proxy: the card is lent")
            return self.ask(m)
        ordnen.alles(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=flaky)
        rec = json.loads(Path(self.dir, "ordnen.json").read_text())
        self.assertEqual(len(rec["antworten"]), 1)
        self.assertTrue(rec["modell_weg"])
        self.assertEqual(rec["lauf"]["offen"], 2)


class Neue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_without_the_order_nothing_happens(self):
        f = base([{"id": 1, "title": "x", "content": INVOICE}])
        ordnen.neue(f, self.dir, args(max=None), log=lambda *_: None, ask=lambda m: self.fail("asked"))
        self.assertEqual(f.writes, [])

    def test_new_documents_without_a_bereich_once_each_and_a_sender_paperless_refuses_is_noted(self):
        f = base([{"id": 1, "title": "Stadtwerke", "content": INVOICE}, {"id": 2, "title": "alt", "content": "x", "tags": []}], korr_403=True)
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        haus = next(t["id"] for t in f.tags if t["name"] == "Haus")
        f.docs[2]["tags"] = [haus]
        f.writes.clear()
        asked = []
        ordnen.neue(f, self.dir, args(max=5), log=lambda *_: None, ask=lambda m: asked.append(1) or answer_invoice())
        self.assertEqual(len(asked), 1, "the document with a Bereich is not asked about")
        self.assertIn(haus, f.docs[1]["tags"])
        self.assertIsNone(f.docs[1]["correspondent"])
        self.assertIn("Absender „Stadtwerke Beispielstadt GmbH“ ist noch kein Korrespondent", f.notes[1][0])
        f.docs[1]["tags"] = []
        ordnen.neue(f, self.dir, args(max=5), log=lambda *_: None, ask=lambda m: asked.append(1) or answer_invoice())
        self.assertEqual(len(asked), 1, "seen once: never asked again")


ARZTBRIEF = ("Dr. med. Beispiel, Facharzt für Kardiologie\nPatient: Anna Muster\nDiagnosen:\n- Arterielle Hypertonie\n"
             "Befund: Echo unauffällig, Verdacht auf Belastungsdyspnoe\nMedikation: Ramipril 5 mg 1-0-0, ASS 100 0-1-0\n")
PLAN = "Bundeseinheitlicher Medikationsplan\nfür Anna Muster\nWirkstoff Stärke\nMetoprolol 47,5 mg morgens\n"


def answer_medizin(**kw):
    a = {"typ": "Befund", "bereiche": ["Gesundheit"], "sicher": True,
         "fachrichtung": {"wert": "Kardiologie", "beleg": "Facharzt für Kardiologie"},
         "anlass": {"wert": "Arterielle Hypertonie", "beleg": "Arterielle Hypertonie"},
         "medikamente": {"wert": "Ramipril 5mg 1-0-0, ASS 100 0-1-0", "beleg": "Ramipril 5 mg 1-0-0, ASS 100 0-1-0"}}
    a.update(kw)
    return a


class Medizin(unittest.TestCase):
    def plan(self, a, content=ARZTBRIEF):
        doc = {"id": 9, "title": "Arztbrief", "content": content}
        return ordnen.auswerten(a, doc, list(ordnen.TYPEN) + ["Befund"], ["Anna"], ["Anna Muster"], [])

    def test_the_three_fields_are_made_with_the_specialties(self):
        f = base()
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        felder = {x["name"]: x for x in f.felder}
        self.assertEqual([o["label"] for o in felder["Fachrichtung"]["extra_data"]["select_options"]], list(ordnen.FACHRICHTUNGEN))
        self.assertEqual(felder["Anlass/Diagnose"]["data_type"], "string")
        self.assertEqual(felder["Medikamente"]["data_type"], "longtext")

    def test_each_value_only_where_the_text_has_it(self):
        p = self.plan(answer_medizin())
        self.assertEqual(p["felder"], {"Fachrichtung": "Kardiologie", "Anlass/Diagnose": "Arterielle Hypertonie",
                                       "Medikamente": "Ramipril 5mg 1-0-0, ASS 100 0-1-0"})

    def test_a_bare_string_is_no_evidence_and_is_counted(self):
        p = self.plan(answer_medizin(fachrichtung="Kardiologie", anlass="Arterielle Hypertonie", medikamente=["Ramipril 5 mg"]))
        self.assertEqual(p["felder"], {})
        self.assertEqual(sorted(w for w in p["weg"] if "ohne Beleg" in w),
                         ["Anlass/Diagnose (ohne Beleg)", "Fachrichtung (ohne Beleg)", "Medikamente (ohne Beleg)"])

    def test_the_question_spells_out_the_shape_of_every_value(self):
        system = ordnen.frage({"title": "x", "content": "y"}, ordnen.TYPEN, [], [], [])[0]["content"]
        for key in ("fachrichtung", "anlass", "medikamente"):
            self.assertIn('"%s": {"wert": ' % key, system)
        self.assertIn("nie eine bloße Zeichenkette", system)

    def test_a_specialty_needs_its_words_and_must_be_one_of_the_list(self):
        p = self.plan(answer_medizin(fachrichtung={"wert": "Kardiologie", "beleg": "Echo unauffällig"}))
        self.assertNotIn("Fachrichtung", p["felder"], "an echo is no cardiology practice")
        p = self.plan(answer_medizin(fachrichtung={"wert": "Herzchirurgie", "beleg": "Facharzt für Kardiologie"}))
        self.assertNotIn("Fachrichtung", p["felder"])
        self.assertTrue(any("keine der Fachrichtungen" in w for w in p["weg"]))

    def test_the_quote_may_begin_with_its_heading(self):
        p = self.plan(answer_medizin(anlass={"wert": "Arterielle Hypertonie", "beleg": "Diagnosen: - Arterielle Hypertonie"},
                                     medikamente={"wert": "Ramipril 5 mg 1-0-0", "beleg": "Medikation: Ramipril 5 mg 1-0-0"}))
        self.assertEqual(p["felder"]["Anlass/Diagnose"], "Arterielle Hypertonie")
        self.assertEqual(p["felder"]["Medikamente"], "Ramipril 5 mg 1-0-0")
        p = self.plan(answer_medizin(anlass={"wert": "Echo unauffällig", "beleg": "Befund: Echo unauffällig"}))
        self.assertNotIn("Anlass/Diagnose", p["felder"], "another heading is no diagnosis")

    def test_a_diagnosis_only_under_its_heading(self):
        p = self.plan(answer_medizin(anlass={"wert": "Belastungsdyspnoe", "beleg": "Verdacht auf Belastungsdyspnoe"}))
        self.assertNotIn("Anlass/Diagnose", p["felder"], "a finding's words are no diagnosis section")
        p = self.plan(answer_medizin(), content=ARZTBRIEF.replace("Diagnosen:", "Notizen"))
        self.assertNotIn("Anlass/Diagnose", p["felder"])
        p = self.plan(answer_medizin(anlass={"wert": "Herzinsuffizienz", "beleg": "Arterielle Hypertonie"}))
        self.assertNotIn("Anlass/Diagnose", p["felder"], "the value must be the quoted words")

    def test_medicines_from_a_plan_or_a_medication_section_only(self):
        p = self.plan(answer_medizin(medikamente={"wert": "Metoprolol 47,5 mg", "beleg": "Metoprolol 47,5 mg"}), content=PLAN)
        self.assertEqual(p["felder"]["Medikamente"], "Metoprolol 47,5 mg")
        p = self.plan(answer_medizin(), content=ARZTBRIEF.replace("Medikation:", "Früher einmal"))
        self.assertNotIn("Medikamente", p["felder"])
        p = self.plan(answer_medizin(medikamente={"wert": "Bisoprolol", "beleg": "Ramipril 5 mg 1-0-0"}))
        self.assertNotIn("Medikamente", p["felder"])

    def test_no_medical_field_on_a_document_that_is_not_medical(self):
        f = base([{"id": 1, "title": "x", "content": ARZTBRIEF, "tags": []}])
        o = ordnen.Ordnung(f)
        ordnen.struktur(o, ["Anna"], log=lambda *_: None)
        body, was = ordnen.neue_werte(f.docs[1], {"bereiche": ["Haus"], "felder": {"Medikamente": "ASS 100"}}, o, {})
        self.assertNotIn("Medikamente", was)
        body, was = ordnen.neue_werte(f.docs[1], {"bereiche": ["Gesundheit"], "felder": {"Medikamente": "ASS 100"}}, o, {})
        self.assertEqual(was["Medikamente"], "ASS 100")

    def test_a_practice_has_one_specialty_decided_once(self):
        f = base([{"id": 1, "correspondent": 7, "tags": [3]}, {"id": 2, "correspondent": 7, "tags": [3]},
                  {"id": 3, "correspondent": 8, "tags": [3]}], korr=[{"id": 7, "name": "Praxis A"}, {"id": 8, "name": "Praxis B"}])
        o = ordnen.Ordnung(f)
        ordnen.struktur(o, ["Anna"], log=lambda *_: None)
        fid, opts = o.feld("Fachrichtung")["id"], ordnen.optionen_von(o, "Fachrichtung")
        fach = {}
        _, was = ordnen.neue_werte(f.docs[1], {"felder": {"Fachrichtung": "Kardiologie"}}, o, fach)
        self.assertEqual(was["Fachrichtung"], "Kardiologie")
        _, was = ordnen.neue_werte(f.docs[2], {"felder": {"Fachrichtung": "Labor"}}, o, fach)
        self.assertEqual(was["Fachrichtung"], "Kardiologie", "the same practice, the same specialty")
        _, was = ordnen.neue_werte(f.docs[2], {"felder": {}}, o, fach)
        self.assertEqual(was["Fachrichtung"], "Kardiologie", "decided: no new evidence needed")
        f.docs[3]["custom_fields"] = [{"field": fid, "value": opts["HNO"]}]
        self.assertEqual(ordnen.fach_lesen(o, list(f.docs.values())), {8: "HNO"}, "one set in Paperless is the practice's")


class Felder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.f = base([{"id": 1, "title": "Stadtwerke - Rechnung", "content": INVOICE, "tags": [1]},
                       {"id": 2, "title": "Arztbrief", "content": ARZTBRIEF, "tags": [3], "correspondent": 1},
                       {"id": 3, "title": "Plan", "content": PLAN, "document_type": 2}])
        ordnen.struktur(ordnen.Ordnung(self.f), ["Anna"], log=lambda *_: None)
        ordnen.felder_anlegen(ordnen.Ordnung(self.f), [], [])
        self.f.felder = [x for x in self.f.felder if x["name"] not in ordnen.MEDIZIN_FELDER]   # the box before this change
        self.f.writes.clear()
        sha = {i: ordnen.sha(d) for i, d in self.f.docs.items()}
        Path(self.dir, "ordnen.json").write_text(json.dumps({"antworten": {   # 3: an answer from the first cut (stand 2), bare strings
            **{str(i): {"sha": sha[i], "antwort": {"typ": "Brief", "bereiche": ["Haus"], "sicher": True}} for i in (1, 2)},
            "3": {"sha": sha[3], "stand": 2, "antwort": {"typ": "Befund", "fachrichtung": "Kardiologie", "sicher": True}}}}))
        self.asked = []

    def ask(self, m):
        self.asked.append(m[1]["content"])
        if "Medikationsplan" in m[1]["content"]:
            return answer_medizin(fachrichtung=None, anlass=None,
                                  medikamente={"wert": "Metoprolol 47,5 mg", "beleg": "Metoprolol 47,5 mg"})
        return answer_medizin()

    def test_dry_run_asks_again_for_medical_documents_only_and_changes_nothing(self):
        text = ordnen.felder(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        self.assertEqual(self.f.writes, [])
        self.assertEqual(len(self.asked), 2, "the invoice is not asked again")
        self.assertIn("Medizinische Dokumente: 2", text)
        self.assertIn("  1  Fachrichtung", text)
        self.assertIn("  2  Medikamente", text)
        self.assertNotIn("Ramipril", Path(self.dir, "zusammenfassung-felder.txt").read_text(), "counts only")

    def test_apply_backs_up_first_and_sets_only_the_medical_fields(self):
        ordnen.felder(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        n = len(self.asked)
        before = copy.deepcopy(self.f.docs)
        ordnen.felder(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual(len(self.asked), n, "the dry run's answers are used")
        self.assertEqual(len(list(Path(self.dir).glob("sicherung-*.json"))), 1)
        o = ordnen.Ordnung(self.f)
        for i, d in self.f.docs.items():
            for k in ("document_type", "correspondent", "tags", "title"):
                self.assertEqual(d.get(k), before[i].get(k))
        self.assertEqual(self.f.docs[1]["custom_fields"], [])
        werte = {c["field"]: c["value"] for c in self.f.docs[2]["custom_fields"]}
        self.assertEqual(werte[o.feld("Fachrichtung")["id"]], ordnen.optionen_von(o, "Fachrichtung")["Kardiologie"])
        self.assertEqual(werte[o.feld("Anlass/Diagnose")["id"]], "Arterielle Hypertonie")
        self.assertEqual({c["field"]: c["value"] for c in self.f.docs[3]["custom_fields"]}[o.feld("Medikamente")["id"]], "Metoprolol 47,5 mg")
        self.assertFalse(self.f.notes)
        before = len(self.f.writes)
        ordnen.felder(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual(len(self.asked), n)
        self.assertEqual([w for w in self.f.writes[before:] if w[1] != "/api/documents/"], [], "a second run sets nothing")


class Kommando(unittest.TestCase):
    def test_paperless_away_in_intake_is_no_failure(self):
        d = tempfile.mkdtemp()
        os.environ.update({"ORDNEN_DIR": d, "PAPERLESS_API": "http://127.0.0.1:9", "PAPERLESS_TOKEN": "t"})
        try:
            self.assertEqual(ordnen.main(["--neue"]), 0)
            self.assertEqual(ordnen.main([]), 2)
        finally:
            for k in ("ORDNEN_DIR", "PAPERLESS_API", "PAPERLESS_TOKEN"):
                del os.environ[k]


def haengt_zeilen(text):
    return [json.loads(z[len("hängt "):]) for z in text.splitlines() if z.startswith("hängt {")]


class Haengt(unittest.TestCase):
    """intake says which step hangs and why (2026-11-08: the tag „gesundheit“ of a retired user, invisible for hours)"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def neue(self, f, ask=lambda m: answer_invoice()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ordnen.neue(f, self.dir, args(max=5), log=lambda *_: None, ask=ask)
        return haengt_zeilen(out.getvalue())

    def eingerichtet(self, docs=()):
        f = base(list(docs))
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        return f

    def test_an_invisible_bereich_tag_is_named(self):
        f = self.eingerichtet()
        f.tags = [t for t in f.tags if t["name"] != "gesundheit"]   # owned by someone Dokumente's token cannot see
        [h] = self.neue(f)
        self.assertEqual(h["was"], "einrichtung")
        self.assertEqual(h["detail"], "Neue Dokumente werden nicht eingeordnet: Bereich-Tag „Gesundheit“ ist für Dokumente in Paperless nicht sichtbar")
        self.assertIn("Eigentümer", h["fix"])

    def test_nothing_set_up_names_all_of_it(self):
        [h] = self.neue(base([]))
        self.assertIn("Bereich-Tags „Haus“, „Auto“", h["detail"])
        self.assertTrue(h["detail"].endswith("und Feld „Person“ sind für Dokumente in Paperless nicht sichtbar"), h["detail"])

    def test_set_up_and_working_says_nothing(self):
        f = self.eingerichtet([{"id": 1, "title": "Stadtwerke", "content": INVOICE, "added": "2099-01-01T00:00:00+01:00"}])
        self.assertEqual(self.neue(f), [])

    def test_the_model_away_and_a_pile_up_hang(self):
        f = self.eingerichtet([{"id": i, "title": "x%d" % i, "content": INVOICE + str(i), "added": "2026-11-01T08:00:00+01:00"} for i in range(1, 4)])

        def weg(m):
            raise OSError("connection refused")
        hs = {h["was"]: h for h in self.neue(f, ask=weg)}
        self.assertEqual(set(hs), {"modell", "stau"})
        self.assertEqual(hs["stau"]["detail"], "3 neue Dokumente warten seit über 24 Stunden darauf, eingeordnet zu werden")

    def test_a_refused_token_hangs(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ordnen.zugang_haengt(urllib.error.HTTPError("/api/tags/", 401, "Unauthorized", {}, None))
            ordnen.zugang_haengt(urllib.error.URLError("connection refused"))
        self.assertEqual([h["was"] for h in haengt_zeilen(out.getvalue())], ["zugang", "paperless"])


# the tax marking (owner 2026-11-09): one invented document per Steuer-Art, the words the model quotes, and the
# Steuer-Art the marking must come to; no real names or documents
STEUER_FAELLE = {
    "Handwerkerleistungen": ("Malerbetrieb Beispiel GmbH\nRechnung Nr. 2025-117 vom 14.03.2025\nAnstrich Wohnzimmer\n"
                             "Arbeitskosten 3 Std. Maler 480,00 €\nMaterial 120,00 €\nGesamt 600,00 €",
                             "Arbeitskosten 3 Std. Maler 480,00 €"),
    "Haushaltsnahe Dienstleistungen": ("Glanz Gebäudereinigung\nRechnung vom 02.05.2025\nFensterreinigung Einfamilienhaus 150,00 €",
                                       "Fensterreinigung Einfamilienhaus"),
    "Arbeitszimmer & Arbeitsmittel": ("Elektromarkt Beispiel\nRechnung vom 10.01.2025\nMonitor 27 Zoll 299,00 €\nBürostuhl Ergo 189,00 €",
                                      "Monitor 27 Zoll 299,00 €"),
    "Fortbildung": ("Akademie Beispiel e. V.\nRechnung vom 20.06.2025\nSeminar Projektmanagement, 2 Tage, 890,00 €",
                    "Seminar Projektmanagement"),
    "Krankheitskosten": ("Zahnarztpraxis Dr. Beispiel\nRechnung nach GOZ vom 05.08.2025\nProfessionelle Zahnreinigung 95,00 €",
                         "Zahnarztpraxis Dr. Beispiel"),
    "Versicherungen & Vorsorge": ("Beispiel Krankenversicherung AG\nBeitragsbescheinigung 2025\nBeiträge zur Krankenversicherung 2025: 6.120,00 €",
                                  "Beiträge zur Krankenversicherung 2025"),
    "Spenden": ("Kinderhilfe Beispiel e. V.\nZuwendungsbestätigung über Geldzuwendungen\nBetrag: 100,00 € am 12.12.2025",
                "Zuwendungsbestätigung über Geldzuwendungen"),
    "Kinderbetreuung": ("Kita Sonnenschein\nJahresbescheinigung 2025\nBetreuungsbeiträge Januar bis Dezember 2025: 2.400,00 €",
                        "Betreuungsbeiträge Januar bis Dezember 2025"),
    "Kapitalerträge": ("Beispielbank AG\nJahressteuerbescheinigung 2025\nKapitalerträge 312,45 €, Kapitalertragsteuer 78,11 €",
                       "Jahressteuerbescheinigung 2025"),
    "Steuerbescheid & Finanzamt": ("Finanzamt Beispielstadt\nBescheid für 2024 über Einkommensteuer\nfestgesetzt 4.210,00 €",
                                   "Bescheid für 2024 über Einkommensteuer"),
}
STEUER_TYP = {"Versicherungen & Vorsorge": "Beitrag/Bescheid", "Spenden": "Urkunde", "Kinderbetreuung": "Urkunde",
              "Kapitalerträge": "Steuer", "Steuerbescheid & Finanzamt": "Steuer"}


def answer_steuer(art, beleg, **kw):
    a = {"typ": STEUER_TYP.get(art, "Rechnung"), "bereiche": ["Steuer"], "sicher": True, "steuer": {"wert": art, "beleg": beleg}}
    a.update(kw)
    return a


class Steuer(unittest.TestCase):
    def plan(self, a, content):
        doc = {"id": 11, "title": "Beleg", "content": content}
        return ordnen.auswerten(a, doc, list(ordnen.TYPEN), ["Anna"], ["Anna Muster"], [])

    def eingerichtet(self, docs=()):
        f = base(list(docs))
        ordnen.struktur(ordnen.Ordnung(f), ["Anna"], log=lambda *_: None)
        return f, ordnen.Ordnung(f)

    def test_the_tag_and_fields_are_made_with_the_kinds_and_the_tag_stays(self):
        f, o = self.eingerichtet()
        felder = {x["name"]: x for x in f.felder}
        self.assertEqual([x["label"] for x in felder["Steuer-Art"]["extra_data"]["select_options"]], list(ordnen.STEUER_ARTEN))
        self.assertEqual(felder["Lohnkosten"]["data_type"], "monetary")
        self.assertIsNotNone(o.tag("steuerlich"))
        self.assertIn(ordnen.falten("steuerlich"), ordnen.bleibende_tags())

    def test_each_kind_from_the_words_that_back_it(self):
        self.assertEqual(set(STEUER_FAELLE), set(ordnen.STEUER_ARTEN), "one case per Steuer-Art")
        for art, (text, beleg) in STEUER_FAELLE.items():
            with self.subTest(art):
                self.assertEqual(self.plan(answer_steuer(art, beleg), text).get("steuer"), art)

    def test_never_a_guess(self):
        text, beleg = STEUER_FAELLE["Handwerkerleistungen"]
        konzert = "Konzertkasse Beispiel\nRechnung vom 01.04.2025\nKonzertkarte Parkett 89,00 €"
        for name, a, t in (("unsure: null", answer_steuer("Spenden", "x", steuer=None), konzert),
                           ("a concert is no gift", answer_steuer("Spenden", "Konzertkarte Parkett 89,00 €"), konzert),
                           ("the words are not in the text", answer_steuer("Handwerkerleistungen", "Arbeitskosten 2 Std. 200,00 €"), text),
                           ("not one of the kinds", answer_steuer("Kfz-Kosten", beleg), text),
                           ("a bare string", answer_steuer("Spenden", "x", steuer="Spenden"), konzert)):
            with self.subTest(name):
                p = self.plan(a, t)
                self.assertNotIn("steuer", p)
                self.assertFalse({"Steuerjahr", "Lohnkosten"} & set(p["felder"]))
                self.assertTrue(any(w.startswith("Steuer-Art") for w in p["weg"]) or a["steuer"] is None)

    def test_labour_cost_for_35a_only_as_the_text_names_it(self):
        text, beleg = STEUER_FAELLE["Handwerkerleistungen"]
        lohn = {"wert": "480,00", "beleg": "Arbeitskosten 3 Std. Maler 480,00 €"}
        self.assertEqual(self.plan(answer_steuer("Handwerkerleistungen", beleg, lohnkosten=lohn), text)["felder"]["Lohnkosten"], "EUR480.00")
        self.assertEqual(self.plan(answer_steuer("Handwerkerleistungen", beleg, lohnkosten={**lohn, "wert": 480}), text)["felder"]["Lohnkosten"],
                         "EUR480.00")
        for name, lk in (("the total is no labour cost", {"wert": 600, "beleg": "Gesamt 600,00 €"}),
                         ("a sum the words do not name", {"wert": 500, "beleg": "Arbeitskosten 3 Std. Maler 480,00 €"}),
                         ("material", {"wert": 120, "beleg": "Material 120,00 €"})):
            with self.subTest(name):
                self.assertNotIn("Lohnkosten", self.plan(answer_steuer("Handwerkerleistungen", beleg, lohnkosten=lk), text)["felder"])
        t, b = STEUER_FAELLE["Krankheitskosten"]
        p = self.plan(answer_steuer("Krankheitskosten", b, lohnkosten={"wert": 95, "beleg": "Professionelle Zahnreinigung 95,00 €"}), t)
        self.assertNotIn("Lohnkosten", p["felder"], "no labour cost outside §35a")

    def test_the_year_is_the_invoices_never_the_scans(self):
        text, beleg = STEUER_FAELLE["Handwerkerleistungen"]
        p = self.plan(answer_steuer("Handwerkerleistungen", beleg, datum={"wert": "2025-03-14", "beleg": "vom 14.03.2025"}), text)
        self.assertEqual(p["felder"]["Steuerjahr"], 2025, "the invoice's date the text backs")
        p = self.plan(answer_steuer("Handwerkerleistungen", beleg, datum={"wert": "2026-11-09", "beleg": "gescannt 09.11.2026"}), text)
        self.assertNotIn("Steuerjahr", p["felder"], "a date the text does not have is no year")
        p = self.plan(answer_steuer("Handwerkerleistungen", beleg, steuerjahr={"wert": 2024, "beleg": "vom 14.03.2025"}), text)
        self.assertNotIn("Steuerjahr", p["felder"])

    def test_marked_only_on_a_receipts_type_and_never_over_the_owner(self):
        text, beleg = STEUER_FAELLE["Handwerkerleistungen"]
        f, o = self.eingerichtet([{"id": 1, "title": "x", "content": text}])
        plan = self.plan(answer_steuer("Handwerkerleistungen", beleg, datum={"wert": "2025-03-14", "beleg": "vom 14.03.2025"},
                                       lohnkosten={"wert": 480, "beleg": "Arbeitskosten 3 Std. Maler 480,00 €"}), text)
        body, was = ordnen.neue_werte(f.docs[1], plan, o, {})
        self.assertIn(o.tag("steuerlich")["id"], body["tags"])
        werte = {c["field"]: c["value"] for c in body["custom_fields"]}
        self.assertEqual(werte[o.feld("Steuer-Art")["id"]], ordnen.optionen_von(o, "Steuer-Art")["Handwerkerleistungen"])
        self.assertEqual(werte[o.feld("Steuerjahr")["id"]], 2025)
        self.assertEqual(werte[o.feld("Lohnkosten")["id"]], "EUR480.00")
        body, was = ordnen.neue_werte(f.docs[1], {**plan, "typ": "Brief", "bereiche": ["Haus"]}, o, {})
        self.assertFalse({"Steuer-Art", "Steuerjahr", "Lohnkosten"} & set(was), "a letter is no receipt")
        self.assertNotIn(o.tag("steuerlich")["id"], body.get("tags", []))
        f.docs[1]["custom_fields"] = [{"field": o.feld("Steuer-Art")["id"], "value": ordnen.optionen_von(o, "Steuer-Art")["Spenden"]}]
        body, was = ordnen.neue_werte(f.docs[1], plan, o, {})
        self.assertNotIn("Steuer-Art", was, "the owner's Steuer-Art stays")
        self.assertNotIn(o.tag("steuerlich")["id"], body.get("tags", []), "and the owner decides the tag")

    def test_intake_marks_a_new_invoice(self):
        text, beleg = STEUER_FAELLE["Haushaltsnahe Dienstleistungen"]
        f, o = self.eingerichtet([{"id": 1, "title": "Reinigung", "content": text}])
        ordnen.neue(f, tempfile.mkdtemp(), args(max=5), log=lambda *_: None,
                    ask=lambda m: answer_steuer("Haushaltsnahe Dienstleistungen", beleg, bereiche=["Haus"]))
        self.assertIn(o.tag("steuerlich")["id"], f.docs[1]["tags"])
        self.assertIn(o.tag("Haus")["id"], f.docs[1]["tags"])

    def test_the_question_names_the_kinds_and_the_shape(self):
        system, user = (m["content"] for m in ordnen.frage({"title": "x", "content": "y"}, ordnen.TYPEN, [], [], []))
        self.assertIn('"steuer": {"wert": eine der Steuer-Arten', system)
        self.assertIn("nie das Datum eines Scans", system)
        for art in ordnen.STEUER_ARTEN:
            self.assertIn("- %s: " % art, user)


class SteuerLauf(unittest.TestCase):
    """ordnen.py steuer: the documents there are, once"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        text, _ = STEUER_FAELLE["Handwerkerleistungen"]
        self.f = base([{"id": 1, "title": "Maler", "content": text, "document_type": 1},
                       {"id": 2, "title": "Konzert", "content": "Konzertkarte 89,00 €", "document_type": 1},
                       {"id": 3, "title": "Arztbrief", "content": ARZTBRIEF, "document_type": 2}])
        ordnen.struktur(ordnen.Ordnung(self.f), ["Anna"], log=lambda *_: None)
        self.f.felder = [x for x in self.f.felder if x["name"] not in ("Steuer-Art", "Lohnkosten")]   # the box before this change
        self.f.tags = [t for t in self.f.tags if t["name"] != "steuerlich"]
        self.f.writes.clear()
        sha = {i: ordnen.sha(d) for i, d in self.f.docs.items()}
        Path(self.dir, "ordnen.json").write_text(json.dumps({"antworten": {
            str(i): {"sha": sha[i], "stand": 3, "antwort": {"typ": "Rechnung", "bereiche": ["Haus"], "sicher": True}} for i in (1, 2, 3)}}))
        self.asked = []

    def ask(self, m):
        self.asked.append(m[1]["content"])
        if "Malerbetrieb" in m[1]["content"]:
            return answer_steuer("Handwerkerleistungen", "Arbeitskosten 3 Std. Maler 480,00 €",
                                 datum={"wert": "2025-03-14", "beleg": "vom 14.03.2025"},
                                 lohnkosten={"wert": 480, "beleg": "Arbeitskosten 3 Std. Maler 480,00 €"})
        return answer_steuer("Spenden", "Konzertkarte 89,00 €")

    def test_dry_run_asks_the_receipts_again_and_changes_nothing(self):
        text = ordnen.steuer(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        self.assertEqual(self.f.writes, [])
        self.assertEqual(len(self.asked), 2, "a medical letter is no receipt")
        self.assertIn("steuerlich markiert: 1", text)
        self.assertIn("    1  Handwerkerleistungen (mit Lohnkosten: 1)", text)
        self.assertIn("#1 Maler → ", Path(self.dir, "zusammenfassung-steuer.txt").read_text())

    def test_apply_backs_up_first_makes_the_fields_and_sets_only_the_marking(self):
        ordnen.steuer(self.f, self.dir, args(dry_run=True), log=lambda *_: None, ask=self.ask)
        n = len(self.asked)
        before = copy.deepcopy(self.f.docs)
        ordnen.steuer(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual(len(self.asked), n, "the dry run's answers are used")
        self.assertEqual(len(list(Path(self.dir).glob("sicherung-*.json"))), 1)
        o = ordnen.Ordnung(self.f)
        self.assertEqual(self.f.docs[1]["tags"], before[1]["tags"] + [o.tag("steuerlich")["id"]])
        werte = {c["field"]: c["value"] for c in self.f.docs[1]["custom_fields"]}
        self.assertEqual(werte, {o.feld("Steuer-Art")["id"]: ordnen.optionen_von(o, "Steuer-Art")["Handwerkerleistungen"],
                                 o.feld("Steuerjahr")["id"]: 2025, o.feld("Lohnkosten")["id"]: "EUR480.00"})
        for i in (2, 3):
            self.assertEqual(self.f.docs[i], before[i])
        for i, d in self.f.docs.items():
            self.assertEqual((d["document_type"], d["correspondent"], d["title"]), (before[i]["document_type"], before[i]["correspondent"], before[i]["title"]))
        before = len(self.f.writes)
        ordnen.steuer(self.f, self.dir, args(), log=lambda *_: None, ask=self.ask)
        self.assertEqual([w for w in self.f.writes[before:] if w[1].startswith("/api/documents/1")], [], "a second run sets nothing")


if __name__ == "__main__":
    unittest.main()
