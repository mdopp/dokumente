#!/usr/bin/env python3
"""Kontakte nachlesen (owner 2026-11-09): a household firm card without a phone, e-mail or address gets them
from its documents.

A card made on an older path („Friedr. Wilh. Wienkämper GmbH & Co. KG“: only the name and an IBAN) is never
filled by vertraege.py, which asks a document's sender once and only for contracts and invoices. This reads
the cards' documents again, once, and again whenever their documents change.

  kontakte_nachlesen.py                 the dry run: asks the model, says per firm what it would take, writes no card
  kontakte_nachlesen.py --apply         writes what it found into the cards
  --firma "<Teil des Namens>"           (repeatable) only the cards whose name has these words
  --max N                               at most N firms a run (0: all, the default)

What it does:
- reads the firm cards (category Firma) through Homes' Haushalt tool firmenkontakte and takes those missing a
  phone, an e-mail or an address. A card edited by hand (von_hand: a phone or a person changed it) is never
  touched: listed, not read.
- finds each card's documents in Paperless with Dokumente's token (PAPERLESS_API or PAPERLESS_URL,
  PAPERLESS_TOKEN): by correspondent, a correspondent being the card's firm by firmen.py's rules (legal form
  and ALIASE folded, OCR's misreading of one word), else a phrase search of the name without its legal form;
  newest first, at most DOKUMENTE (3).
- asks the household model per document with vertraege.py's KONTAKT question and reads the answer with its
  kontaktdaten(): only values the text backs, never the household's own (Paperless' users, HAUSHALT_NAMEN).
  A document whose sender is another firm gives nothing. The answers are kept by the text's hash, so --apply
  after a dry run asks again only for a text that changed.
- first the owner's own phone contacts (Homes' handy_firmen: the firms in the Immich key owner's
  /<login>/handy-kontakte/, nobody else's): when exactly one is the card's firm by firmen.py's rules (FN or ORG),
  its first number, mail and whole address go into the card's empty fields and win over a document's text;
  several: none is taken, the line names them. A Homes without the tool: the documents only.
- writes through kontakt_ergaenzen only what the card lacks (phone, e-mail, address, contact person, an IBAN
  with its bank and holder): Homes fills only what is empty and never changes a value.
- says per firm in German what it took and from where („Telefon … übernommen (aus deinen
  Handy-Kontakten); Anschrift … übernommen (aus Dokument #123)“) or „nichts im
  Text gefunden“, and records it in /dokumente/ordnen/nachlesen.json (NACHLESEN_FILE): a rerun skips a firm
  already read (with --apply) whose documents are the same, by id and text.

Env: HAUSHALT_URL, HAUSHALT_TOKEN, PAPERLESS_TOKEN, and the model as the scan has it (MODEL_URL, MODEL_KEY,
MODEL_NAME; or PAPERLESS_AI_LLM_*); the scan container has them all. Stdlib only; firmen.py,
kontakte_aufraeumen.py and vertraege.py beside it (the image's /opt) or in the repo."""
import argparse, hashlib, importlib.util, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

E = os.environ
HIER = os.path.dirname(os.path.abspath(__file__))
DOKUMENTE = 3      # documents read per firm, the newest first: the household model is shared


def _lade(name, *orte):
    pfad = next(p for p in (os.path.join(HIER, *o) for o in orte) if os.path.exists(p))
    spec = importlib.util.spec_from_file_location("nachlesen_" + name, pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


firmen = _lade("firmen", ("firmen.py",))
aufraeumen = _lade("aufraeumen", ("kontakte_aufraeumen.py",))
vertraege = _lade("vertraege", ("vertraege.py",), (os.pardir, "vertraege", "vertraege.py"))
WORT = {"telefon": "Telefon", "email": "E-Mail", "anschrift": "Anschrift", "ansprechpartner": "Ansprechpartner", "iban": "IBAN"}
LUECKEN = ("telefon", "email", "anschrift")


class Paperless:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def get(self, path):
        r = urllib.request.Request(self.base + path, headers={"Authorization": "Token " + self.token, "Accept": "application/json"})
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read())

    def alle(self, path):
        out, page = [], path + ("&" if "?" in path else "?") + "page_size=100"
        while page:
            res = self.get(page)
            out += res.get("results", [])
            page = res.get("next") and res["next"][res["next"].index("/api/"):]
        return out

    def korrespondenten(self):
        return [{"id": c["id"], "name": c["name"]} for c in self.alle("/api/correspondents/")]

    def dokumente(self, korrespondenten=(), phrase=None, n=DOKUMENTE):
        """the newest n documents of these correspondents, else of the phrase search"""
        if korrespondenten:
            res = self.get("/api/documents/?correspondent__id__in=%s&ordering=-created&page_size=%d" % (
                ",".join(map(str, korrespondenten)), n))
        else:
            res = self.get("/api/documents/?query=%s&page_size=25" % urllib.parse.quote('"%s"' % phrase))
        docs = res.get("results") or []
        return sorted(docs, key=lambda d: (str(d.get("created") or ""), d["id"]), reverse=True)[:n]

    def benutzer(self):
        return self.alle("/api/users/")


