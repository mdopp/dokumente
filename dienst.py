#!/usr/bin/env python3
"""Dokumente's service (laib ADR 0028): the scan as before, and the contract
and invoice helper run whenever Paperless has taken new documents.

Paperless' workflow („Dokument hinzugefügt“) posts to /hook with the
X-Dokumente-Token header; a stack's documents arrive one after another, so
a run starts once the hooks have been quiet for AUSWERTUNG_RUHE_S, and at
the latest AUSWERTUNG_SPAETESTENS_S after the first. Every
AUSWERTUNG_STUENDLICH_S a run starts anyway, the catch-all for a hook
that never came. The helper (vertraege.py) runs as its own process with
Paperless' token and the model through the lease's proxy; its lines
„meldung {json}“ become one file each in MELDUNGEN, laib-notify's
eingang, which tells the owner; its other lines go to the log. What the
last run did goes into the scan's status as „auswertung“.

The listener is for Paperless only: laib binds it where Paperless' pod
reaches the host (as Homes' 8650) and keeps it from the LAN; the token is
the box's, generated, never typed.
"""
import hmac, importlib.util, json, os, subprocess, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _lade_scan():
    """scan.py beside this file (the image's /opt) or in scan/ (the repo), by its path:
    in the repo a bare `import scan` would find the scan/ folder instead"""
    hier = os.path.dirname(os.path.abspath(__file__))
    for pfad in (os.path.join(hier, "scan.py"), os.path.join(hier, "scan", "scan.py")):
        if os.path.exists(pfad):
            spec = importlib.util.spec_from_file_location("dokumente_scan", pfad)
            m = importlib.util.module_from_spec(spec)
            sys.modules["dokumente_scan"] = m
            spec.loader.exec_module(m)
            return m
    raise ImportError("scan.py fehlt")


scan = _lade_scan()


def log(msg):
    print("dokumente: " + msg, flush=True)


