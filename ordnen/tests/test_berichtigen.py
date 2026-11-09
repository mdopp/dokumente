"""2026-11-08, from the box: a fund policy's yearly Wertmitteilung and Standmitteilung came in typed
Versicherungsschein (so vertraege.py read them as contracts and filled Vertragsbeginn, -ende, Betrag) and
scanned titles came out as „Skandia - Dokument“ or „… (5)“ without a date. ordnen.py's guard and title
form for new documents; berichtigen.py for the ones filed before; ordnen.py --neue --max 0 for all at once.
Against test_ordnen's fake Paperless and a fake model; the texts are the box's, without names or numbers."""
import contextlib, copy, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

HIER = Path(__file__).resolve().parent


def _lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ordnen = _lade("ordnen_skript", HIER.parent / "ordnen.py")
berichtigen = _lade("berichtigen_skript", HIER.parent / "berichtigen.py")
t = _lade("test_ordnen_fake", HIER / "test_ordnen.py")

SKANDIA = "Skandia Lebensversicherung AG"
WERT = ("Skandia Lebensversicherung AG\nHerrn Ben Muster\nJährliche Wertmitteilung über Ihre Skandia Fondspolice\n"
        "Berlin, 05.12.2007\nVersicherungsnummer: FP-0001\nVersicherungsbeginn: 01.12.2002  Ablauf: 30.11.2039\n"
        "Beitrag: 50,00 EUR monatlich\nIhr Fondsguthaben zum Stichtag beträgt 4.321,00 EUR.\n")
STAND = ("Skandia Lebensversicherung AG\nMitteilung zur Fondsgebundenen Lebensversicherung\n"
         "Übersicht über den wert- und anteilmäßigen Stand Ihres Lebensversicherungsvertrages zum 05.12.2007\n"
         "Versicherungsnummer: FP-0001\nBeitrag: 50,00 EUR monatlich\n")
MONAT = ("Skandia Lebensversicherung AG\nJährliche Wertmitteilung über Ihre Skandia Fondspolice\nStand: Dezember 2007\n"
         "Versicherungsnummer: FP-0001\n")
INFO = ("Skandia Lebensversicherung AG\nWichtige Informationen zu Ihrer Fondspolice\n"
        "Allgemeine Hinweise zu Fondswechseln, Kosten und Ihren Rechten.\n")
POLICE = ("Skandia Lebensversicherung AG\nVersicherungsschein Nr. FP-0001\nFondsgebundene Lebensversicherung\n"
          "Versicherungsbeginn: 01.12.2002  Ablauf: 30.11.2039\nBeitrag: 50,00 EUR monatlich\n"
          "Sie erhalten jedes Jahr eine Wertmitteilung.\n")
NACHTRAG = ("Skandia Lebensversicherung AG\nNachtrag zum Versicherungsschein FP-0001\n"
            "Ab der nächsten Wertmitteilung gilt der neue Beitrag von 60,00 EUR monatlich.\n")
BEITRAG = "Skandia Lebensversicherung AG\nBeitragsinformation 2008\nIhr Beitrag ab 01.01.2008: 50,00 EUR monatlich\n"
STAPEL = 90   # the field Stapel: Dokumente filed it
VERTRAG_FELDER = {"Vertragsbeginn": 91, "Vertragsende": 92, "Betrag": 93, "Zahlungsintervall": 94, "Vertragsnummer": 95}
NOTIZ_VERTRAEGE = "Vertragsdaten aus dem Text übernommen (laib): Vertragsbeginn, Vertragsende, Betrag, Zahlungsintervall."


def antwort(typ="Versicherungsschein", **kw):
    return {"typ": typ, "bereiche": ["Versicherung"], "absender": {"wert": SKANDIA, "beleg": SKANDIA},
            "versicherungsnummer": {"wert": "FP-0001", "beleg": "Versicherungsnummer: FP-0001"}, "sicher": True, **kw}


def gescannt(i, title, content, created="2007-12-05", **kw):
    """a document as Dokumente's scan filed it: the title is the file's name, the field Stapel set"""
    return {"id": i, "title": title, "content": content, "created": created, "original_file_name": title + ".pdf",
            "custom_fields": [{"field": STAPEL, "value": "2026-11-08T10-00-00"}], **kw}


