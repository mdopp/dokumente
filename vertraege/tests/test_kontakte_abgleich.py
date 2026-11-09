"""Owner 2026-11-09: a backfill of vertraege.py made one firm several household contacts („Union
Krankenversicherung“ and „… Aktiengesellschaft“) and OCR's damage („BKK rmus“ for BKK firmus). Before
kontakt_ergaenzen makes a card, the sender is matched against the address book by ordnen/firmen.py's rules;
only when nothing matches is one made. Against test_vertraege's fake Paperless, model and Homes."""
import importlib.util, json, os, unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent


def _lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


t = _lade("test_vertraege_fakes", HIER / "test_vertraege.py")
v = _lade("vertraege_skript", HIER.parent / "vertraege.py")


def absender(firma, text=None):
    """MALER_KONTAKT and its answer with another sender's name"""
    return (text or t.MALER_KONTAKT).replace("Malerbetrieb Beispiel GmbH", firma), \
        {**t.KONTAKT_ANTWORT, "Firma": {"wert": firma, "beleg": firma}}


class Nacheinander(t.Model):
    """a model that gives each ask the next answer"""

    def __init__(self, antworten):
        self.folge = list(antworten)
        super().__init__(None)

    @property
    def answer(self):
        return self.folge.pop(0) if len(self.folge) > 1 else self.folge[0]

    @answer.setter
    def answer(self, _):
        pass


