#!/usr/bin/env python3
"""Berichtigen: once, what intake filed before the statements guard and the title form (2026-11-08).

A fund policy's yearly Wertmitteilung came in as a Versicherungsschein, so it
was a contract and vertraege.py filled contract fields on it (Vertragsbeginn,
Vertragsende, Betrag, Zahlungsintervall); scanned titles came out as
„Skandia - Dokument“ or „… (5)“ without a date. ordnen.py --neue does it right
from now on (STAND, titel_neu); this puts right what is filed already:

- Type: a document of a contract type (VERTRAG_TYPEN) whose heading is a
  periodic statement (ordnen.stand) gets the statement's type (Kontoauszug,
  Beitrag/Bescheid) — only when ordnen's record shows Dokumente gave it that
  type (the model's answer named it); a type a person or another tool set is
  listed, never changed.
- Contract fields: on such a statement, the fields vertraege.py's notes
  „Vertragsdaten … aus dem Text übernommen (laib): …“ name are taken off;
  any other field stays. A reminder already in Homes' calendar is named.
- Titles: every machine-made title (ordnen.titel_neu: still the file's name,
  filed by Dokumente or naming its correspondent) with no date, a „(n)“ or
  the bare „Dokument“ becomes „JJJJ-MM-TT Absender - Gegenstand“.

Each changed document gets a note saying what was put right.

  berichtigen.py --dry-run [--hintergrund]
      changes nothing; writes plan-berichtigen.json and
      zusammenfassung-berichtigen.txt, the list to look at first.
  berichtigen.py [--hintergrund]
      a backup first (ordnen's sicherung-<zeit>.json, which
      `ordnen.py wiederherstellen` puts back, titles included), then the
      changes. One run at a time with ordnen.py (its lauf.lock).

Paperless' API with PAPERLESS_TOKEN as ordnen.py; the folder as ordnen.py.
The model is never asked. The last line „berichtigen {json}“ counts."""
import argparse, importlib.util, json, os, re, sys, time


