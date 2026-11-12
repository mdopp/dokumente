"""doppelt.py: a document scanned twice becomes one. Page by page against a fake
Paperless: a partial rescan of the household's goes to the trash and the whole one
takes what it lacks; a person's partial rescan gets the tag Doppelt for them to
delete, and every run says how many wait; two halves become one document in page
order; an unsure pair is only marked; an insurer's letters of two years are never
one. The cases are those of the stacks scanned on 2026-11-08 (ids kept, texts made
up): the invoice twice (526/527, the scanning person's), the Allianz letter three
times under two spellings of the sender (405/438/439), a scan whose date Paperless
had not set (440), and the Skandia Jahresmitteilungen 2008–2012 (599–602)."""
import contextlib, difflib, importlib.util, io, json, os, random, shutil, subprocess, tempfile, unittest, unittest.mock, urllib.error
from pathlib import Path

_spec = importlib.util.spec_from_file_location("doppelt_skript", Path(__file__).resolve().parent.parent / "doppelt.py")
doppelt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doppelt)

WORTE = ("Versicherung Vertrag Beitrag Leistung Schaden Anpassung Bedingungen Kunde Tarif Zahlung Konto Laufzeit Kündigung "
         "Hinweis Mitteilung Anlage Unterlagen Erstattung Antrag Prüfung Haftung Deckung Umfang Wohnung Gebäude Hausrat "
         "Fahrzeug Person Zeitraum Rechnung Betrag Frist Ausnahme Regelung Erklärung Nachtrag Information Bestätigung").split()


def seite(absender, k, n, datum, betrag, saat, zusatz=""):
    r = random.Random(saat * 100 + k)
    text = " ".join(r.choice(WORTE) for _ in range(90))
    return ("%s\nBeispielweg 12, 12345 Beispielstadt\nVersicherungsschein 4711%02d\nSeite %d von %d\n%s\nSchreiben vom %s, Betrag %s EUR\n%s"
            % (absender, saat, k, n, text, datum, betrag, zusatz))


def brief(absender, datum, betrag, n, saat, zusatz=""):
    return [seite(absender, k, n, datum, betrag, saat, zusatz) for k in range(1, n + 1)]


def rauschen(t, schritt=41):
    """a second scan's OCR: a letter here and there read otherwise, the digits as they were"""
    out = list(t)
    for i in range(7, len(out), schritt):
        if out[i].isalpha():
            out[i] = "e" if out[i] != "e" else "c"
    return "".join(out)


def abgenutzt(t):
    """a worn sheet's second pass: every 9th letter otherwise and every 8th word short of two letters (~0.88 alike)"""
    woerter = rauschen(t, 9).split(" ")
    for i in range(3, len(woerter), 8):
        if woerter[i] and not any(c.isdigit() for c in woerter[i]):
            woerter[i] = woerter[i][:-2]
    return " ".join(woerter)


ALLIANZ = brief("Allianz Lebensversicherungs-AG", "17.06.2017", "234,50", 6, 1)
ZWEI = brief("Beispiel Bausparkasse", "02.03.2019", "80,00", 6, 2)
DREI = brief("Beispiel Leben AG", "11.11.2020", "19,99", 6, 3)
VIER = brief("Stadtwerke Beispielstadt", "05.05.2021", "112,40", 4, 4)
RECHNUNG = brief("Beispiel Handwerk GmbH", "01.02.2022", "1.234,00", 2, 5)
MDOPP = 7   # the scanning person: Paperless gives them the scan


def jahresmitteilung(jahr, betrag):
    """Skandia's yearly statement: one template, the policy's start date on every one, the year and amount its own"""
    return brief("Skandia Lebensversicherung AG", "20.01.%d" % (jahr + 1), betrag, 2, 6, "Vertragsbeginn 01.12.2005, Stand 31.12.%d\n" % jahr)