class Fake(t.Fake):
    """test_ordnen's Paperless, and a document's notes read back"""

    def __call__(self, method, path, body=None):
        if method == "GET" and path.endswith("/notes/"):
            return [{"note": n} for n in self.notes.get(int(path.split("/")[3]), [])]
        return super().__call__(method, path, body)


def paperless(docs, typen=None):
    typen = typen or [{"id": i + 1, "name": n} for i, n in enumerate(ordnen.TYPEN)]
    f = Fake(docs, tags=[{"id": 1, "name": "Stapel"}, {"id": 2, "name": "prüfen"}], typen=typen, korr=[{"id": 7, "name": SKANDIA}],
             felder=[{"id": STAPEL, "name": "Stapel", "data_type": "string"}] +
                    [{"id": i, "name": n, "data_type": "string"} for n, i in VERTRAG_FELDER.items()])
    ordnen.struktur(ordnen.Ordnung(f), ["Anna", "Ben"], log=lambda *_: None)
    f.writes.clear()
    return f


def typ_id(f, name):
    return next(x["id"] for x in f.typen if x["name"] == name)


class Art(unittest.TestCase):
    """a periodic statement is never a contract type; the policy and its Nachtrag are"""

    def plan(self, content, typ="Versicherungsschein", title=""):
        return ordnen.auswerten(antwort(typ), {"id": 1, "title": title, "content": content}, list(ordnen.TYPEN), ["Ben"], [], [SKANDIA])

    def test_a_yearly_statement_is_a_kontoauszug_and_the_note_says_why(self):
        for text in (WERT, STAND, MONAT):
            p = self.plan(text)
            self.assertEqual(p["typ"], "Kontoauszug", text[:60])
            self.assertTrue(any("regelmäßige Mitteilung, kein Vertrag" in w for w in p["weg"]))
        self.assertEqual(self.plan(WERT, typ="Vertrag")["typ"], "Kontoauszug")
        self.assertEqual(self.plan(INFO, title="Skandia - Jahresmitteilung")["typ"], "Kontoauszug", "the title is the heading too")

    def test_the_policy_its_nachtrag_and_other_types_stay(self):
        self.assertEqual(self.plan(POLICE)["typ"], "Versicherungsschein", "a Wertmitteilung further down is no heading")
        self.assertEqual(self.plan(NACHTRAG)["typ"], "Versicherungsschein")
        self.assertEqual(self.plan(INFO)["typ"], "Versicherungsschein", "no statement's words: the model's type")
        self.assertEqual(self.plan(WERT, typ="Brief")["typ"], "Brief", "only a contract type is overruled")

    def test_a_beitragsinformation_is_a_beitrag(self):
        self.assertEqual(self.plan(BEITRAG)["typ"], "Beitrag/Bescheid")

    def test_the_question_says_it(self):
        system = ordnen.frage({"title": "", "content": WERT}, ordnen.TYPEN, ["Ben"], [], [])[0]["content"]
        self.assertIn("Wert- oder Standmitteilung, ein Statusreport oder eine Information zu einem Vertrag ist kein Vertrag", system)
        self.assertIn("nie eine Wert-, Stand- oder Jahresmitteilung", ordnen.TYPEN["Versicherungsschein"])
        self.assertIn("Wert- oder Standmitteilung", ordnen.TYPEN["Kontoauszug"])
        self.assertNotIn("Versicherung", ordnen.TYPEN["Beitrag/Bescheid"].split("Bescheid einer")[-1], "an insurer's letter is no Bescheid")
        self.assertIn("Informationsblatt", ordnen.TYPEN["Brief"])


