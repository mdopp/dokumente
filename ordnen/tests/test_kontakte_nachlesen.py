"""kontakte_nachlesen.py (owner 2026-11-09): a household firm card without a phone, e-mail or address gets them from
its documents. The firm's name is the box's (the card measured 2026-11-09 with only an IBAN); its street, numbers and
the household are made up. Against test_kontakte_aufraeumen's fake of Homes' Haushalt tools (kontakt_ergaenzen fills
only what is empty), a fake Paperless and a fake model."""
import importlib.util, json, os, tempfile, unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent


def _lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


n = _lade("nachlesen_test", HIER.parent / "kontakte_nachlesen.py")
fa = _lade("aufraeumen_fakes", HIER / "test_kontakte_aufraeumen.py")

FIRMA = "Friedr. Wilh. Wienkämper GmbH & Co. KG"
IBAN = "DE89370400440532013000"
EMPF = "Familie Erika Mustermann\nMusterstraße 1\n12345 Beispielstadt\n"
BRIEF = (FIRMA + "\nHafenstraße 12, 49090 Osnabrück\nTel. 0541 123456\n\n" + EMPF +
         "Sehr geehrte Familie Mustermann,\nwir bestätigen Ihren Termin am 12. Mai.\nMit freundlichen Grüßen\nWienkämper")
OHNE = FIRMA + "\n\n" + EMPF + "Sehr geehrte Familie Mustermann,\nanbei die Unterlagen.\nMit freundlichen Grüßen"
ANTWORT = {"Firma": {"wert": FIRMA, "beleg": FIRMA},
           "Telefon": {"wert": "0541 123456", "beleg": "Tel. 0541 123456"},
           "Anschrift": {"wert": {"strasse": "Hafenstraße 12", "plz": "49090", "ort": "Osnabrück", "land": "DE"},
                         "beleg": "Hafenstraße 12, 49090 Osnabrück"},
           "Empfänger": {"wert": "Familie Erika Mustermann, Musterstraße 1, 12345 Beispielstadt",
                         "beleg": "Familie Erika Mustermann Musterstraße 1 12345 Beispielstadt"}}
NICHTS = {"Firma": {"wert": FIRMA, "beleg": FIRMA}, "Telefon": None, "Anschrift": None,
          "Empfänger": ANTWORT["Empfänger"]}


def dok(i, text, korr=7, created="2026-05-12"):
    return {"id": i, "title": "Brief", "content": text, "correspondent": korr, "created": created}


class Paperless:
    def __init__(self, docs, korr=({"id": 7, "name": FIRMA},)):
        self.docs, self.korr, self.calls = docs, list(korr), []

    def korrespondenten(self):
        return list(self.korr)

    def benutzer(self):
        return [{"username": "erika", "first_name": "Erika", "last_name": "Mustermann", "email": "erika@example.org"}]

    def dokumente(self, korrespondenten=(), phrase=None, n=3):
        self.calls.append((tuple(korrespondenten), phrase))
        if korrespondenten:
            out = [d for d in self.docs if d["correspondent"] in korrespondenten]
        else:
            out = [d for d in self.docs if phrase.casefold() in d["content"].casefold()]
        return sorted(out, key=lambda d: (d["created"], d["id"]), reverse=True)[:n]


class Model:
    """answers by the document's text: {a phrase of the text: the answer}"""

    def __init__(self, antworten):
        self.antworten, self.asks = antworten, []

    def __call__(self, messages):
        text = messages[-1]["content"]
        self.asks.append(text)
        return next((a for k, a in self.antworten.items() if k in text), {})


