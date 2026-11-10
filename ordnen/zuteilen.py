#!/usr/bin/env python3
"""Zuteilen: whose a new document is (laib docs/DATA.md, gap 6; owner 2026-11-10: „Was du scannst oder
hochlädst, gehört von Anfang an dir. Geteilt wird nur, was die Sortierung als Haushaltsdokument erkennt oder
was du teilst.“).

laib's paperless template gives what comes in through the consume folder and the household's mail address
to Dokumente's Paperless user with the tag „Eingang“: no one in the family sees it. A person's own mail
address (<login>+doc@) gives it to that person, with the tag too. This script, run by dienst.py before and
after ordnen.py --neue, decides each document still tagged Eingang, once:

- Owned by Dokumente, with a tag „von-<login>“ (the folder Homes and the Bote file into): given to that
  person at once (owner; Dokumente keeps view and change, as on every document), before the model reads it.
  A person without a Paperless account yet (they never logged in): it waits, Dokumente's, and the run says
  so (`hängt konto`, yellow); the next run after their first login gives it.
- ordnen.py read it (its record's `zuteilung`): a household document (ordnen's haushaltsdokument: the
  model sure, only the Bereiche Haus, Auto, Versicherung, Steuer, nothing medical) gets the tag „Haushalt“
  and, while Dokumente owns it, no owner and no extra rights: the family's. A person's own (a von- or mail
  owner) gets the tag „Haushalt“ too, and Paperless' workflow „Für den Haushalt“ takes it out of their hands
  (Paperless lets no API user but the owner do that). Else, owned by Dokumente: the person the model read as
  the one it concerns (its addressee: the field Person, matched to a Paperless account by first name or
  login); a person's own: stays theirs. Then the tag Eingang goes.
- Owned by Dokumente and nobody found: it stays Dokumente's and Homes asks the admins „Wem gehört dieser
  Scan?“ (Homes' POST /intern/zuordnen, with the Haushalt bearer): Dokumente posts the list (sender, date,
  pages, when it came, and a Zeichen over them) and the people with a Paperless account, and takes the
  answers back; an answer counts only while the Zeichen is still the document's. Never the whole family by
  default. A document ordnen has not read a day after it came is asked the same way; a person's own one then
  just stays theirs.

What it never does: change a document that is not tagged Eingang, or give one away twice.

  zuteilen.py [--dry-run]      prints „zuteilen {json}“ (counts) last; „hängt {json}“ for dienst.py's watch.

Env as ordnen.py --neue (PAPERLESS_API, PAPERLESS_TOKEN, ORDNEN_DIR), and HAUSHALT_URL, HAUSHALT_TOKEN
(Homes' Haushalt address on the host: its origin is where /intern/zuordnen is). Stdlib only."""
import argparse, hashlib, importlib.util, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

E = os.environ
EINGANG, HAUSHALT, VON = "Eingang", "Haushalt", "von-"
ZUORDNEN = "/intern/zuordnen"
_spec = importlib.util.spec_from_file_location("ordnen_zuteilen", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ordnen.py"))
ordnen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ordnen)


def tag_id(api, name):
    res = api("GET", "/api/tags/?name__iexact=" + urllib.parse.quote(name)) or {}
    return next((t["id"] for t in res.get("results") or [] if str(t.get("name", "")).casefold() == name.casefold()), None)


def personen(api):
    """the household's people with a Paperless account (users in a group: Paperless syncs Authelia's): {login: user}"""
    return {u["username"].lower(): u for u in api.all("/api/users/") if u.get("groups") and u.get("username")}


def wer(label, leute):
    """the login whose account the Person field's option label is: its first name, or the login; None if not one"""
    f = ordnen.falten(label or "")
    treffer = [l for l, u in leute.items() if f and f in (ordnen.falten(l), ordnen.falten(u.get("first_name") or ""),
                                                           (ordnen.falten(u.get("first_name") or "").split() or [""])[0])]
    return treffer[0] if len(treffer) == 1 else None


def zeichen(eintrag):
    """the mark of what the admins see: an answer counts only while it is still this"""
    return hashlib.sha256(json.dumps([eintrag[k] for k in ("id", "absender", "datum", "seiten")], ensure_ascii=False).encode()).hexdigest()[:16]