class Titel(unittest.TestCase):
    def neu(self, doc, absender=SKANDIA, **kw):
        return ordnen.titel_neu(doc, absender, stapel=STAPEL, **kw)

    def test_the_box_titles_get_their_date_and_lose_the_count_and_dokument(self):
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", WERT)),
                         "2007-12-05 Skandia Lebensversicherung AG - Jährliche Wertmitteilung über Ihre Skandia Fondspolice")
        self.assertEqual(self.neu(gescannt(2, "Skandia - Mitteilung zur Fondsgebundenen Lebensversicherung (5)", STAND)),
                         "2007-12-05 Skandia Lebensversicherung AG - Mitteilung zur Fondsgebundenen Lebensversicherung")
        self.assertEqual(self.neu(gescannt(3, "Skandia - Jährliche Wertmitteilung über Ihre Skandia Fondspolice (6)", WERT)),
                         "2007-12-05 Skandia Lebensversicherung AG - Jährliche Wertmitteilung über Ihre Skandia Fondspolice")

    def test_only_a_month_in_the_text_is_a_month(self):
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", MONAT, created="2007-12-01")),
                         "2007-12 Skandia Lebensversicherung AG - Jährliche Wertmitteilung über Ihre Skandia Fondspolice")

    def test_a_date_the_text_does_not_name_is_left_out(self):
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", INFO.replace("Wichtige", "Jahresinformation:"), created="2026-11-08")),
                         "Skandia Lebensversicherung AG - Jahresinformation: Informationen zu Ihrer Fondspolice")

    def test_the_models_date_and_subject_come_first(self):
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", INFO, created="2026-11-08"), gegenstand="Informationen Fondspolice",
                                  datum_=None), "Skandia Lebensversicherung AG - Informationen Fondspolice")
        p = ordnen.auswerten(antwort("Brief", datum={"wert": "2007-12", "beleg": "Stand: Dezember 2007"},
                                     gegenstand={"wert": "Wertmitteilung Fondspolice", "beleg": ""}),
                             {"id": 1, "title": "", "content": MONAT}, list(ordnen.TYPEN), [], [], [SKANDIA])
        self.assertEqual((p["datum"], p["gegenstand"]), ("2007-12", "Wertmitteilung Fondspolice"))
        p = ordnen.auswerten(antwort("Brief", gegenstand={"wert": "Rentenbescheid", "beleg": ""}), {"id": 1, "content": MONAT},
                             list(ordnen.TYPEN), [], [], [])
        self.assertIsNone(p["gegenstand"], "a subject in words the text does not have is made up")
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", MONAT, created="2007-12-01"), datum_=p.get("datum"),
                                  gegenstand="Wertmitteilung Fondspolice"), "2007-12 Skandia Lebensversicherung AG - Wertmitteilung Fondspolice")

    def test_without_a_subject_the_type_never_dokument(self):
        self.assertEqual(self.neu(gescannt(1, "Skandia - Dokument", "Skandia Lebensversicherung AG\n05.12.2007\n"), typ="Brief"),
                         "2007-12-05 Skandia Lebensversicherung AG - Brief")

    def test_a_title_a_person_set_or_one_already_right_stays(self):
        renamed = {**gescannt(1, "Skandia Rente 2007", WERT), "original_file_name": "Skandia - Dokument.pdf"}
        self.assertIsNone(self.neu(renamed), "a person renamed it: never overruled")
        richtig = {"id": 2, "title": "2017-06-17 Allianz Lebensversicherungs-AG - Vereinbarte Erhöhung", "content": "17.06.2017",
                   "original_file_name": "2017-06-17 Allianz Lebensversicherungs-AG - Vereinbarte Erhöhung.pdf", "created": "2017-06-17"}
        self.assertIsNone(self.neu(richtig, absender="Allianz Lebensversicherungs-AG"))
        upload = {"id": 3, "title": "Mietvertrag Wohnung - Kopie (2)", "content": "Mietvertrag 01.02.2020", "created": "2020-02-01",
                  "original_file_name": "Mietvertrag Wohnung - Kopie (2).pdf"}
        self.assertIsNone(self.neu(upload, absender="Hausverwaltung Beispiel"), "not Dokumente's, and no sender of its correspondent")

    def test_a_mails_machine_subject_is_rewritten(self):
        # document 641 (2026-11-09): mailed in, the forwarded subject became the title, the attachment had its own name
        mail = {"id": 641, "title": "Fwd: Rechnung_R-261241", "content": "Rollladen Herkenhoff GmbH\nRechnung R-261241 vom 05.11.2026\n",
                "created": "2026-11-05", "original_file_name": "R-261241.pdf"}
        self.assertEqual(self.neu(mail, absender="Rollladen Herkenhoff GmbH"), "2026-11-05 Rollladen Herkenhoff GmbH - Rechnung R-261241")
        self.assertEqual(self.neu(mail, absender="Rollladen Herkenhoff GmbH", gegenstand="Reparatur Rollladen"),
                         "2026-11-05 Rollladen Herkenhoff GmbH - Reparatur Rollladen")
        self.assertEqual(self.neu({**mail, "title": "WG: AW: Ihre Rechnung"}, absender=None), "2026-11-05 Ihre Rechnung")
        for titel in ("Re: Rechnung", "Scan_20261109", "IMG-4711.jpg", "Rechnung_R-261241", "Rechnung2026"):
            self.assertIsNotNone(self.neu({**mail, "title": titel}, absender="Rollladen Herkenhoff GmbH"), titel)
        for titel in ("Stromrechnung Oktober", "Rechnung", "Kfz-Versicherung", "2026-11-05 Rollladen Herkenhoff GmbH - Rechnung R-261241"):
            self.assertIsNone(self.neu({**mail, "title": titel}, absender="Rollladen Herkenhoff GmbH"), "a person's subject stays: " + titel)

    def test_an_older_machine_title_without_a_stack_needs_its_correspondent(self):
        alt = {"id": 4, "title": "Skandia - Dokument", "content": WERT, "created": "2007-12-05", "original_file_name": "Skandia - Dokument.pdf"}
        self.assertEqual(self.neu(alt), "2007-12-05 Skandia Lebensversicherung AG - Jährliche Wertmitteilung über Ihre Skandia Fondspolice")
        self.assertIsNone(self.neu(alt, absender="Allianz Lebensversicherungs-AG"))