def luecken(k):
    """what of phone, e-mail, address a card lacks"""
    w = aufraeumen.werte(k)
    return [f for f in LUECKEN if not w[f]]


def suchname(name):
    """a firm's name for the phrase search: its words without the legal form at the end („… GmbH & Co. KG“)"""
    w = str(name).split()
    while len(w) > 1:
        f = [firmen.falten(x) for x in w]
        if f[-1] in ("", "u", "und"):
            w = w[:-1]
            continue
        n = next((len(r) for r in firmen.RECHTSFORMEN if len(r) < len(w) and " ".join(f[-len(r):]) == " ".join(r)), 0)
        if not n:
            break
        w = w[:-n]
    return " ".join(w)


def korrespondenten_von(karte, korr):
    """the correspondents that are the card's firm (firmen's gleich or ocr)"""
    return [c["id"] for c in korr if firmen.gleich(karte["name"], c["name"]) in ("gleich", "ocr")
            or firmen.gleich(c["name"], karte["name"]) in ("gleich", "ocr")]


def sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def anschrift_text(a):
    return "%s, %s %s" % (a.get("strasse", ""), a.get("plz", ""), a.get("ort", ""))


def zeigen(f, v):
    return "%s %s" % (WORT[f], anschrift_text(v) if f == "anschrift" else v)


def fehlende_args(args, stand):
    """of the document's values (kontaktdaten) those the card lacks, as kontakt_ergaenzen's arguments"""
    out = {}
    for f in ("telefon", "email", "anschrift", "iban"):
        if args.get(f) and not stand[f]:
            out[f] = args[f]
    if "iban" in out:
        out.update({f: args[f] for f in ("bank", "kontoinhaber") if args.get(f)})
    if args.get("ansprechpartner") and not stand["ansprechpartner"]:
        out["ansprechpartner"] = args["ansprechpartner"]
    return out


HANDY = "handy"   # the source of a value from the owner's phone contacts, beside a document's id


def handy_treffer(karte, handy):
    """the owner's phone contacts that are the card's firm by FN or ORG: firmen.py's rules (legal form and ALIASE
    folded, OCR's misreading, the shorter name for the fuller one), else, none matching so, all of one name's words
    in the other's („Wienkämper“ for „Friedr. Wilh. Wienkämper GmbH & Co. KG“, firmen.derselbe)"""
    def namen(h):
        return [n for n in {h.get("name"), h.get("org")} if n]
    out = [h for h in handy if any(firmen.gleich(n, karte["name"]) or firmen.gleich(karte["name"], n) for n in namen(h))]
    return out or [h for h in handy if any(firmen.derselbe(n, karte["name"]) for n in namen(h))]


def handy_args(h):
    """a phone contact's values as kontaktdaten gives a document's: the first number, mail, whole address"""
    adr = [a for a in h.get("anschrift") or [] if a.get("strasse") and a.get("plz") and a.get("ort")]
    out = {"telefon": (h.get("telefon") or [""])[0], "email": (h.get("email") or [""])[0]}
    if adr:
        out["anschrift"] = {k: adr[0][k] for k in ("strasse", "plz", "ort", "land") if adr[0].get(k)}
    return {k: v for k, v in out.items() if v}


def schreiben(karte, args, stand, quelle, haushalt, apply, log):
    """of args what the card lacks into it through kontakt_ergaenzen (the dry run: as if); [(field, value, quelle)]
    taken, stand updated; None when Homes took nothing"""
    neu = fehlende_args(args, stand)
    if not neu:
        return []
    neu = {"firma": karte["name"], **neu}
    if apply:
        text_h, res = haushalt("kontakt_ergaenzen", neu)
        if res.get("status") not in ("ergaenzt", "angelegt", "unveraendert"):
            log("„%s“: Homes nahm nichts (%s)" % (karte["name"], text_h.split("\n")[0]))
            return None
        felder = res.get("felder") or {}
        taken = [f for f in neu if f != "firma" and felder.get(f) in ("angelegt", "ergaenzt")]
    else:
        taken = [f for f in neu if f != "firma"]
    out = []
    for f in taken:
        if f not in ("bank", "kontoinhaber"):
            stand[f] = [neu[f]] if f in ("telefon", "email", "iban", "anschrift") else neu[f]
            out.append((f, neu[f], quelle))
    return out