class Nachlesen(unittest.TestCase):
    def setUp(self):
        self.datei = os.path.join(tempfile.mkdtemp(), "ordnen", "nachlesen.json")

    def lauf(self, argv, homes, paperless, model, rc=0):
        out = []
        r = n.main(argv, haushalt=homes, paperless=paperless, frag=model, log=out.append, datei=self.datei)
        self.assertEqual(r, rc, out)
        return "\n".join(out)

    def ergaenzt(self, h):
        return [a for name, a in h.calls if name == "kontakt_ergaenzen"]

    def test_a_card_without_phone_gets_phone_and_address_from_its_letter_dry_run_first(self):
        h = fa.Homes([fa.karte("w1", FIRMA, iban=[IBAN])])
        p, m = Paperless([dok(123, BRIEF)]), Model({"Hafenstraße": ANTWORT})
        out = self.lauf([], h, p, m)
        self.assertEqual(self.ergaenzt(h), [], "a dry run writes no card")
        self.assertIn("%s: Telefon 0541 123456, Anschrift Hafenstraße 12, 49090 Osnabrück würde übernommen (aus Dokument #123)" % FIRMA, out)
        self.assertIn("Absender", m.asks[0], "vertraege's KONTAKT question")
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual(len(m.asks), 1, "the dry run's answer is kept: the same text is not asked again")
        self.assertEqual(self.ergaenzt(h), [{"firma": FIRMA, "telefon": "0541 123456",
                                             "anschrift": {"strasse": "Hafenstraße 12", "plz": "49090", "ort": "Osnabrück"}}],
                         "only what the card lacks, under the card's own name; never its IBAN again")
        self.assertIn("%s: Telefon 0541 123456, Anschrift Hafenstraße 12, 49090 Osnabrück übernommen (aus Dokument #123)" % FIRMA, out)
        k = h.karten[0]
        self.assertEqual((k["telefon"], k["iban"], len(k["anschrift"])), (["0541 123456"], [IBAN], 1))
        with open(self.datei) as f:
            rec = json.load(f)
        self.assertEqual(rec["firmen"]["w1"]["uebernommen"][0], {"feld": "telefon", "wert": "0541 123456", "dokument": 123})

    def test_a_rerun_skips_a_firm_read_unless_its_documents_changed(self):
        karten = [fa.karte("w1", FIRMA, iban=[IBAN])]
        p, m = Paperless([dok(5, OHNE)]), Model({"anbei": NICHTS, "Hafenstraße": ANTWORT})
        self.lauf(["--apply"], fa.Homes(karten), p, m)
        out = self.lauf(["--apply"], fa.Homes(karten), p, m)
        self.assertEqual(len(m.asks), 1, "read before, its documents the same: skipped")
        self.assertIn("1 Firmen schon nachgelesen", out)
        p.docs.append(dok(6, BRIEF, created="2026-06-01"))
        h = fa.Homes(karten)
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual(len(m.asks), 2, "a new document: read again, the old one's answer kept")
        self.assertIn("übernommen (aus Dokument #6)", out)

    def test_a_card_edited_on_a_phone_is_never_touched(self):
        h = fa.Homes([fa.karte("w1", FIRMA, von_hand=True, iban=[IBAN])])
        p, m = Paperless([dok(123, BRIEF)]), Model({"Hafenstraße": ANTWORT})
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual((self.ergaenzt(h), m.asks, p.calls), ([], [], []), "neither read nor written")
        self.assertIn("%s: von Hand bearbeitet, nicht angefasst" % FIRMA, out)

    def test_a_text_without_a_phone_says_nothing_found(self):
        h = fa.Homes([fa.karte("w1", FIRMA, iban=[IBAN])])
        p, m = Paperless([dok(77, OHNE)]), Model({"anbei": NICHTS})
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual(self.ergaenzt(h), [])
        self.assertIn("%s: nichts im Text gefunden (Dokument #77)" % FIRMA, out)

    def test_without_a_correspondent_the_name_is_searched_without_its_legal_form(self):
        h = fa.Homes([fa.karte("w1", FIRMA, iban=[IBAN])])
        p, m = Paperless([dok(9, BRIEF, korr=None)], korr=[]), Model({"Hafenstraße": ANTWORT})
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual(p.calls, [((), "Friedr. Wilh. Wienkämper")])
        self.assertIn("übernommen (aus Dokument #9)", out)

    def test_another_sender_gives_nothing_and_at_most_three_documents_newest_first(self):
        fremd = {**ANTWORT, "Firma": {"wert": "Stadtwerke Osnabrück AG", "beleg": "Stadtwerke Osnabrück AG"}}
        text = BRIEF.replace("Mit freundlichen", "Stadtwerke Osnabrück AG\nMit freundlichen")
        docs = [dok(i, text + " #%d" % i, created="2026-0%d-01" % i) for i in range(1, 6)]
        h, p, m = fa.Homes([fa.karte("w1", FIRMA)]), Paperless(docs), Model({"Hafenstraße": fremd})
        out = self.lauf(["--apply"], h, p, m)
        self.assertEqual(self.ergaenzt(h), [])
        self.assertEqual(len(m.asks), 3)
        self.assertIn("#5", m.asks[0], "the newest first")
        self.assertIn("nichts im Text gefunden (Dokument #5, Dokument #4, Dokument #3); Absender dort: „Stadtwerke Osnabrück AG“", out)

    def test_a_full_card_is_not_read_and_firma_narrows_the_run(self):
        voll = fa.karte("s1", "Stadtwerke Beispielstadt GmbH", telefon=["0123 4567"], email=["info@stadtwerke.example"],
                        anschrift=[{"strasse": "Weg 1", "plz": "12345", "ort": "Beispielstadt"}])
        h = fa.Homes([voll, fa.karte("w1", FIRMA), fa.karte("a1", "Allianz Versicherungs-AG")])
        p, m = Paperless([dok(1, BRIEF)]), Model({"Hafenstraße": ANTWORT})
        out = self.lauf(["--firma", "Wienkämper"], h, p, m)
        self.assertIn("1 Firmenkontakte ohne Telefon, E-Mail oder Anschrift", out)
        self.assertNotIn("Allianz", out)
        self.assertNotIn("Stadtwerke", out)

    def test_homes_away_changes_nothing(self):
        def weg(name, args):
            raise OSError("connection refused")
        self.assertIn("Homes antwortet nicht", self.lauf([], weg, Paperless([]), Model({}), rc=1))


class Namen(unittest.TestCase):
    def test_the_search_name_and_the_same_sender(self):
        self.assertEqual(n.suchname(FIRMA), "Friedr. Wilh. Wienkämper")
        self.assertEqual(n.suchname("Muster GmbH u. Co. KG"), "Muster")
        self.assertTrue(n.firmen.derselbe("Wienkämper GmbH", FIRMA), "the short name of the letter's footer")
        self.assertFalse(n.firmen.derselbe("Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG"))
        self.assertFalse(n.firmen.derselbe("Stadtwerke Osnabrück AG", FIRMA))


if __name__ == "__main__":
    unittest.main()