def _lade_ordnen():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ordnen.py")
    spec = importlib.util.spec_from_file_location("dokumente_ordnen", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ordnen = _lade_ordnen()
falten = ordnen.falten
# vertraege.py's contract fields (FELDER) and its note: „Vertragsdaten[ und Rechnungsdaten] aus dem Text übernommen (laib): A, B.“
VERTRAGSFELDER = ("Anbieter", "Vertragsnummer", "Vertragsbeginn", "Vertragsende", "Kündigungsfrist", "Nächster Kündigungstermin",
                  "Betrag", "Zahlungsintervall")
NOTIZ = re.compile(r"((?:Vertrags|Rechnungs)daten(?: und (?:Vertrags|Rechnungs)daten)?) aus dem Text übernommen \(laib\): ([^.]+)\.")


def gefuellt(notizen):
    """the contract fields vertraege.py's notes say it filled; Betrag not when it was an invoice's too"""
    out = set()
    for n in notizen:
        for was, namen in NOTIZ.findall(str((n or {}).get("note") if isinstance(n, dict) else n or "")):
            if "Vertragsdaten" in was:
                out |= {x.strip() for x in namen.split(",") if x.strip() in VERTRAGSFELDER and not (x.strip() == "Betrag" and "Rechnungsdaten" in was)}
    return out


def planen(api, o, docs, rec, vrec, log=print):
    """per document what is put right: {id: {"body", "was"[]}}, and the statements whose type is not Dokumente's"""
    vertrag, erlaubt = {falten(t) for t in ordnen.vertrag_typen()}, ordnen.erlaubte_typen(o)
    korr, felder = [c["name"] for c in o.korr], {f["id"]: f["name"] for f in o.felder}
    plaene, fremd = {}, []
    for d in docs:
        typ, st = ordnen.name_von(o.typen, d.get("document_type")), ordnen.stand(d)
        a = (rec.get("antworten") or {}).get(str(d["id"])) or {}
        body, was = {}, []
        if typ and falten(typ) in vertrag and st and o.typ(st):
            if falten((a.get("antwort") or {}).get("typ")) == falten(typ):
                body["document_type"] = o.typ(st)["id"]
                was.append("Art %s → %s (eine regelmäßige Mitteilung, kein Vertrag)" % (typ, st))
            else:
                fremd.append({"id": d["id"], "titel": d.get("title"), "typ": typ})
        if st and falten(ordnen.name_von(o.typen, body.get("document_type", d.get("document_type")))) not in vertrag:
            namen = gefuellt(api("GET", "/api/documents/%d/notes/" % d["id"]) or [])
            weg = [c for c in d.get("custom_fields") or [] if felder.get(c["field"]) in namen]
            if weg:
                body["custom_fields"] = [c for c in d.get("custom_fields") or [] if c not in weg]
                was.append("Vertragsfelder entfernt: %s" % ", ".join(sorted(felder[c["field"]] for c in weg)))
                if any(k.startswith("%d@" % d["id"]) for k in (vrec.get("erinnerungen") or {})):
                    was.append("eine Kündigungserinnerung im Kalender bleibt, bitte in Homes löschen")
        plan = ordnen.auswerten(a["antwort"], d, erlaubt, [], [], korr) if a.get("sha") == ordnen.sha(d) else {}
        titel = ordnen.titel_fuer(d, body, plan, o)
        if titel:
            body["title"] = titel
            was.append("Titel „%s“ → „%s“" % (d.get("title"), titel))
        if body:
            plaene[d["id"]] = {"titel": d.get("title"), "body": body, "was": was}
    return plaene, fremd


def berichtigen(api, ordner, args, log=print):
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    rec_path = os.path.join(ordner, "ordnen.json")
    rec = ordnen.lesen(rec_path, {})
    vrec = ordnen.lesen(os.environ.get("VERTRAEGE_FILE") or os.path.join(os.path.dirname(os.path.abspath(ordner)), "laib-vertraege.json"), {})
    o = ordnen.Ordnung(api, dry=args.dry_run, log=log)
    docs = sorted(api.all("/api/documents/?fields=id,title,content,created,original_file_name,document_type,correspondent,tags,custom_fields"),
                  key=lambda d: d["id"])
    plaene, fremd = planen(api, o, docs, rec, vrec, log)
    if plaene and not args.dry_run:
        path, _ = ordnen.sicherung(api, ordner, stamp)
        log("berichtigen: Sicherung %s" % path)
    zahl = {"typ": 0, "felder": 0, "titel": 0}
    for i, p in plaene.items():
        zahl["typ"] += "document_type" in p["body"]
        zahl["felder"] += "custom_fields" in p["body"]
        zahl["titel"] += "title" in p["body"]
        if args.dry_run:
            continue
        api("PATCH", "/api/documents/%d/" % i, p["body"])
        api("POST", "/api/documents/%d/notes/" % i, {"note": "Berichtigt (Dokumente): %s." % "; ".join(p["was"])})
        a = (rec.get("antworten") or {}).get(str(i))
        d = next(x for x in docs if x["id"] == i)
        if a and "title" in p["body"] and a.get("sha") == ordnen.sha(d):   # the answer stays this document's
            a["sha"] = ordnen.sha({**d, "title": p["body"]["title"]})
    if not args.dry_run:
        ordnen.schreiben(rec_path, rec)
    zeilen = ["%s – %d Dokumente durchgesehen" % ("PROBELAUF, nichts geändert" if args.dry_run else "Angewendet", len(docs)),
              "Art berichtigt: %d, Vertragsfelder entfernt: %d, Titel berichtigt: %d" % (zahl["typ"], zahl["felder"], zahl["titel"]), ""]
    zeilen += ["#%d %s\n     %s" % (i, p["titel"], "\n     ".join(p["was"])) for i, p in plaene.items()]
    if fremd:
        zeilen += ["", "Regelmäßige Mitteilungen mit Vertragsart, die nicht Dokumente gesetzt hat (bleiben, bitte selbst ansehen):"]
        zeilen += ["#%d %s (%s)" % (x["id"], x["titel"], x["typ"]) for x in fremd]
    text = "\n".join(zeilen)
    ordnen.schreiben(os.path.join(ordner, "plan-berichtigen.json"), {"at": stamp, "dry_run": args.dry_run, "dokumente": plaene, "fremd": fremd})
    with open(os.path.join(ordner, "zusammenfassung-berichtigen.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    log(text)
    print("berichtigen " + json.dumps({**zahl, "fremd": len(fremd), "dry_run": args.dry_run}), flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="berichtigen")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hintergrund", action="store_true")
    args = ap.parse_args(argv)
    ordner = ordnen.ordner_waehlen()
    if args.hintergrund:
        ordnen.hintergrund(ordner)
    if not ordnen.sperre(ordner):
        print("berichtigen: ordnen.py läuft gerade, später noch einmal")
        return 1
    try:
        return berichtigen(ordnen.api_aus_env(), ordner, args)
    except (OSError, ValueError) as e:
        print("berichtigen: abgebrochen: %s" % e, flush=True)
        return 2
    finally:
        try:
            os.remove(os.path.join(ordner, "lauf.lock"))
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
