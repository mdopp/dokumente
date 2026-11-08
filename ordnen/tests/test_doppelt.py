"""doppelt.py: a document scanned twice becomes one. Page by page against a fake
Paperless: a partial rescan goes to the trash and the whole one takes what it
lacks, two halves become one document in page order, an unsure pair is only
marked, and an insurer's letters of two years are never one. The cases are
those of the stacks scanned on 2026-11-08 (ids kept, texts made up)."""
import contextlib, importlib.util, io, json, os, random, shutil, subprocess, tempfile, unittest, urllib.error
from pathlib import Path

_spec = importlib.util.spec_from_file_location("doppelt_skript", Path(__file__).resolve().parent.parent / "doppelt.py")
doppelt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doppelt)

WORTE = ("Versicherung Vertrag Beitrag Leistung Schaden Anpassung Bedingungen Kunde Tarif Zahlung Konto Laufzeit Kündigung "
         "Hinweis Mitteilung Anlage Unterlagen Erstattung Antrag Prüfung Haftung Deckung Umfang Wohnung Gebäude Hausrat "
         "Fahrzeug Person Zeitraum Rechnung Betrag Frist Ausnahme Regelung Erklärung Nachtrag Information Bestätigung").split()


def seite(absender, k, n, datum, betrag, saat):
    r = random.Random(saat * 100 + k)
    text = " ".join(r.choice(WORTE) for _ in range(90))
    return ("%s\nBeispielweg 12, 12345 Beispielstadt\nVersicherungsschein 4711%02d\nSeite %d von %d\n%s\nSchreiben vom %s, Betrag %s EUR\n"
            % (absender, saat, k, n, text, datum, betrag))


def brief(absender, datum, betrag, n, saat):
    return [seite(absender, k, n, datum, betrag, saat) for k in range(1, n + 1)]


def rauschen(t, schritt=41):
    """a second scan's OCR: a letter here and there read otherwise, the digits as they were"""
    out = list(t)
    for i in range(7, len(out), schritt):
        if out[i].isalpha():
            out[i] = "e" if out[i] != "e" else "c"
    return "".join(out)


ALLIANZ = brief("Beispiel Versicherung AG", "17.06.2017", "234,50", 6, 1)
ZWEI = brief("Beispiel Bausparkasse", "02.03.2019", "80,00", 6, 2)
DREI = brief("Beispiel Leben AG", "11.11.2020", "19,99", 6, 3)
VIER = brief("Stadtwerke Beispielstadt", "05.05.2021", "112,40", 4, 4)
RECHNUNG = brief("Beispiel Handwerk GmbH", "01.02.2022", "1.234,00", 2, 5)


class Fake(doppelt.Quelle):
    """Paperless with documents, their pages and notes; every write is kept"""

    def __init__(self, docs, seiten, loeschen=True):
        self.docs = {d["id"]: {"title": "Dokument %d" % d["id"], "owner": None, "tags": [], "custom_fields": [], "correspondent": None,
                               "document_type": None, "created": "2017-06-17", **d} for d in docs}
        self.texte = dict(seiten)
        self.seiten_cache = {}
        self.darf = loeschen
        self.trash, self.writes, self.notes, self.uploads, self.tags = [], [], {}, [], [{"id": 2, "name": "prüfen"}]
        self.gefiltert = {}

    def dokumente(self):
        return [json.loads(json.dumps(d)) for d in self.docs.values()]

    def dokument(self, i):
        return json.loads(json.dumps(self.docs[i]))

    def seiten(self, i):
        return [doppelt.falten(t) for t in self.texte[i]]

    def pdf(self, i):
        return ("pdf-%d" % i).encode()

    def darf_loeschen(self):
        return self.darf

    def loeschen(self, i):
        if i not in self.docs:
            raise urllib.error.HTTPError("/", 404, "Not Found", {}, None)
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

    def pruefen_tag(self):
        return 2

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


