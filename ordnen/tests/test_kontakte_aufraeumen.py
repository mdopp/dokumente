"""kontakte_aufraeumen.py (owner 2026-11-09): the firm cards a backfill made twice become one. The names are the
box's (firms, no private persons' data); numbers, mails and IBANs are made up. Against a fake of Homes' Haushalt
tools firmenkontakte, kontakt_ergaenzen and kontakt_entfernen that keeps kontakt_ergaenzen's rules: found by
name, fills only what is empty, another IBAN is abweichend, a card edited by hand is never removed."""
import copy, importlib.util, json, re, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("aufraeumen_test", Path(__file__).resolve().parent.parent / "kontakte_aufraeumen.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

IBAN_1, IBAN_2 = "DE89370400440532013000", "DE02120300000000202051"


def karte(kennung, name, von_hand=False, telefon=(), email=(), iban=(), notiz="", anschrift=()):
    return {"kennung": kennung, "name": name, "org": name, "telefon": list(telefon), "email": list(email), "iban": list(iban),
            "notiz": notiz, "anschrift": list(anschrift), "von_hand": von_hand}


KOENIGIN = {"strasse": "Königinstr. 28", "plz": "80802", "ort": "München", "land": "DE"}
REINSBURG = {"strasse": "Reinsburgstr. 19", "plz": "70178", "ort": "Stuttgart", "land": "DE"}
BOX = [
    karte("a1", "Allianz", anschrift=[KOENIGIN]),
    karte("a2", "Allianz Lebensversicherungs-AG", telefon=["0711 1234"], anschrift=[REINSBURG]),
    karte("a3", "Allianz Lebensversicherungs-Aktiengesellschaft", email=["leben@allianz.example"], iban=[IBAN_1],
          notiz="Bank: Beispielbank\nIBAN: DE89 3704 0044 0532 0130 00"),
    karte("a4", "Allianz Versicherungs-AG", telefon=["089 3800"], anschrift=[KOENIGIN]),
    karte("l1", "LBS", telefon=["0511 9150"]),
    karte("l2", "LBS Norddeutsche Landesbausparkasse", email=["service@lbs.example"]),
    karte("l3", "LBS Norddeutsche Landesbausparkasse Berlin - Hannover", iban=[IBAN_2]),
    karte("d1", "DKV Deutsche Krankenversicherung AG", telefon=["0221 5780"]),
    karte("d2", "Deutsche Krankenversicherung", telefon=["0221 578 0"], email=["service@dkv.example"]),
    karte("u1", "Union Krankenversicherung"),
    karte("u2", "Union Krankenversicherung Aktiengesellschaft", notiz="Ansprechpartner: Frau Muster"),
    karte("u3", "Union Krankenversicherung AG", von_hand=True, telefon=["0681 8440"]),
    karte("t1", "Thomas Stölting", telefon=["05251 1111"]),
    karte("t2", "Thomas Stölting & Partner"),
    karte("h1", "Dr. Stefan Himmeke", telefon=["05251 2222"]),
    karte("h2", "Dr. Stefan Hümmeke"),
    karte("b1", "BKK rmus", telefon=["0800 1111"]),
    karte("f1", "BFS health nance GmbH"),
    karte("r1", "Rollladen Herkenho GmbH", telefon=["05251 3333"]),
    karte("s1", "Stadtwerke Beispielstadt GmbH", telefon=["0123 4567"]),
]
KORR = ["BKK firmus", "BFS health finance GmbH", "LBS Landesbausparkasse NordWest", "Union Krankenversicherung AG"]


# the box's dry run of 2026-11-09: Paperless' correspondents carry the short and the OCR spelling, no card was edited by hand
BOX_0911 = [
    karte("a2", "Allianz Lebensversicherungs-AG", telefon=["0711 1234"]),
    karte("a3", "Allianz Lebensversicherungs-Aktiengesellschaft", email=["leben@allianz.example"]),
    karte("a0", "Allianz"),
    karte("a4", "Allianz Versicherungs-AG", telefon=["089 3800"]),
    karte("d2", "Deutsche Krankenversicherung", telefon=["0221 5780"]),
    karte("d1", "DKV Deutsche Krankenversicherung AG", email=["service@dkv.example"]),
    karte("h1", "Dr. Stefan Himmeke", telefon=["05251 2222"]),
    karte("h2", "Dr. Stefan Hümmeke"),
    karte("u1", "Union Krankenversicherung", telefon=["0681 8440"]),
    karte("u2", "Union Krankenversicherung Aktiengesellschaft", notiz="Ansprechpartner: Frau Muster"),
]
KORR_0911 = ["Deutsche Krankenversicherung", "Dr. Stefan Himmeke", "Union Krankenversicherung"]


class Homes:
    """the three tools on a list of cards"""

    def __init__(self, karten):
        self.karten, self.calls, self.neu = copy.deepcopy(karten), [], 0

    def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "firmenkontakte":
            return "%d Firmenkontakte\n" % len(self.karten), {"kontakte": copy.deepcopy(self.karten), "teilweise": False}
        if name == "kontakt_entfernen":
            k = next(k for k in self.karten if k["kennung"] == args["kennung"])
            if k["von_hand"]:
                raise RuntimeError("kontakt_entfernen: von Hand bearbeitet")
            self.karten.remove(k)
            return "entfernt\n", {"status": "entfernt"}
        key = lambda s: re.sub(r"\s", "", s).casefold()
        hits = [k for k in self.karten if key(k["name"]) == key(args["firma"])]
        if len(hits) > 1:
            return "mehrdeutig\n", {"status": "mehrdeutig", "felder": {}}
        felder, st = {}, "ergaenzt"
        if not hits:
            self.neu += 1
            hits = [karte("neu%d" % self.neu, args["firma"])]
            self.karten.append(hits[0])
            st = "angelegt"
        k = hits[0]
        for f in ("telefon", "email"):
            if f in args:
                felder[f] = "vorhanden" if args[f] in k[f] else st
                k[f] += [] if args[f] in k[f] else [args[f]]
        if "iban" in args:
            felder["iban"] = "vorhanden" if args["iban"] in k["iban"] else "abweichend" if k["iban"] else st
            k["iban"] += [] if k["iban"] else [args["iban"]]
        if "anschrift" in args and not k["anschrift"]:
            k["anschrift"], felder["anschrift"] = [args["anschrift"]], st
        if "ansprechpartner" in args:
            k["notiz"] += "\nAnsprechpartner: " + args["ansprechpartner"]
            felder["ansprechpartner"] = st
        return "ok\n", {"status": st if st == "angelegt" else "ergaenzt", "felder": felder}


class Liste(unittest.TestCase):
    def lauf(self, argv, karten=BOX, korr=KORR):
        h, out = Homes(karten), []

        class P:
            def korrespondenten(self):
                return list(korr)

            def kennt(self, wort):
                return False
        rc = a.main(argv, haushalt=h, paperless=P(), log=out.append)
        self.assertEqual(rc, 0, out)
        return h, out

    def test_the_dry_run_lists_each_group_and_changes_nothing(self):
        h, out = self.lauf([])
        self.assertEqual([c[0] for c in h.calls], ["firmenkontakte"], "a dry run only reads")
        liste = "\n".join(out)
        for z in [
            "„Allianz Lebensversicherungs-AG“ + „Allianz Lebensversicherungs-Aktiengesellschaft“ → „Allianz Lebensversicherungs-AG“ "
            "(übernimmt: E-Mail, IBAN)",
            "„Allianz Versicherungs-AG“ + „Allianz“ → „Allianz Versicherungs-AG“",
            "„LBS“ + „LBS Norddeutsche Landesbausparkasse“ + „LBS Norddeutsche Landesbausparkasse Berlin - Hannover“ → "
            "„LBS Landesbausparkasse NordWest“ (übernimmt: Telefon, E-Mail, IBAN); neu angelegt",
            "„DKV Deutsche Krankenversicherung AG“ + „Deutsche Krankenversicherung“ → „DKV Deutsche Krankenversicherung AG“ (übernimmt: E-Mail)",
            "„Union Krankenversicherung AG“ + „Union Krankenversicherung“ + „Union Krankenversicherung Aktiengesellschaft“ → "
            "„Union Krankenversicherung AG“ (übernimmt: Ansprechpartner)",
            "„Thomas Stölting & Partner“ + „Thomas Stölting“ → „Thomas Stölting & Partner“ (übernimmt: Telefon)",
            "„Dr. Stefan Hümmeke“ + „Dr. Stefan Himmeke“ → „Dr. Stefan Hümmeke“ (übernimmt: Telefon)",
            "„BKK rmus“ → „BKK firmus“ (übernimmt: Telefon); neu angelegt",
            "„BFS health nance GmbH“ → „BFS health finance GmbH“; neu angelegt",
        ]:
            self.assertIn(z, liste)
        self.assertNotIn("Stadtwerke", liste, "a card alone with its name whole stays as it is")

    def test_apply_merges_removes_and_keeps_the_hand_edited_card(self):
        h, out = self.lauf(["--apply"])
        namen = sorted(k["name"] for k in h.karten)
        self.assertEqual(namen, sorted([
            "Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG", "LBS Landesbausparkasse NordWest",
            "DKV Deutsche Krankenversicherung AG", "Union Krankenversicherung AG", "Thomas Stölting & Partner", "Dr. Stefan Hümmeke",
            "BKK firmus", "BFS health finance GmbH", "Rollladen Herkenho GmbH", "Stadtwerke Beispielstadt GmbH"]), out)
        k = {k["name"]: k for k in h.karten}
        self.assertEqual(k["Allianz Lebensversicherungs-AG"]["iban"], [IBAN_1])
        self.assertEqual(k["Allianz Lebensversicherungs-AG"]["email"], ["leben@allianz.example"])
        self.assertEqual(k["LBS Landesbausparkasse NordWest"]["telefon"], ["0511 9150"])
        self.assertEqual(k["Union Krankenversicherung AG"]["kennung"], "u3", "the card edited by hand is the one kept")
        self.assertIn("Ansprechpartner: Frau Muster", k["Union Krankenversicherung AG"]["notiz"])
        self.assertEqual(k["BKK firmus"]["telefon"], ["0800 1111"])
        self.assertFalse(any(c[0] == "kontakt_entfernen" and c[1]["kennung"] == "u3" for c in h.calls))
        h2, out2 = self.lauf([], karten=h.karten)
        self.assertEqual(out2[1:], ["- nichts doppelt"], "a second run finds nothing")

    def test_another_iban_keeps_the_card(self):
        karten = [karte("x1", "Muster Versicherung AG", iban=[IBAN_1]), karte("x2", "Muster Versicherung Aktiengesellschaft", iban=[IBAN_2])]
        h, out = self.lauf([], karten=karten)
        self.assertIn("„Muster Versicherung Aktiengesellschaft“ bleibt: eine andere IBAN (%s)" % IBAN_2, "\n".join(out))
        h, out = self.lauf(["--apply"], karten=karten)
        self.assertEqual(len(h.karten), 2, "nothing removed")
        self.assertFalse([c for c in h.calls if c[0] == "kontakt_entfernen"])

    def test_a_short_name_fitting_two_stays_and_is_listed(self):
        karten = [karte("a1", "Allianz"), karte("a2", "Allianz Lebensversicherungs-AG"), karte("a4", "Allianz Versicherungs-AG")]
        h, out = self.lauf(["--apply"], karten=karten)
        self.assertIn("„Allianz“ passt zu „Allianz Lebensversicherungs-AG“ und „Allianz Versicherungs-AG“: bleibt, bitte sagen, wohin",
                      "\n".join(out))
        self.assertEqual(len(h.karten), 3)

    def test_two_hand_edited_cards_are_left_and_named(self):
        karten = [karte("x1", "Muster AG", von_hand=True), karte("x2", "Muster Aktiengesellschaft", von_hand=True)]
        h, out = self.lauf(["--apply"], karten=karten)
        self.assertIn("von Hand bearbeitet: nichts geändert", "\n".join(out))
        self.assertEqual(len(h.karten), 2)

    def test_a_hand_edited_card_with_ocr_is_only_named(self):
        h, out = self.lauf(["--apply"], karten=[karte("b1", "BKK rmus", von_hand=True)])
        self.assertIn("„BKK rmus“ ist von Hand bearbeitet; heißt sie „BKK firmus“?", "\n".join(out))
        self.assertEqual([c[0] for c in h.calls], ["firmenkontakte"])

    def test_paperless_documents_give_a_lost_ligature(self):
        h, out = Homes([karte("r1", "Rollladen Herkenho GmbH")]), []

        class P:
            def korrespondenten(self):
                return []

            def kennt(self, wort):
                return wort == "Herkenhoff"
        a.main([], haushalt=h, paperless=P(), log=out.append)
        self.assertIn("„Rollladen Herkenho GmbH“ → „Rollladen Herkenhoff GmbH“; neu angelegt", "\n".join(out))

    def test_the_fullest_name_and_the_umlaut_win_over_the_correspondents_spelling(self):
        h, out = self.lauf([], karten=BOX_0911, korr=KORR_0911)
        liste = "\n".join(out)
        for z in ["→ „DKV Deutsche Krankenversicherung AG“ (übernimmt: Telefon)",
                  "„Dr. Stefan Hümmeke“ + „Dr. Stefan Himmeke“ → „Dr. Stefan Hümmeke“ (übernimmt: Telefon)",
                  "„Union Krankenversicherung“ + „Union Krankenversicherung Aktiengesellschaft“ → „Union Krankenversicherung AG“ "
                  "(übernimmt: Telefon, Ansprechpartner); neu angelegt",
                  "„Allianz Lebensversicherungs-AG“ + „Allianz Lebensversicherungs-Aktiengesellschaft“ → „Allianz Lebensversicherungs-AG“"]:
            self.assertIn(z, liste)

    def test_the_fullest_name_is_the_order_of_names(self):
        rang = lambda *n: min(n, key=lambda x: a.voll(x, ()))
        self.assertEqual(rang("Deutsche Krankenversicherung", "DKV Deutsche Krankenversicherung AG"), "DKV Deutsche Krankenversicherung AG")
        self.assertEqual(rang("Dr. Stefan Himmeke", "Dr. Stefan Hümmeke"), "Dr. Stefan Hümmeke")
        self.assertEqual(rang("BFS health nance GmbH", "BFS health finance GmbH"), "BFS health finance GmbH")
        self.assertEqual(rang("Muster Versicherung", "Muster Versicherung AG"), "Muster Versicherung AG")
        self.assertEqual(rang("Muster Versicherung Aktiengesellschaft", "Muster Versicherung AG"), "Muster Versicherung AG")
        self.assertEqual(a.kurzform("Union Krankenversicherung Aktiengesellschaft"), "Union Krankenversicherung AG")
        self.assertEqual(a.kurzform("Allianz Lebensversicherungs-Aktiengesellschaft"), "Allianz Lebensversicherungs-AG")
        self.assertEqual(a.kurzform("Muster Gesellschaft mit beschränkter Haftung"), "Muster GmbH")

    def test_name_sets_the_kept_name_and_the_dry_run_shows_it(self):
        karten = [karte("x1", "Muster Versicherung", telefon=["0123 1"]), karte("x2", "Muster Versicherung AG", email=["m@x.example"])]
        h, out = self.lauf(["--name", "Muster Versicherung AG=Muster Versicherung"], karten=karten)
        self.assertIn("„Muster Versicherung“ + „Muster Versicherung AG“ → „Muster Versicherung“ (statt „Muster Versicherung AG“, --name) "
                      "(übernimmt: E-Mail)", "\n".join(out))
        self.assertEqual([c[0] for c in h.calls], ["firmenkontakte"])
        h, out = self.lauf(["--apply", "--name", "„Muster Versicherung AG“=„Muster Versicherung“"], karten=karten)
        self.assertEqual([(k["kennung"], k["name"], k["email"]) for k in h.karten], [("x1", "Muster Versicherung", ["m@x.example"])])

    def test_name_takes_any_name_of_the_group_and_the_proposed_one_is_a_check(self):
        flaggen = ["--name", "Deutsche Krankenversicherung=DKV Deutsche Krankenversicherung AG",
                   "--name", "Dr. Stefan Himmeke=Dr. Stefan Hümmeke",
                   "--name", "Union Krankenversicherung=Union Krankenversicherung AG"]
        h, out = self.lauf(flaggen, karten=BOX_0911, korr=KORR_0911)
        self.assertNotIn("statt", "\n".join(out), "the owner's names are the proposed ones already")
        self.assertEqual(sum("(--name: wie vorgeschlagen)" in z for z in out), 3, out)
        h, out = self.lauf(["--name", "Union Krankenversicherung AG=Union Krankenversicherung"], karten=BOX_0911, korr=KORR_0911)
        self.assertIn("→ „Union Krankenversicherung“ (statt „Union Krankenversicherung AG“, --name)", "\n".join(out))

    def test_name_to_a_name_no_card_has_makes_it(self):
        karten = [karte("x1", "Muster Versicherung", telefon=["0123 1"]), karte("x2", "Muster Versicherung AG")]
        h, out = self.lauf(["--apply", "--name", "Muster Versicherung AG=Muster Versicherung Aktiengesellschaft"], karten=karten)
        self.assertEqual([(k["name"], k["telefon"]) for k in h.karten], [("Muster Versicherung Aktiengesellschaft", ["0123 1"])], out)

    def fehler(self, argv, karten):
        h, out = Homes(karten), []
        rc = a.main(argv, haushalt=h, paperless=None, log=out.append)
        self.assertEqual(rc, 2, out)
        self.assertEqual([c[0] for c in h.calls], ["firmenkontakte"], "a flag that fits nothing changes nothing")
        self.assertEqual(out[-1], "kontakte_aufraeumen: nichts geändert")
        return "\n".join(out)

    def test_name_that_fits_no_group_or_another_firm_is_refused(self):
        karten = [karte("x1", "Muster Versicherung"), karte("x2", "Muster Versicherung AG")]
        self.assertIn("--name „Beispiel AG=Muster“: kein Zielname im Probelauf; vorgeschlagen sind „Muster Versicherung AG“",
                      self.fehler(["--apply", "--name", "Beispiel AG=Muster"], karten))
        self.assertIn("„Ganz Andere GmbH“ ist keiner der Namen dieser Firma",
                      self.fehler(["--apply", "--name", "Muster Versicherung AG=Ganz Andere GmbH"], karten))
        h, out = Homes(karten), []
        self.assertEqual(a.main(["--name", "Muster"], haushalt=h, paperless=None, log=out.append), 2)
        self.assertIn("bitte als „Vorschlag=Name“", out[0])

    def test_name_on_a_hand_edited_group_is_refused(self):
        karten = [karte("x1", "Muster Versicherung"), karte("x2", "Muster Versicherung AG", von_hand=True)]
        self.assertIn("„Muster Versicherung AG“ ist von Hand bearbeitet, bitte dort ändern",
                      self.fehler(["--name", "Muster Versicherung AG=Muster Versicherung"], karten))

    def test_entfernen_removes_an_empty_card_and_the_dry_run_says_so(self):
        h, out = self.lauf(["--entfernen", "Allianz"], karten=BOX_0911, korr=KORR_0911)
        self.assertIn("- „Allianz“ wird entfernt (leer, von Homes angelegt)", out)
        self.assertNotIn("passt zu", "\n".join(out))
        self.assertEqual([c[0] for c in h.calls], ["firmenkontakte"])
        h, out = self.lauf(["--apply", "--entfernen", "Allianz", "--name", "Dr. Stefan Hümmeke=Dr. Stefan Hümmeke"],
                           karten=BOX_0911, korr=KORR_0911)
        self.assertIn(("kontakt_entfernen", {"kennung": "a0"}), h.calls)
        self.assertEqual(sorted(k["name"] for k in h.karten), ["Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG",
                                                               "DKV Deutsche Krankenversicherung AG",
                                                               "Dr. Stefan Hümmeke", "Union Krankenversicherung AG"], out)
        self.assertIn("1 leere entfernt", out[-1])

    def test_entfernen_refuses_a_card_with_data_or_edited_by_hand(self):
        for k, grund in [(karte("x", "Allianz", telefon=["089 1"]), "hat Telefon"),
                         (karte("x", "Allianz", email=["a@x.example"]), "hat E-Mail"),
                         (karte("x", "Allianz", iban=[IBAN_1]), "hat IBAN"),
                         (karte("x", "Allianz", anschrift=[KOENIGIN]), "hat Anschrift"),
                         (karte("x", "Allianz", anschrift=[{"ort": "München"}]), "hat Anschrift"),
                         (karte("x", "Allianz", von_hand=True), "von Hand bearbeitet")]:
            self.assertIn("--entfernen „Allianz“: %s, wird nicht entfernt" % grund, self.fehler(["--apply", "--entfernen", "Allianz"], [k]))
        self.assertIn("--entfernen „Allianz AG“: kein Firmenkontakt dieses Namens",
                      self.fehler(["--entfernen", "Allianz AG"], [karte("x", "Allianz")]))

    def test_homes_without_the_tool_says_so(self):
        out = []

        def h(name, args):
            raise RuntimeError("Homes kennt firmenkontakte nicht oder lehnt ab: unknown tool")
        self.assertEqual(a.main([], haushalt=h, paperless=None, log=out.append), 1)
        self.assertIn("firmenkontakte", out[0])


if __name__ == "__main__":
    unittest.main()
