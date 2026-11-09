#!/usr/bin/env python3
"""Kontakte aufräumen (owner 2026-11-09): the household's firm cards that are one firm become one.

A backfill of vertraege.py made 25 household contacts, many one firm under
two names („Union Krankenversicherung“, „… Aktiengesellschaft“) or with OCR's
damage („BKK rmus“ for BKK firmus, „Dr. Stefan Himmeke“ for Hümmeke).
vertraege.py matches before it makes a card now (firmen.py); this puts right
the cards made before.

  kontakte_aufraeumen.py            the dry run: what would become one, in German
  kontakte_aufraeumen.py --apply    does it

What it does:
- reads the firm cards (category Firma) through Homes' Haushalt tool
  firmenkontakte; Paperless' correspondents (PAPERLESS_API or PAPERLESS_URL,
  PAPERLESS_TOKEN) when it has them, for names and for the fi, fl, ff OCR
  dropped (firmen.ligaturen; a word no name has is looked up in Paperless'
  documents with the ligature at its start or end).
- groups them by firmen.py's rules: the same name once legal form and ALIASE
  are folded, or OCR's misreading of one word; a shorter name joins the one
  group whose name it starts (a bare „Allianz“ with both the Lebens- and the
  Sach-AG there joins the one that shares a number, mail, IBAN or address
  with it, else it stays and the list says so).
- per group the name it keeps: a card edited by hand keeps its own (two such
  in one group: nothing is changed, it is listed); else Paperless'
  correspondent's, else ALIASE's fuller one, else the most specific, with
  umlauts, with a legal form, shortest. A card of that name stays; the others'
  numbers, mails, IBAN with bank and holder, address and contact person go into
  it through kontakt_ergaenzen (which makes it under that name when there is
  none), and each is removed (kontakt_entfernen) once Homes took all it had.
  A card with another IBAN than the kept one, or a value Homes refused, stays.
- a card edited by hand is never removed, and never renamed: its OCR is
  listed for the owner.

Env: HAUSHALT_URL, HAUSHALT_TOKEN (the scan container has them). Stdlib only;
firmen.py and ordnen.py beside it."""
import argparse, importlib.util, json, os, re, sys, urllib.error, urllib.parse, urllib.request

E = os.environ