def heute(loeschen=True):
    """2026-11-08's pairs, and a letter of the same insurer a year later"""
    docs = [{"id": 405, "title": "2017-06-17 Beispiel Versicherung - Beitragsanpassung", "document_type": 7, "tags": [30]},
            {"id": 438, "title": "Dokument", "correspondent": 9, "tags": [31, 2], "custom_fields": [{"field": 5, "value": "2026-11-08T10-00-00"},
                                                                                       {"field": 6, "value": "VS-471101"}]},
            {"id": 439},
            {"id": 407, "created": "2019-03-02"}, {"id": 436, "created": "2019-03-02"},
            {"id": 409, "created": "2020-11-11"}, {"id": 434, "created": "2020-11-11"}, {"id": 435, "created": "2020-11-11"},
            {"id": 433, "created": "2021-05-05"}, {"id": 411, "created": "2021-05-05"},
            {"id": 526, "created": "2022-02-01"}, {"id": 527, "created": "2022-02-01"},
            {"id": 450, "created": "2018-06-15", "title": "2018-06-15 Beispiel Versicherung - Beitragsanpassung"}]
    seiten = {405: ALLIANZ, 438: [rauschen(t) for t in ALLIANZ[:2]], 439: [rauschen(t, 37) for t in ALLIANZ],
              407: ZWEI, 436: [rauschen(t) for t in ZWEI[:2]],
              409: DREI, 434: [rauschen(t) for t in DREI[:4]], 435: [rauschen(t, 29) for t in DREI],
              411: [rauschen(VIER[0])],
              526: RECHNUNG, 527: [rauschen(t) for t in RECHNUNG],
              450: brief("Beispiel Versicherung AG", "15.06.2018", "241,10", 6, 1)}
    # 433 has six pages: the letter's four and two of an enclosure; 411 the first alone
    seiten[433] = VIER + brief("Stadtwerke Beispielstadt", "05.05.2021", "112,40", 2, 14)
    f = Fake(docs, seiten, loeschen)
    f.notes[438] = ["Bitte bis Juli zahlen"]
    return f


def lauf(f, argv):
    d = tempfile.mkdtemp()
    os.environ["ORDNEN_DIR"] = d
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = doppelt.main(argv, quelle=f)
    finally:
        del os.environ["ORDNEN_DIR"]
    return rc, out.getvalue(), d


class Seiten(unittest.TestCase):
    def test_a_rescanned_page_is_the_same_page(self):
        self.assertTrue(doppelt.gleiche_seite(doppelt.falten(ALLIANZ[0]), doppelt.falten(rauschen(ALLIANZ[0]))))
        self.assertFalse(doppelt.gleiche_seite(doppelt.falten(ALLIANZ[0]), doppelt.falten(ALLIANZ[1])), "another page of the letter")

    def test_the_insurers_letter_of_another_year_is_never_the_same(self):
        """same template, the first page ~0.9 alike as text: its date, amount and year differ"""
        a = [doppelt.falten(t) for t in ALLIANZ]
        b = [doppelt.falten(t) for t in brief("Beispiel Versicherung AG", "15.06.2018", "241,10", 6, 1)]
        import difflib
        self.assertGreater(difflib.SequenceMatcher(None, a[0][:1500], b[0][:1500]).ratio(), 0.9, "the fixture is the hard case")
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


