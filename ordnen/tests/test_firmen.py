"""ordnen/firmen.py (owner 2026-11-09): whether two names are one firm, each rule with the names a backfill
made on the box, and the negatives that must stay two firms."""
import importlib.util, unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("firmen_test", Path(__file__).resolve().parent.parent / "firmen.py")
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class Gleich(unittest.TestCase):
    def test_legal_forms_fold(self):
        for a, b in [("Allianz Lebensversicherungs-AG", "Allianz Lebensversicherungs-Aktiengesellschaft"),
                     ("Union Krankenversicherung", "Union Krankenversicherung Aktiengesellschaft"),
                     ("Union Krankenversicherung AG", "Union Krankenversicherung Aktiengesellschaft"),
                     ("Muster GmbH & Co. KG", "Muster"), ("Reha-Zentrum Borkum", "Rehazentrum Borkum")]:
            self.assertEqual(f.gleich(a, b), "gleich", (a, b))

    def test_aliases(self):
        self.assertEqual(f.gleich("Deutsche Krankenversicherung", "DKV Deutsche Krankenversicherung AG"), "gleich")
        self.assertEqual(f.gleich("DKV", "Deutsche Krankenversicherung AG"), "gleich")
        for lbs in ("LBS", "LBS Norddeutsche Landesbausparkasse", "LBS Norddeutsche Landesbausparkasse Berlin - Hannover",
                    "LBS Norddeutsche Landesbausparkasse Berlin-Hannover"):
            self.assertEqual(f.gleich(lbs, "LBS Landesbausparkasse NordWest"), "gleich", lbs)
            self.assertEqual(f.kanonisch(lbs), "LBS Landesbausparkasse NordWest")
        self.assertIsNone(f.kanonisch("Union Krankenversicherung"))

    def test_the_shorter_name_is_the_fuller_one_a_longer_only_with_a_suffix(self):
        self.assertEqual(f.gleich("Allianz", "Allianz Versicherungs-AG"), "anfang")
        self.assertEqual(f.gleich("Thomas Stölting & Partner", "Thomas Stölting"), "anfang")
        self.assertEqual(f.gleich("Thomas Stölting", "Thomas Stölting & Partner"), "anfang")
        self.assertIsNone(f.gleich("Allianz Lebensversicherungs-AG", "Allianz"), "a longer name says a different firm")
        self.assertIsNone(f.gleich("AB", "AB Bau GmbH"), "one word of two letters is no name")

    def test_life_is_not_property(self):
        self.assertIsNone(f.gleich("Allianz Lebensversicherungs-AG", "Allianz Versicherungs-AG"))
        self.assertIsNone(f.gleich("Allianz Versicherungs-AG", "Allianz Lebensversicherungs-Aktiengesellschaft"))
        self.assertIsNone(f.gleich("Deutsche Krankenversicherung", "Deutsche Rentenversicherung Bund"))

    def test_ocr_one_letter_where_ocr_errs(self):
        self.assertEqual(f.gleich("Dr. Stefan Himmeke", "Dr. Stefan Hümmeke"), "ocr")
        self.assertEqual(f.gleich("Stadtwerke Bochurn", "Stadtwerke Bochum"), "ocr", "rn for m")
        self.assertIsNone(f.gleich("Malerei Schmidt", "Malerei Schmitt"), "d for t: two names")
        self.assertEqual(f.gleich("Malerei Schmidt", "Malerei Schmitt", belegt=True), "ocr", "unless they share a number")
        self.assertIsNone(f.gleich("Stadtwerke Kiel", "Stadtwerke Kehl", belegt=True), "a short word is never near enough")
        self.assertIsNone(f.gleich("Huber", "Hüber"), "a short name neither")
        self.assertIsNone(f.gleich("Dr. Anna Hümmeke", "Dr. Stefan Hümmeke"), "another word is another person")

    def test_beste_gives_only_the_best_reason(self):
        self.assertEqual(f.beste("Allianz Versicherungs-AG", ["Allianz", "Allianz Versicherungs-Aktiengesellschaft"]),
                         [("gleich", "Allianz Versicherungs-Aktiengesellschaft")])
        self.assertEqual(len(f.beste("Allianz", ["Allianz Versicherungs-AG", "Allianz Lebensversicherungs-AG"])), 2)
        self.assertEqual(f.beste("Neue Firma", ["Allianz"]), [])


class Ligaturen(unittest.TestCase):
    bekannt = f.wortschatz("BKK firmus", "BFS health finance GmbH", "Rollladen Herkenhoff GmbH", "Pflegedienst Muster")

    def test_put_back_where_a_known_word_has_it(self):
        for kaputt, heil in [("BKK rmus", "BKK firmus"), ("BFS health nance GmbH", "BFS health finance GmbH"),
                             ("Rollladen Herkenho GmbH", "Rollladen Herkenhoff GmbH"), ("Pegedienst Muster", "Pflegedienst Muster")]:
            self.assertEqual(f.ligaturen(kaputt, self.bekannt), heil)

    def test_nothing_known_nothing_changed(self):
        self.assertEqual(f.ligaturen("BKK rmus", f.wortschatz("BKK Mobil Oil")), "BKK rmus")
        self.assertEqual(f.ligaturen("Herr Rat", self.bekannt), "Herr Rat")

    def test_a_word_known_as_it_is_stays(self):
        self.assertEqual(f.ligaturen("Schaer GmbH", f.wortschatz("Schaffer"), f.wortschatz("Schaer GmbH")), "Schaer GmbH")
        self.assertEqual(f.ligaturen("Schaer GmbH", f.wortschatz("Schaffer")), "Schaffer GmbH", "unless only the text has it")

    def test_two_ways_change_nothing(self):
        self.assertEqual(f.ligaturen("ach", f.wortschatz("flach fiach")), "ach")


if __name__ == "__main__":
    unittest.main()