def alt(doc, jetzt):
    """whether a document came over a day ago"""
    try:
        t = datetime.fromisoformat(str(doc.get("added") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return jetzt - (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp() > 86400


class Lauf:
    def __init__(self, api, ich, ein, haus, dry=False, log=print):
        self.api, self.ich, self.ein, self.haus, self.dry, self.log = api, ich, ein, haus, dry, log
        self.n = {"gegeben": 0, "haushalt": 0, "bleibt": 0, "gefragt": 0}

    def patch(self, doc, body, was):
        self.log("zuteilen: #%d %s" % (doc["id"], was))
        if not self.dry:
            self.api("PATCH", "/api/documents/%d/" % doc["id"], body)

    def geben(self, doc, uid, login, fertig):
        """the person owns it; who could see and change it still can, Dokumente too (scan.py's geben)"""
        perms = (self.api("GET", "/api/documents/%d/?full_perms=true" % doc["id"]) or {}).get("permissions") or {}
        rechte = {k: {"users": sorted({u for u in (perms.get(k) or {}).get("users") or [] if u != uid} | {self.ich}),
                      "groups": list((perms.get(k) or {}).get("groups") or [])} for k in ("view", "change")}
        body = {"owner": uid, "set_permissions": rechte}
        if fertig:
            body["tags"] = [t for t in doc["tags"] if t != self.ein]
        self.patch(doc, body, "gehört jetzt %s" % login)
        doc["owner"] = uid
        self.n["gegeben"] += 1

    def haushalt(self, doc):
        body = {"tags": sorted({t for t in doc["tags"] if t != self.ein} | {self.haus})}
        if doc.get("owner") == self.ich:   # Dokumente's own: it may give it to everybody itself
            body.update({"owner": None, "set_permissions": {"view": {"users": [], "groups": []}, "change": {"users": [], "groups": []}}})
        self.patch(doc, body, "gehört dem Haushalt")
        self.n["haushalt"] += 1

    def bleibt(self, doc):
        self.patch(doc, {"tags": [t for t in doc["tags"] if t != self.ein]}, "bleibt bei seiner Person")
        self.n["bleibt"] += 1


def lauf(api, ordner, dry=False, log=print, homes=None, jetzt=None):
    """one pass over the documents tagged Eingang; the counts"""
    jetzt = jetzt or time.time()
    ich = ((api("GET", "/api/ui_settings/") or {}).get("user") or {}).get("id")
    ein, haus = tag_id(api, EINGANG), tag_id(api, HAUSHALT)
    if ein is None or ich is None:
        log("zuteilen: Paperless hat keinen Tag „Eingang“ (laib's paperless template ohne EINGANG_SORTIERT): nichts zu tun")
        return {}
    if haus is None and not dry:
        haus = api("POST", "/api/tags/", {"name": HAUSHALT, "matching_algorithm": 0, "owner": None})["id"]
    leute = personen(api)
    namen = {t["id"]: t["name"] for t in api.all("/api/tags/")}
    korr = {c["id"]: c["name"] for c in api.all("/api/correspondents/")}
    z = ordnen.lesen(os.path.join(ordner, "ordnen.json"), {}).get("zuteilung") or {}
    docs = api.all("/api/documents/?tags__id__all=%d&fields=id,owner,tags,correspondent,created,added,page_count&ordering=added" % ein)
    l, offen, ohne_konto = Lauf(api, ich, ein, haus, dry, log), [], set()
    for d in docs:
        von = next((namen[t][len(VON):].lower() for t in d.get("tags") or [] if str(namen.get(t, "")).lower().startswith(VON)), None)
        entscheid = z.get(str(d["id"]))
        if d.get("owner") == ich and von and not (entscheid and entscheid.get("haushalt")):
            if von not in leute:
                ohne_konto.add(von)
            else:   # theirs at once; the tag Eingang stays until ordnen read it
                l.geben(d, leute[von]["id"], von, fertig=bool(entscheid))
            continue
        if d.get("owner") is None:
            l.bleibt(d)
        elif entscheid and entscheid.get("haushalt"):
            l.haushalt(d)
        elif d.get("owner") != ich:
            if entscheid or alt(d, jetzt):
                l.bleibt(d)   # a person's own (their address, their folder): stays theirs
        elif entscheid and wer(entscheid.get("person"), leute):
            login = wer(entscheid["person"], leute)
            l.geben(d, leute[login]["id"], login, fertig=True)
        elif entscheid or alt(d, jetzt):
            e = {"id": d["id"], "absender": korr.get(d.get("correspondent")) or "", "datum": str(d.get("created") or "")[:10],
                 "seiten": d.get("page_count") or 0, "seit": str(d.get("added") or "")[:16].replace("T", " ")}
            offen.append({**e, "zeichen": zeichen(e), "doc": d})
    fragen(l, offen, leute, homes, log)
    if ohne_konto:
        haengt("konto", "Dokumente von %s warten, bis %s sich einmal bei Paperless angemeldet %s; bis dahin sieht sie niemand"
               % (", ".join(sorted(ohne_konto)), "sie" if len(ohne_konto) == 1 else "diese", "hat" if len(ohne_konto) == 1 else "haben"),
               "Die Person meldet sich einmal bei Paperless an (mit dem Familienkonto); der nächste Lauf gibt ihr die Dokumente.")
    return l.n


def fragen(l, offen, leute, homes, log):
    """the scans nobody was found for, to Homes; the admins' answers back, each only while its Zeichen holds"""
    homes = homes or zuordnen_homes
    liste = [{k: v for k, v in e.items() if k != "doc"} for e in offen]
    wer_alle = [{"login": login, "name": u.get("first_name") or login} for login, u in sorted(leute.items())]
    try:
        antworten = homes({"offen": liste, "personen": wer_alle}).get("antworten") or []
    except (OSError, ValueError, AttributeError) as x:
        if offen:
            haengt("homes", "%d Dokument%s warte%s darauf, dass ein Admin sagt, wem es gehört, aber Homes ist nicht zu erreichen"
                   % (len(offen), "" if len(offen) == 1 else "e", "t" if len(offen) == 1 else "n"),
                   "Der nächste Lauf fragt wieder; bleibt es so, sollte der Operator nachsehen (Homes, HAUSHALT_URL).", str(x))
        return
    l.n["gefragt"] = len(offen)
    nach_id = {e["id"]: e for e in offen}
    for a in antworten:
        e = nach_id.get(a.get("id"))
        if not e or a.get("zeichen") != e["zeichen"]:
            continue
        if a.get("an") == "haushalt":
            l.haushalt(e["doc"])
        elif a.get("an") in leute:
            l.geben(e["doc"], leute[a["an"]]["id"], a["an"], fertig=True)
        else:
            continue
        l.n["gefragt"] -= 1
        log("zuteilen: #%d nach der Antwort eines Admins" % e["id"])


def zuordnen_homes(body):
    """POST the list to Homes' /intern/zuordnen (the origin of HAUSHALT_URL), its JSON answer"""
    teile = urllib.parse.urlsplit(E.get("HAUSHALT_URL", ""))
    if not teile.netloc or not E.get("HAUSHALT_TOKEN"):
        raise OSError("kein HAUSHALT_URL oder HAUSHALT_TOKEN")
    r = urllib.request.Request("%s://%s%s" % (teile.scheme, teile.netloc, ZUORDNEN), method="POST", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + E["HAUSHALT_TOKEN"], "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def haengt(was, detail, fix, einzelheiten=""):
    print("hängt " + json.dumps({"was": was, "detail": detail, "fix": fix, "einzelheiten": einzelheiten[:300]}, ensure_ascii=False), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wem ein neues Dokument gehört (laib DATA.md, Lücke 6)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ordner = E.get("ORDNEN_DIR") or "/dokumente/ordnen"
    try:
        n = lauf(ordnen.api_aus_env(), ordner, a.dry_run)
    except urllib.error.HTTPError as x:
        was = "zugang" if x.code == 401 else "rechte"
        haengt(was, "Dokumente kann neue Dokumente nicht zuteilen: Paperless lehnt ab (%d)" % x.code,
               "Paperless neu anwenden (laib apply paperless) stellt Dokumentes Zugang und Rechte wieder her.", str(x))
        return 1
    except (OSError, ValueError) as x:
        haengt("paperless", "Dokumente kann neue Dokumente nicht zuteilen: Paperless antwortet nicht",
               "Der nächste Lauf versucht es wieder.", str(x))
        return 1
    print("zuteilen " + json.dumps(n, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
