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
reaches the host (as Haus's 8650) and keeps it from the LAN; the token is
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
            self.zeigen({"zustand": "aus", "at": jetzt(), "detail": "Verträge und Rechnungen werden nicht ausgewertet: Dokumente hat keinen Zugang zu Paperless."})
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
        for k in ("VERTRAG_ARTEN", "VERTRAG_VORLAUF_TAGE", "RECHNUNG_ARTEN", "HAUSHALT_URL", "HAUSHALT_TOKEN", "HAUSHALT_NAMEN", "HEUTE"):
            if e.get(k):
                env[k] = e[k]
        start = time.monotonic()
        try:
            p = subprocess.Popen([sys.executable, self.skript], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as x:
            return self.zeigen({"zustand": "fehler", "at": jetzt(), "anlass": anlass,
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
        befunde = leere_absender(rec)
        dauer = int(time.monotonic() - start)
        if rc != 0:
            satz = "Die Auswertung brach ab; der nächste Lauf versucht es wieder." if dauer < self.zeit else \
                   "Die Auswertung dauerte zu lange und wurde abgebrochen; der nächste Lauf macht weiter."
            return self.zeigen({"zustand": "fehler", "at": jetzt(), "anlass": anlass, "lauf": lauf, "detail": satz,
                                "einzelheiten": " | ".join(letzte), "findings": befunde})
        self.zeigen({"zustand": "fertig", "at": jetzt(), "anlass": anlass, "lauf": lauf, "detail": satz_lauf(lauf, anlass),
                     "findings": befunde})

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