class Fake(doppelt.Quelle):
    """Paperless with documents, their pages, correspondents, users and notes; every write is kept"""

    def __init__(self, docs, seiten, loeschen=True, korrespondenten=None):
        self.docs = {d["id"]: {"title": "Dokument %d" % d["id"], "owner": None, "tags": [], "custom_fields": [], "correspondent": None,
                               "document_type": None, "created": "2017-06-17", **d} for d in docs}
        self.texte = dict(seiten)
        self.korr = korrespondenten or {}
        self.seiten_cache = {}
        self.darf = loeschen
        self.verboten = set()   # Paperless answers 403 for these (not Dokumente's to delete)
        self.trash, self.writes, self.notes, self.uploads, self.tags = [], [], {}, [], [{"id": 2, "name": "prüfen"}]
        self.gefiltert = {}

    def dokumente(self):
        return [json.loads(json.dumps({**d, "content": "\f".join(self.texte.get(d["id"], []))})) for d in self.docs.values()]

    def dokument(self, i):
        return json.loads(json.dumps(self.docs[i]))

    def korrespondenten(self):
        return dict(self.korr)

    def benutzer(self, i):
        return {MDOPP: "mdopp"}.get(i, "Benutzer %d" % i)

    def seiten(self, i):
        return [doppelt.falten(t) for t in self.texte[i]]

    def pdf(self, i):
        return ("pdf-%d" % i).encode()

    def darf_loeschen(self):
        return self.darf

    def loeschen(self, i):
        if i not in self.docs:
            raise urllib.error.HTTPError("/", 404, "Not Found", {}, None)
        if i in self.verboten:
            raise urllib.error.HTTPError("/", 403, "Forbidden", {}, None)
        self.writes.append(("DELETE", i))
        self.trash.append(self.docs.pop(i))

    def notizen(self, i):
        return list(self.notes.get(i, []))

    def notiz(self, i, text):
        self.writes.append(("NOTE", i))
        self.notes.setdefault(i, []).append(text)

    def aendern(self, i, body):
        self.writes.append(("PATCH", i, body))
        self.docs[i].update(json.loads(json.dumps(body)))

    def tag_id(self, name, anlegen=True):
        t = next((t for t in self.tags if doppelt.falten(t["name"]) == doppelt.falten(name)), None)
        if t is None and anlegen:
            t = {"id": max(t["id"] for t in self.tags) + 1, "name": name}
            self.tags.append(t)
            self.writes.append(("TAG", name))
        return t["id"] if t else None

    def hochladen(self, inhalt, titel, felder):
        self.writes.append(("UPLOAD", titel))
        self.uploads.append((inhalt, titel, felder))

    def mit_pruefsumme(self, summen):
        return [i for i, s in self.gefiltert.items() if s == summen]

    def ablegen(self, neu_id):
        """Paperless files the last upload: Dokumente's, with the tags it was given"""
        inhalt, titel, felder = self.uploads[-1]
        self.docs[neu_id] = {"id": neu_id, "title": titel, "owner": 3, "tags": list(felder.get("tags") or []), "custom_fields": [],
                             "correspondent": felder.get("correspondent"), "document_type": felder.get("document_type"),
                             "created": felder.get("created")}
        self.texte[neu_id] = [t for t in json.loads(inhalt.decode())["seiten"]]
        self.gefiltert[neu_id] = doppelt.summen(inhalt)


KORRESPONDENTEN = {9: "Beispiel Bausparkasse", 11: "Allianz Lebensversicherungs-AG", 12: "Allianz Lebensversicherungs-Aktiengesellschaft",
                   14: "Beispiel Handwerk GmbH", 15: "Skandia Lebensversicherung AG"}


def heute(loeschen=True):
    """2026-11-08's pairs: the person's (mdopp scanned them) and the household's, and a letter of the same insurer a year later"""
    docs = [{"id": 405, "title": "2017-06-17 Allianz Lebensversicherungs-AG - Beitragsanpassung", "document_type": 7, "tags": [30],
             "owner": MDOPP, "correspondent": 11},
            {"id": 438, "title": "Dokument", "correspondent": 12, "owner": MDOPP, "tags": [31],
             "custom_fields": [{"field": 5, "value": "2026-11-08T10-00-00"}, {"field": 6, "value": "VS-471101"}]},
            # the sender under its other name, created the day it was filed; the title keeps the letter's date
            {"id": 439, "title": "2017-06-17 Allianz Lebensversicherungs-Aktiengesellschaft - Beitragsanpassung", "created": "2017-08-01",
             "correspondent": 12, "owner": MDOPP},
            # scanned today, no date set yet: the first page says 17.06.2017
            {"id": 440, "title": "Dokument", "created": "2026-11-08", "owner": MDOPP},
            {"id": 407, "created": "2019-03-02"}, {"id": 436, "created": "2019-03-02", "correspondent": 9, "tags": [31, 2]},
            {"id": 409, "created": "2020-11-11"}, {"id": 434, "created": "2020-11-11"}, {"id": 435, "created": "2020-11-11"},
            {"id": 433, "created": "2021-05-05"}, {"id": 411, "created": "2021-05-05"},
            {"id": 526, "created": "2022-02-01", "owner": MDOPP, "correspondent": 14, "title": "2022-02-01 Beispiel Handwerk GmbH - Rechnung 4711"},
            {"id": 527, "created": "2022-02-01", "owner": MDOPP, "correspondent": 14, "title": "2022-02-01 Beispiel Handwerk GmbH - Rechnung 4711"},
            {"id": 450, "created": "2018-06-15", "owner": MDOPP, "correspondent": 11, "title": "2018-06-15 Allianz Lebensversicherungs-AG - Beitragsanpassung"}]
    seiten = {405: ALLIANZ, 438: [rauschen(t) for t in ALLIANZ[:2]], 439: [rauschen(t, 37) for t in ALLIANZ], 440: [rauschen(t, 23) for t in ALLIANZ[:3]],
              407: ZWEI, 436: [rauschen(t) for t in ZWEI[:2]],
              409: DREI, 434: [rauschen(t) for t in DREI[:4]], 435: [rauschen(t, 29) for t in DREI],
              411: [rauschen(VIER[0])],
              526: RECHNUNG, 527: [rauschen(t) for t in RECHNUNG],
              450: brief("Allianz Lebensversicherungs-AG", "15.06.2018", "241,10", 6, 1)}
    # 433 has six pages: the letter's four and two of an enclosure; 411 the first alone
    seiten[433] = VIER + brief("Stadtwerke Beispielstadt", "05.05.2021", "112,40", 2, 14)
    f = Fake(docs, seiten, loeschen, KORRESPONDENTEN)
    f.notes[438] = ["Bitte bis Juli zahlen"]
    return f


