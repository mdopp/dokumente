#!/usr/bin/env python3
"""Doppelt: the same document scanned twice becomes one (owner, 2026-11-08).

Paperless refuses only a byte-identical file, so a stack scanned again (the
feeder took some sheets of the first scan, not all) leaves two documents of
one letter, one or both missing pages. Doppelt finds them and makes one:

- Candidates: documents with the same date (Paperless' `created`), whose
  correspondents do not differ, both the household's (no owner). Annual
  letters of one sender share a template and differ in their dates, so they
  never pair.
- Evidence is page by page: each document's archive PDF read per page
  (pdftotext); two pages are the same page when their folded texts are 90 %
  alike and their numbers (dates, amounts, policy numbers) are the same.
  Pages with hardly any text say nothing either way.
- Every page of one is a page of the other: it is contained. The fuller one
  (the older one when both are whole) stays, takes what it lacks of the
  other's title, type, correspondent, tags, custom fields and notes (only
  what is empty; prüfen is not carried), and the other goes to Paperless'
  trash, from where it can be restored.
- Both start with the same page and each has pages the other lacks: one PDF
  with all pages in order (the one's, the other's slotted in after the page
  they follow) is uploaded with the metadata of both; once Paperless filed
  it, it is the household's, gets the notes, and the two go to the trash.
- Any other shared page: unsure. Nothing is deleted; both get `prüfen` and a
  note naming the other and why.

Dokumente's Paperless user needs `delete_document` for the trash (laib's
paperless template). Without it nothing is uploaded or trashed: the one that
would go gets `prüfen` and a note, and the run says it hangs.

Commands (Paperless' API with PAPERLESS_TOKEN as ordnen.py; the folder as
ordnen.py, record doppelt.json):

  doppelt.py --neue [--dry-run] [--max N]
      intake (dienst.py, before ordnen.py --neue): documents added since the
      record began, at most N (5) per run, each against the documents of its
      date; first the merges a run before uploaded.
  doppelt.py --alle [--dry-run] [--max N] [--hintergrund]
      once over the documents there are: every date's documents pairwise.
      --dry-run changes nothing and writes plan-doppelt.json and
      zusammenfassung-doppelt.txt, the list to look at first.

Each line „hängt {json}“ is a step that cannot do its work (dienst.py's
watch); the last line „doppelt {json}“ counts what was done."""
import argparse, difflib, hashlib, importlib.util, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request