class Alle(unittest.TestCase):
    def test_dry_run_changes_nothing_and_lists_every_case(self):
        f = heute()
        rc, out, d = lauf(f, ["--alle", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(f.writes, [])
        text = Path(d, "zusammenfassung-doppelt.txt").read_text()
        self.assertTrue(text.startswith("PROBELAUF"))
        for zeile in ("#438 (2 S.) steckt ganz in #405 (6 S.)", "#439 (6 S.) steckt ganz in #405", "#436 (2 S.) steckt ganz in #407",
                      "#434 (4 S.) steckt ganz in #409", "#435 (6 S.) steckt ganz in #409", "#411 (1 S.) steckt ganz in #433",
                      "#527 (2 S.) steckt ganz in #526"):
            self.assertIn(zeile, text)
        self.assertNotIn("450", text, "the next year's letter is no duplicate")
        plan = json.loads(Path(d, "plan-doppelt.json").read_text())
        self.assertEqual(len(plan["faelle"]), 7)

    def test_the_partial_ones_go_to_the_trash_and_the_whole_ones_take_what_they_lack(self):
        f = heute()
        rc, out, d = lauf(f, ["--alle"])
        self.assertEqual(sorted(x["id"] for x in f.trash), [411, 434, 435, 436, 438, 439, 527])
        self.assertEqual(sorted(f.docs), [405, 407, 409, 433, 450, 526])
        k = f.docs[405]
        self.assertEqual(k["correspondent"], 9, "the sender the partial one had")
        self.assertEqual(k["document_type"], 7, "its own type stays")
        self.assertEqual(sorted(k["tags"]), [30, 31], "tags joined, prüfen not carried")
        self.assertEqual(k["title"], "2017-06-17 Beispiel Versicherung - Beitragsanpassung")
        self.assertEqual(k["custom_fields"], [{"field": 5, "value": "2026-11-08T10-00-00"}, {"field": 6, "value": "VS-471101"}])
        self.assertIn("Aus #438 übernommen: Bitte bis Juli zahlen", f.notes[405])
        self.assertTrue(any("#438" in n and "Papierkorb" in n for n in f.notes[405]))
        rec = json.loads(Path(d, "doppelt.json").read_text())
        self.assertEqual(len(rec["erledigt"]), 7)
        self.assertIn('doppelt {"zusammengefuehrt": 7', out)
        n = len(f.writes)
        os.environ["ORDNEN_DIR"] = d
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                doppelt.main(["--alle"], quelle=f)
        finally:
            del os.environ["ORDNEN_DIR"]
        self.assertEqual(len(f.writes), n, "a second run does nothing")

    def test_without_the_right_to_delete_nothing_goes_and_it_says_so(self):
        f = heute(loeschen=False)
        rc, out, d = lauf(f, ["--alle"])
        self.assertEqual(f.trash, [])
        self.assertIn(2, f.docs[438]["tags"])
        self.assertIn(2, f.docs[436]["tags"])
        self.assertTrue(any("Dokumente darf nicht löschen" in n for n in f.notes[436]))
        [h] = {json.loads(z[len("hängt "):])["was"] for z in out.splitlines() if z.startswith("hängt {")}
        self.assertEqual(h, "loeschen")

    def test_a_private_document_is_never_touched(self):
        f = heute()
        f.docs[436]["owner"] = 7
        lauf(f, ["--alle"])
        self.assertIn(436, f.docs)
        self.assertFalse([w for w in f.writes if w[1] in (407, 436)])


class Zusammensetzen(unittest.TestCase):
    def setUp(self):
        orig = doppelt.pdf_bauen
        doppelt.pdf_bauen = lambda teile: json.dumps({"seiten": [self.f.texte[int(p.decode()[4:])][i] for p, i in teile]}).encode()
        self.addCleanup(setattr, doppelt, "pdf_bauen", orig)

    def test_two_halves_become_one_document_in_page_order(self):
        """425 has pages 1, 2 and 4, 430 pages 1 and 3"""
        self.f = f = Fake([{"id": 425, "title": "2021-01-04 Beispiel Kasse - Bescheid", "tags": [30], "document_type": 4,
                            "custom_fields": [{"field": 5, "value": "2026-11-08T09-00-00"}]},
                           {"id": 430, "correspondent": 8, "custom_fields": [{"field": 5, "value": "2026-11-08T11-00-00"}]}],
                          {425: [VIER[0], VIER[1], VIER[3]], 430: [rauschen(VIER[0]), VIER[2]]})
        f.notes[430] = ["angerufen"]
        rc, out, d = lauf(f, ["--alle"])
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
        self.assertTrue(any("aus #425 (3 Seiten) und #430 (2 Seiten)" in n for n in f.notes[612]))

    def test_unsure_is_only_marked_on_both_with_the_other_named(self):
        self.f = f = Fake([{"id": 600}, {"id": 601}], {600: [VIER[1], VIER[2]], 601: [VIER[3], rauschen(VIER[2])]})
        lauf(f, ["--alle"])
        self.assertEqual((f.trash, f.uploads), ([], []))
        self.assertIn(2, f.docs[600]["tags"])
        self.assertIn(2, f.docs[601]["tags"])
        self.assertIn("#601", f.notes[600][0])
        self.assertIn("#600", f.notes[601][0])
        self.assertIn("nicht dieselbe erste Seite", f.notes[600][0])


def lauf_in(f, d, argv):
    os.environ["ORDNEN_DIR"] = d
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = doppelt.main(argv, quelle=f)
    finally:
        del os.environ["ORDNEN_DIR"]
    return rc, out.getvalue(), d


class Neue(unittest.TestCase):
    def test_intake_begins_after_what_was_there_and_takes_each_new_one_once(self):
        f = heute()
        rc, out, d = lauf(f, ["--neue"])
        self.assertEqual(f.writes, [], "the first run only notes where intake begins")
        f.docs[700] = {"id": 700, "title": "Dokument", "owner": None, "tags": [], "custom_fields": [], "correspondent": None,
                       "document_type": None, "created": "2022-02-01"}
        f.texte[700] = [rauschen(RECHNUNG[0])]
        lauf_in(f, d, ["--neue"])
        self.assertEqual([x["id"] for x in f.trash], [700])
        self.assertIn(526, f.docs, "the older duplicate pair 526/527 is --alle's")
        n = len(f.writes)
        lauf_in(f, d, ["--neue"])
        self.assertEqual(len(f.writes), n)

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