def lauf(f, argv):
    d = tempfile.mkdtemp()
    return lauf_in(f, d, argv)


def lauf_in(f, d, argv):
    os.environ["ORDNEN_DIR"] = d
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = doppelt.main(argv, quelle=f)
    finally:
        del os.environ["ORDNEN_DIR"]
    return rc, out.getvalue(), d


def alle(f, argv=(), d=None):
    """intake over every document of the fixture, as if each had just come: the record begins before them"""
    d = d or tempfile.mkdtemp()
    p = Path(d, "doppelt.json")
    if not p.exists():
        p.write_text(json.dumps({"ab": 0}))
    return lauf_in(f, d, ["--neue", "--max", "99", *argv])


def haengt(out):
    """the kinds a run says hang, each with its detail"""
    return {json.loads(z[len("hängt "):])["was"]: json.loads(z[len("hängt "):])["detail"] for z in out.splitlines() if z.startswith("hängt {")}


def ratio(a, b):
    return difflib.SequenceMatcher(None, a[:3000], b[:3000], autojunk=False).ratio()


class Seiten(unittest.TestCase):
    def test_a_rescanned_page_is_the_same_page(self):
        self.assertTrue(doppelt.gleiche_seite(doppelt.falten(ALLIANZ[0]), doppelt.falten(rauschen(ALLIANZ[0]))))
        self.assertFalse(doppelt.gleiche_seite(doppelt.falten(ALLIANZ[0]), doppelt.falten(ALLIANZ[1])), "another page of the letter")

    def test_a_worn_sheets_second_pass_is_the_same_page_when_its_numbers_are(self):
        """the text 85–90 % alike (2026-11-08: the box's rescans measured 0.98, the threshold leaves room for a worse feeder);
        the numbers decide: the same page with a letter here and there otherwise, yes; the same template with other numbers, no"""
        a, b = doppelt.falten(ALLIANZ[0]), doppelt.falten(abgenutzt(ALLIANZ[0]))
        self.assertTrue(0.85 < ratio(a, b) < 0.9, "the fixture is the hard case: %.3f" % ratio(a, b))
        self.assertTrue(doppelt.gleiche_seite(a, b))
        andere = doppelt.falten(abgenutzt(ALLIANZ[0].replace("234,50", "981,20").replace("17.06.2017", "17.06.2018")))
        self.assertGreater(ratio(a, andere), 0.85)
        self.assertFalse(doppelt.gleiche_seite(a, andere), "the amount and the year differ: another letter")

    def test_the_insurers_letter_of_another_year_is_never_the_same(self):
        """same template, the first page ~0.9 alike as text: its date, amount and year differ"""
        a = [doppelt.falten(t) for t in ALLIANZ]
        b = [doppelt.falten(t) for t in brief("Allianz Lebensversicherungs-AG", "15.06.2018", "241,10", 6, 1)]
        self.assertGreater(ratio(a[0], b[0]), 0.9, "the fixture is the hard case")
        self.assertEqual(doppelt.vergleich(a, b)["art"], "verschieden")

    def test_kinds(self):
        a = [doppelt.falten(t) for t in ALLIANZ]
        self.assertEqual(doppelt.vergleich(a, a[:2])["art"], "b_in_a")
        self.assertEqual(doppelt.vergleich(a[:2], a)["art"], "a_in_b")
        self.assertEqual(doppelt.vergleich(a, list(a))["art"], "gleich")
        v = doppelt.vergleich([a[0], a[1], a[3]], [a[0], a[2]])
        self.assertEqual((v["art"], v["folge"]), ("ergaenzt", [("a", 0), ("a", 1), ("b", 1), ("a", 2)]), "by the printed page numbers")
        self.assertEqual(doppelt.vergleich([a[1], a[2]], [a[3], a[2]])["art"], "unsicher", "a shared page, not the same start")

    def test_without_page_numbers_the_order_must_be_clear(self):
        ohne = [doppelt.falten(t.replace("Seite %d von 6" % (i + 1), "")) for i, t in enumerate(ALLIANZ)]
        self.assertEqual(doppelt.vergleich([ohne[0], ohne[1]], [ohne[0], ohne[2]])["art"], "unsicher", "page 2 or 3 first?")
        v = doppelt.vergleich([ohne[0], ohne[1], ohne[2], ohne[4]], [ohne[0], ohne[2], ohne[3], ohne[4]])
        self.assertEqual(v["folge"], [("a", 0), ("a", 1), ("a", 2), ("b", 2), ("a", 3)], "page 4 between its neighbours")