def _firmen():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmen.py")
    spec = importlib.util.spec_from_file_location("aufraeumen_firmen", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


firmen = _firmen()
FELDER = ("telefon", "email", "iban", "anschrift", "ansprechpartner")
WORT = {"telefon": "Telefon", "email": "E-Mail", "iban": "IBAN", "anschrift": "Anschrift", "ansprechpartner": "Ansprechpartner"}


class Haushalt:
    """Homes' Haushalt tools over JSON-RPC: the German line(s), the last line's JSON"""

    def __init__(self, url, token):
        self.url, self.token = url, token

    def __call__(self, name, args):
        r = urllib.request.Request(self.url, method="POST", data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(r, timeout=60) as resp:
            res = json.loads(resp.read())
        if res.get("error"):
            raise RuntimeError("Homes kennt %s nicht oder lehnt ab: %s" % (name, res["error"].get("message")))
        out = res.get("result") or {}
        text = str(((out.get("content") or [{}])[0]).get("text") or "")
        if out.get("isError"):
            raise RuntimeError("%s: %s" % (name, text))
        try:
            return text, json.loads(text.rsplit("\n", 1)[-1])
        except ValueError:
            return text, {}


class Paperless:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def get(self, path):
        r = urllib.request.Request(self.base + path, headers={"Authorization": "Token " + self.token, "Accept": "application/json"})
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read())

    def korrespondenten(self):
        out, page = [], "/api/correspondents/?page_size=100"
        while page:
            res = self.get(page)
            out += [c["name"] for c in res.get("results", [])]
            page = res.get("next") and res["next"][res["next"].index("/api/"):]
        return out

    def kennt(self, wort):
        return (self.get("/api/documents/?page_size=1&query=" + urllib.parse.quote(wort)).get("count") or 0) > 0


def nummer(s):
    return re.sub(r"^(?:0049|49|0)", "", re.sub(r"\D", "", s))


def notizwert(notiz, label):
    for z in str(notiz or "").splitlines():
        if z.strip().startswith(label + ":"):
            return z.split(":", 1)[1].strip()
    return ""


def werte(k):
    """a card's values as kontakt_ergaenzen takes them"""
    adr = [a for a in k.get("anschrift") or [] if a.get("strasse") and a.get("plz") and a.get("ort")]
    return {"telefon": list(k.get("telefon") or []), "email": list(k.get("email") or []),
            "iban": [re.sub(r"\s", "", i).upper() for i in k.get("iban") or []],
            "bank": notizwert(k.get("notiz"), "Bank"), "kontoinhaber": notizwert(k.get("notiz"), "Kontoinhaber"),
            "anschrift": adr[:1], "ansprechpartner": notizwert(k.get("notiz"), "Ansprechpartner")}


def teilen(a, b):
    """whether two cards share a number, mail, IBAN or postcode and street"""
    wa, wb = werte(a), werte(b)
    return bool({nummer(x) for x in wa["telefon"]} & {nummer(x) for x in wb["telefon"]}
                or {x.casefold() for x in wa["email"]} & {x.casefold() for x in wb["email"]}
                or set(wa["iban"]) & set(wb["iban"])
                or {(x["plz"], firmen.falten(x["strasse"])) for x in wa["anschrift"]}
                & {(x["plz"], firmen.falten(x["strasse"])) for x in wb["anschrift"]})


def heile(karten, korr, paperless=None):
    """each card's name with the ligatures OCR dropped put back: from the other cards' and the correspondents'
    words, else (paperless) from a word Paperless' documents have with one at its start or end"""
    for k in karten:
        andere = firmen.wortschatz(*korr, *(x["name"] for x in karten if x is not k))
        heil = firmen.ligaturen(k["name"], andere, andere)
        if heil == k["name"] and paperless:
            for w in re.findall(r"[^\W\d_]{4,}", k["name"]):
                if firmen.falten(w) in andere:
                    continue
                treffer = {v for lig in firmen.LIGATUREN for v in (lig + w if not w.isupper() else lig.upper() + w, w + lig)
                           if paperless.kennt(v)}
                if len(treffer) == 1:
                    heil = firmen.ligaturen(heil, firmen.wortschatz(*treffer))
        k["heil"] = heil


def gruppen(karten):
    """the cards that are one firm: [[card, …]], and the short names that fit several ({name: [groups' names]})"""
    eltern = list(range(len(karten)))

    def wurzel(i):
        while eltern[i] != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i
    for i, a in enumerate(karten):
        for j in range(i):
            if firmen.gleich(a["heil"], karten[j]["heil"], teilen(a, karten[j])) in ("gleich", "ocr"):
                eltern[wurzel(i)] = wurzel(j)
    gr = {}
    for i in range(len(karten)):
        gr.setdefault(wurzel(i), []).append(i)
    offen = {}
    # a shorter name joins the one group it starts; one that shares a value decides between several
    for w, glieder in sorted(gr.items(), key=lambda kv: min(len(firmen.kern(karten[i]["heil"])) for i in kv[1])):
        if w not in gr or len(glieder) > 1:
            continue
        k = karten[glieder[0]]
        ziele = [z for z, g in gr.items() if z != w and any(
            firmen.gleich(k["heil"], karten[i]["heil"]) == "anfang" and len(firmen.kern(k["heil"])) < len(firmen.kern(karten[i]["heil"]))
            for i in g)]
        if len(ziele) > 1:
            ziele = [z for z in ziele if any(teilen(k, karten[i]) for i in gr[z])] or ziele
        if len(ziele) == 1:
            gr[ziele[0]] += gr.pop(w)
        elif ziele:
            offen[k["name"]] = sorted(karten[gr[z][0]]["name"] for z in ziele)
    return [[karten[i] for i in g] for g in gr.values()], offen


def ziel(gruppe, korr):
    """the name the group keeps, and the card that has it (None: kontakt_ergaenzen makes it)"""
    hand = [k for k in gruppe if k.get("von_hand")]
    if hand:
        return hand[0]["name"], hand[0]
    name = next((c for c in korr if any(firmen.gleich(k["heil"], c) == "gleich" for k in gruppe)), None) \
        or next((firmen.kanonisch(k["heil"]) for k in gruppe if firmen.kanonisch(k["heil"])), None)
    if not name:
        def rang(k):
            n = k["heil"]
            return (-len(firmen.kern(n)), not firmen.mit_umlaut(n), firmen.worte(n) == firmen.falten(n).split(), len(n),
                    -sum(bool(v) for v in werte(k).values()))
        name = min(gruppe, key=rang)["heil"]
    schluessel = lambda s: re.sub(r"\s", "", s).casefold()
    return name, next((k for k in gruppe if schluessel(k["name"]) == schluessel(name)), None)


def plan(karten, korr):
    """what becomes one: [{"ziel", "behalten", "quellen": [(card, [fields taken], why it stays or None)], "hinweis"}]"""
    gr, offen = gruppen(karten)
    out = []
    for g in sorted(gr, key=lambda g: firmen.falten(g[0]["heil"])):
        hand = [k for k in g if k.get("von_hand")]
        if len(g) == 1 and g[0]["heil"] == g[0]["name"] and not (firmen.kanonisch(g[0]["name"]) or g[0]["name"]) != g[0]["name"]:
            continue   # alone, its name whole: not renamed after Paperless' spelling
        name, behalten = ziel(g, korr)
        if len(hand) > 1:
            out.append({"ziel": name, "behalten": None, "quellen": [], "hinweis": "„%s“ sind von Hand bearbeitet: nichts geändert, bitte "
                        "selbst zusammenführen" % "“, „".join(k["name"] for k in hand)})
            continue
        if len(g) == 1 and behalten:
            if behalten["heil"] != behalten["name"]:
                out.append({"ziel": name, "behalten": behalten, "quellen": [], "hinweis": "„%s“ ist von Hand bearbeitet; "
                            "heißt sie „%s“? Bitte selbst ändern" % (behalten["name"], behalten["heil"])})
            continue
        stand = werte(behalten) if behalten else {f: [] if f in ("telefon", "email", "iban", "anschrift") else "" for f in FELDER}
        quellen = []
        for k in g:
            if k is behalten:
                continue
            w, nimmt, bleibt = werte(k), [], None
            for f in ("telefon", "email"):
                neu = [x for x in w[f] if (nummer(x) if f == "telefon" else x.casefold()) not in
                       {nummer(y) if f == "telefon" else y.casefold() for y in stand[f]}]
                if neu:
                    nimmt.append(WORT[f])
                    stand[f] += neu
            if w["iban"]:
                if not stand["iban"]:
                    nimmt.append(WORT["iban"])
                    stand["iban"] = w["iban"][:1]
                if set(w["iban"]) - set(stand["iban"]):
                    bleibt = "eine andere IBAN (%s) als „%s“" % (", ".join(sorted(set(w["iban"]) - set(stand["iban"]))), name)
            for f in ("anschrift", "ansprechpartner"):
                if w[f] and not stand[f]:
                    nimmt.append(WORT[f])
                    stand[f] = w[f]
            quellen.append((k, nimmt, bleibt))
        if quellen:
            out.append({"ziel": name, "behalten": behalten, "quellen": quellen, "hinweis": None})
    for n, ziele in sorted(offen.items()):
        out.append({"ziel": None, "behalten": None, "quellen": [], "hinweis": "„%s“ passt zu „%s“: bleibt, bitte sagen, wohin" % (
            n, "“ und „".join(ziele))})
    return out


def zeilen(p):
    """the dry run in German, a line a group"""
    out = []
    for g in p:
        if not g["quellen"]:
            out.append(g["hinweis"])
            continue
        namen = ([g["behalten"]["name"]] if g["behalten"] else []) + [k["name"] for k, _, _ in g["quellen"]]
        nimmt = []
        for _, n, _ in g["quellen"]:
            nimmt += [x for x in n if x not in nimmt]
        z = "„%s“ → „%s“%s" % ("“ + „".join(namen), g["ziel"], " (übernimmt: %s)" % ", ".join(nimmt) if nimmt else "")
        if not g["behalten"]:
            z += "; neu angelegt"
        for k, _, bleibt in g["quellen"]:
            if bleibt:
                z += "; „%s“ bleibt: %s" % (k["name"], bleibt)
        out.append(z)
    return out


def ausfuehren(p, haushalt, log=print):
    """the plan, card by card: its values into the kept name through kontakt_ergaenzen, then removed when Homes took
    all of them; returns how many cards were removed"""
    weg = 0
    for g in p:
        for k, _, bleibt in g["quellen"]:
            w = werte(k)
            aufrufe = [{"firma": g["ziel"]}]
            for f in ("telefon", "email"):
                for i, x in enumerate(w[f]):
                    if i >= len(aufrufe):
                        aufrufe.append({"firma": g["ziel"]})
                    aufrufe[i][f] = x
            if w["iban"]:
                aufrufe[0].update({"iban": w["iban"][0], **{f: w[f] for f in ("bank", "kontoinhaber") if w[f]}})
            if w["anschrift"]:
                aufrufe[0]["anschrift"] = {x: w["anschrift"][0][x] for x in ("strasse", "plz", "ort", "land") if w["anschrift"][0].get(x)}
            if w["ansprechpartner"]:
                aufrufe[0]["ansprechpartner"] = w["ansprechpartner"]
            ok = not bleibt
            for a in aufrufe:
                text, res = haushalt("kontakt_ergaenzen", a)
                schlecht = {f: s for f, s in (res.get("felder") or {}).items() if s in ("abweichend", "abgelehnt")}
                if res.get("status") not in ("angelegt", "ergaenzt", "unveraendert") or schlecht:
                    ok = False
                    log("„%s“ → „%s“: Homes nahm nicht alles (%s): „%s“ bleibt" % (k["name"], g["ziel"], text.split("\n")[0], k["name"]))
                    break
            if ok:
                haushalt("kontakt_entfernen", {"kennung": k["kennung"]})
                weg += 1
                log("„%s“ → „%s“: übernommen und entfernt" % (k["name"], g["ziel"]))
    return weg


def main(argv=None, haushalt=None, paperless=None, log=print):
    ap = argparse.ArgumentParser(description="Doppelte Firmenkontakte im Haushalts-Adressbuch zusammenführen (Probelauf ohne --apply)")
    ap.add_argument("--apply", action="store_true", help="zusammenführen, sonst nur die Liste")
    args = ap.parse_args(argv)
    if haushalt is None:
        if not (E.get("HAUSHALT_URL") and E.get("HAUSHALT_TOKEN")):
            log("kontakte_aufraeumen: HAUSHALT_URL und HAUSHALT_TOKEN fehlen (im Scan-Container gesetzt)")
            return 2
        haushalt = Haushalt(E["HAUSHALT_URL"], E["HAUSHALT_TOKEN"])
    if paperless is None and E.get("PAPERLESS_TOKEN"):
        paperless = Paperless(E.get("PAPERLESS_API") or E.get("PAPERLESS_URL") or "http://127.0.0.1:8000", E["PAPERLESS_TOKEN"])
    try:
        text, res = haushalt("firmenkontakte", {})
    except (OSError, RuntimeError) as x:
        log("kontakte_aufraeumen: %s" % x)
        return 1
    if res.get("teilweise"):
        log("kontakte_aufraeumen: Homes las nur einen Teil des Adressbuchs; nichts geändert")
        return 1
    karten = res.get("kontakte") or []
    korr = []
    if paperless:
        try:
            korr = paperless.korrespondenten()
        except (OSError, ValueError) as x:
            log("kontakte_aufraeumen: Paperless antwortet nicht (%s): ohne seine Namen" % x)
            paperless = None
    heile(karten, korr, paperless)
    p = plan(karten, korr)
    log("%d Firmenkontakte; %s:" % (len(karten), "so wird zusammengeführt" if args.apply else "Probelauf, so würde zusammengeführt"))
    for z in zeilen(p) or ["nichts doppelt"]:
        log("- " + z)
    if args.apply:
        try:
            weg = ausfuehren(p, haushalt, log)
        except (OSError, RuntimeError) as x:
            log("kontakte_aufraeumen: abgebrochen: %s; der nächste Lauf macht den Rest" % x)
            return 1
        log("%d Kontakte zusammengeführt und entfernt" % weg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