def nachlesen(karte, docs, frag, haushalt, personen, mails, namen, antworten, apply, log, handy=None):
    """one card: first its one phone contact (handy, the owner's; it wins over a document's text), then its
    documents, newest first, until it has phone, e-mail and address: [(field, value, HANDY or doc id)] taken (or,
    in the dry run, that would be), and the senders that were another firm"""
    stand = aufraeumen.werte(karte)
    stand["anschrift"] = list(stand["anschrift"])
    genommen, fremd = [], []
    if handy:
        g = schreiben(karte, handy_args(handy), stand, HANDY, haushalt, apply, log)
        if g is None:
            return genommen, fremd
        genommen += g
    for d in docs:
        if all(stand[f] for f in LUECKEN):
            break
        text = d.get("content") or ""
        if not text.strip():
            continue
        e = antworten.get(str(d["id"]))
        if e and e.get("text") == sha(text):
            antwort = e["antwort"]
        else:
            antwort = frag(vertraege.frage(text, dict(vertraege.KONTAKT), "den Absender"))
            antwort = antwort if isinstance(antwort, dict) else {}
            antworten[str(d["id"])] = {"text": sha(text), "antwort": antwort, "am": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        args, _ = vertraege.kontaktdaten(antwort, text, personen, mails, namen)
        if not args:
            continue
        if not firmen.derselbe(args["firma"], karte["name"]):
            fremd.append((args["firma"], d["id"]))
            continue
        g = schreiben(karte, args, stand, d["id"], haushalt, apply, log)
        if g is None:
            break
        genommen += g
    return genommen, fremd


def herkunft(quelle):
    return "aus deinen Handy-Kontakten" if quelle == HANDY else "aus Dokument #%d" % quelle


def satz(name, genommen, fremd, docs, apply, mehrdeutig=()):
    """the German line for one firm"""
    hinweis = "; passt zu mehreren Handy-Kontakten (%s): von dort nichts übernommen" % ", ".join(
        "„%s“" % m for m in mehrdeutig) if mehrdeutig else ""
    if not genommen:
        if not docs:
            return "%s: keine Dokumente in Paperless gefunden%s" % (name, hinweis)
        z = "%s: nichts im Text gefunden (%s)" % (name, ", ".join("Dokument #%d" % d["id"] for d in docs))
        if fremd:
            z += "; Absender dort: %s" % ", ".join("„%s“ (#%d)" % f for f in fremd)
        return z + hinweis
    teile = []
    for q in dict.fromkeys(i for _, _, i in genommen):
        werte = ", ".join(zeigen(f, v) for f, v, i in genommen if i == q)
        teile.append("%s %s (%s)" % (werte, "übernommen" if apply else "würde übernommen", herkunft(q)))
    return "%s: %s%s" % (name, "; ".join(teile), hinweis)


def laden(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        rec = {}
    rec.setdefault("firmen", {})
    rec.setdefault("antworten", {})
    return rec


def speichern(pfad, rec):
    os.makedirs(os.path.dirname(os.path.abspath(pfad)), exist_ok=True)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, pfad)


def main(argv=None, haushalt=None, paperless=None, frag=None, log=print, datei=None):
    ap = argparse.ArgumentParser(description="Fehlende Telefonnummern, E-Mails und Anschriften der Firmenkontakte aus ihren "
                                             "Dokumenten nachlesen (Probelauf ohne --apply)")
    ap.add_argument("--apply", action="store_true", help="in die Kontakte schreiben, sonst nur sagen, was gefunden wurde")
    ap.add_argument("--firma", action="append", default=[], metavar="NAME", help="nur Firmen, deren Name diese Wörter hat")
    ap.add_argument("--max", type=int, default=0, metavar="N", help="höchstens N Firmen in diesem Lauf (0: alle)")
    args = ap.parse_args(argv)
    if haushalt is None:
        if not (E.get("HAUSHALT_URL") and E.get("HAUSHALT_TOKEN")):
            log("kontakte_nachlesen: HAUSHALT_URL und HAUSHALT_TOKEN fehlen (im Scan-Container gesetzt)")
            return 2
        haushalt = aufraeumen.Haushalt(E["HAUSHALT_URL"], E["HAUSHALT_TOKEN"])
    if paperless is None:
        if not E.get("PAPERLESS_TOKEN"):
            log("kontakte_nachlesen: PAPERLESS_TOKEN fehlt (im Scan-Container gesetzt)")
            return 2
        paperless = Paperless(E.get("PAPERLESS_API") or E.get("PAPERLESS_URL") or "http://127.0.0.1:8000", E["PAPERLESS_TOKEN"])
    if frag is None:
        # vertraege.modell reads Paperless' names; in Dokumente's container the scan's MODEL_* are the same model
        for ziel, quelle in (("PAPERLESS_AI_LLM_ENDPOINT", "MODEL_URL"), ("PAPERLESS_AI_LLM_API_KEY", "MODEL_KEY"),
                             ("PAPERLESS_AI_LLM_MODEL", "MODEL_NAME")):
            if not E.get(ziel) and E.get(quelle):
                E[ziel] = E[quelle]
        if not E.get("PAPERLESS_AI_LLM_ENDPOINT"):
            log("kontakte_nachlesen: MODEL_URL fehlt: ohne das Haushaltsmodell liest niemand die Dokumente")
            return 2
        frag = vertraege.modell
    datei = datei or E.get("NACHLESEN_FILE") or "/dokumente/ordnen/nachlesen.json"
    rec = laden(datei)
    try:
        _, res = haushalt("firmenkontakte", {})
    except (OSError, RuntimeError) as x:
        log("kontakte_nachlesen: Homes antwortet nicht: %s" % x)
        return 1
    if res.get("teilweise"):
        log("kontakte_nachlesen: Homes las nur einen Teil des Adressbuchs; nichts gelesen")
        return 1
    karten = [k for k in res.get("kontakte") or [] if luecken(k)]
    if args.firma:
        such = [set(firmen.falten(f).split()) for f in args.firma]
        karten = [k for k in karten if any(s <= set(firmen.falten(k["name"]).split()) for s in such)]
    try:
        korr = paperless.korrespondenten()
        try:
            users = paperless.benutzer()
        except urllib.error.HTTPError:
            users = []   # Dokumente's user may not list them: the household's names from HAUSHALT_NAMEN only
    except (OSError, ValueError) as x:
        log("kontakte_nachlesen: Paperless antwortet nicht: %s" % x)
        return 1
    personen = vertraege.haushalt(users, E.get("HAUSHALT_NAMEN", ""), E.get("PAPERLESS_USER", "dokumente"))
    mails = {str(u["email"]).casefold() for u in users if u.get("email")}
    namen = [c["name"] for c in korr]
    try:   # the owner's own phone contacts (Homes knows whose: the Immich key's owner), only their firms
        handy = haushalt("handy_firmen", {})[1].get("kontakte") or []
    except (OSError, RuntimeError) as x:
        handy = []
        log("kontakte_nachlesen: deine Handy-Kontakte nicht gelesen (%s): nur die Dokumente" % str(x).split("\n")[0])
    log("%d Firmenkontakte ohne Telefon, E-Mail oder Anschrift; %s:" % (len(karten), "es wird nachgelesen" if args.apply
                                                                        else "Probelauf, nichts wird geschrieben"))
    gelesen = schon = 0
    for k in sorted(karten, key=lambda k: firmen.falten(k["name"])):
        if k.get("von_hand"):
            log("- %s: von Hand bearbeitet, nicht angefasst" % k["name"])
            continue
        if args.max and gelesen >= args.max:
            log("- weitere Firmen im nächsten Lauf (--max %d)" % args.max)
            break
        try:
            ids = korrespondenten_von(k, korr)
            docs = paperless.dokumente(korrespondenten=ids) if ids else paperless.dokumente(phrase=suchname(k["name"]))
        except (OSError, ValueError) as x:
            log("kontakte_nachlesen: Paperless antwortet nicht (%s); der nächste Lauf macht weiter" % x)
            speichern(datei, rec)
            return 1
        treffer = handy_treffer(k, handy)
        eins = treffer[0] if len(treffer) == 1 else None
        stand = sorted([str(d["id"]), sha(d.get("content"))] for d in docs) + (
            [[HANDY, sha(json.dumps(handy_args(eins), sort_keys=True))]] if eins else [])
        alt = rec["firmen"].get(k["kennung"])
        if alt and alt.get("dokumente") == stand:
            schon += 1
            continue
        try:
            genommen, fremd = nachlesen(k, docs, frag, haushalt, personen, mails, namen, rec["antworten"], args.apply,
                                        lambda z: log("- " + z), eins)
        except (OSError, ValueError, KeyError, IndexError) as x:
            log("kontakte_nachlesen: das Modell oder Homes antwortet nicht (%s); der nächste Lauf macht weiter" % x)
            speichern(datei, rec)
            return 1
        except RuntimeError as x:
            log("kontakte_nachlesen: %s" % x)
            speichern(datei, rec)
            return 1
        gelesen += 1
        log("- " + satz(k["name"], genommen, fremd, docs, args.apply,
                        sorted(h.get("name") or h.get("org") for h in treffer) if len(treffer) > 1 else ()))
        if args.apply:
            rec["firmen"][k["kennung"]] = {"name": k["name"], "dokumente": stand, "am": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                           "uebernommen": [{"feld": f, "wert": v, "quelle": i} for f, v, i in genommen]}
        speichern(datei, rec)
    if schon:
        log("%d Firmen schon nachgelesen, ihre Dokumente unverändert: übersprungen" % schon)
    return 0


if __name__ == "__main__":
    sys.exit(main())