class Kandidaten(unittest.TestCase):
    """who is compared with whom (2026-11-08: 340 documents, 0 found; 299 had the scanning person as owner)"""

    def doc(self, i, **k):
        return {"id": i, "title": "Dokument", "created": "2017-06-17", "owner": None, "correspondent": None, "content": "", **k}

    def test_the_same_owner_pairs_the_household_or_one_person(self):
        self.assertTrue(doppelt.paar_moeglich(self.doc(1), self.doc(2)), "both the household's")
        self.assertTrue(doppelt.paar_moeglich(self.doc(1, owner=MDOPP), self.doc(2, owner=MDOPP)), "both one person's")
        self.assertFalse(doppelt.paar_moeglich(self.doc(1, owner=MDOPP), self.doc(2)), "a person's and the household's")
        self.assertFalse(doppelt.paar_moeglich(self.doc(1, owner=MDOPP), self.doc(2, owner=8)), "two persons'")
        self.assertFalse(doppelt.paar_moeglich(self.doc(1), self.doc(1)))

    def test_one_sender_under_two_names(self):
        passen = doppelt.absender_passen
        self.assertTrue(passen("Allianz Lebensversicherungs-AG", "Allianz Lebensversicherungs-Aktiengesellschaft"), "the legal form")
        self.assertTrue(passen("DRV Bund", "Deutsche Rentenversicherung Bund"), "ordnen's ALIASE")
        self.assertTrue(passen("Deutsche Rentenversicherung", "Deutsche Rentenversicherung Bund"), "one the other's beginning")
        self.assertTrue(passen("Beispiel Handwerk GmbH & Co. KG", "Beispiel Handwerk"))
        self.assertFalse(passen("Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG"), "two companies of one group")
        self.assertFalse(passen("Stadtwerke Beispielstadt", "Stadt Beispielstadt"))
        namen = {11: "Allianz Lebensversicherungs-AG", 12: "Allianz Lebensversicherungs-Aktiengesellschaft", 13: "Allianz Versicherungs-AG"}
        self.assertTrue(doppelt.paar_moeglich(self.doc(1, correspondent=11), self.doc(2, correspondent=12), namen))
        self.assertTrue(doppelt.paar_moeglich(self.doc(1, correspondent=11), self.doc(2), namen), "one without a sender")
        self.assertTrue(doppelt.paar_moeglich(self.doc(1, correspondent=13), self.doc(2, correspondent=13), namen))
        self.assertFalse(doppelt.paar_moeglich(self.doc(1, correspondent=11), self.doc(2, correspondent=13), namen))
        self.assertFalse(doppelt.paar_moeglich(self.doc(1, correspondent=11), self.doc(2, correspondent=99), namen), "a name not known: not one sender")

    def test_a_date_in_common_created_the_titles_or_the_first_pages(self):
        self.assertEqual(doppelt.daten_im_text("Schreiben vom 17.06.2017, Stand 2017-06-17, am 17. Juni 2017; nicht 31.02.2017, nicht 12.03.1870"),
                         {"2017-06-17"})
        a = self.doc(1, created="2017-06-17")
        self.assertTrue(doppelt.paar_moeglich(a, self.doc(2, created="2017-06-17")), "created")
        self.assertFalse(doppelt.paar_moeglich(a, self.doc(2, created="2017-08-01")))
        self.assertTrue(doppelt.paar_moeglich(a, self.doc(2, created="2017-08-01", title="2017-06-17 Allianz - Beitragsanpassung")), "the title's date")
        self.assertTrue(doppelt.paar_moeglich(a, self.doc(2, created="2026-11-08", content=ALLIANZ[0] + "\f" + ALLIANZ[1])), "the first page's")
        self.assertFalse(doppelt.paar_moeglich(a, self.doc(2, created="2026-11-08", content="Deckblatt\f" + ALLIANZ[0])), "a later page's is not")
        self.assertEqual(doppelt.daten(self.doc(3, created="2017-08-01", title="2017-06-17 x", content="am 05.05.2021")),
                         {"2017-08-01", "2017-06-17", "2021-05-05"})


