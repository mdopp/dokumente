"""scan (laib #568) without a scanner, Tesseract or a GPU: pages are tiny
synthetic PGMs whose bytes the fake OCR maps to text, the model is a
function, scanimage a script that stops where it is told."""
import importlib.util, json, os, tempfile, textwrap, time, unittest

# by its path: run from the repo root, "scan" is the folder, not the program
_spec = importlib.util.spec_from_file_location("scan", os.path.join(os.path.dirname(__file__), "..", "scan.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

TEXTE, DREHUNG = {}, {}


def seite(wert, marke, w=20, h=10):
    data = bytearray([wert]) * (w * h)
    data[0], data[3] = marke, (marke * 7) % 256
    return scan.Bild(w, h, 1, bytes(data))


def vorne(marke, text):
    b = seite(120, marke)
    TEXTE[b.data] = text
    return b


def leer(marke):
    return seite(252, marke)


def durchscheinend(marke):
    b = seite(250, marke)
    TEXTE[b.data] = "Kehr ngeb ssuM ldeB reiF"   # read as it lies: noise, too many words for blank alone
    TEXTE[scan.gespiegelt(b).data] = " ".join(["Schornsteinfeger", "Rechnung", "Kehrbezirk", "Messung"] * 4)
    return b


def fake_ocr(pfad):
    return TEXTE.get(scan.lies_pnm(pfad).data, "")


def fake_osd(pfad):
    return DREHUNG.get(scan.lies_pnm(pfad).data, (0, 0.0))


BRIEF_A1 = "Bezirksschornsteinfeger Mosel Rechnung Nr 4711 vom 01.05.2023 Kehrung Messung Abgaswege Betrag Euro Seite eins von zwei"
BRIEF_A2 = "Fortsetzung Seite zwei von zwei Zahlbar innerhalb vierzehn Tagen ohne Abzug Bankverbindung Sparkasse Osnabrueck"
BRIEF_B = "Abfallwirtschaft ASV Bescheid ueber Abfallgebuehren vom 03.02.2021 Grundstueck Restmuell Biotonne Gebuehr Jahr"


def antwort(seiten):
    return json.dumps({"seiten": seiten, "dokumente": [
        {"erste_seite": 1, "art": "Rechnung", "absender": "Mosel", "datum": "2023-05-01", "titel": "Rechnung Schornsteinfeger"},
        {"erste_seite": 5, "art": "Bescheid", "absender": "ASV", "datum": "2021-02-03", "titel": "Abfallgebühren"}]})


class Bilder(unittest.TestCase):
    def test_turns_and_mirrors_like_the_page_lies(self):
        # 3 wide, 2 high: a b c / d e f
        b = scan.Bild(3, 2, 1, b"abcdef")
        self.assertEqual(scan.gedreht(b, 90), scan.Bild(2, 3, 1, b"daebfc"))
        self.assertEqual(scan.gedreht(b, 270), scan.Bild(2, 3, 1, b"cfbead"))
        self.assertEqual(scan.gedreht(b, 180).data, b"fedcba")
        self.assertEqual(scan.gespiegelt(b).data, b"cbafed")
        farbe = scan.Bild(2, 1, 3, b"RGBrgb")
        self.assertEqual(scan.gespiegelt(farbe).data, b"rgbRGB")
        self.assertEqual(scan.gedreht(farbe, 180).data, b"rgbRGB")

    def test_a_cut_off_scan_is_no_page(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.pnm")
            scan.schreib_pnm(seite(120, 1), p)
            self.assertEqual(scan.lies_pnm(p).w, 20)
            with open(p, "r+b") as f:
                f.truncate(os.path.getsize(p) - 7)
            with self.assertRaises(ValueError):
                scan.lies_pnm(p)


class Seiten(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.alt = scan.ocr, scan.osd
        scan.ocr, scan.osd = fake_ocr, fake_osd

    def tearDown(self):
        scan.ocr, scan.osd = self.alt

    def pruefe(self, b, nr=1, text=None):
        p = os.path.join(self.d, "s-%d.pgm" % nr)
        scan.schreib_pnm(b, p)
        return scan.pruefe_seite(p, nr, text)

    def test_blank_and_show_through_go_a_printed_front_stays(self):
        self.assertTrue(self.pruefe(leer(1)).leer)
        s = self.pruefe(durchscheinend(2))
        self.assertTrue(s.leer, s)
        self.assertIn("durchscheinend", s.warum)
        self.assertFalse(self.pruefe(vorne(3, BRIEF_A1)).leer)
        # bright but read: a short letter on white paper is no blank page
        kurz = seite(250, 4)
        TEXTE[kurz.data] = "Kündigung zum Monatsende Mit freundlichen Grüßen Musterfrau"
        self.assertFalse(self.pruefe(kurz).leer)

    def test_a_page_upside_down_is_turned_only_when_osd_is_sure(self):
        b = vorne(5, "")
        DREHUNG[b.data] = (180, 9.5)
        TEXTE[scan.gedreht(b, 180).data] = BRIEF_B
        s = self.pruefe(b)
        self.assertEqual((s.drehung, s.text), (180, BRIEF_B))
        unsicher = vorne(6, BRIEF_A2)
        DREHUNG[unsicher.data] = (90, 1.2)
        self.assertEqual(self.pruefe(unsicher, 2).drehung, 0)

    def test_a_pdfs_text_layer_is_read_as_it_is(self):
        b = seite(130, 7)
        s = self.pruefe(b, 1, BRIEF_A1 + " " + BRIEF_A2)
        self.assertEqual(s.text, BRIEF_A1 + " " + BRIEF_A2)


def inhalt(nrs, hell=0.5):
    return [scan.Seite(nr=n, bild="", hell=hell, text="Text %d" % n, woerter=10) for n in nrs]


class Trennen(unittest.TestCase):
    def test_documents_are_runs_from_each_start_never_the_models_page_groups(self):
        seiten = [{"seite": 1, "neu": True}, {"seite": 2, "neu": False}, {"seite": 3, "neu": True}, {"seite": 4}]
        docs, _ = scan.trennen(inhalt([1, 2, 3, 4]), lambda m: "```json\n" + json.dumps({"seiten": seiten, "dokumente": [
            {"erste_seite": 3, "absender": "ASV", "titel": "Bescheid"}, {"erste_seite": 3, "seiten": [1, 3]}]}) + "\n```")
        self.assertEqual([[s.nr for s in d.seiten] for d in docs], [[1, 2], [3, 4]])

    def test_the_first_page_starts_a_document_even_unmarked(self):
        docs, _ = scan.trennen(inhalt([2, 5]), lambda m: json.dumps({"seiten": [{"seite": 5, "neu": True}]}))
        self.assertEqual([[s.nr for s in d.seiten] for d in docs], [[2], [5]])

    def test_the_models_blank_counts_only_on_a_bright_page(self):
        answer = lambda m: json.dumps({"seiten": [{"seite": 1, "neu": True}, {"seite": 2, "leer": True}]})
        docs, weg = scan.trennen(inhalt([1, 2], hell=0.96), answer)
        self.assertEqual(([s.nr for s in weg], [[s.nr for s in d.seiten] for d in docs]), ([2], [[1]]))
        docs, weg = scan.trennen(inhalt([1, 2], hell=0.92), answer)
        self.assertEqual((weg, [[s.nr for s in d.seiten] for d in docs]), ([], [[1, 2]]))

    def test_broken_or_shapeless_answers_are_errors(self):
        for a in ("Ich kann das nicht.", '{"seiten": [', '{"dokumente": []}'):
            with self.assertRaises(ValueError):
                scan.trennen(inhalt([1, 2]), lambda m, a=a: a)

    def test_a_big_stack_goes_in_overlapping_windows_and_each_page_counts_once(self):
        self.assertEqual(scan.fenster(40), [(0, 40)])
        self.assertEqual(scan.fenster(60), [(0, 30), (25, 55), (50, 60)])
        calls = []

        def frage(messages):
            nrs = [int(x) for x in __import__("re").findall(r"--- Seite (\d+) ---", messages[1]["content"])]
            calls.append(nrs)
            # every window marks its own first page new, and every tenth page
            return json.dumps({"seiten": [{"seite": n, "neu": n == nrs[0] or n % 10 == 1} for n in nrs]})
        docs, _ = scan.trennen(inhalt(range(1, 61)), frage)
        self.assertEqual(len(calls), 3)
        self.assertEqual([d.seiten[0].nr for d in docs], [1, 11, 21, 31, 41, 51])
        self.assertEqual(sum(len(d.seiten) for d in docs), 60)

    def test_names_are_paperless_titles_and_never_collide(self):
        self.assertEqual(scan.dateiname(scan.Dokument([], absender="Mosel", datum="2023-05-01", titel="Rechnung")), "2023-05-01 Mosel - Rechnung")
        self.assertEqual(scan.dateiname(scan.Dokument([], absender="A/B: C", datum="1.5.23", titel="")), "A B C - Dokument")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "X.pdf"), "w").close()
            vergeben = set()
            self.assertEqual([scan.frei(d, "X", vergeben) for _ in range(2)], ["X (2).pdf", "X (3).pdf"])

    def test_filing_is_whole_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = scan.Config(consume=d, tag="Stapel", owner=(os.getuid(), os.getgid()))
            pdf = os.path.join(d, "quelle.pdf")
            with open(pdf, "wb") as f:
                f.write(b"%PDF-1.4 x")
            scan.ablegen(cfg, pdf, "2023-05-01 Mosel - Rechnung.pdf")
            self.assertEqual(os.listdir(os.path.join(d, "Stapel")), ["2023-05-01 Mosel - Rechnung.pdf"])


FAKE_SCANIMAGE = textwrap.dedent("""\
    #!/usr/bin/env python3
    # scanimage as the WF-3540 behaves: the runs in $FAKE_RUNS, one per call
    import json, os, sys
    if "-L" in sys.argv:   # the search: what $FAKE_LIST says is on the network
        print(os.environ.get("FAKE_LIST", ""))
        sys.exit(0)
    if "-A" in sys.argv:   # the device opened, its options read
        if os.environ.get("FAKE_A_FAILS"):
            print("scanimage: open of device %s failed: Error during device I/O" % sys.argv[sys.argv.index("-d") + 1], file=sys.stderr)
            sys.exit(1)
        print("Options specific to device ...")
        sys.exit(0)
    runs = json.load(open(os.environ["FAKE_RUNS"]))
    run, rest = runs[0], runs[1:]
    json.dump(rest, open(os.environ["FAKE_RUNS"], "w"))
    args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    n = int(args["batch-start"])
    for page in run["pages"]:
        open(args["batch"] % n, "wb").write(bytes.fromhex(page))
        print("Scanned page %d. (scanner status = 5)" % n, file=sys.stderr)
        n += 1
    print(run["end"], file=sys.stderr)
    print("scanimage: Batch terminated, %d pages scanned" % len(run["pages"]), file=sys.stderr)
    sys.exit(0 if "out of documents" in run["end"] else 7)
""")


def schreib(pfad, data):
    with open(pfad, "wb") as f:
        f.write(data)


def pnm(b):
    return (b"P5\n%d %d\n255\n" % (b.w, b.h) + b.data).hex()


class Dienst(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = scan.Config(data=os.path.join(self.d, "data"), eingang=os.path.join(self.d, "eingang"),
                               consume=os.path.join(self.d, "consume"), scanner="epson2:net:192.0.2.7", notify="papa",
                               scanimage=os.path.join(self.d, "scanimage"), stable_s=0.2, poll_s=0, owner=(os.getuid(), os.getgid()))
        for k in ("data", "eingang", "consume"):
            os.makedirs(os.path.join(self.d, k))
        with open(self.cfg.scanimage, "w") as f:
            f.write(FAKE_SCANIMAGE)
        os.chmod(self.cfg.scanimage, 0o755)
        self.meldungen = []
        self.alt = scan.ocr, scan.osd, scan.melde, scan.pdf_aus_bildern, scan.run, scan.tcp, scan.hole, scan.nachbarn, scan.wecken
        scan.ocr, scan.osd = fake_ocr, fake_osd
        # the LAN as the tests set it: every port answers, no neighbour, no wake
        self.zu, self.escl, self.lan, self.geweckt = {}, {}, [], []
        scan.tcp = lambda host, port, timeout: self.zu.get((host, port), "")
        scan.hole = self.hole
        scan.nachbarn = lambda: list(self.lan)
        scan.wecken = lambda ips, warte: self.geweckt.append(list(ips))
        scan.melde = lambda cfg, person, text: self.meldungen.append((person, text)) or True
        scan.pdf_aus_bildern = lambda cfg, bilder, ziel: schreib(ziel, b"%%PDF %d" % len(bilder))

    def tearDown(self):
        scan.ocr, scan.osd, scan.melde, scan.pdf_aus_bildern, scan.run, scan.tcp, scan.hole, scan.nachbarn, scan.wecken = self.alt
        for k in ("FAKE_RUNS", "FAKE_LIST", "FAKE_A_FAILS"):
            os.environ.pop(k, None)

    def hole(self, url, timeout):
        if url not in self.escl:
            raise OSError("[Errno 111] Connection refused")
        return self.escl[url]

    def runs(self, *runs):
        p = os.path.join(self.d, "runs.json")
        with open(p, "w") as f:
            json.dump([{"pages": [pnm(b) for b in r[0]], "end": r[1]} for r in runs], f)
        os.environ["FAKE_RUNS"] = p

    def auftrag(self, dienst, aktion, **mehr):
        with open(os.path.join(self.cfg.data, "auftrag", "%d.json" % time.time_ns()), "w") as f:
            json.dump({"aktion": aktion, "von": "haus", **mehr}, f)
        dienst.tick()

    def status(self):
        with open(os.path.join(self.cfg.data, "laib-status.json")) as f:
            return json.load(f)

    def abgelegt(self):
        return sorted(os.listdir(os.path.join(self.cfg.consume, "Stapel")))

    def test_a_jam_mid_stack_waits_for_the_rest_and_weiter_finishes_it(self):
        # duplex, back first: sheet 1 blank back + A1, sheet 2 show-through + A2, sheet 3 blank + B
        b1, a1, st2, a2, b3, b = leer(11), vorne(12, BRIEF_A1), durchscheinend(13), vorne(14, BRIEF_A2), leer(15), vorne(16, BRIEF_B)
        self.runs(([b1, a1, st2, a2, b3], "scanimage: sane_start: Error during device I/O"), ([b3, b], "scanimage: sane_start: Document feeder out of documents"))
        gefragt = []

        def frage(messages):
            gefragt.append(messages[1]["content"])
            return antwort([{"seite": 1, "neu": True}, {"seite": 3, "neu": False}, {"seite": 5, "neu": True}])
        dienst = scan.Dienst(self.cfg)
        dienst.verarbeiten = lambda stapel, frage_=None, v=dienst.verarbeiten: v(stapel, frage)
        self.auftrag(dienst, "start", person="mama")
        s = self.status()
        self.assertEqual((s["zustand"], s["blatt"]), ("wartet-auf-rest", 3), s)
        self.assertEqual(len(self.meldungen), 1)
        self.assertEqual(self.meldungen[0][0], "mama")
        self.assertIn("bei Blatt 3 abgebrochen (Scanner: sane_start: Error during device I/O)", self.meldungen[0][1])
        self.assertFalse(os.path.exists(os.path.join(self.cfg.consume, "Stapel")), "nothing filed while the stack waits")

        self.auftrag(dienst, "weiter")
        s = self.status()
        self.assertEqual(s["zustand"], "fertig", s)
        self.assertEqual(self.abgelegt(), ["2021-02-03 ASV - Abfallgebühren.pdf", "2023-05-01 Mosel - Rechnung Schornsteinfeger.pdf"])
        self.assertEqual(len(self.meldungen), 2)
        self.assertTrue(self.meldungen[1][1].startswith("Scan fertig: 3 Blatt, 6 Seiten, 3 leer entfernt, 2 Dokumente"), self.meldungen[1][1])
        # the model saw the content pages, front first, the blanks never
        self.assertEqual(__import__("re").findall(r"--- Seite (\d+) ---", gefragt[0]), ["1", "3", "5"])
        self.assertEqual(s["letzte"][0]["dokumente"][0]["titel"], "2023-05-01 Mosel - Rechnung Schornsteinfeger")
        with open(os.path.join(self.cfg.data, s["stapel"], "plan.json")) as f:
            self.assertEqual([d["seiten"] for d in json.load(f)["dokumente"]], [[1, 3], [5]])

    def test_verwerfen_files_nothing_and_says_so_once(self):
        self.runs(([leer(21), vorne(22, BRIEF_A1), leer(23)], "scanimage: sane_start: Document feeder jammed"))
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "start")
        self.assertEqual(self.status()["blatt"], 2)
        self.auftrag(dienst, "verwerfen")
        self.assertEqual(self.status()["zustand"], "bereit")
        self.assertEqual([p for p, _ in self.meldungen], ["papa", "papa"])
        self.assertIn("nichts abgelegt", self.meldungen[1][1])
        self.assertFalse(os.path.exists(os.path.join(self.cfg.consume, "Stapel")))

    def test_a_model_that_fails_files_the_stack_unsorted(self):
        self.runs(([leer(31), vorne(32, BRIEF_A1), leer(33), vorne(34, BRIEF_B)], "scanimage: sane_start: Document feeder out of documents"))
        dienst = scan.Dienst(self.cfg)
        dienst.verarbeiten = lambda stapel, frage=None, v=dienst.verarbeiten: v(stapel, lambda m: "kaputt {")
        self.auftrag(dienst, "start")
        self.assertEqual(self.abgelegt(), ["Stapel unsortiert.pdf"])
        self.assertIn("Stapel unsortiert", self.meldungen[-1][1])
        self.assertEqual(len(self.meldungen), 1)

    def test_a_pdf_in_the_inbox_is_filed_once_it_stops_growing(self):
        TEXTE_PDF = {1: BRIEF_A1 + " " + BRIEF_A1, 2: "", 3: BRIEF_B + " " + BRIEF_B}
        seiten_bilder = {1: seite(140, 41), 2: leer(42), 3: seite(140, 43)}
        calls = []

        def fake_run(cmd, timeout=600):
            calls.append(cmd[0])
            out = ""
            if cmd[0] == "pdftoppm":
                for n, b in seiten_bilder.items():
                    scan.schreib_pnm(b, "%s-%d.pgm" % (cmd[-1], n))
            elif cmd[0] == "pdftotext":
                out = TEXTE_PDF[int(cmd[2])]
            elif cmd[0] == "qpdf" and "--empty" in cmd:
                schreib(cmd[-1], ("%PDF pages " + cmd[4]).encode())
            return scan.subprocess.CompletedProcess(cmd, 0, out, "")
        scan.run = fake_run
        os.makedirs(os.path.join(self.cfg.eingang, "mama"))
        pdf = os.path.join(self.cfg.eingang, "mama", "Scan_0001.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.7 half")
        dienst = scan.Dienst(self.cfg)
        dienst.verarbeiten = lambda stapel, frage=None, v=dienst.verarbeiten: v(stapel, lambda m: antwort([{"seite": 1, "neu": True}, {"seite": 3, "neu": True}]).replace('"erste_seite": 5', '"erste_seite": 3'))
        dienst.tick()
        time.sleep(0.1)
        with open(pdf, "ab") as f:
            f.write(b" grows")   # the scanner still writing
        dienst.tick()
        time.sleep(0.1)
        dienst.tick()
        self.assertTrue(os.path.exists(pdf), "a file still growing was taken")
        time.sleep(0.25)
        dienst.tick()
        self.assertFalse(os.path.exists(pdf), "the inbox keeps the file")
        self.assertEqual(self.abgelegt(), ["2021-02-03 ASV - Abfallgebühren.pdf", "2023-05-01 Mosel - Rechnung Schornsteinfeger.pdf"])
        with open(os.path.join(self.cfg.consume, "Stapel", "2021-02-03 ASV - Abfallgebühren.pdf")) as f:
            self.assertEqual(f.read(), "%PDF pages 3", "the original's own page, not a re-render")
        self.assertNotIn("tesseract", calls)
        self.assertEqual(len(self.meldungen), 1)
        self.assertEqual(self.meldungen[0][0], "mama")
        self.assertTrue(self.meldungen[0][1].startswith("Scan fertig: Scan_0001.pdf: 3 Seiten, 1 leer entfernt, 2 Dokumente"), self.meldungen[0][1])
        st = self.status()
        self.assertTrue(os.path.exists(os.path.join(self.cfg.data, st["stapel"], "original.pdf")))

    def test_a_restart_mid_stack_ends_it_with_fehler_and_what_is_filed(self):
        dienst = scan.Dienst(self.cfg)
        stapel = dienst.neuer_stapel("scanner", "mama")
        stapel.abgelegt = ["2023-05-01 Mosel - Rechnung.pdf"]
        dienst.speichern(stapel)
        dienst.zustand("legt-ab", "legt 2 Dokumente ab", stapel)
        scan.Dienst(self.cfg)
        s = self.status()
        self.assertEqual(s["zustand"], "fehler")
        self.assertEqual(s["findings"][0]["fix"], "laib scan redo --stapel " + stapel.zeit)
        self.assertEqual(len(self.meldungen), 1)
        self.assertIn("Schon in Paperless: 1 Dokumente", self.meldungen[0][1])

    def test_a_waiting_stack_waits_on_after_a_restart_and_is_dropped_after_a_day(self):
        self.runs(([leer(51), vorne(52, BRIEF_A1)], "scanimage: sane_start: Error during device I/O"))
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "start")
        jetzt = [time.time()]
        wieder = scan.Dienst(self.cfg, now=lambda: jetzt[0])
        self.assertEqual(self.status()["zustand"], "wartet-auf-rest")
        jetzt[0] += 1801
        wieder.tick()
        self.assertTrue(self.meldungen[-1][1].startswith("Erinnerung:"))
        jetzt[0] += 86400
        wieder.tick()
        self.assertEqual(self.status()["zustand"], "bereit")
        self.assertIn("einen Tag", self.meldungen[-1][1])
        self.assertEqual(len(self.meldungen), 3)


    def test_the_search_lists_the_networks_scanners_and_keeps_them_through_a_stack(self):
        os.environ["FAKE_LIST"] = ("device `epson2:net:192.0.2.7' is a Epson PID 0899 flatbed scanner\n"
                                   "device `airscan:e0:Brother ADS-4300N' is a eSCL Brother ADS-4300N ip=192.0.2.9\n"
                                   "No scanners were identified. If you were expecting something different,")
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "suchen")
        s = self.status()
        self.assertEqual(s["zustand"], "bereit")
        self.assertEqual(s["suche"]["zustand"], "fertig")
        self.assertEqual(s["suche"]["funde"], [{"geraet": "epson2:net:192.0.2.7", "name": "Epson PID 0899 flatbed scanner"},
                                               {"geraet": "airscan:e0:Brother ADS-4300N", "name": "eSCL Brother ADS-4300N ip=192.0.2.9"}])
        self.runs(([leer(61), vorne(62, BRIEF_A1)], "scanimage: sane_start: Document feeder out of documents"))
        dienst.verarbeiten = lambda stapel, frage=None, v=dienst.verarbeiten: v(stapel, lambda m: antwort([{"seite": 1, "neu": True}]))
        self.auftrag(dienst, "start")
        self.assertEqual(self.status()["zustand"], "fertig")
        self.assertEqual(len(self.status()["suche"]["funde"]), 2, "a stack wiped the search")
        self.assertEqual(len(scan.Dienst(self.cfg).suche["funde"]), 2, "a restart wiped the search")

    def test_nothing_found_says_what_to_do(self):
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "suchen")
        self.assertIn("von Hand", self.status()["suche"]["detail"])

    def test_a_new_scanner_is_checked_at_once_and_a_dead_one_says_why(self):
        dienst = scan.Dienst(self.cfg)
        dienst.tick()
        p = self.status()["pruefung"]
        self.assertEqual((p["geraet"], p["ok"], p["detail"]), ("epson2:net:192.0.2.7", True, "Scanner antwortet"))
        os.environ["FAKE_A_FAILS"] = "1"
        dienst.tick()
        self.assertTrue(self.status()["pruefung"]["ok"], "checked once per device, not on every tick")
        self.auftrag(dienst, "pruefen")
        p = self.status()["pruefung"]
        self.assertFalse(p["ok"])
        self.assertNotIn("Error during device I/O", p["detail"], "SANE's words only in einzelheiten (#589)")
        self.assertIn("lässt sich aber nicht öffnen", p["detail"])
        self.assertIn("Error during device I/O", p["einzelheiten"])
        self.assertEqual(self.meldungen, [], "a check is no stack: no message")

    def test_a_scanner_that_is_off_is_asked_whether_it_is_on_not_shown_sanes_error(self):
        # the WF-3540 off: no answer on 1865, and SANE would say "Invalid argument" (#589)
        os.environ["FAKE_A_FAILS"] = "1"
        self.zu[("192.0.2.7", 1865)] = "timed out"
        dienst = scan.Dienst(self.cfg)
        for aktion in ("pruefen", "probe"):
            self.auftrag(dienst, aktion)
            p = self.status()["pruefung"]
            self.assertFalse(p["ok"])
            self.assertEqual(p["detail"], "Der Scanner unter 192.0.2.7 antwortet nicht – ist er an?")
            self.assertIn("192.0.2.7:1865", p["einzelheiten"])
            self.assertIn("timed out", p["einzelheiten"])
        self.assertTrue(p["probe"])
        self.assertEqual(self.meldungen, [])

    def test_a_stack_on_a_scanner_that_is_off_asks_whether_it_is_on(self):
        self.runs(([], "scanimage: open of device epson2:net:192.0.2.7 failed: Invalid argument"))
        self.zu[("192.0.2.7", 1865)] = "[Errno 113] No route to host"
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "start")
        (person, text), = self.meldungen
        self.assertIn("Der Scanner unter 192.0.2.7 antwortet nicht – ist er an?", text)
        self.assertNotIn("Invalid argument", text)
        self.assertIn("Invalid argument", self.status()["letzte"][0]["einzelheiten"])

    def test_the_search_asks_the_lans_neighbours_on_the_two_scanner_ports(self):
        # sane's own search misses the WF-3540; it answers on 1865 (#589)
        self.lan = ["192.0.2.87", "192.0.2.9", "192.0.2.50"]
        self.zu = {(ip, 1865): "Connection refused" for ip in ("192.0.2.9", "192.0.2.50")}
        self.escl["http://192.0.2.9/eSCL/ScannerCapabilities"] = (
            b'<?xml version="1.0"?><scan:ScannerCapabilities xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">'
            b'<pwg:Version>2.6</pwg:Version><pwg:MakeAndModel>Brother ADS-4300N</pwg:MakeAndModel></scan:ScannerCapabilities>')
        self.escl["http://192.0.2.50/eSCL/ScannerCapabilities"] = b"<html>router</html>"
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "suchen")
        s = self.status()["suche"]
        self.assertEqual(self.geweckt, [["192.0.2.87", "192.0.2.9", "192.0.2.50"]], "one gentle wake first")
        self.assertEqual(s["funde"], [{"geraet": "epson2:net:192.0.2.87", "name": "Epson-Scanner unter 192.0.2.87"},
                                      {"geraet": "escl:http://192.0.2.9:80", "name": "Brother ADS-4300N"}])
        self.assertEqual(s["gefragt"], 3)
        self.assertIn("2 Scanner gefunden", s["detail"])
        # sane-airscan knows the eSCL one: its device, not a second find
        os.environ["FAKE_LIST"] = "device `airscan:e0:Brother ADS-4300N' is a eSCL Brother ADS-4300N ip=192.0.2.9\n"
        self.auftrag(dienst, "suchen")
        self.assertEqual([f["geraet"] for f in self.status()["suche"]["funde"]], ["airscan:e0:Brother ADS-4300N", "epson2:net:192.0.2.87"])

    def test_the_direct_search_stays_within_its_seconds(self):
        self.cfg.suche_s, self.cfg.scanner = 0.3, ""   # no first-tick check of a device
        self.lan = ["192.0.2.%d" % i for i in range(1, 60)]
        scan.tcp = lambda host, port, timeout: time.sleep(1) or ""
        dienst = scan.Dienst(self.cfg)
        t = time.monotonic()
        self.auftrag(dienst, "suchen")
        self.assertLess(time.monotonic() - t, 0.9, "the search waited for the slow knocks")
        self.assertEqual((self.status()["suche"]["zustand"], self.status()["suche"]["funde"]), ("fertig", []), "late is no find")

    def test_an_idle_service_looks_again_every_15_minutes_and_says_what_it_saw(self):
        # #785: the Box page read "bereit · geprüft vor 2 Tagen": the status was written on a change only
        jetzt = [1000.0]
        dienst = scan.Dienst(self.cfg, now=lambda: jetzt[0])
        dienst.tick()
        s = self.status()
        self.assertEqual(s["detail"], "bereit · Scanner unter 192.0.2.7 an")
        os.utime(os.path.join(self.cfg.data, "laib-status.json"), (0, 0))
        alt = s["at"]
        self.zu[("192.0.2.7", 1865)] = "timed out"
        jetzt[0] += 60
        dienst.tick()
        self.assertEqual(self.status()["detail"], "bereit · Scanner unter 192.0.2.7 an", "not before 15 minutes")
        jetzt[0] += 900
        dienst.letzte = [{"stapel": "x", "ende": "fertig", "at": "2026-10-23T09:05:00Z", "dokumente": [{"titel": "a"}, {"titel": "b"}]}]
        dienst.tick()
        s = self.status()
        self.assertEqual(s["detail"], "bereit · Scanner unter 192.0.2.7 aus oder nicht im Netz, der Ordner scan-eingang geht · zuletzt am 23.10.: 2 Dokumente abgelegt")
        self.assertGreaterEqual(s["at"], alt)
        self.assertEqual(self.meldungen, [], "a look is no stack: no message")

    def test_without_a_scanner_the_look_says_only_the_folder(self):
        self.cfg.scanner = ""
        dienst = scan.Dienst(self.cfg)
        dienst.tick()
        self.assertEqual(self.status()["detail"], "bereit · nur der Ordner scan-eingang, kein Scanner eingerichtet")

    def test_a_probe_sheet_is_read_and_never_filed(self):
        self.runs(([vorne(71, BRIEF_B)], "scanimage: sane_start: Document feeder out of documents"))
        dienst = scan.Dienst(self.cfg)
        self.auftrag(dienst, "probe")
        p = self.status()["pruefung"]
        self.assertTrue(p["ok"] and p["probe"], p)
        self.assertTrue(p["detail"].startswith("Probeseite: %d Wörter gelesen" % scan.woerter(BRIEF_B)), p["detail"])
        self.assertFalse(os.path.exists(os.path.join(self.cfg.consume, "Stapel")))
        self.assertFalse(os.path.exists(os.path.join(self.cfg.data, "probe")))
        self.assertEqual(self.meldungen, [])