def jetzt():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Auswertung:
    """when the helper runs, and what its last run did"""

    def __init__(self, env, dienst=None):
        self.env = env
        self.dienst = dienst
        self.token = env.get("PAPERLESS_TOKEN", "").strip()
        self.skript = env.get("VERTRAEGE_SKRIPT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertraege.py")
        if not os.path.exists(self.skript):
            self.skript = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertraege", "vertraege.py")
        # ordnen.py --neue (2026-11-05): each new document's type, Bereich, sender, person and fields, before the helper;
        # doppelt.py --neue (2026-11-08) before it: a document scanned twice becomes one
        self.ordnen, self.doppelt = (self.beiliegend(env, k, n) for k, n in (("ORDNEN_SKRIPT", "ordnen.py"), ("DOPPELT_SKRIPT", "doppelt.py")))
        self.doppelt_wie = (env.get("DOPPELT") or "an").strip().lower()   # an | probe (a dry run) | aus
        data = env.get("DOKUMENTE_DATA", "/data")
        # the record is backed up (laib mounts /dokumente); /data is the scan's transient folder
        self.datei = env.get("VERTRAEGE_FILE") or (
            "/dokumente/laib-vertraege.json" if os.path.isdir("/dokumente") else os.path.join(data, "laib-vertraege.json"))
        self.meldungen = env.get("MELDUNGEN", "/meldungen")
        self.ruhe = float(env.get("AUSWERTUNG_RUHE_S") or 60)
        self.spaetestens = float(env.get("AUSWERTUNG_SPAETESTENS_S") or 600)
        self.stuendlich = float(env.get("AUSWERTUNG_STUENDLICH_S") or 3600)
        self.zeit = float(env.get("AUSWERTUNG_ZEIT_S") or 1800)
        self.lock = threading.Lock()
        self.erster = self.letzter = 0.0   # the first and last hook not yet run
        self.n = 0
        self.wach = threading.Event()
        self.wache = Wache(os.path.join(os.path.dirname(self.datei), "dokumente-haengt.json"), env)

    @staticmethod
    def beiliegend(env, key, name):
        """a helper script: as the env names it, else beside this file (the image's /opt) or in ordnen/ (the repo)"""
        p = env.get(key)
        if p is not None:
            return p
        hier = os.path.dirname(os.path.abspath(__file__))
        return next((p for p in (os.path.join(hier, name), os.path.join(hier, "ordnen", name)) if os.path.exists(p)), "")

    def an(self):
        return bool(self.token)

    def neu(self):
        """a hook: Paperless took a document"""
        with self.lock:
            now = time.monotonic()
            if not self.erster:
                self.erster = now
            self.letzter = now
        self.wach.set()

    def faellig(self, now, seit_lauf):
        """the reason a run is due now, or "" """
        with self.lock:
            if self.erster and (now - self.letzter >= self.ruhe or now - self.erster >= self.spaetestens):
                self.erster = self.letzter = 0.0
                return "neue Dokumente"
        if seit_lauf >= self.stuendlich:
            return "stündlich"
        return ""

    def schleife(self):
        letzter_lauf = time.monotonic()
        if not self.an():
            self.zeigen({"zustand": "aus", "at": jetzt(), "detail": "Verträge und Rechnungen werden nicht ausgewertet: Dokumente hat keinen Zugang zu Paperless.",
                         "findings": [{"severity": "gelb", "detail": "Neue Dokumente werden weder eingeordnet noch ausgewertet: Dokumente hat keinen Zugang zu Paperless",
                                       "fix": "Laib stellt den Zugang beim Einrichten von Paperless aus (Paperless neu anwenden, dann Scannen neu starten); "
                                              "sonst sollte der Operator nachsehen."}]})
            return
        while True:
            self.wach.wait(timeout=min(self.ruhe, 30))
            self.wach.clear()
            now = time.monotonic()
            anlass = self.faellig(now, now - letzter_lauf)
            if anlass:
                letzter_lauf = time.monotonic()
                try:
                    self.lauf(anlass)
                except Exception:
                    log(traceback.format_exc())

    def lauf(self, anlass):
        """one run of the helper; its messages to laib-notify, its result into the status"""
        self.zeigen({"zustand": "läuft", "at": jetzt(), "anlass": anlass, "detail": "Wertet Verträge und Rechnungen aus …"})
        e = self.env
        env = {"PATH": e.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1", "TZ": e.get("TZ", ""),
               "PAPERLESS_API": e.get("PAPERLESS_URL", "http://127.0.0.1:8000"), "PAPERLESS_TOKEN": self.token,
               "PAPERLESS_ADMIN_USER": e.get("PAPERLESS_USER", "dokumente"),
               "PAPERLESS_URL": e.get("PAPERLESS_PUBLIC_URL", ""),
               "PAPERLESS_AI_ENABLED": "true" if e.get("MODEL_URL") else "false",
               "PAPERLESS_AI_LLM_ENDPOINT": e.get("MODEL_URL", ""), "PAPERLESS_AI_LLM_API_KEY": e.get("MODEL_KEY", ""),
               "PAPERLESS_AI_LLM_MODEL": e.get("MODEL_NAME", ""), "VERTRAEGE_FILE": self.datei}
        for k in ("VERTRAG_TYPEN", "VERTRAG_VORLAUF_TAGE", "RECHNUNG_ARTEN", "HAUSHALT_URL", "HAUSHALT_TOKEN", "HAUSHALT_NAMEN", "HEUTE",
                  "ORDNEN_STAU_H", "DOPPELT_PRO_LAUF"):
            if e.get(k):
                env[k] = e[k]
        probleme = {}
        doppelt = self.doppelt_lauf(env, probleme)
        eingeordnet = self.einordnen(env, probleme) if self.ordnen and os.path.exists(self.ordnen) else None
        start = time.monotonic()
        try:
            p = subprocess.Popen([sys.executable, self.skript], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as x:
            probleme["start"] = {"detail": "Die Auswertung von Verträgen und Rechnungen startet nicht", "einzelheiten": str(x),
                                 "fix": "Der nächste Lauf versucht es wieder; bleibt es so, sollte der Operator nachsehen."}
            return self.zeigen({"zustand": "fehler", "at": jetzt(), "anlass": anlass, "findings": self.wache.lauf(probleme),
                                "detail": "Die Auswertung ließ sich nicht starten; der nächste Lauf versucht es wieder.", "einzelheiten": str(x)})
        uhr = threading.Timer(self.zeit, p.kill)
        uhr.start()
        letzte = []
        for z in p.stdout:
            z = z.rstrip("\n")
            if z.startswith("meldung {"):
                self.melden(z[len("meldung "):])
            elif z:
                log(z)
                letzte = (letzte + [z])[-5:]
        rc = p.wait()
        uhr.cancel()
        lauf, rec = {}, {}
        try:
            with open(self.datei, encoding="utf-8") as f:
                rec = json.load(f)
            lauf = rec.get("lauf") or {}
        except (OSError, ValueError, AttributeError):
            pass
        if eingeordnet:
            lauf = {**lauf, "eingeordnet": eingeordnet}
        for k in ("zusammengefuehrt", "zum_loeschen", "pruefen"):
            if doppelt.get(k):
                lauf = {**lauf, "doppelt_" + k: doppelt[k]}
        dauer = int(time.monotonic() - start)
        vertraege_probleme(lauf, rc, letzte, dauer >= self.zeit, probleme)
        befunde = leere_absender(rec) + self.wache.lauf(probleme)
        if rc != 0:
            satz = "Die Auswertung brach ab; der nächste Lauf versucht es wieder." if dauer < self.zeit else \
                   "Die Auswertung dauerte zu lange und wurde abgebrochen; der nächste Lauf macht weiter."
            return self.zeigen({"zustand": "fehler", "at": jetzt(), "anlass": anlass, "lauf": lauf, "detail": satz,
                                "einzelheiten": " | ".join(letzte), "findings": befunde})
        self.zeigen({"zustand": "fertig", "at": jetzt(), "anlass": anlass, "lauf": lauf, "detail": satz_lauf(lauf, anlass),
                     "findings": befunde})

    def helfer(self, name, argv, env, probleme):
        """one of ordnen/'s scripts: its lines to the log, its „hängt {json}“ lines into probleme, its last
        „<name> {json}“ line back; it failing never stops the run. (the folder, or None when it failed; that line)"""
        ordner = os.path.join(os.path.dirname(self.datei), "ordnen")
        ergebnis = {}
        abbruch = {"fix": "Der nächste Lauf versucht es wieder; bleibt es so, sollte der Operator ins Protokoll von Scannen sehen."}
        try:
            r = subprocess.run([sys.executable] + argv, env={**env, "ORDNEN_DIR": ordner}, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=self.zeit / 2 + 60)
        except (OSError, subprocess.TimeoutExpired) as x:
            log("%s: %s" % (name, x))
            probleme.setdefault(name + "-abbruch", {**abbruch, "einzelheiten": str(x),
                                                    "detail": "Ein Schritt nach dem Scannen (%s) kommt nicht zu Ende" % name.capitalize()})
            return None, ergebnis
        for z in r.stdout.splitlines():
            if z.startswith("hängt {"):
                try:
                    h = json.loads(z[len("hängt "):])
                    probleme.setdefault(h["was"], h)
                except (ValueError, KeyError, TypeError):
                    log("%s: %s" % (name, z))
            elif z.startswith(name + " {"):
                try:
                    ergebnis = json.loads(z[len(name) + 1:])
                except ValueError:
                    pass
            elif z:
                log(z)
        if r.returncode != 0:
            probleme.setdefault(name + "-abbruch", {**abbruch, "einzelheiten": " | ".join(r.stdout.splitlines()[-3:]),
                                                    "detail": "Ein Schritt nach dem Scannen (%s) bricht ab" % name.capitalize()})
            return None, ergebnis
        return ordner, ergebnis

    def doppelt_lauf(self, env, probleme):
        """doppelt.py --neue first: a partial scan goes before anyone reads it; what it did back"""
        if not (self.doppelt and os.path.exists(self.doppelt)) or self.doppelt_wie == "aus":
            return {}
        _, ergebnis = self.helfer("doppelt", [self.doppelt, "--neue"] + (["--dry-run"] if self.doppelt_wie == "probe" else []), env, probleme)
        return ergebnis if isinstance(ergebnis, dict) else {}

    def einordnen(self, env, probleme=None):
        """ordnen.py --neue before the helper, so a document it typed a contract or invoice is read in the same
        run; its lines to the log, how many it filed in order back. It failing never stops the helper"""
        ordner, _ = self.helfer("ordnen", [self.ordnen, "--neue", "--zeit", str(int(self.zeit / 2))], env, {} if probleme is None else probleme)
        if not ordner:
            return 0
        try:
            with open(os.path.join(ordner, "ordnen.json"), encoding="utf-8") as f:
                return int((json.load(f).get("lauf") or {}).get("eingeordnet") or 0)
        except (OSError, ValueError, AttributeError) as x:
            log("ordnen: %s" % x)
            return 0

    def melden(self, roh):
        """one message for the owner: a file in laib-notify's eingang, whole or not at all"""
        try:
            json.loads(roh)
        except ValueError:
            return log("eine Meldung war kein JSON, verworfen")
        self.n += 1
        ziel = os.path.join(self.meldungen, "dokumente-%d-%d-%d.json" % (time.time(), os.getpid(), self.n))
        try:
            os.makedirs(self.meldungen, exist_ok=True)
            with open(ziel + ".neu", "w", encoding="utf-8") as f:
                f.write(roh + "\n")
            os.replace(ziel + ".neu", ziel)
            log("Mitteilung an laib-notify übergeben")
        except OSError as x:
            log("Mitteilung nicht übergeben: %s" % x)

    def zeigen(self, stand):
        scan.AUSWERTUNG.clear()
        scan.AUSWERTUNG.update(stand)
        if self.dienst is not None:
            self.dienst.neu_zeigen = True


class Wache:
    """the watch over the steps after a scan (owner, 2026-11-08: „Es muss auf jeden Fall auffallen und gelöst
    werden“): ordnen sat for hours on „noch nicht eingerichtet“ and the service stayed green. Each run hands
    what hangs, by kind; a kind that a run no longer reports is over. A finding: yellow at once for what never
    mends itself (a missing piece, a refused token, a pile-up), else from the HAENGT_GELB_LAEUFE-th run in a row;
    red once it lasts HAENGT_ROT_H hours, which reaches the Operator and the owner's red message. The record
    survives a restart, so a restart is no cure."""
    SOFORT = ("einrichtung", "zugang", "rechte", "loeschen", "stau", "vertraege-einrichtung", "doppelt")
    NUR_GELB = ("doppelt",)   # waits on a person (duplicates tagged Doppelt to delete), never the Operator's

    def __init__(self, pfad, env):
        self.pfad = pfad
        self.gelb = int(env.get("HAENGT_GELB_LAEUFE") or 3)
        self.rot = float(env.get("HAENGT_ROT_H") or 6) * 3600

    def lauf(self, probleme, now=None):
        """this run's problems ({kind: {detail, fix, einzelheiten?}}) as findings"""
        now = time.time() if now is None else now
        try:
            with open(self.pfad, encoding="utf-8") as f:
                alt = json.load(f)
        except (OSError, ValueError):
            alt = {}
        alt = alt if isinstance(alt, dict) else {}
        neu, out = {}, []
        for was, p in sorted(probleme.items()):
            a = alt.get(was) if isinstance(alt.get(was), dict) else {}
            n = {"seit": a.get("seit") or now, "laeufe": int(a.get("laeufe") or 0) + 1, "detail": p.get("detail", "")}
            neu[was] = n
            dauer = now - n["seit"]
            if dauer >= self.rot and n["laeufe"] >= 2 and was not in self.NUR_GELB:
                sev = "rot"
            elif was in self.SOFORT or n["laeufe"] >= self.gelb:
                sev = "gelb"
            else:
                continue
            seit = time.strftime("%d.%m. %H:%M", time.localtime(n["seit"]))
            detail = (p.get("detail") or "Ein Schritt nach dem Scannen hängt") + (" (seit %s)" % seit if dauer >= 3600 else "")
            fix = p.get("fix") or ""
            if sev == "rot":
                fix = "Das löst sich nicht von selbst; der Operator sollte nachsehen. " + fix
            f = {"severity": sev, "detail": detail, "fix": fix, "topic": "dokumente-" + was}
            if p.get("einzelheiten"):
                f["einzelheiten"] = p["einzelheiten"]
            out.append(f)
        for was in sorted(set(alt) - set(neu)):
            log("wieder in Ordnung: %s" % was)
        try:
            os.makedirs(os.path.dirname(self.pfad) or ".", exist_ok=True)
            with open(self.pfad + ".neu", "w", encoding="utf-8") as f:
                json.dump(neu, f, ensure_ascii=False)
            os.replace(self.pfad + ".neu", self.pfad)
        except OSError as x:
            log("die Wache kann ihren Stand nicht schreiben: %s" % x)
        return out


def vertraege_probleme(lauf, rc, letzte, zu_lange, probleme):
    """what of the contract helper's run hangs, into probleme"""
    if lauf.get("modell_weg"):
        probleme.setdefault("modell", {"detail": "Das Hausmodell antwortet nicht, neue Dokumente werden nicht ausgewertet",
                                       "fix": "Meist ist es nur eine Weile belegt, der nächste Lauf fragt wieder. Hält es an, die Leihe "
                                              "des Modells prüfen (Laib-Status, Dienst des Modells)."})
    if lauf.get("fehlt"):
        probleme["vertraege-einrichtung"] = {"detail": "Verträge und Rechnungen werden nicht gelesen: %s" % lauf["fehlt"],
                                             "fix": "Laib legt die Felder beim Einrichten von Paperless an (Paperless neu anwenden)."}
    if rc == 0:
        return
    roh = " | ".join(letzte)
    if "401" in roh:
        probleme.setdefault("zugang", {"detail": "Paperless lehnt den Zugang von Dokumente ab, Verträge und Rechnungen werden nicht gelesen",
                                       "fix": "Der Zugang wird beim Einrichten von Paperless neu ausgestellt (Paperless neu anwenden); "
                                              "sonst sollte der Operator nachsehen.", "einzelheiten": roh})
    else:
        probleme["vertraege-abbruch"] = {
            "detail": "Die Auswertung von Verträgen und Rechnungen %s" % ("dauert zu lange" if zu_lange else "bricht ab"),
            "fix": "Der nächste Lauf versucht es wieder; bleibt es so, sollte der Operator ins Protokoll von Scannen sehen.",
            "einzelheiten": roh}


def leere_absender(rec):
    """laib#737, dokumente#5: a household person the helper cleared off its documents stays a
    correspondent (Paperless marks none as made by its AI), for the owner to delete"""
    try:
        leer = (rec.get("korrespondenten") or {}).get("leer") or []
    except AttributeError:
        return []
    if not leer:
        return []
    return [{"severity": "gelb",
             "detail": "Absender aus dem Haushalt ohne Dokumente: %s (Dokumente hat sie von den Dokumenten genommen, die KI hatte "
                       "sie als Absender eingetragen)" % ", ".join("„%s“" % n for n in leer[:10]),
             "fix": "in Paperless unter Korrespondenten löschen, wenn sie niemand braucht; Dokumente löscht keine, "
                    "weil Paperless nicht kennzeichnet, welche die KI angelegt hat"}]


def satz_lauf(lauf, anlass):
    """a run in the family's words"""
    teile = []
    for k, eins, viele in (("vertraege", "Vertrag", "Verträge"), ("rechnungen", "Rechnung", "Rechnungen"),
                           ("kontakte", "Kontakt", "Kontakte"), ("erinnerungen", "Erinnerung", "Erinnerungen")):
        n = int(lauf.get(k) or 0)
        if n:
            teile.append("%d %s" % (n, eins if n == 1 else viele))
    was = ("Gelesen: " + ", ".join(teile) + ".") if teile else "Nichts Neues zu lesen."
    if lauf.get("fehlt"):
        was = "Nicht ausgewertet: " + lauf["fehlt"] + "."
    n = int(lauf.get("eingeordnet") or 0)
    if n:   # ordnen.py --neue: type, Bereich, sender, person
        was = "%d Dokument%s eingeordnet. " % (n, "" if n == 1 else "e") + was
    n = int(lauf.get("doppelt_zusammengefuehrt") or 0)
    if n:   # doppelt.py --neue
        was = "%d doppelt gescannte%s Dokument%s zusammengeführt. " % (n, "s" if n == 1 else "", "" if n == 1 else "e") + was
    if int(lauf.get("doppelt_zum_loeschen") or 0):   # a person's duplicates: tagged Doppelt, they delete
        n = int(lauf["doppelt_zum_loeschen"])
        was += " %d doppelt gescannte%s Dokument%s zum Löschen markiert (Tag „Doppelt“)." % (n, "s" if n == 1 else "", "" if n == 1 else "e")
    if int(lauf.get("doppelt_pruefen") or 0):
        n = int(lauf["doppelt_pruefen"])
        was += " %d Dokument%s vielleicht doppelt gescannt, zum Prüfen markiert." % (n, "" if n == 1 else "e")
    if int(lauf.get("pruefen") or 0):
        n = int(lauf["pruefen"])
        was += " %d Dokument%s zum Prüfen markiert." % (n, "" if n == 1 else "e")
    return ("Nach neuen Dokumenten ausgewertet. " if anlass == "neue Dokumente" else "Ausgewertet. ") + was


def hook_handler(auswertung, token):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def antwort(self, code, text=""):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode())

        def do_GET(self):
            if self.path == "/gesund":
                return self.antwort(200, "ok")
            self.antwort(404)

        def do_POST(self):
            n = min(int(self.headers.get("Content-Length") or 0), 1 << 16)
            self.rfile.read(n)
            if self.path.split("?")[0] != "/hook":
                return self.antwort(404)
            if not token or not hmac.compare_digest(self.headers.get("X-Dokumente-Token", ""), token):
                return self.antwort(401)
            auswertung.neu()
            self.antwort(202, "angenommen")
    return H