def _lade_ordnen():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ordnen.py")
    spec = importlib.util.spec_from_file_location("dokumente_ordnen", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ordnen = _lade_ordnen()
falten, haengt, lesen, schreiben = ordnen.falten, ordnen.haengt, ordnen.lesen, ordnen.schreiben

E = os.environ
PRUEFEN = "prüfen"
AEHNLICH = 0.9      # two pages' folded texts at least this alike …
ZAHLEN = 0.8        # … and their numbers at least this much the same
WENIG = 40          # a page with fewer folded characters says nothing
PRO_LAUF = int(E.get("DOPPELT_PRO_LAUF") or 5)
KANDIDATEN = 6      # documents of one date a new one is compared with
WARTEN_S = 2 * 3600  # an uploaded merge Paperless has not filed by then is given up


def jetzt():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- pages ----

def zahlen(t):
    return set(re.findall(r"\d{2,}", t))


def gleiche_seite(a, b):
    """two pages are one page: the texts alike and the numbers the same (a letter of another year is not)"""
    if len(a) < WENIG or len(b) < WENIG:
        return False
    m = difflib.SequenceMatcher(None, a[:3000], b[:3000], autojunk=False)
    if m.real_quick_ratio() < AEHNLICH or m.quick_ratio() < AEHNLICH or m.ratio() < AEHNLICH:
        return False
    za, zb = zahlen(a), zahlen(b)
    if len(za | zb) >= 3:
        return len(za & zb) / len(za | zb) >= ZAHLEN
    return za == zb


def zuordnen(pa, pb):
    """for each page of b the page of a it is (each page of a once), or None"""
    frei, out = set(range(len(pa))), []
    for t in pb:
        j = next((j for j in sorted(frei) if gleiche_seite(pa[j], t)), None)
        if j is not None:
            frei.discard(j)
        out.append(j)
    return out


def vergleich(pa, pb):
    """two documents' folded page texts: {"art": gleich | a_in_b | b_in_a | ergaenzt | unsicher | verschieden, "folge", "grund"}"""
    zu = zuordnen(pa, pb)
    paare = [(j, i) for i, j in enumerate(zu) if j is not None]
    if not paare:
        return {"art": "verschieden"}
    in_a = {j for j, _ in paare}
    b_drin = all(zu[i] is not None for i, t in enumerate(pb) if len(t) >= WENIG)
    a_drin = all(j in in_a for j, t in enumerate(pa) if len(t) >= WENIG)
    n = len(paare)
    if a_drin and b_drin:
        return {"art": "gleich", "grund": "alle %s sind gleich" % seiten_wort(n) if n > 1 else "die eine Seite ist gleich"}
    if a_drin or b_drin:
        return {"art": "b_in_a" if b_drin else "a_in_b", "grund": "%s des kürzeren steh%s auch im längeren" % (
            "alle " + seiten_wort(n) if n > 1 else "die eine Seite", "en" if n > 1 else "t")}
    if zu[0] != 0:
        return {"art": "unsicher", "grund": "%s gleich, aber nicht dieselbe erste Seite" % seiten_wort(n)}
    f = folge(pa, pb, zu)
    if f is None:
        return {"art": "unsicher", "grund": "dieselbe erste Seite und %s gleich, jedes hat Seiten, die dem anderen fehlen, "
                                            "aber ihre Reihenfolge ist nicht klar" % seiten_wort(n)}
    return {"art": "ergaenzt", "folge": f, "grund": "dieselbe erste Seite, %s gleich, jedes hat Seiten, die dem anderen fehlen" % seiten_wort(n)}


def seiten_wort(n):
    return "%d Seite%s" % (n, "" if n == 1 else "n")


def seitenzahl(t):
    m = re.search(r"\bseite (\d{1,2})\b", t)
    return int(m.group(1)) if m else None


def folge(pa, pb, zu):
    """all pages in order, as [("a"|"b", index)]: a's, and b's pages a lacks; None when their place is not clear.
    Printed page numbers („Seite 3 von 4“) on every page decide; else a page of b goes right before the page
    that follows it in b, which is clear only when a has no page of its own in that gap"""
    teile = [("a", i) for i in range(len(pa))] + [("b", i) for i, j in enumerate(zu) if j is None]
    nummern = [seitenzahl(pa[i] if s == "a" else pb[i]) for s, i in teile]
    if None not in nummern and len(set(nummern)) == len(nummern):
        return [t for _, t in sorted(zip(nummern, teile))]
    vor = {}   # a's index a run of b's pages goes before (len(pa): at the end)
    letzte = -1
    for i, j in enumerate(zu):
        if j is not None:
            if j < letzte:
                return None   # b's shared pages in another order than a's
            letzte = j
            continue
        weiter = next((k for k in zu[i + 1:] if k is not None), len(pa))
        if weiter - letzte > 1:   # a has its own page(s) in that gap too
            return None
        vor.setdefault(weiter, []).append(("b", i))
    out = []
    for k in range(len(pa) + 1):
        out += vor.get(k, [])
        if k < len(pa):
            out.append(("a", k))
    return out


# ---- Paperless ----

class Quelle:
    """what Doppelt needs of Paperless beyond ordnen.API's JSON: a PDF's pages, an upload, a delete"""

    def __init__(self, api):
        self.api = api
        self.seiten_cache = {}   # the texts only: a run over all documents must not hold their PDFs

    def roh(self, method, path, data=None, ctype=None):
        h = {"Authorization": self.api.auth}
        if ctype:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.api.base + path, method=method, data=data, headers=h)
        with urllib.request.urlopen(r, timeout=300) as resp:
            return resp.read()

    def dokumente(self):
        return self.api.all("/api/documents/?fields=id,title,created,added,correspondent,document_type,tags,custom_fields,owner")

    def dokument(self, i):
        return self.api("GET", "/api/documents/%d/" % i)

    def pdf(self, i):
        """the archive version (it has the text), else the original"""
        try:
            return self.roh("GET", "/api/documents/%d/download/" % i)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            return self.roh("GET", "/api/documents/%d/download/?original=true" % i)

    def seiten(self, i):
        """each page's text, folded"""
        if i not in self.seiten_cache:
            try:
                self.seiten_cache[i] = [falten(t) for t in pdf_seiten(self.pdf(i))]
            except urllib.error.HTTPError:   # no file to read (403, 404): no evidence, never a pair
                self.seiten_cache[i] = []
        return self.seiten_cache[i]

    def darf_loeschen(self):
        try:
            perms = (self.api("GET", "/api/ui_settings/") or {}).get("permissions") or []
        except urllib.error.HTTPError:
            return False
        return "delete_document" in perms

    def loeschen(self, i):
        """into Paperless' trash (restorable there)"""
        self.api("DELETE", "/api/documents/%d/" % i)

    def notizen(self, i):
        try:
            res = self.api("GET", "/api/documents/%d/notes/" % i)
        except urllib.error.HTTPError:
            return []
        return [n.get("note") for n in (res if isinstance(res, list) else (res or {}).get("results") or []) if n.get("note")]

    def notiz(self, i, text):
        self.api("POST", "/api/documents/%d/notes/" % i, {"note": text})

    def aendern(self, i, body):
        self.api("PATCH", "/api/documents/%d/" % i, body)

    def pruefen_tag(self):
        t = next((t for t in self.api.all("/api/tags/") if falten(t["name"]) == falten(PRUEFEN)), None)
        if t is None:
            t = self.api("POST", "/api/tags/", {"name": PRUEFEN, "matching_algorithm": 0, "owner": None})
        return t["id"]

    def hochladen(self, inhalt, titel, felder):
        grenze = "doppelt-%d" % time.monotonic_ns()
        teile = [("document", ("%s.pdf" % titel.replace("/", "-")[:100], inhalt)), ("title", titel)]
        for k in ("correspondent", "document_type"):
            if felder.get(k) is not None:
                teile.append((k, str(felder[k])))
        if felder.get("created"):
            teile.append(("created", str(felder["created"])[:10]))
        teile += [("tags", str(t)) for t in felder.get("tags") or []]
        out = b""
        for name, wert in teile:
            out += b"--" + grenze.encode() + b"\r\n"
            if isinstance(wert, tuple):
                out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: application/pdf\r\n\r\n'
                        % (name, wert[0].replace('"', "'"))).encode() + wert[1]
            else:
                out += ('Content-Disposition: form-data; name="%s"\r\n\r\n%s' % (name, wert)).encode()
            out += b"\r\n"
        out += b"--" + grenze.encode() + b"--\r\n"
        self.roh("POST", "/api/documents/post_document/", out, "multipart/form-data; boundary=" + grenze)

    def mit_pruefsumme(self, summen):
        """Paperless 3 keeps the original's SHA-256, Paperless 2 its MD5 (as scan.py asks)"""
        for k in ("sha256", "md5"):
            res = self.api("GET", "/api/documents/?checksum__iexact=%s&fields=id" % summen[k]) or {}
            ids = [d["id"] for d in res.get("results") or []]
            if ids:
                return ids
        return []