class Neue(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_a_new_statement_is_typed_and_titled_and_its_answer_stays_its_own(self):
        f = paperless([gescannt(599, "Skandia - Dokument", WERT), gescannt(601, "Skandia - Mitteilung zur Fondsgebundenen Lebensversicherung (5)", STAND)])
        ordnen.neue(f, self.dir, SimpleNamespace(max=None, zeit=600.0), log=lambda *_: None, ask=lambda m: antwort())
        for i in (599, 601):
            self.assertEqual(f.docs[i]["document_type"], typ_id(f, "Kontoauszug"))
            self.assertTrue(f.docs[i]["title"].startswith("2007-12-05 Skandia Lebensversicherung AG - "), f.docs[i]["title"])
            self.assertNotIn("(5)", f.docs[i]["title"])
        rec = json.loads(Path(self.dir, "ordnen.json").read_text())
        self.assertEqual(rec["antworten"]["599"]["sha"], ordnen.sha(f.docs[599]), "a new title asks the model nothing again")

    def test_a_mailed_invoice_gets_its_sender_whole_and_a_title(self):
        # document 641 (2026-11-09): the correspondent is made with the ff the owner's phone knows, the title rewritten
        text = "Rollladen Herkenho GmbH\nRechnung R-261241 vom 05.11.2026\nBetrag 412,00 EUR\n"
        f = paperless([{"id": 641, "title": "Fwd: Rechnung_R-261241", "content": text, "created": "2026-11-05",
                        "original_file_name": "R-261241.pdf"}])
        a = {"typ": "Rechnung", "bereiche": ["Haus"], "absender": {"wert": "Rollladen Herkenho GmbH", "beleg": "Rollladen Herkenho GmbH"},
             "sicher": True}
        gefragt = []
        homes = {"firmenkontakte": {"kontakte": [{"name": "Stadtwerke"}]},
                 "handy_firmen": {"kontakte": [{"name": "Herr Meier", "org": "Rollladen Herkenhoff"}]}}
        ordnen.neue(f, self.dir, SimpleNamespace(max=None, zeit=600.0), log=lambda *_: None, ask=lambda m: a,
                    homes_frag=lambda w: gefragt.append(w) or homes[w])
        self.assertEqual(gefragt, ["firmenkontakte", "handy_firmen"])
        self.assertEqual([c["name"] for c in f.korr if "Herkenho" in c["name"]], ["Rollladen Herkenhoff GmbH"])
        self.assertEqual(f.docs[641]["title"], "2026-11-05 Rollladen Herkenhoff GmbH - Rechnung R-261241")

    def test_homes_away_leaves_the_name_as_the_text_has_it(self):
        f = paperless([{"id": 642, "title": "Rechnung", "content": "Rollladen Herkenho GmbH\n", "original_file_name": "Rechnung.pdf"}])
        a = {"typ": "Rechnung", "bereiche": ["Haus"], "absender": {"wert": "Rollladen Herkenho GmbH", "beleg": "Rollladen Herkenho GmbH"},
             "sicher": True}
        said = []

        def weg(w):
            raise OSError("connection refused")
        ordnen.neue(f, self.dir, SimpleNamespace(max=None, zeit=600.0), log=said.append, ask=lambda m: a, homes_frag=weg)
        self.assertIn("Rollladen Herkenho GmbH", [c["name"] for c in f.korr])
        self.assertTrue(any("Homes nennt handy_firmen nicht" in z for z in said), said)

    def test_max_0_files_all_of_them_ten_at_a_time(self):
        docs = [{"id": i, "title": "Brief %d" % i, "content": WERT} for i in range(1, 26)]
        f, asked = paperless(docs), []
        ordnen.neue(f, self.dir, SimpleNamespace(max=None, zeit=600.0), log=lambda *_: None, ask=lambda m: asked.append(1) or antwort())
        self.assertEqual(len(asked), ordnen.PRO_LAUF, "the hook's run: at most PRO_LAUF")
        ordnen.neue(f, self.dir, SimpleNamespace(max=0, zeit=600.0), log=lambda *_: None, ask=lambda m: asked.append(1) or antwort())
        self.assertEqual(len(asked), 25, "--max 0: the rest, all of them")
        versicherung = next(x["id"] for x in f.tags if x["name"] == "Versicherung")
        self.assertTrue(all(versicherung in d["tags"] for d in f.docs.values()))

    def test_max_0_stops_with_the_model_and_keeps_what_it_filed(self):
        docs = [{"id": i, "title": "Brief %d" % i, "content": WERT} for i in range(1, 26)]
        f, asked = paperless(docs), []

        def bis_15(m):
            asked.append(1)
            if len(asked) > 15:
                raise OSError("the card is lent")
            return antwort()
        with contextlib.redirect_stdout(io.StringIO()):
            ordnen.neue(f, self.dir, SimpleNamespace(max=0, zeit=600.0), log=lambda *_: None, ask=bis_15)
        versicherung = next(x["id"] for x in f.tags if x["name"] == "Versicherung")
        self.assertEqual(sum(versicherung in d["tags"] for d in f.docs.values()), 15, "each answered one filed, not only the first ten")


class Berichtigen(unittest.TestCase):
    """the one-off correction of what was filed before"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        felder = [{"field": STAPEL, "value": "2026-11-08T10-00-00"}, {"field": 91, "value": "2002-12-01"}, {"field": 92, "value": "2039-11-30"},
                  {"field": 93, "value": "EUR50.00"}, {"field": 94, "value": "monatlich"}]
        self.f = f = paperless([
            gescannt(599, "Skandia - Dokument", WERT, correspondent=7),
            gescannt(601, "Skandia - Mitteilung zur Fondsgebundenen Lebensversicherung (5)", STAND, correspondent=7),
            gescannt(700, "Skandia - Versicherungsschein", POLICE, correspondent=7, created="2002-12-01"),
            gescannt(701, "Skandia - Jährliche Wertmitteilung", WERT, correspondent=7),
            {**gescannt(702, "Unsere Skandia-Police, Wert 2007", WERT, correspondent=7), "original_file_name": "Skandia - Dokument.pdf"},
        ])
        vs = typ_id(f, "Versicherungsschein")
        for i in (599, 601, 700, 701):
            f.docs[i]["document_type"] = vs
            f.docs[i]["custom_fields"] = copy.deepcopy(felder) + ([{"field": 95, "value": "FP-0001"}] if i == 601 else [])
            f.notes[i] = [NOTIZ_VERTRAEGE]
        # ordnen gave 599, 601, 700 their type (its record names it); 701's came from someone else
        rec = {"antworten": {str(i): {"sha": ordnen.sha(f.docs[i]), "antwort": antwort()} for i in (599, 601, 700)}}
        Path(self.dir, "ordnen.json").write_text(json.dumps(rec))
        self.vor = copy.deepcopy(f.docs)

    def lauf(self, dry):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            berichtigen.berichtigen(self.f, self.dir, SimpleNamespace(dry_run=dry), log=lambda *_: None)
        return json.loads(out.getvalue().strip().splitlines()[-1][len("berichtigen "):])

    def test_dry_run_changes_nothing_and_lists_it(self):
        n = self.lauf(True)
        self.assertEqual([w for w in self.f.writes if w[0] != "GET"], [])
        self.assertEqual(self.f.docs, self.vor)
        self.assertEqual(n, {"typ": 2, "felder": 2, "titel": 4, "fremd": 1, "dry_run": True})
        text = Path(self.dir, "zusammenfassung-berichtigen.txt").read_text()
        self.assertIn("PROBELAUF", text)
        self.assertIn("Art Versicherungsschein → Kontoauszug", text)
        self.assertIn("#701 Skandia - Jährliche Wertmitteilung (Versicherungsschein)", text)
        self.assertFalse(list(Path(self.dir).glob("sicherung-*.json")))

    def test_apply_backs_up_retypes_takes_off_only_vertraeges_fields_and_fixes_machine_titles(self):
        self.lauf(False)
        f, kontoauszug, vs = self.f, typ_id(self.f, "Kontoauszug"), typ_id(self.f, "Versicherungsschein")
        self.assertEqual(len(list(Path(self.dir).glob("sicherung-*.json"))), 1)
        for i in (599, 601):
            self.assertEqual(f.docs[i]["document_type"], kontoauszug)
            self.assertEqual({c["field"] for c in f.docs[i]["custom_fields"]} & {91, 92, 93, 94}, set(), "vertraege's fields off")
            self.assertIn("Berichtigt (Dokumente)", f.notes[i][-1])
        self.assertIn({"field": 95, "value": "FP-0001"}, f.docs[601]["custom_fields"], "a field its note does not name stays")
        self.assertIn({"field": STAPEL, "value": "2026-11-08T10-00-00"}, f.docs[599]["custom_fields"])
        self.assertEqual(f.docs[599]["title"], "2007-12-05 Skandia Lebensversicherung AG - Jährliche Wertmitteilung über Ihre Skandia Fondspolice")
        self.assertEqual(f.docs[601]["title"], "2007-12-05 Skandia Lebensversicherung AG - Mitteilung zur Fondsgebundenen Lebensversicherung")
        self.assertEqual((f.docs[700]["document_type"], f.docs[700]["custom_fields"]), (vs, self.vor[700]["custom_fields"]), "the policy stays")
        self.assertEqual(f.docs[701]["document_type"], vs, "a type Dokumente did not set stays")
        self.assertEqual(f.docs[701]["custom_fields"], self.vor[701]["custom_fields"])
        self.assertEqual(f.docs[702]["title"], "Unsere Skandia-Police, Wert 2007", "a person's title stays")
        rec = json.loads(Path(self.dir, "ordnen.json").read_text())
        self.assertEqual(rec["antworten"]["599"]["sha"], ordnen.sha(f.docs[599]))
        before = len(f.writes)
        self.lauf(False)
        self.assertEqual([w for w in f.writes[before:] if w[0] == "PATCH"], [], "a second run has nothing left to put right")

    def test_the_backup_puts_titles_types_and_fields_back(self):
        self.lauf(False)
        backup = str(next(Path(self.dir).glob("sicherung-*.json")))
        self.assertEqual(ordnen.wiederherstellen(self.f, backup, log=lambda *_: None), 0)
        for i, d in self.vor.items():
            for k in ("title", "document_type", "custom_fields"):
                self.assertEqual(self.f.docs[i][k], d[k], (i, k))

    def test_a_reminder_in_homes_is_named(self):
        vfile = os.path.join(self.dir, "v.json")
        Path(vfile).write_text(json.dumps({"erinnerungen": {"599@2008-11-30": {"wann": "x"}}}))
        os.environ["VERTRAEGE_FILE"] = vfile
        try:
            self.lauf(True)
        finally:
            del os.environ["VERTRAEGE_FILE"]
        self.assertIn("bitte in Homes löschen", Path(self.dir, "zusammenfassung-berichtigen.txt").read_text())

    def test_one_run_at_a_time_with_ordnen(self):
        os.environ["ORDNEN_DIR"] = self.dir
        anderer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])   # an ordnen run that is alive
        try:
            Path(self.dir, "lauf.lock").write_text(str(anderer.pid))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(berichtigen.main(["--dry-run"]), 1)
            self.assertIn("ordnen.py läuft gerade", out.getvalue())
        finally:
            anderer.kill()
            anderer.wait()
            del os.environ["ORDNEN_DIR"]


if __name__ == "__main__":
    unittest.main()