class Abgleich(t.Lauf):
    def rec(self):
        with open(self.record) as f:
            return json.load(f)

    def lauf(self, firma, adressbuch=(), korrespondenten=None, text=None):
        if os.path.exists(self.record):
            os.remove(self.record)
        text, antwort = absender(firma, text)
        p, h = t.Paperless([t.rechnung(text=text)], correspondents=korrespondenten), t.Homes(adressbuch=adressbuch)
        r = self.run_job(p, t.Model(antwort), homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        return h, r

    def test_another_legal_form_is_the_card_there(self):
        h, r = self.lauf("Union Krankenversicherung Aktiengesellschaft", [("Union Krankenversicherung AG",)])
        self.assertEqual([k["firma"] for k in h.kontakte()], ["Union Krankenversicherung AG"], "the card's own name: Homes finds it")
        self.assertEqual(self.rec()["kontakte"]["1"]["als"], "Union Krankenversicherung AG")
        self.assertIn("ist „Union Krankenversicherung AG“ im Adressbuch", r.stdout)

    def test_an_alias_is_the_card_there_and_a_new_card_takes_the_fuller_name(self):
        h, _ = self.lauf("Deutsche Krankenversicherung", [("DKV Deutsche Krankenversicherung AG",)])
        self.assertEqual(h.kontakte()[0]["firma"], "DKV Deutsche Krankenversicherung AG")
        h, r = self.lauf("LBS Norddeutsche Landesbausparkasse Berlin - Hannover")
        self.assertEqual(h.kontakte()[0]["firma"], "LBS Landesbausparkasse NordWest", "nothing there: made under ALIASE's name")
        self.assertIn("kommt als „LBS Landesbausparkasse NordWest“ ins Adressbuch", r.stdout)

    def test_a_life_insurer_is_not_the_property_insurer_of_the_same_group(self):
        h, _ = self.lauf("Allianz Lebensversicherungs-AG", [("Allianz Versicherungs-AG",), ("Allianz",)])
        self.assertEqual(h.kontakte()[0]["firma"], "Allianz Lebensversicherungs-AG", "neither the Sach-AG nor the bare name: its own card")

    def test_the_short_name_is_the_one_fuller_card_and_several_write_nothing(self):
        h, _ = self.lauf("Allianz", [("Allianz Versicherungs-AG",)])
        self.assertEqual(h.kontakte()[0]["firma"], "Allianz Versicherungs-AG")
        h, r = self.lauf("Allianz", [("Allianz Versicherungs-AG",), ("Allianz Lebensversicherungs-AG",)])
        self.assertEqual(h.kontakte(), [], "two firms fit: none is guessed, none made")
        e = self.rec()["kontakte"]["1"]
        self.assertEqual((e["status"], e["kandidaten"]), ("mehrdeutig", ["Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG"]))
        self.assertIn("passt zu mehreren Kontakten", r.stdout)
        h, _ = self.lauf("Allianz", [("Allianz Versicherungs-AG",), ("Allianz Lebensversicherungs-AG", ["+49 30 1234567"])])
        self.assertEqual(h.kontakte()[0]["firma"], "Allianz Lebensversicherungs-AG", "the document's number is on one of them")

    def test_a_suffix_like_partner_is_the_same_firm(self):
        h, _ = self.lauf("Thomas Stölting & Partner", [("Thomas Stölting",)])
        self.assertEqual(h.kontakte()[0]["firma"], "Thomas Stölting")

    def test_ocr_misreading_one_letter_is_the_card_there_another_name_is_not(self):
        h, _ = self.lauf("Dr. Stefan Himmeke", [("Dr. Stefan Hümmeke",)])
        self.assertEqual(h.kontakte()[0]["firma"], "Dr. Stefan Hümmeke")
        h, _ = self.lauf("Malerei Schmidt", [("Malerei Schmitt",)])
        self.assertEqual(h.kontakte()[0]["firma"], "Malerei Schmidt", "d for t is no OCR error: another firm")
        h, _ = self.lauf("Malerei Schmidt", [("Malerei Schmitt", ["030 1234567"])])
        self.assertEqual(h.kontakte()[0]["firma"], "Malerei Schmitt", "with the document's number it is")

    def test_a_lost_ligature_is_put_back_only_from_a_known_name(self):
        h, r = self.lauf("BKK rmus", korrespondenten=[{"id": 5, "name": "BKK firmus"}])
        self.assertEqual(h.kontakte()[0]["firma"], "BKK firmus", "Paperless knows the word with fi")
        h, _ = self.lauf("BFS health nance GmbH", [("BFS health finance GmbH",)])
        self.assertEqual(h.kontakte()[0]["firma"], "BFS health finance GmbH", "the address book knows it")
        h, _ = self.lauf("Rollladen Herkenho GmbH", text=t.MALER_KONTAKT + "\nwww.herkenhoff-rollladen.example")
        self.assertEqual(h.kontakte()[0]["firma"], "Rollladen Herkenhoff GmbH", "the text has it elsewhere")
        h, _ = self.lauf("BKK rmus")
        self.assertEqual(h.kontakte()[0]["firma"], "BKK rmus", "nobody knows „firmus“: the name stays as the text has it")

    def test_a_firm_twice_in_one_run_is_one_card(self):
        text = absender("Union Krankenversicherung")[0] + "\nUnion Krankenversicherung Aktiengesellschaft"
        p, h = t.Paperless([t.rechnung(1, text=text), t.rechnung(2, text=text)]), t.Homes()
        m = Nacheinander([absender("Union Krankenversicherung")[1], absender("Union Krankenversicherung Aktiengesellschaft")[1]])
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([k["firma"] for k in h.kontakte()], ["Union Krankenversicherung", "Union Krankenversicherung"],
                         "the second document finds the card the first made")

    def test_homes_without_the_search_keeps_the_contact_for_the_next_run(self):
        text, antwort = absender("Union Krankenversicherung")
        p, h = t.Paperless([t.rechnung(text=text)]), t.Homes()
        h.adressbuch = None   # kontakte_suchen fails
        r = self.run_job(p, t.Model(antwort), homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(h.kontakte(), [], "unmatched, nothing is made")
        self.assertIn("offen", self.rec()["kontakte"]["1"])


class Zeilen(unittest.TestCase):
    """kontakte_suchen's lines, and im_adressbuch on them"""

    def test_lines_give_names_numbers_and_mails(self):
        z = v.adressbuchzeilen("Allianz Versicherungs-AG (Allianz Versicherungs-AG) — Tel. 089 1234, +49 30 55 · E-Mail Info@Allianz.example\n"
                               "Thomas Stölting — Geburtstag 3. Mai\n… und 3 weitere; such genauer.")
        self.assertEqual(z, {"Allianz Versicherungs-AG": (["089 1234", "+49 30 55"], ["info@allianz.example"]), "Thomas Stölting": ([], [])})
        self.assertEqual(v.adressbuchzeilen("Kein Kontakt passt zu „x“."), {})

    def test_the_search_words_leave_out_legal_forms_and_short_words(self):
        self.assertEqual(v.suchworte("Dr. Stefan Hümmeke GmbH & Co. KG"), ["Stefan", "Hümmeke"])
        self.assertEqual(v.suchworte("BKK rmus"), ["BKK", "rmus"])

    def test_none_and_the_name_put_right(self):
        leer = lambda w: ""
        self.assertEqual(v.im_adressbuch({"firma": "Neue Firma GmbH"}, leer), (None, None))


WIENK = "Friedr. Wilh. Wienkämper GmbH & Co. KG"
WIENK_BRIEF = (WIENK + "\nHafenstraße 12, 49090 Osnabrück\nTel. 0541 123456\n\n" + t.ANSCHRIFT +
               "Sehr geehrte Familie Mustermann,\nwir bestätigen Ihren Termin am 12. Mai.\nMit freundlichen Grüßen\nWienkämper GmbH")
WIENK_ANTWORT = {"Firma": {"wert": "Wienkämper GmbH", "beleg": "Wienkämper GmbH"},
                 "Telefon": {"wert": "0541 123456", "beleg": "Tel. 0541 123456"},
                 "Anschrift": {"wert": {"strasse": "Hafenstraße 12", "plz": "49090", "ort": "Osnabrück", "land": "DE"},
                               "beleg": "Hafenstraße 12, 49090 Osnabrück"},
                 "Empfänger": t.KONTAKT_ANTWORT["Empfänger"]}


def karte(kennung, name, von_hand=False, telefon=(), iban=("DE02120300000000202051",), anschrift=()):
    return {"kennung": kennung, "name": name, "org": name, "telefon": list(telefon), "email": [], "iban": list(iban),
            "notiz": "", "anschrift": list(anschrift), "von_hand": von_hand}


def brief(i, korr, text=WIENK_BRIEF, created="2026-05-12"):
    return {**t.vertrag(i, typ=9, text=text, title="Terminbestätigung", correspondent=korr), "created": created}


class Briefe(t.Lauf):
    """2026-11-09: a household firm card without a phone or an address (made on an older path) is filled from a
    letter of its correspondent, not only from a contract or an invoice; once per document, bounded per run"""

    def rec(self):
        with open(self.record) as f:
            return json.load(f)

    def test_a_letter_fills_the_card_that_lacks_phone_and_address_once(self):
        p = t.Paperless([brief(5, 7), brief(4, 7, created="2026-01-02")], correspondents=[{"id": 7, "name": WIENK}])
        h, m = t.Homes(adressbuch=[(WIENK,)], karten=[karte("w1", WIENK)]), t.Model(WIENK_ANTWORT)
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1, "one letter a card a run, the newest")
        self.assertIn("Absender", m.asks[0][2]["messages"][-1]["content"])
        self.assertEqual(h.kontakte(), [{"firma": WIENK, "telefon": "0541 123456",
                                         "anschrift": {"strasse": "Hafenstraße 12", "plz": "49090", "ort": "Osnabrück"}}],
                         "only what the card lacks, under its own name")
        self.assertEqual(self.rec()["kontakte"]["5"]["fuer"], WIENK)
        self.assertIn("weil „%s“ im Adressbuch Telefon und Anschrift fehlt" % WIENK, r.stdout)
        h.karten = [karte("w1", WIENK, telefon=["0541 123456"], anschrift=[{"strasse": "Hafenstraße 12", "plz": "49090", "ort": "Osnabrück"}])]
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), 1, "the card is whole now: no letter is asked")

    def test_a_card_edited_by_hand_or_whole_is_left_alone(self):
        p = t.Paperless([brief(5, 7), brief(6, 8)], correspondents=[{"id": 7, "name": WIENK}, {"id": 8, "name": "Stadtwerke Beispielstadt"}])
        voll = karte("s1", "Stadtwerke Beispielstadt", telefon=["0123 4567"], anschrift=[{"strasse": "Weg 1", "plz": "12345", "ort": "Beispielstadt"}])
        h, m = t.Homes(karten=[karte("w1", WIENK, von_hand=True), voll]), t.Model(WIENK_ANTWORT)
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((m.asks, h.kontakte()), ([], []))

    def test_another_sender_writes_nothing_and_the_run_is_bounded(self):
        andere = {**WIENK_ANTWORT, "Firma": {"wert": "Stadtwerke Osnabrück AG", "beleg": "Stadtwerke Osnabrück AG"}}
        text = WIENK_BRIEF + "\nStadtwerke Osnabrück AG"
        namen = ["Firma %s Beispiel GmbH" % x for x in ("Anton", "Berta", "Caesar", "Dora", "Emil")]
        p = t.Paperless([brief(10 + i, i + 1, text=text) for i in range(5)],
                        correspondents=[{"id": i + 1, "name": n} for i, n in enumerate(namen)])
        h, m = t.Homes(karten=[karte("k%d" % i, n) for i, n in enumerate(namen)]), t.Model(andere)
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(m.asks), v.BRIEFE_PRO_LAUF, "bounded per run")
        self.assertEqual(h.kontakte(), [], "the letter's sender is another firm")
        self.assertEqual(self.rec()["kontakte"]["10"]["status"], "anderer Absender")
        self.assertIn("weitere Absender von Briefen im nächsten Lauf", r.stdout)

    def test_a_homes_without_firmenkontakte_reads_no_letter(self):
        p = t.Paperless([brief(5, 7)], correspondents=[{"id": 7, "name": WIENK}])
        h, m = t.Homes(), t.Model(WIENK_ANTWORT)
        r = self.run_job(p, m, homes=h)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(m.asks, [])