class Faelle(unittest.TestCase):
    def test_dry_run_changes_nothing_and_logs_every_case_with_the_kept_and_the_marked_one(self):
        f = heute()
        rc, out, d = alle(f, ["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(f.writes, [])
        self.assertTrue(out.startswith("doppelt: Start") and "(Probelauf)" in out.splitlines()[0])
        for zeile in ("#438 „Dokument“ (2 Seiten) steckt ganz in #405 „2017-06-17 Allianz Lebensversicherungs-AG - Beitragsanpassung“ (6 Seiten): "
                      "bekäme den Tag „Doppelt“ (gehört mdopp), übernommen: Tags, Felder",
                      "#439 „2017-06-17 Allianz Lebensversicherungs-Aktiengesellschaft - Beitragsanpassung“ (6 Seiten) steckt ganz in #405",
                      "#440 „Dokument“ (3 Seiten) steckt ganz in #405",
                      "#436 „Dokument 436“ (2 Seiten) steckt ganz in #407 „Dokument 407“ (6 Seiten): würde in den Papierkorb gelegt, übernommen: Absender, Tags",
                      "#434 „Dokument 434“ (4 Seiten) steckt ganz in #409", "#435 „Dokument 435“ (6 Seiten) steckt ganz in #409",
                      "#411 „Dokument 411“ (1 Seite) steckt ganz in #433",
                      "#527 „2022-02-01 Beispiel Handwerk GmbH - Rechnung 4711“ (2 Seiten) steckt ganz in #526"):
            self.assertIn(zeile, out)
        self.assertEqual(out.count(" steckt ganz in "), 8)
        self.assertNotIn("#450", out, "the next year's letter is no duplicate")
        self.assertEqual(haengt(out), {}, "nothing is tagged yet")
        self.assertFalse(Path(d, "doppelt.json").read_text().count("gesehen"), "a dry run keeps no record")

    def test_the_households_partial_ones_go_to_the_trash_and_the_whole_ones_take_what_they_lack(self):
        f = heute()
        rc, out, d = alle(f)
        self.assertEqual(sorted(x["id"] for x in f.trash), [411, 434, 435, 436])
        k = f.docs[407]
        self.assertEqual(k["correspondent"], 9, "the sender the partial one had")
        self.assertEqual(sorted(k["tags"]), [31], "tags joined, prüfen not carried")
        self.assertTrue(any("#436" in n and "Papierkorb" in n for n in f.notes[407]))
        rec = json.loads(Path(d, "doppelt.json").read_text())
        self.assertEqual(len(rec["erledigt"]), 8)
        self.assertIn('doppelt {"zusammengefuehrt": 4, "zum_loeschen": 4, "pruefen": 0}', out)
        n = len(f.writes)
        rc, out, d = alle(f, d=d)
        self.assertEqual(len(f.writes), n, "a second run does nothing")
        self.assertIn("4 doppelte Scans warten auf dein Löschen", haengt(out)["doppelt"], "but still says what waits")

    def test_a_persons_partial_one_is_tagged_doppelt_for_them_to_delete_and_the_run_says_how_many(self):
        f = heute()
        rc, out, d = alle(f)
        t = f.tag_id("Doppelt", anlegen=False)
        self.assertIsNotNone(t, "made once, the household's")
        for i in (438, 439, 440, 527):
            self.assertIn(i, f.docs, "never deleted: not Dokumente's")
            self.assertIn(t, f.docs[i]["tags"])
            self.assertNotIn(2, f.docs[i]["tags"], "not prüfen: nothing to look at, only to delete")
        self.assertEqual(sorted(f.docs[405]["tags"]), [30, 31], "the kept one takes the tags, not Doppelt")
        self.assertEqual(f.docs[405]["custom_fields"], [{"field": 5, "value": "2026-11-08T10-00-00"}, {"field": 6, "value": "VS-471101"}])
        self.assertEqual(f.docs[405]["correspondent"], 11, "its own sender stays")
        self.assertIn("Aus #438 übernommen: Bitte bis Juli zahlen", f.notes[405])
        self.assertTrue(any("#438 ist eine unvollständige Kopie (2 von 6 Seiten) – zum Löschen markiert." in n for n in f.notes[405]))
        self.assertTrue(any("#439 ist eine zweite Kopie (6 Seiten) – zum Löschen markiert." in n for n in f.notes[405]))
        n = f.notes[438][-1]
        self.assertIn("#405", n)
        self.assertIn("Tag „Doppelt“", n)
        self.assertEqual(haengt(out), {"doppelt": "4 doppelte Scans warten auf dein Löschen: in Paperless den Tag „Doppelt“ öffnen und alle löschen."})
        for i in (438, 439, 440, 527):
            del f.docs[i]
        rc, out, d = lauf_in(f, d, ["--neue"])
        self.assertEqual(haengt(out), {}, "deleted by hand: over")

    def test_without_the_right_to_delete_the_households_wait_marked_and_it_says_so(self):
        f = heute(loeschen=False)
        rc, out, d = alle(f)
        self.assertEqual(f.trash, [])
        self.assertIn(2, f.docs[436]["tags"])
        self.assertIn(2, f.docs[434]["tags"])
        self.assertTrue(any("Dokumente darf nicht löschen" in n for n in f.notes[434]))
        self.assertIn(f.tag_id("Doppelt", anlegen=False), f.docs[438]["tags"], "a person's needs no right")
        self.assertEqual(set(haengt(out)), {"loeschen", "doppelt"})

    def test_paperless_refusing_to_delete_marks_instead_of_breaking_off(self):
        f = heute()
        f.verboten = {436}
        rc, out, d = alle(f)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(x["id"] for x in f.trash), [411, 434, 435])
        self.assertIn(f.tag_id("Doppelt", anlegen=False), f.docs[436]["tags"])
        self.assertTrue(any("#436 ist eine unvollständige Kopie (2 von 6 Seiten) – zum Löschen markiert." in n for n in f.notes[407]))
        self.assertFalse(any("Papierkorb" in n for n in f.notes[407]))
        self.assertIn('"zum_loeschen": 5', out)

    def test_a_document_of_another_owner_is_never_paired(self):
        f = heute()
        f.docs[436]["owner"] = 8
        f.docs[438]["owner"] = None
        alle(f)
        self.assertIn(436, f.docs)
        self.assertIn(438, f.docs)
        self.assertFalse([w for w in f.writes if w[1] in (407, 436, 438)])

    def test_the_insurers_yearly_statements_share_a_date_on_the_page_and_never_pair(self):
        """Skandia's Jahresmitteilungen 2008–2012 (599–602): one template, one owner, one sender, the policy's start date
        on every first page, so each is a candidate against each; their year, date and amount differ"""
        jahre = {599: (2008, "12.345,67"), 600: (2009, "12.890,12"), 601: (2010, "13.401,55"), 602: (2011, "13.950,00"), 603: (2012, "14.502,30")}
        f = Fake([{"id": i, "created": "%d-01-20" % (j + 1), "owner": MDOPP, "correspondent": 15,
                   "title": "%d-01-20 Skandia Lebensversicherung AG - Jahresmitteilung %d" % (j + 1, j)} for i, (j, _) in jahre.items()],
                 {i: jahresmitteilung(j, b) for i, (j, b) in jahre.items()}, True, KORRESPONDENTEN)
        a, b = doppelt.falten(f.texte[599][0]), doppelt.falten(f.texte[600][0])
        self.assertGreater(ratio(a, b), 0.9, "the fixture is the hard case")
        docs, namen = {d["id"]: d for d in f.dokumente()}, f.korrespondenten()
        self.assertEqual(sum(doppelt.paar_moeglich(docs[a], docs[b], namen) for a in jahre for b in jahre if a < b), 10,
                         "the start date makes them candidates")
        rc, out, d = alle(f)
        self.assertEqual(f.writes, [])
        self.assertIn('doppelt {"zusammengefuehrt": 0, "zum_loeschen": 0, "pruefen": 0}', out)


class Zusammensetzen(unittest.TestCase):
    def setUp(self):
        orig = doppelt.pdf_bauen
        doppelt.pdf_bauen = lambda teile: json.dumps({"seiten": [self.f.texte[int(p.decode()[4:])][i] for p, i in teile]}).encode()
        self.addCleanup(setattr, doppelt, "pdf_bauen", orig)

    def haelften(self, owner=None):
        """425 has pages 1, 2 and 4, 430 pages 1 and 3"""
        self.f = f = Fake([{"id": 425, "title": "2021-01-04 Beispiel Kasse - Bescheid", "tags": [30], "document_type": 4, "owner": owner,
                            "custom_fields": [{"field": 5, "value": "2026-11-08T09-00-00"}]},
                           {"id": 430, "correspondent": 8, "owner": owner, "custom_fields": [{"field": 5, "value": "2026-11-08T11-00-00"}]}],
                          {425: [VIER[0], VIER[1], VIER[3]], 430: [rauschen(VIER[0]), VIER[2]]})
        f.notes[430] = ["angerufen"]
        return f

    def test_two_halves_become_one_document_in_page_order(self):
        f = self.haelften()
        rc, out, d = alle(f)
        [(inhalt, titel, felder)] = f.uploads
        self.assertEqual(json.loads(inhalt)["seiten"], [VIER[0], VIER[1], VIER[2], VIER[3]])
        self.assertEqual((titel, felder["document_type"], felder["correspondent"], felder["tags"], felder["created"]),
                         ("2021-01-04 Beispiel Kasse - Bescheid", 4, 8, [30], "2017-06-17"))
        self.assertEqual(f.trash, [], "the two stay until Paperless filed the new one")
        f.ablegen(612)
        rc, out, d2 = lauf_in(f, d, ["--neue"])
        self.assertEqual(sorted(x["id"] for x in f.trash), [425, 430])
        neu = f.docs[612]
        self.assertIsNone(neu["owner"], "the household's, as the two were")
        self.assertEqual(neu["custom_fields"], [{"field": 5, "value": "2026-11-08T09-00-00"}])
        self.assertIn("Aus #430 übernommen: angerufen", f.notes[612])
        self.assertTrue(any("aus #425 (3 Seiten) und #430 (2 Seiten)" in n and "Papierkorb" in n for n in f.notes[612]))

    def test_a_persons_two_halves_become_their_document_and_both_halves_wait_for_their_deleting(self):
        f = self.haelften(owner=MDOPP)
        rc, out, d = alle(f, ["--dry-run"])
        self.assertIn("ein Dokument mit 4 Seiten (A=#425, B=#430: A1 A2 B2 A3)", out)
        self.assertEqual((f.writes, f.uploads), ([], []))
        rc, out, d = alle(f)
        self.assertEqual(len(f.uploads), 1)
        f.ablegen(612)
        rc, out, d = lauf_in(f, d, ["--neue"])
        self.assertEqual(f.trash, [])
        self.assertEqual(f.docs[612]["owner"], MDOPP, "whose the two were")
        t = f.tag_id("Doppelt", anlegen=False)
        for i in (425, 430):
            self.assertIn(t, f.docs[i]["tags"])
            self.assertNotIn(2, f.docs[i]["tags"])
            self.assertTrue(any("#612" in n for n in f.notes[i]))
        self.assertTrue(any("Beide sind zum Löschen markiert" in n for n in f.notes[612]))
        self.assertIn("2 doppelte Scans warten auf dein Löschen", haengt(out)["doppelt"])
        n = len(f.writes)
        rc, out, d = lauf_in(f, d, ["--neue"])
        self.assertEqual(len(f.writes), n, "the new one is not paired with its own halves")

    def test_unsure_is_only_marked_on_both_with_the_other_named(self):
        self.f = f = Fake([{"id": 600}, {"id": 601}], {600: [VIER[1], VIER[2]], 601: [VIER[3], rauschen(VIER[2])]})
        alle(f)
        self.assertEqual((f.trash, f.uploads), ([], []))
        self.assertIn(2, f.docs[600]["tags"])
        self.assertIn(2, f.docs[601]["tags"])
        self.assertIn("#601", f.notes[600][0])
        self.assertIn("#600", f.notes[601][0])
        self.assertIn("nicht dieselbe erste Seite", f.notes[600][0])


class Neue(unittest.TestCase):
    def test_intake_begins_after_what_was_there_and_takes_each_new_one_once(self):
        f = heute()
        rc, out, d = lauf(f, ["--neue"])
        self.assertEqual(f.writes, [], "the first run only notes where intake begins")
        f.docs[700] = {"id": 700, "title": "Dokument", "owner": None, "tags": [], "custom_fields": [], "correspondent": None,
                       "document_type": None, "created": "2019-03-02"}
        f.texte[700] = [rauschen(ZWEI[0])]
        lauf_in(f, d, ["--neue"])
        self.assertEqual([x["id"] for x in f.trash], [700])
        self.assertIn(407, f.docs, "the older duplicate pair 407/436 came before intake began")
        n = len(f.writes)
        lauf_in(f, d, ["--neue"])
        self.assertEqual(len(f.writes), n)

    def test_a_persons_new_scan_of_their_invoice_is_tagged_at_once(self):
        f = heute()
        rc, out, d = lauf(f, ["--neue"])
        f.docs[701] = {"id": 701, "title": "Dokument", "owner": MDOPP, "tags": [], "custom_fields": [], "correspondent": None,
                       "document_type": None, "created": "2026-11-08"}
        f.texte[701] = [rauschen(RECHNUNG[0], 17)]
        rc, out, d = lauf_in(f, d, ["--neue"])
        self.assertIn(701, f.docs)
        self.assertIn(f.tag_id("Doppelt", anlegen=False), f.docs[701]["tags"])
        self.assertTrue(any("#526" in n or "#527" in n for n in f.notes[701]), "the first page's date found its letter")
        self.assertIn("1 doppelte Scans warten", haengt(out)["doppelt"])

    def test_paperless_away_is_no_failure(self):
        class Weg(Fake):
            def dokumente(self):
                raise urllib.error.URLError("connection refused")
        rc, out, d = lauf(Weg([], {}), ["--neue"])
        self.assertEqual(rc, 0)
        self.assertIn("doppelt: abgebrochen", out)

    def test_a_refused_token_hangs(self):
        class Abgelehnt(Fake):
            def dokumente(self):
                raise urllib.error.HTTPError("/api/documents/", 401, "Unauthorized", {}, None)
        rc, out, d = lauf(Abgelehnt([], {}), ["--neue"])
        self.assertIn('hängt {"was": "zugang"', out)


def mini_pdf(seiten):
    """a PDF with one line of text per page (Helvetica), enough for pdftotext and qpdf"""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for t in seiten:
        strom = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % t
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(strom), strom))
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>" % len(objs))
        kids.append(len(objs))
    objs[1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode()
    x = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode() + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)).encode()
    return out


