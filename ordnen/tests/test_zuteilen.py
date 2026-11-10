"""zuteilen.py (laib DATA.md gap 6): a new document is someone's from the start. What came with a person
(von-<login>, their own mail address) is theirs; what ordnen finds is the household's goes to the
household; a scan addressed to a person goes to them; nobody found: it stays Dokumente's and the admins
are asked in Homes, never the whole family by default. Against a fake Paperless and a fake Homes."""
import contextlib, importlib.util, io, json, os, tempfile, unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("zuteilen_skript", Path(__file__).resolve().parent.parent / "zuteilen.py")
zuteilen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zuteilen)
ordnen = zuteilen.ordnen

ICH, ANNA, BEN = 9, 4, 5
JETZT = 1794312000.0   # 2026-11-10T12:00Z, three hours after the documents came
EINGANG, HAUSHALT, VON_ANNA, VON_CARL, ANDERES = 30, 31, 32, 33, 34


class Fake:
    def __init__(self, docs):
        self.docs = {d["id"]: {"owner": ICH, "correspondent": None, "created": "2026-11-03", "added": "2026-11-10T09:14:00Z",
                               "page_count": 2, "permissions": {"view": {"users": [ICH], "groups": []}, "change": {"users": [ICH], "groups": []}},
                               **d} for d in docs}
        self.tags = [{"id": EINGANG, "name": "Eingang"}, {"id": HAUSHALT, "name": "Haushalt"}, {"id": VON_ANNA, "name": "von-anna"},
                     {"id": VON_CARL, "name": "von-carl"}, {"id": ANDERES, "name": "Steuer"}]
        self.users = [{"id": ICH, "username": "dokumente", "groups": []}, {"id": 1, "username": "admin", "groups": []},
                      {"id": ANNA, "username": "anna", "first_name": "Anna", "groups": [2]},
                      {"id": BEN, "username": "ben", "first_name": "Ben Muster", "groups": [1, 2]}]
        self.writes = []

    def all(self, path):
        p, _, q = path.partition("?")
        if p == "/api/documents/":
            assert "tags__id__all=%d" % EINGANG in q, q
            return [dict(d) for d in self.docs.values() if EINGANG in d["tags"]]
        return [dict(x) for x in {"/api/users/": self.users, "/api/tags/": self.tags,
                                  "/api/correspondents/": [{"id": 7, "name": "Praxis Dr. Muster"}]}[p]]

    def __call__(self, method, path, body=None):
        p, _, q = path.partition("?")
        if p == "/api/ui_settings/":
            return {"user": {"id": ICH}}
        if p == "/api/tags/" and method == "GET":
            name = q.split("=", 1)[1]
            return {"results": [t for t in self.tags if t["name"].lower() == name.lower()]}
        i = int(p.strip("/").split("/")[-1])
        if method == "GET":
            return dict(self.docs[i])
        self.writes.append((i, body))
        d = self.docs[i]
        if "owner" in body:
            assert d["owner"] in (ICH, None), "only the owner gives a document away (Paperless 403)"
        d.update({k: v for k, v in body.items() if k != "set_permissions"})
        if "set_permissions" in body:
            d["permissions"] = body["set_permissions"]
        return dict(d)