class Einrichtung(unittest.TestCase):
    def test_the_host_network_reaches_the_lease_on_loopback(self):
        cfg = scan.Config.from_env({"MODEL_URL": "http://host.containers.internal:11435/v1/", "MODEL_LEASE": "http://host.containers.internal:11435/api/model-lease",
                                    "NOTIFY_PERSON": "papa", "SCAN_AREA": "210x297", "SCAN_DUPLEX": "false"})
        self.assertEqual((cfg.model_url, cfg.model_lease), ("http://127.0.0.1:11435/v1", "http://127.0.0.1:11435/api/model-lease"))
        self.assertEqual((cfg.notify, cfg.area, cfg.duplex), ("papa", (210.0, 297.0), False))
        self.assertEqual(scan.Config.from_env({"NOTIFY_PERSON": "papa", "SCAN_NOTIFY": "mama"}).notify, "mama")

    def test_a_network_scanner_lands_in_its_backends_config_once(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "epson2.conf"), "w") as f:
                f.write("usb\n")
            for _ in range(2):
                scan.sane_netz("epson2:net:192.0.2.7", d)
            with open(os.path.join(d, "epson2.conf")) as f:
                self.assertEqual(f.read().count("net 192.0.2.7"), 1)

    def test_epson2_searches_the_network_once_told(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "epson2.conf"), "w") as f:
                f.write("usb\n#net autodiscovery\n")
            for _ in range(2):
                scan.sane_suche(d)
            with open(os.path.join(d, "epson2.conf")) as f:
                self.assertEqual(f.read().count("\nnet autodiscovery\n"), 1)

    def test_the_lans_neighbours_are_the_complete_entries_on_the_default_route(self):
        with tempfile.TemporaryDirectory() as d:
            schreib(os.path.join(d, "arp"), (
                "IP address       HW type     Flags       HW address            Mask     Device\n"
                "192.0.2.87       0x1         0x2         64:eb:8c:00:00:01     *        eth0\n"
                "192.0.2.1        0x1         0x2         00:00:5e:00:53:01     *        eth0\n"
                "192.0.2.99       0x1         0x0         00:00:00:00:00:00     *        eth0\n"
                "10.88.0.5        0x1         0x2         0a:58:0a:58:00:05     *        podman0\n"
                "192.0.2.87       0x1         0x2         64:eb:8c:00:00:01     *        eth0\n").encode())
            schreib(os.path.join(d, "route"), (
                "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
                "eth0\t00000000\t010200C0\t0003\t0\t0\t100\t00000000\n"
                "podman0\t0000580A\t00000000\t0001\t0\t0\t0\t0000FFFF\n").encode())
            self.assertEqual(scan.nachbarn(os.path.join(d, "arp"), os.path.join(d, "route")), ["192.0.2.87", "192.0.2.1"])
            self.assertEqual(scan.nachbarn(os.path.join(d, "fehlt"), os.path.join(d, "route")), [])

    def test_a_device_names_where_to_knock(self):
        self.assertEqual(scan.adresse("epson2:net:192.0.2.87"), ("192.0.2.87", 1865))
        self.assertEqual(scan.adresse("escl:https://192.0.2.9:443"), ("192.0.2.9", 443))
        self.assertEqual(scan.adresse("escl:http://192.0.2.9"), ("192.0.2.9", 80))
        self.assertIsNone(scan.adresse("airscan:e0:Brother ADS-4300N"))

    def test_the_scanner_reason_is_never_batch_terminated(self):
        err = "Scanned page 4. (scanner status = 5)\nscanimage: sane_start: Error during device I/O\nscanimage: Batch terminated, 5 pages scanned\n"
        self.assertEqual(scan.scanner_grund(err), "sane_start: Error during device I/O")


if __name__ == "__main__":
    unittest.main()