@unittest.skipUnless(shutil.which("pdftotext") and shutil.which("qpdf"), "poppler and qpdf: in the image")
class Eingang(unittest.TestCase):
    """laib DATA.md gap 6: a new document is Dokumente's until zuteilen decides; a duplicate of it goes to the
    trash like the household's (Dokumente owns it, Paperless lets it), never tagged for a person to delete"""

    def test_dokumentes_own_is_trashed_like_the_households(self):
        f = Fake([], {})
        f.benutzer = lambda i: {9: "dokumente", MDOPP: "mdopp"}.get(i, "x")
        d = doppelt.Doppelt(f, tempfile.mkdtemp(), log=lambda *_: None)
        env = {"PAPERLESS_TOKEN": "t", "PAPERLESS_ADMIN_USER": "dokumente"}
        with unittest.mock.patch.dict(doppelt.E, env):
            self.assertEqual((d.besitzer({"owner": 9}), d.besitzer({"owner": None}), d.besitzer({"owner": MDOPP})), ("", "", "mdopp"))
        with unittest.mock.patch.dict(doppelt.E, {"PAPERLESS_TOKEN": ""}):   # inside Paperless as its admin: no exception
            self.assertEqual(doppelt.Doppelt(f, tempfile.mkdtemp(), log=lambda *_: None).besitzer({"owner": 9}), "dokumente")


class Werkzeuge(unittest.TestCase):
    def test_pages_are_read_and_put_together_in_order(self):
        a, b = mini_pdf(["Seite eins", "Seite zwei", "Seite vier"]), mini_pdf(["Seite eins", "Seite drei"])
        self.assertEqual([t.strip() for t in doppelt.pdf_seiten(a)], ["Seite eins", "Seite zwei", "Seite vier"])
        neu = doppelt.pdf_bauen([(a, 0), (a, 1), (b, 1), (a, 2)])
        self.assertEqual([t.strip() for t in doppelt.pdf_seiten(neu)], ["Seite eins", "Seite zwei", "Seite drei", "Seite vier"])
        with self.assertRaises(subprocess.CalledProcessError):
            doppelt.pdf_bauen([(b"kein pdf", 0)])


if __name__ == "__main__":
    unittest.main()