class Zuteilen(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.gesendet = []

    def lauf(self, f, zuteilung=None, antworten=(), jetzt=JETZT):
        ordnen.schreiben(os.path.join(self.dir, "ordnen.json"), {"zuteilung": zuteilung or {}})

        def homes(body):
            self.gesendet.append(body)
            return {"antworten": list(antworten)}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            n = zuteilen.lauf(f, self.dir, log=lambda *_: None, homes=homes, jetzt=jetzt)
        self.out = out.getvalue()
        return n

    def test_what_a_person_sent_is_theirs_at_once(self):
        f = Fake([{"id": 1, "tags": [EINGANG, VON_ANNA]}])
        self.lauf(f)
        d = f.docs[1]
        self.assertEqual(d["owner"], ANNA, "before the model read it")
        self.assertIn(EINGANG, d["tags"], "the sorting may still find it is the household's")
        self.assertEqual(d["permissions"]["view"]["users"], [ICH], "no one else; Dokumente keeps its view")
        # ordnen read it: not the household's → it stays hers, the Eingang is done
        self.lauf(f, {"1": {"haushalt": False, "person": "Ben"}})
        self.assertEqual((d["owner"], EINGANG in d["tags"], HAUSHALT in d["tags"]), (ANNA, False, False),
                         "a person named inside never takes it from the one who sent it")

    def test_a_household_document_goes_to_the_household(self):
        f = Fake([{"id": 1, "tags": [EINGANG, VON_ANNA]}, {"id": 2, "tags": [EINGANG]}, {"id": 3, "tags": [EINGANG], "owner": BEN}])
        n = self.lauf(f, {k: {"haushalt": True, "person": None} for k in ("1", "2", "3")})
        for i in (1, 2):
            d = f.docs[i]
            self.assertEqual((d["owner"], EINGANG in d["tags"], HAUSHALT in d["tags"]), (None, False, True), i)
            self.assertEqual(d["permissions"], {"view": {"users": [], "groups": []}, "change": {"users": [], "groups": []}})
        d = f.docs[3]   # Ben's own (his mail address): Paperless' workflow „Für den Haushalt“ takes it out of his hands
        self.assertEqual((d["owner"], EINGANG in d["tags"], HAUSHALT in d["tags"]), (BEN, False, True))
        self.assertEqual(n["haushalt"], 3)

    def test_a_scan_addressed_to_a_person_goes_to_them(self):
        f = Fake([{"id": 1, "tags": [EINGANG, ANDERES]}, {"id": 2, "tags": [EINGANG]}])
        self.lauf(f, {"1": {"haushalt": False, "person": "Anna"}, "2": {"haushalt": False, "person": "Ben"}})
        self.assertEqual((f.docs[1]["owner"], f.docs[1]["tags"]), (ANNA, [ANDERES]))
        self.assertEqual(f.docs[2]["owner"], BEN, "the option „Ben“ is the account whose first name begins so")

    def test_nobody_found_stays_dokumentes_and_the_admins_are_asked(self):
        f = Fake([{"id": 1, "tags": [EINGANG], "correspondent": 7}, {"id": 2, "tags": [EINGANG]}])
        n = self.lauf(f, {"1": {"haushalt": False, "person": None}})
        self.assertEqual(f.writes, [], "never the family's by default")
        [body] = self.gesendet
        [e] = body["offen"]
        self.assertEqual({k: e[k] for k in ("id", "absender", "datum", "seiten", "seit")},
                         {"id": 1, "absender": "Praxis Dr. Muster", "datum": "2026-11-03", "seiten": 2, "seit": "2026-11-10 09:14"},
                         "only what the admins need to decide: no title, no text")
        self.assertEqual(body["personen"], [{"login": "anna", "name": "Anna"}, {"login": "ben", "name": "Ben Muster"}])
        self.assertEqual(n["gefragt"], 1, "#2 waits for ordnen")
        # a day later ordnen never read #2: asked too
        self.lauf(f, {"1": {"haushalt": False, "person": None}}, jetzt=1e10)
        self.assertEqual([e["id"] for e in self.gesendet[-1]["offen"]], [1, 2])

    def test_an_answer_counts_only_for_what_the_admin_saw(self):
        f = Fake([{"id": 1, "tags": [EINGANG], "correspondent": 7}, {"id": 2, "tags": [EINGANG]}])
        z = {"1": {"haushalt": False, "person": None}, "2": {"haushalt": False, "person": None}}
        self.lauf(f, z)
        zeichen = {e["id"]: e["zeichen"] for e in self.gesendet[0]["offen"]}
        n = self.lauf(f, z, antworten=[{"id": 1, "zeichen": zeichen[1], "an": "ben"},
                                       {"id": 2, "zeichen": "gefaelscht", "an": "haushalt"},
                                       {"id": 2, "zeichen": zeichen[2], "an": "fremd"}])
        self.assertEqual((f.docs[1]["owner"], EINGANG in f.docs[1]["tags"]), (BEN, False))
        self.assertEqual((f.docs[2]["owner"], f.docs[2]["tags"]), (ICH, [EINGANG]), "a list someone else posted gives nothing away")
        self.assertEqual(n["gefragt"], 1)
        n = self.lauf(f, z, antworten=[{"id": 2, "zeichen": zeichen[2], "an": "haushalt"}])
        self.assertEqual((f.docs[2]["owner"], HAUSHALT in f.docs[2]["tags"]), (None, True))

    def test_a_person_without_an_account_waits_and_it_is_said(self):
        f = Fake([{"id": 1, "tags": [EINGANG, VON_CARL]}])
        self.lauf(f, {"1": {"haushalt": False, "person": None}})
        self.assertEqual(f.writes, [])
        self.assertEqual(self.gesendet[0]["offen"], [], "carl's, not the admins' question")
        h = json.loads(next(z for z in self.out.splitlines() if z.startswith("hängt "))[len("hängt "):])
        self.assertEqual(h["was"], "konto")
        self.assertIn("carl", h["detail"])

    def test_homes_away_is_said_and_nothing_given(self):
        f = Fake([{"id": 1, "tags": [EINGANG]}])
        ordnen.schreiben(os.path.join(self.dir, "ordnen.json"), {"zuteilung": {"1": {"haushalt": False, "person": None}}})

        def weg(body):
            raise OSError("connection refused")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            zuteilen.lauf(f, self.dir, log=lambda *_: None, homes=weg, jetzt=JETZT)
        self.assertEqual(f.writes, [])
        self.assertIn('"was": "homes"', out.getvalue())


class Haushaltsdokument(unittest.TestCase):
    """the conservative rule: only the house, the car, insurance and tax, the model sure, nothing medical"""

    def plan(self, **kw):
        return {"typ": "Rechnung", "bereiche": ["Haus"], "person": None, "pruefen": [], **kw}

    def test_the_rule(self):
        h = ordnen.haushaltsdokument
        self.assertTrue(h(self.plan()))
        self.assertTrue(h(self.plan(person="Anna")), "a utility bill addressed to Anna is the household's")
        self.assertTrue(h(self.plan(typ="Versicherungsschein", bereiche=["Versicherung", "Auto"], person="Ben")))
        self.assertTrue(h(self.plan(typ="Steuer", bereiche=["Steuer"])))
        self.assertFalse(h(self.plan(typ="Versicherungsschein", bereiche=["Versicherung"], person="Anna")), "her own life insurance")
        self.assertFalse(h(self.plan(typ="Befund", bereiche=["Gesundheit"])))
        self.assertFalse(h(self.plan(typ="Arztbrief", bereiche=["Versicherung"])), "a medical type, whatever the Bereich")
        self.assertFalse(h(self.plan(bereiche=["Haus", "Arbeit & Rente"])))
        self.assertFalse(h(self.plan(bereiche=["Finanzen"])), "a bank statement is a person's")
        self.assertFalse(h(self.plan(pruefen=["das Modell ist unsicher"])))
        self.assertFalse(h(self.plan(typ=None)))
        self.assertFalse(h(self.plan(bereiche=[])))


if __name__ == "__main__":
    unittest.main()