def lausche(auswertung, env):
    """the hook's listener; none without a token"""
    token = env.get("DOKUMENTE_WEBHOOK_TOKEN", "").strip()
    if not token:
        log("kein DOKUMENTE_WEBHOOK_TOKEN: neue Dokumente kommen nur stündlich dran")
        return None
    host, _, port = env.get("DOKUMENTE_LISTEN", "0.0.0.0:8720").rpartition(":")
    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port)), hook_handler(auswertung, token))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("lauscht auf %s:%s/hook" % (host, srv.server_port))
    return srv


def main():
    env = dict(os.environ)
    cfg = scan.Config.from_env(env)
    for d in (cfg.data, cfg.eingang):
        os.makedirs(d, exist_ok=True)
    env.setdefault("DOKUMENTE_DATA", cfg.data)
    scan.sane_netz(cfg.scanner)
    scan.sane_suche()
    dienst = scan.Dienst(cfg)
    a = Auswertung(env, dienst)
    lausche(a, env)
    threading.Thread(target=a.schleife, daemon=True).start()
    scan.log("bereit: Eingang %s, Scanner %s, Modell %s, Trennblätter %s, Auswertung %s" % (
        cfg.eingang, cfg.scanner or "keiner", cfg.model_name or "keins", "ja" if scan.zbar() else "nein (libzbar.so.0 fehlt)",
        "an" if a.an() else "aus (kein PAPERLESS_TOKEN)"))
    dienst.schleife()


if __name__ == "__main__":
    main()