def pdf_seiten(inhalt):
    """a PDF's text per page (poppler's pdftotext ends each page with a form feed)"""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(inhalt)
        f.flush()
        r = subprocess.run(["pdftotext", "-q", f.name, "-"], stdout=subprocess.PIPE, timeout=120)
    seiten = r.stdout.decode("utf-8", "replace").split("\f")
    return seiten[:-1] if seiten and not seiten[-1].strip() else seiten


def pdf_bauen(teile):
    """one PDF of [(pdf bytes, page index)] in that order (qpdf)"""
    with tempfile.TemporaryDirectory() as d:
        namen, args = {}, []
        for inhalt, seite in teile:
            k = id(inhalt)
            if k not in namen:
                namen[k] = os.path.join(d, "%d.pdf" % len(namen))
                with open(namen[k], "wb") as f:
                    f.write(inhalt)
            args += [namen[k], str(seite + 1)]
        ziel = os.path.join(d, "neu.pdf")
        subprocess.run(["qpdf", "--warning-exit-0", "--empty", "--pages"] + args + ["--", ziel], check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with open(ziel, "rb") as f:
            return f.read()


def summen(inhalt):
    return {"md5": hashlib.md5(inhalt).hexdigest(), "sha256": hashlib.sha256(inhalt).hexdigest()}


# ---- what goes where ----

def tag_von(d):
    return str(d.get("created") or "")[:10]


def paar_moeglich(a, b):
    """the same date, no two different correspondents, both the household's"""
    return (a["id"] != b["id"] and tag_von(a) and tag_von(a) == tag_von(b) and a.get("owner") is None and b.get("owner") is None
            and not (a.get("correspondent") and b.get("correspondent") and a["correspondent"] != b["correspondent"]))


def leer(v):
    return v in (None, "", [])


def uebernehmen(behalten, weg, pruefen_id=None):
    """what the one kept lacks of the other: a PATCH body (only empty things filled; prüfen not carried) and their names"""
    body, namen = {}, []
    if falten(behalten.get("title")) in ("", "dokument") and falten(weg.get("title")) not in ("", "dokument"):
        body["title"] = weg["title"]
        namen.append("Titel")
    for k, name in (("document_type", "Art"), ("correspondent", "Absender")):
        if leer(behalten.get(k)) and not leer(weg.get(k)):
            body[k] = weg[k]
            namen.append(name)
    neu = [t for t in weg.get("tags") or [] if t not in (behalten.get("tags") or []) and t != pruefen_id]
    if neu:
        body["tags"] = list(behalten.get("tags") or []) + neu
        namen.append("Tags")
    have = {c["field"]: c.get("value") for c in behalten.get("custom_fields") or []}
    dazu = {c["field"]: c.get("value") for c in weg.get("custom_fields") or [] if leer(have.get(c["field"])) and not leer(c.get("value"))}
    if dazu:
        body["custom_fields"] = [{"field": f, "value": v} for f, v in {**have, **dazu}.items()]
        namen.append("Felder")
    return body, namen


def beschreibung(d, n):
    return "#%d „%s“ (%s)" % (d["id"], d.get("title") or "", seiten_wort(n))


class Doppelt:
    def __init__(self, q, ordner, dry=False, log=print):
        self.q, self.dry, self.log = q, dry, log
        self.pfad = os.path.join(ordner, "doppelt.json")
        self.rec = lesen(self.pfad, {})
        self.weg = set()        # trashed or about to be, this run
        self.plan = []          # what was (or in a dry run would be) done
        self.zaehler = {"zusammengefuehrt": 0, "pruefen": 0}
        self._loeschen = None
        self._pruefen = None

    def speichern(self):
        if not self.dry:
            schreiben(self.pfad, self.rec)

    def darf_loeschen(self):
        if self._loeschen is None:
            self._loeschen = self.q.darf_loeschen()
        return self._loeschen

    def pruefen_id(self):
        if self._pruefen is None and not self.dry:
            self._pruefen = self.q.pruefen_tag()
        return self._pruefen

    def markieren(self, d, text):
        """prüfen and a note: someone should look"""
        if self.dry:
            return
        t = self.pruefen_id()
        try:
            frisch = self.q.dokument(d["id"])
        except urllib.error.HTTPError as e:
            if e.code == 404:   # gone meanwhile (someone deleted it)
                return
            raise
        if t not in (frisch.get("tags") or []):
            self.q.aendern(d["id"], {"tags": list(frisch.get("tags") or []) + [t]})
        self.q.notiz(d["id"], text)

    def paar(self, a, b):
        """two candidates compared page by page and acted on; True when one or both went"""
        if a["id"] in self.weg or b["id"] in self.weg:
            return False
        key = "%d-%d" % tuple(sorted((a["id"], b["id"])))
        if key in self.rec.setdefault("paare", {}):
            return False
        pa, pb = self.q.seiten(a["id"]), self.q.seiten(b["id"])
        v = vergleich(pa, pb)
        if v["art"] == "verschieden":
            return False
        if v["art"] in ("gleich", "a_in_b", "b_in_a"):
            if v["art"] == "gleich":   # the one with more pages (a near-blank one), else the older
                behalten, weg = (a, b) if (len(pa), -a["id"]) >= (len(pb), -b["id"]) else (b, a)
            else:
                behalten, weg = (b, a) if v["art"] == "a_in_b" else (a, b)
            n_b, n_w = (len(pa), len(pb)) if behalten is a else (len(pb), len(pa))
            return self.enthalten(key, behalten, weg, n_b, n_w, v["grund"])
        if v["art"] == "ergaenzt":
            return self.ergaenzen(key, a, b, pa, pb, v)
        self.rec["paare"][key] = {"art": "unsicher", "am": jetzt()}
        self.zaehler["pruefen"] += 1
        self.plan.append({"art": "unsicher", "dokumente": [a["id"], b["id"]], "grund": v["grund"]})
        self.log("doppelt: #%d und #%d vielleicht dasselbe (%s), zum Prüfen markiert" % (a["id"], b["id"], v["grund"]))
        for x, y in ((a, b), (b, a)):
            self.markieren(x, "Vielleicht doppelt gescannt (Dokumente): %s hat dasselbe Datum, %s. Nichts wurde gelöscht; "
                              "bitte ansehen und, wenn es dasselbe ist, das unvollständige löschen."
                           % (beschreibung(y, len(pb if y is b else pa)), v["grund"]))
        self.speichern()
        return False

    def enthalten(self, key, behalten, weg, n_b, n_w, grund):
        body, namen = uebernehmen(self.q.dokument(behalten["id"]) if not self.dry else behalten,
                                  self.q.dokument(weg["id"]) if not self.dry else weg, self.pruefen_id())
        eintrag = {"art": "enthalten", "behalten": behalten["id"], "weg": [weg["id"]], "seiten": [n_b, n_w], "grund": grund,
                   "uebernommen": namen, "am": jetzt()}
        if not self.darf_loeschen():
            return self.ohne_loeschen(key, eintrag, weg, behalten, n_b)
        self.plan.append(eintrag)
        self.weg.add(weg["id"])
        self.log("doppelt: %s steckt ganz in %s: %s%s" % (beschreibung(weg, n_w), beschreibung(behalten, n_b),
                 "würde in den Papierkorb gelegt" if self.dry else "in den Papierkorb gelegt",
                 ", übernommen: " + ", ".join(namen) if namen else ""))
        if self.dry:
            return True
        if body:
            self.q.aendern(behalten["id"], body)
        for n in self.q.notizen(weg["id"]):
            self.q.notiz(behalten["id"], "Aus #%d übernommen: %s" % (weg["id"], n))
        self.q.notiz(behalten["id"], "Doppelt gescannt (Dokumente, %s): %s stand ganz in diesem und liegt im Papierkorb von "
                                     "Paperless, von dort lässt es sich wiederherstellen.%s"
                     % (jetzt()[:10], beschreibung(weg, n_w), " Übernommen: %s." % ", ".join(namen) if namen else ""))
        self.q.loeschen(weg["id"])
        self.rec["paare"][key] = eintrag
        self.rec.setdefault("erledigt", []).append(eintrag)
        self.zaehler["zusammengefuehrt"] += 1
        self.speichern()
        return True

    def ohne_loeschen(self, key, eintrag, weg, behalten, n_b):
        haengt("loeschen", "Doppelt gescannte Dokumente bleiben liegen: Dokumente darf in Paperless nichts in den Papierkorb legen",
               "Laib muss dem Paperless-Benutzer dokumente das Recht zum Löschen geben (Vorlage paperless); bis dahin sind die "
               "Doppelten mit prüfen markiert und lassen sich von Hand löschen.")
        self.plan.append({**eintrag, "art": "enthalten, nicht gelöscht (kein Recht)"})
        self.rec["paare"][key] = {**eintrag, "art": "kein Recht"}
        self.zaehler["pruefen"] += 1
        self.log("doppelt: #%d steckt ganz in #%d, Dokumente darf es nicht löschen: zum Prüfen markiert" % (weg["id"], behalten["id"]))
        self.markieren(weg, "Doppelt gescannt (Dokumente): alle Seiten stehen auch in %s. Dokumente darf nicht löschen; bitte dieses "
                            "löschen, wenn es stimmt." % beschreibung(behalten, n_b))
        self.speichern()
        return False

    def ergaenzen(self, key, a, b, pa, pb, v):
        if len(pb) > len(pa) or (len(pb) == len(pa) and b["id"] < a["id"]):   # the fuller (else older) one is the base
            a, b, pa, pb = b, a, pb, pa
            v = vergleich(pa, pb)
            if v["art"] != "ergaenzt":
                return False
        reihe = "".join("%s%d " % ("A" if s == "a" else "B", i + 1) for s, i in v["folge"]).strip()
        eintrag = {"art": "zusammengesetzt", "aus": [a["id"], b["id"]], "seiten": [len(pa), len(pb), len(v["folge"])],
                   "reihe": reihe, "grund": v["grund"], "am": jetzt()}
        if not self.darf_loeschen():
            return self.ohne_loeschen(key, eintrag, b, a, len(pa))
        self.plan.append(eintrag)
        self.weg |= {a["id"], b["id"]}
        self.log("doppelt: %s und %s ergänzen sich: %s ein Dokument mit %d Seiten (A=#%d, B=#%d: %s)" % (
            beschreibung(a, len(pa)), beschreibung(b, len(pb)), "würden" if self.dry else "werden", len(v["folge"]), a["id"], b["id"], reihe))
        if self.dry:
            return True
        da, db = self.q.dokument(a["id"]), self.q.dokument(b["id"])
        body, namen = uebernehmen(da, db, self.pruefen_id())
        meta = {**{k: da.get(k) for k in ("title", "correspondent", "document_type", "created", "tags", "custom_fields")}, **body}
        meta["tags"] = [t for t in meta.get("tags") or [] if t != self._pruefen]
        meta["custom_fields"] = [{"field": c["field"], "value": c.get("value")} for c in meta.get("custom_fields") or []]
        pdfs = {"a": self.q.pdf(a["id"]), "b": self.q.pdf(b["id"])}
        inhalt = pdf_bauen([(pdfs[s], i) for s, i in v["folge"]])
        self.q.hochladen(inhalt, meta.get("title") or "Dokument", meta)
        self.rec.setdefault("offen", []).append({**eintrag, "summen": summen(inhalt), "meta": meta, "uebernommen": namen, "tag": tag_von(a),
                                                 "seit": time.time()})
        self.rec["paare"][key] = eintrag
        self.speichern()
        return True

    def offene(self):
        """the merges a run before uploaded: once Paperless filed one, it is the household's with both notes, the two go"""
        bleiben = []
        for o in self.rec.get("offen") or []:
            self.weg |= set(o["aus"])
            ids = self.q.mit_pruefsumme(o["summen"])
            if not ids:
                if time.time() - o.get("seit", 0) < WARTEN_S:
                    bleiben.append(o)
                    continue
                self.log("doppelt: das zusammengesetzte Dokument aus #%d und #%d kam nicht an; beide bleiben" % tuple(o["aus"]))
                for i in o["aus"]:
                    self.markieren({"id": i}, "Doppelt gescannt (Dokumente): ein Dokument aus #%d und #%d mit allen Seiten "
                                              "kam in Paperless nicht an; beide bleiben, bitte ansehen." % tuple(o["aus"]))
                self.weg -= set(o["aus"])
                continue
            neu = ids[0]   # not seen: it is compared as a new one, so a third scan of the same letter finds it
            body = {"owner": None, "tags": o["meta"].get("tags") or []}
            if o["meta"].get("custom_fields"):
                body["custom_fields"] = o["meta"]["custom_fields"]
            self.q.aendern(neu, body)
            for i in o["aus"]:
                for n in self.q.notizen(i):
                    self.q.notiz(neu, "Aus #%d übernommen: %s" % (i, n))
            self.q.notiz(neu, "Zusammengesetzt (Dokumente, %s) aus #%d (%d Seiten) und #%d (%d Seiten), %s; Seiten: %s. Beide liegen im "
                              "Papierkorb von Paperless und lassen sich von dort wiederherstellen."
                         % (jetzt()[:10], o["aus"][0], o["seiten"][0], o["aus"][1], o["seiten"][1], o["grund"], o["reihe"]))
            for i in o["aus"]:
                try:
                    self.q.loeschen(i)
                except urllib.error.HTTPError as e:
                    if e.code != 404:   # already in the trash
                        raise
            self.rec.setdefault("erledigt", []).append({**{k: v for k, v in o.items() if k not in ("summen", "meta", "seit")}, "neu": neu})
            self.zaehler["zusammengefuehrt"] += 1
            self.log("doppelt: #%d und #%d sind jetzt #%d" % (o["aus"][0], o["aus"][1], neu))
        self.rec["offen"] = bleiben
        self.speichern()

    def neue(self, maximal):
        """intake: documents added since the record began, each against the documents of its date"""
        if not self.dry:
            self.offene()
        docs = self.q.dokumente()
        hoechste = max((d["id"] for d in docs), default=0)
        if "ab" not in self.rec:   # the documents there were before are --alle's, after the owner saw its list
            self.rec["ab"] = hoechste
            self.speichern()
            return
        gesehen = self.rec.setdefault("gesehen", {})
        neue = sorted((d for d in docs if d["id"] > self.rec["ab"] and str(d["id"]) not in gesehen and d["id"] not in self.weg),
                      key=lambda d: d["id"])[:maximal]
        warten = {o.get("tag") for o in self.rec.get("offen") or []}
        for d in neue:
            if tag_von(d) in warten:   # perhaps the merge itself, filed meanwhile: after it is done
                continue
            andere = sorted((x for x in docs if paar_moeglich(d, x)), key=lambda x: -x["id"])[:KANDIDATEN]
            for x in andere:
                if self.paar(d, x) and d["id"] in self.weg:
                    break
            if not self.dry:
                gesehen[str(d["id"])] = jetzt()
                self.speichern()

    def alle(self, maximal=None):
        """once: every date's documents pairwise; the contained ones first, so a letter scanned three times ends as one"""
        if not self.dry:
            self.offene()
        docs = sorted(self.q.dokumente(), key=lambda d: d["id"])
        if "ab" not in self.rec:   # intake goes on after these
            self.rec["ab"] = max((d["id"] for d in docs), default=0)
            self.speichern()
        tage = {}
        for d in docs:
            if tag_von(d) and d.get("owner") is None:
                tage.setdefault(tag_von(d), []).append(d)
        paare = [(a, b) for gruppe in tage.values() for i, a in enumerate(gruppe) for b in gruppe[i + 1:] if paar_moeglich(a, b)]
        self.log("doppelt: %d Dokumente, %d Paare mit demselben Datum" % (len(docs), len(paare)))
        rang = {"gleich": 0, "a_in_b": 0, "b_in_a": 0, "ergaenzt": 1, "unsicher": 2}
        bewertet = []
        for a, b in paare:
            art = vergleich(self.q.seiten(a["id"]), self.q.seiten(b["id"]))["art"]
            if art in rang:
                bewertet.append((rang[art], a["id"], b["id"], a, b))
        n = 0
        for _, _, _, a, b in sorted(bewertet, key=lambda x: x[:3]):
            if maximal is not None and n >= maximal:
                self.log("doppelt: Menge erreicht, der Rest beim nächsten Mal")
                break
            n += bool(self.paar(a, b))

    def zusammenfassung(self):
        zeilen = ["Doppelt gescannt: %d Fälle" % len(self.plan), ""]
        for p in self.plan:
            if p["art"].startswith("enthalten"):
                zeilen.append("  #%d (%d S.) steckt ganz in #%d (%d S.) → #%d in den Papierkorb%s%s" % (
                    p["weg"][0], p["seiten"][1], p["behalten"], p["seiten"][0], p["weg"][0],
                    "; übernommen: " + ", ".join(p["uebernommen"]) if p.get("uebernommen") else "",
                    "" if p["art"] == "enthalten" else " (Dokumente darf nicht löschen: nur markiert)"))
            elif p["art"] == "zusammengesetzt":
                zeilen.append("  #%d (%d S.) + #%d (%d S.) → ein neues Dokument mit %d Seiten (%s), beide in den Papierkorb" % (
                    p["aus"][0], p["seiten"][0], p["aus"][1], p["seiten"][1], p["seiten"][2], p["reihe"]))
            else:
                zeilen.append("  #%d und #%d: unsicher (%s) → beide prüfen" % (p["dokumente"][0], p["dokumente"][1], p["grund"]))
        return "\n".join(zeilen)


def sperre(ordner):
    """one Doppelt at a time (the intake and a run over all); a lock whose process is gone is taken over"""
    p = os.path.join(ordner, "doppelt.lock")
    try:
        pid = int(open(p).read().strip() or 0)
        if pid and pid != os.getpid():
            os.kill(pid, 0)
            return False
    except (OSError, ValueError):
        pass
    with open(p, "w") as f:
        f.write(str(os.getpid()))
    return True


def api_aus_env():
    """dienst.py hands PAPERLESS_API; inside the container (a run by hand) it is PAPERLESS_URL"""
    return ordnen.API(E.get("PAPERLESS_API") or E.get("PAPERLESS_URL") or "http://127.0.0.1:8000", E.get("PAPERLESS_TOKEN", ""),
                      E.get("PAPERLESS_ADMIN_USER", "admin"), E.get("PAPERLESS_ADMIN_PASSWORD", ""))


def main(argv=None, quelle=None):
    ap = argparse.ArgumentParser(prog="doppelt")
    wie = ap.add_mutually_exclusive_group(required=True)
    wie.add_argument("--neue", action="store_true")
    wie.add_argument("--alle", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--hintergrund", action="store_true")
    args = ap.parse_args(argv)
    ordner = ordnen.ordner_waehlen()
    if args.hintergrund:
        ordnen.hintergrund(ordner)
    if not sperre(ordner):
        print("doppelt: ein Lauf ist schon im Gang")
        return 0
    d = None
    try:
        print("doppelt: Start %s%s" % (jetzt(), " (Probelauf)" if args.dry_run else ""), flush=True)
        d = Doppelt(quelle or Quelle(api_aus_env()), ordner, dry=args.dry_run)
        if args.neue:
            d.neue(args.max or PRO_LAUF)
        else:
            d.alle(args.max)
            text = d.zusammenfassung()
            schreiben(os.path.join(ordner, "plan-doppelt.json"), {"at": jetzt(), "dry_run": args.dry_run, "faelle": d.plan})
            with open(os.path.join(ordner, "zusammenfassung-doppelt.txt"), "w", encoding="utf-8") as f:
                f.write(("PROBELAUF – nichts geändert\n" if args.dry_run else "Angewendet\n") + text + "\n")
            print(text, flush=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as e:   # Paperless away: said, the next run tries again
        print("doppelt: abgebrochen: %s" % e, flush=True)
        if args.neue:
            if getattr(e, "code", None) in (401, 403):   # Paperless away is ordnen's to say, once
                ordnen.zugang_haengt(e)
        return 0 if args.neue else 2
    finally:
        if d is not None:
            print("doppelt " + json.dumps(d.zaehler), flush=True)
        try:
            os.remove(os.path.join(ordner, "doppelt.lock"))
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
