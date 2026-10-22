#!/usr/bin/env python3
"""Scan (laib #568): paper stacks into Paperless.

Two ways in, one way through. A stack is a file in the inbox folder
(/eingang, the share's scan-eingang: a scanner that scans to a network
folder) or a run of a SANE network scanner (SCANNER, ordered with
`laib scan start`). Each page is turned (Tesseract OSD), read (OCR deu)
and judged blank (bright and almost no words, or a back page where the
front's print shows through: mirrored, the OCR reads clearly more). The
household model marks where a new document starts; documents are runs
from one start to the next, never page groups the model makes up. Each
document becomes a PDF in Paperless' consume folder under SCAN_TAG, named
as Paperless titles it: "JJJJ-MM-TT Absender - Titel".

One stack at a time, and exactly one closing message per stack through
bote-notify: FERTIG, WARTET (the scanner stopped mid-stack: clear the jam,
put the rest in, then weiter, abschliessen or verwerfen), ABGEBROCHEN
(nothing filed) or FEHLER (also after a crash, with what is filed
already). The state is the status file laib, Haus and the Operator read.

Orders come as files in /data/auftrag/ (laib scan writes them): start,
weiter, abschliessen, verwerfen, redo. Each stack is archived under
/data/<zeit>/ (the original or the raw pages, plan.json, log.txt) for
SCAN_KEEP_DAYS; `laib scan redo --stapel <zeit>` splits it again.

Stdlib only, apart from Pillow and img2pdf for images and PDFs; the
command-line tools (scanimage, tesseract, pdftoppm, pdftotext, qpdf) come
with the image (scan/Containerfile).
"""
import datetime, glob, json, os, re, shutil, subprocess, sys, time, traceback, urllib.request
from dataclasses import dataclass, field

ZEIT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
PERSON = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ENDUNGEN = {".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png"}
UNFERTIG = (".part", ".tmp", ".crdownload", ".partial", "~")
HELL = 0.94         # a blank page's mean brightness: blank backs 95-97 %, a printed front 92 % (#568)
LEER_WOERTER = 4    # fewer words than this on a bright page: blank
FENSTER, SCHRITT, AB_FENSTER = 30, 25, 40   # big stacks go to the model in overlapping windows


@dataclass
class Config:
    data: str = "/data"
    eingang: str = "/eingang"
    consume: str = "/consume"
    scanner: str = ""
    dpi: int = 300
    mode: str = "Gray"
    area: tuple = (210.0, 297.0)
    duplex: bool = True
    back_first: bool = True
    tag: str = "Stapel"
    notify: str = ""
    keep_days: int = 14
    notify_url: str = ""
    notify_token: str = ""
    model_url: str = ""
    model_lease: str = ""
    model_key: str = ""
    model_name: str = ""
    scanimage: str = "scanimage"
    stable_s: float = 15
    poll_s: float = 2
    remind_s: float = 1800       # WARTET: one reminder after 30 min
    drop_s: float = 86400        # and dropped after a day
    model_wait_s: float = 1800   # the card lent to another mode: wait this long, then unsorted
    owner: tuple = (1000, 1000)  # Paperless' user (paperless.container USERMAP_UID): it removes what it filed

    @classmethod
    def from_env(cls, env):
        def flag(k, d):
            return (env.get(k) or d).strip().lower() in ("1", "true", "yes", "ja")
        # the host network: the model role's proxy is the box's own loopback here
        here = lambda u: (u or "").strip().replace("://host.containers.internal:", "://127.0.0.1:")
        w, _, h = (env.get("SCAN_AREA") or "210x297").partition("x")
        return cls(scanner=(env.get("SCANNER") or "").strip(), dpi=int(env.get("SCAN_DPI") or 300),
                   mode=(env.get("SCAN_MODE") or "Gray").strip(), area=(float(w), float(h or 297)),
                   duplex=flag("SCAN_DUPLEX", "true"), back_first=flag("SCAN_BACK_FIRST", "true"),
                   tag=(env.get("SCAN_TAG") or "Stapel").strip(),
                   notify=(env.get("SCAN_NOTIFY") or env.get("NOTIFY_PERSON") or "").strip(),
                   keep_days=int(env.get("SCAN_KEEP_DAYS") or 14),
                   notify_url=(env.get("NOTIFY_URL") or "").strip(), notify_token=(env.get("NOTIFY_TOKEN") or "").strip(),
                   model_url=here(env.get("MODEL_URL")).rstrip("/"), model_lease=here(env.get("MODEL_LEASE")),
                   model_key=(env.get("MODEL_KEY") or "").strip(), model_name=(env.get("MODEL_NAME") or "").strip())


def log(*a):
    print("scan:", *a, file=sys.stderr, flush=True)


def run(cmd, timeout=600):
    """runs a tool; its output as text, never a shell"""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---- images: PGM/PPM, as scanimage and pdftoppm write them ----

@dataclass
class Bild:
    w: int
    h: int
    c: int        # 1 gray, 3 colour
    data: bytes


def lies_pnm(pfad):
    """an 8-bit P5/P6; ValueError for anything else or a cut-off file (a scan the device broke off)"""
    with open(pfad, "rb") as f:
        raw = f.read()
    tokens, i = [], 0
    while len(tokens) < 4:
        while i < len(raw) and raw[i:i + 1].isspace():
            i += 1
        if raw[i:i + 1] == b"#":
            while i < len(raw) and raw[i:i + 1] not in (b"\n", b"\r"):
                i += 1
            continue
        j = i
        while j < len(raw) and not raw[j:j + 1].isspace():
            j += 1
        if j == i:
            raise ValueError("%s: kein PNM-Kopf" % pfad)
        tokens.append(raw[i:j])
        i = j
    magic, w, h, maxval = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    if magic not in (b"P5", b"P6") or maxval > 255:
        raise ValueError("%s: nur 8-Bit-PGM/PPM" % pfad)
    c = 1 if magic == b"P5" else 3
    data = raw[i + 1:i + 1 + w * h * c]
    if len(data) != w * h * c:
        raise ValueError("%s: abgeschnitten (%d von %d Bytes)" % (pfad, len(data), w * h * c))
    return Bild(w, h, c, data)


def schreib_pnm(b, pfad):
    with open(pfad, "wb") as f:
        f.write(b"%s\n%d %d\n255\n" % (b"P5" if b.c == 1 else b"P6", b.w, b.h))
        f.write(b.data)


def helligkeit(b):
    return sum(b.data) / (len(b.data) * 255.0) if b.data else 1.0


def _kanaele(data, c):
    return [data[k::c] for k in range(c)]


def _mischen(kanaele, n):
    out = bytearray(n * len(kanaele))
    for k, ch in enumerate(kanaele):
        out[k::len(kanaele)] = ch
    return bytes(out)


def gespiegelt(b):
    """left-right mirrored: how a back page's show-through reads"""
    rows = []
    for r in range(b.h):
        row = b.data[r * b.w * b.c:(r + 1) * b.w * b.c]
        rows.append(row[::-1] if b.c == 1 else _mischen([ch[::-1] for ch in _kanaele(row, b.c)], b.w))
    return Bild(b.w, b.h, b.c, b"".join(rows))


def gedreht(b, grad):
    """turned clockwise by 90, 180 or 270 degrees (Tesseract OSD's Rotate)"""
    if grad == 180:
        return Bild(b.w, b.h, b.c, b.data[::-1] if b.c == 1 else _mischen([ch[::-1] for ch in _kanaele(b.data, b.c)], b.w * b.h))
    if grad not in (90, 270):
        return b
    chans = _kanaele(b.data, b.c)
    rows = []
    for i in range(b.w):
        # clockwise: the new row i is old column i bottom-up; counter-clockwise: column w-1-i top-down
        cols = [ch[i::b.w][::-1] for ch in chans] if grad == 90 else [ch[b.w - 1 - i::b.w] for ch in chans]
        rows.append(cols[0] if b.c == 1 else _mischen(cols, b.h))
    return Bild(b.h, b.w, b.c, b"".join(rows))


# ---- one page: turned, read, judged ----

@dataclass
class Seite:
    nr: int                  # its place in the stack, from 1
    bild: str                # the turned page image
    drehung: int = 0
    hell: float = 1.0
    woerter: int = 0
    spiegel: int = 0         # words the mirrored page reads (show-through)
    text: str = ""
    leer: bool = False
    warum: str = ""
    quelle: int = 0          # a PDF's page number, 0 for a scanned image


def woerter(text):
    return len(re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", text or ""))


def osd(pfad):
    """(degrees clockwise, confidence) from Tesseract's orientation detection; (0, 0) when it cannot tell"""
    try:
        out = run(["tesseract", pfad, "stdout", "--psm", "0", "-l", "osd"], timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return 0, 0.0
    grad = re.search(r"Rotate:\s*(\d+)", out)
    konf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    return (int(grad.group(1)) if grad else 0), (float(konf.group(1)) if konf else 0.0)


def ocr(pfad):
    try:
        return run(["tesseract", pfad, "stdout", "-l", "deu", "--psm", "3"], timeout=300).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log("ocr %s: %s" % (pfad, e))
        return ""


def pruefe_seite(pfad, nr, text=None, quelle=0):
    """a page's turn, text and blankness. text: a PDF's own text layer, used as it is when it reads"""
    b = lies_pnm(pfad)
    s = Seite(nr=nr, bild=pfad, hell=helligkeit(b), quelle=quelle)
    if text is not None and woerter(text) >= 20:
        s.text = text
    else:
        grad, konf = osd(pfad)
        if konf >= 2 and grad in (90, 180, 270):
            b = gedreht(b, grad)
            s.drehung = grad
            s.bild = re.sub(r"(\.p[gpn]m)$", r"-gedreht\1", pfad)
            schreib_pnm(b, s.bild)
        s.text = ocr(s.bild)
    s.woerter = woerter(s.text)
    if s.hell >= HELL and s.woerter < LEER_WOERTER:
        s.leer, s.warum = True, "hell, %d Wörter" % s.woerter
    elif s.hell >= HELL and s.woerter < 40:
        spiegel = s.bild + ".spiegel" + os.path.splitext(s.bild)[1]
        schreib_pnm(gespiegelt(b), spiegel)
        s.spiegel = woerter(ocr(spiegel))
        os.unlink(spiegel)
        if s.spiegel >= max(8, 2 * s.woerter):
            s.leer, s.warum = True, "durchscheinende Rückseite (%d Wörter gespiegelt, %d so)" % (s.spiegel, s.woerter)
    return s


# ---- splitting: the model marks the starts, documents are runs ----

@dataclass
class Dokument:
    seiten: list
    art: str = ""
    absender: str = ""
    datum: str = ""
    titel: str = ""


ANWEISUNG = """Du trennst einen gescannten Papierstapel in einzelne Dokumente. Du bekommst den OCR-Text jeder Seite in der Reihenfolge des Stapels.

Für jede Seite:
- neu: true, wenn auf dieser Seite ein neues Dokument beginnt (ein neuer Brief, eine neue Rechnung, ein neuer Bescheid). Auch derselbe Absender schreibt oft mehrere Dokumente: ein anderes Datum, ein anderer Betreff oder eine andere Beleg- oder Rechnungsnummer heißt neu. Folgeseiten („Seite 2 von 3“, Fortsetzung, Anlage, AGB, Überweisungsträger) gehören zum Dokument davor. Ein anderer Absender ist ein starker Hinweis auf ein neues Dokument, aber kein Beweis.
- leer: true nur für eine Seite ohne Inhalt (Rauschen, durchscheinende Schrift der Rückseite).
- absender: kurz, wer die Seite geschrieben hat.
- grund: ein kurzer Satz, warum neu oder nicht.

Für jedes Dokument, das du mit neu beginnst:
- erste_seite: die Nummer der Seite, auf der es beginnt
- art: Rechnung, Brief, Bescheid, Vertrag, Kontoauszug, Bescheinigung …
- absender: kurz, z. B. „Stadtwerke“ oder „Finanzamt Osnabrück“
- datum: das Datum des Schreibens als JJJJ-MM-TT, leer wenn keins zu sehen ist
- titel: kurz und deutsch, ohne Absender und Datum, z. B. „Rechnung Schornsteinfeger 2023“

Die erste Seite des Auszugs ist immer neu. Antworte nur mit JSON in genau dieser Form:
{"seiten":[{"seite":1,"neu":true,"leer":false,"absender":"…","grund":"…"}],"dokumente":[{"erste_seite":1,"art":"…","absender":"…","datum":"JJJJ-MM-TT","titel":"…"}]}"""


def nachricht(seiten):
    """the model's messages for these pages; each page's text cut so a big stack still fits"""
    limit = max(300, min(1500, 24000 // max(1, len(seiten))))
    teile = ["--- Seite %d ---\n%s" % (s.nr, re.sub(r"\s+\n", "\n", s.text.strip())[:limit] or "(kein Text)") for s in seiten]
    return [{"role": "system", "content": ANWEISUNG}, {"role": "user", "content": "\n\n".join(teile)}]


def json_aus(text):
    """the JSON object in a model's answer, fences and talk around it ignored"""
    if isinstance(text, dict):
        return text
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("keine JSON-Antwort")
    obj = json.loads(text[a:b + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("seiten"), list):
        raise ValueError("die Antwort hat keine Seitenliste")
    return obj


def entscheidungen(antwort):
    """per page number: (neu, leer, absender); per first page: the document's details"""
    seiten, dokumente = {}, {}
    for s in antwort.get("seiten") or []:
        if isinstance(s, dict) and isinstance(s.get("seite"), int):
            seiten[s["seite"]] = (s.get("neu") is True, s.get("leer") is True, str(s.get("absender") or ""))
    for d in antwort.get("dokumente") or []:
        if isinstance(d, dict) and isinstance(d.get("erste_seite"), int):
            dokumente[d["erste_seite"]] = d
    return seiten, dokumente


def laeufe(seiten, flags, meta):
    """documents as runs from one start to the next; the model's blank only for a bright page.
    A page the model left out continues the document before it: nothing is lost, nothing doubled."""
    docs, leer = [], []
    for s in seiten:
        neu, ist_leer, absender = flags.get(s.nr, (False, False, ""))
        if ist_leer and s.hell >= HELL:
            s.leer, s.warum = True, "das Modell: leer"
            leer.append(s)
            continue
        if not docs or neu:
            d = meta.get(s.nr) or {}
            docs.append(Dokument(seiten=[s], art=str(d.get("art") or ""), absender=str(d.get("absender") or absender),
                                 datum=str(d.get("datum") or ""), titel=str(d.get("titel") or "")))
        else:
            docs[-1].seiten.append(s)
    return docs, leer


def fenster(n):
    """(start, end) windows over n pages: one up to AB_FENSTER, else FENSTER wide every SCHRITT"""
    if n <= AB_FENSTER:
        return [(0, n)]
    out, a = [], 0
    while True:
        out.append((a, min(n, a + FENSTER)))
        if a + FENSTER >= n:
            return out
        a += SCHRITT


def trennen(seiten, frage):
    """the stack's documents from the model's answers (frage: messages → answer text); a window's
    page counts from its fourth page on, the first three are the window before's call"""
    flags, meta = {}, {}
    for i, (a, b) in enumerate(fenster(len(seiten))):
        f, m = entscheidungen(json_aus(frage(nachricht(seiten[a:b]))))
        for pos, s in enumerate(seiten[a:b]):
            if i == 0 or pos >= 3:
                flags[s.nr] = f.get(s.nr, (False, False, ""))
                if s.nr in m:
                    meta[s.nr] = m[s.nr]
                else:
                    meta.pop(s.nr, None)
    return laeufe(seiten, flags, meta)


# ---- the model, through the lease's proxy ----

def http_json(url, method="GET", key="", body=None, timeout=30):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def karte_vergeben(cfg):
    """why the household model is not there now, or "": the card lent to another mode"""
    if not cfg.model_lease:
        return ""
    try:
        l = http_json(cfg.model_lease, timeout=10)
    except Exception:
        return ""
    mode = l.get("mode") or ""
    if mode and mode != "haushalt":
        return "%s hält die Grafikkarte im Modus %s" % (l.get("holder") or "jemand", mode)
    return ""


def modell(cfg, warte=None):
    """a function messages → answer text, asking the household model; it waits while the card is lent"""
    def frage(messages):
        if not cfg.model_url:
            raise RuntimeError("kein Modell eingerichtet")
        bis = time.monotonic() + cfg.model_wait_s
        while (grund := karte_vergeben(cfg)):
            if time.monotonic() > bis:
                raise RuntimeError(grund)
            if warte:
                warte(grund)
            time.sleep(30)
        body = {"model": cfg.model_name, "messages": messages, "temperature": 0.1, "max_tokens": 8192,
                "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}}
        out = http_json(cfg.model_url + "/chat/completions", "POST", cfg.model_key, body, timeout=600)
        return out["choices"][0]["message"].get("content") or ""
    return frage


# ---- PDFs, names, the consume folder ----

def sauber(s, n=60):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", s or "")
    return re.sub(r"\s+", " ", s).strip(" .-")[:n].strip()


def dateiname(d):
    """Paperless' title: "JJJJ-MM-TT Absender - Titel", parts it does not know left out"""
    datum = d.datum if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.datum or "") else ""
    absender, titel = sauber(d.absender, 50), sauber(d.titel) or sauber(d.art) or "Dokument"
    name = (absender + " - " + titel) if absender else titel
    return sauber((datum + " " + name).strip(), 120) or "Dokument"


def frei(ordner, name, vergeben):
    """name.pdf, else name (2).pdf …: never over a file in the folder or one of this stack"""
    n = 1
    while True:
        kandidat = name + (" (%d)" % n if n > 1 else "") + ".pdf"
        if kandidat not in vergeben and not os.path.exists(os.path.join(ordner, kandidat)):
            vergeben.add(kandidat)
            return kandidat
        n += 1


def pdf_aus_bildern(cfg, bilder, ziel):
    from PIL import Image
    import img2pdf, io
    pngs = []
    for p in bilder:
        buf = io.BytesIO()
        Image.open(p).save(buf, format="PNG", dpi=(cfg.dpi, cfg.dpi))
        pngs.append(buf.getvalue())
    with open(ziel, "wb") as f:
        f.write(img2pdf.convert(pngs))


def pdf_aus_original(original, seiten, ziel):
    """the original PDF's pages, as they were (text layer included), turned where OSD said so"""
    r = run(["qpdf", "--empty", "--pages", original, ",".join(str(s.quelle) for s in seiten), "--", ziel])
    if r.returncode not in (0, 3):  # 3: warnings, the file is written
        raise RuntimeError("qpdf: " + (r.stderr or "").strip()[-300:])
    for i, s in enumerate(seiten, 1):
        if s.drehung:
            r = run(["qpdf", ziel, "--replace-input", "--rotate=+%d:%d" % (s.drehung, i)])
            if r.returncode not in (0, 3):
                raise RuntimeError("qpdf: " + (r.stderr or "").strip()[-300:])


def ablegen(cfg, pdf, name):
    """the PDF into <consume>/<tag>/name, whole or not at all: staged as ._scan-*.part (Paperless
    ignores it), then renamed; Paperless' user owns it, so it can remove it once filed"""
    ordner = os.path.join(cfg.consume, cfg.tag)
    if not os.path.isdir(ordner):
        os.mkdir(ordner, 0o755)
        try:
            os.chown(ordner, *cfg.owner)
        except OSError:
            pass
    stage = os.path.join(ordner, "._scan-%d.part" % time.monotonic_ns())
    shutil.copyfile(pdf, stage)
    with open(stage, "rb+") as f:
        os.fsync(f.fileno())
    os.chmod(stage, 0o644)
    try:
        os.chown(stage, *cfg.owner)
    except OSError:
        pass
    os.rename(stage, os.path.join(ordner, name))


def melde(cfg, person, text):
    """one message to the person through bote-notify; to SCAN_NOTIFY when that person is not reachable"""
    for an in dict.fromkeys(p for p in (person, cfg.notify) if p):
        if not cfg.notify_url or not cfg.notify_token:
            break
        try:
            http_json(cfg.notify_url, "POST", cfg.notify_token, {"an": an, "text": text}, timeout=30)
            return True
        except Exception as e:
            log("meldung an %s: %s" % (an, e))
    log("keine Meldung zugestellt: " + text.splitlines()[0])
    return False


# ---- the stack, its archive and the status ----

@dataclass
class Stapel:
    zeit: str
    quelle: str                    # ordner | scanner
    person: str = ""
    datei: str = ""                # the inbox file's name
    bilder: list = field(default_factory=list)   # the scanner's raw pages, in scan order
    blatt: int = 0                 # sheets scanned whole
    grund: str = ""                # why the scanner stopped
    seit: float = 0                # WARTET since (epoch)
    erinnert: bool = False
    abgelegt: list = field(default_factory=list)  # file names in the consume folder


class Dienst:
    def __init__(self, cfg, now=time.time):
        self.cfg, self.now = cfg, now
        self.offen = None          # a scanner stack waiting for the rest
        self.gesehen = {}          # inbox file → (size, mtime, stable since)
        self.letzte = []
        self.status = {}
        self.aufgeraeumt = 0
        os.makedirs(os.path.join(cfg.data, "auftrag"), exist_ok=True)
        self.wieder_an()

    # status

    def zustand(self, zustand, detail, stapel=None, **mehr):
        s = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "detail": detail,
             "zustand": zustand, "scanner": bool(self.cfg.scanner), "eingang": "scan-eingang",
             "stapel": stapel.zeit if stapel else "", "person": stapel.person if stapel else "", "letzte": self.letzte}
        s.update(mehr)
        if zustand == "fehler":
            s["findings"] = [{"severity": "gelb", "detail": "Der letzte Stapel ist fehlgeschlagen: " + detail,
                              "fix": "laib scan redo --stapel %s" % s["stapel"] if s["stapel"] else "laib logs scan"}]
        elif zustand == "wartet-auf-rest":
            s["findings"] = [{"severity": "info", "detail": "Ein Stapel wartet auf den Rest: " + detail,
                              "fix": "laib scan weiter | abschliessen | verwerfen"}]
        self.status = s
        pfad = os.path.join(self.cfg.data, "laib-status.json")
        with open(pfad + ".tmp", "w") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(pfad + ".tmp", pfad)

    def ende(self, stapel, ende, meldung, dokumente=(), **zahlen):
        """the stack's one closing message, its line in the status's last stacks"""
        melde(self.cfg, stapel.person, meldung)
        self.letzte = ([{"stapel": stapel.zeit, "ende": ende, "quelle": stapel.quelle, "datei": stapel.datei,
                         "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "dokumente": [{"titel": t[:-4], "datei": t} for t in dokumente], "meldung": meldung, **zahlen}]
                       + self.letzte)[:10]
        zu = {"fertig": "fertig", "fehler": "fehler"}.get(ende, "bereit")
        self.zustand(zu, meldung.splitlines()[0], stapel)
        self.speichern(stapel, ende=ende)

    def ordner(self, stapel):
        return os.path.join(self.cfg.data, stapel.zeit)

    def speichern(self, stapel, **mehr):
        with open(os.path.join(self.ordner(stapel), "stapel.json"), "w") as f:
            json.dump({**stapel.__dict__, **mehr}, f, ensure_ascii=False, indent=1)

    def neuer_stapel(self, quelle, person="", datei=""):
        while True:
            zeit = datetime.datetime.fromtimestamp(self.now()).strftime("%Y-%m-%dT%H-%M-%S")
            try:
                os.makedirs(os.path.join(self.cfg.data, zeit))
                break
            except FileExistsError:
                time.sleep(1)
        stapel = Stapel(zeit=zeit, quelle=quelle, person=person or self.cfg.notify, datei=datei)
        self.speichern(stapel)
        return stapel

    def protokoll(self, stapel, zeile):
        with open(os.path.join(self.ordner(stapel), "log.txt"), "a") as f:
            f.write(datetime.datetime.now().strftime("%H:%M:%S ") + zeile + "\n")

    def wieder_an(self):
        """after a restart: a waiting stack waits on, one cut off mid-work gets its FEHLER"""
        try:
            with open(os.path.join(self.cfg.data, "laib-status.json")) as f:
                alt = json.load(f)
        except (OSError, ValueError):
            alt = {}
        self.letzte = alt.get("letzte") or []
        zeit = alt.get("stapel") or ""
        stapel = None
        if ZEIT.match(zeit):
            try:
                with open(os.path.join(self.cfg.data, zeit, "stapel.json")) as f:
                    raw = json.load(f)
                stapel = Stapel(**{k: v for k, v in raw.items() if k in Stapel.__dataclass_fields__})
            except (OSError, ValueError, TypeError):
                stapel = None
        if stapel and alt.get("zustand") == "wartet-auf-rest":
            self.offen = stapel
            self.zustand("wartet-auf-rest", alt.get("detail") or "", stapel, blatt=stapel.blatt + 1, grund=stapel.grund)
            return
        if stapel and alt.get("zustand") in ("scannt", "trennt", "legt-ab"):
            schon = "Schon in Paperless: %d Dokumente." % len(stapel.abgelegt) if stapel.abgelegt else "In Paperless liegt noch nichts davon."
            self.ende(stapel, "fehler", "Scan fehlgeschlagen: Der Dienst wurde mitten im Stapel neu gestartet. %s "
                      "Neu trennen: „laib scan redo --stapel %s“." % (schon, stapel.zeit))
            return
        self.zustand("bereit", "bereit")

    # the loop

    def schleife(self):
        while True:
            try:
                self.tick()
            except Exception:
                log(traceback.format_exc())
            time.sleep(self.cfg.poll_s)

    def tick(self):
        for pfad in sorted(glob.glob(os.path.join(self.cfg.data, "auftrag", "*.json"))):
            try:
                with open(pfad) as f:
                    auftrag = json.load(f)
            except (OSError, ValueError):
                auftrag = {}
            os.unlink(pfad)
            self.auftrag(auftrag)
        if self.offen:
            self.warten()
        else:
            datei = self.eingang_fertig()
            if datei:
                self.ordner_stapel(datei)
        if self.now() - self.aufgeraeumt > 3600:
            self.aufraeumen()

    def auftrag(self, a):
        akt, person = a.get("aktion"), a.get("person") or ""
        person = person if PERSON.match(person) else ""
        log("auftrag %s von %s" % (akt, a.get("von") or "?"))
        if akt == "start":
            if self.offen:
                melde(self.cfg, person or self.offen.person, "Es wartet noch ein Stapel auf den Rest (ab Blatt %d): erst „weiter“, „abschliessen“ oder „verwerfen“." % (self.offen.blatt + 1))
            elif not self.cfg.scanner:
                melde(self.cfg, person or self.cfg.notify, "Hier ist kein Scanner eingerichtet: lass den Scanner auf \\\\<box>\\data\\scan-eingang scannen.")
            else:
                self.scanner_stapel(self.neuer_stapel("scanner", person))
        elif akt in ("weiter", "abschliessen", "verwerfen") and self.offen:
            stapel, self.offen = self.offen, None
            if person:
                stapel.person = person
            if akt == "weiter":
                self.scanner_stapel(stapel)
            elif akt == "abschliessen":
                self.verarbeiten(stapel)
            else:
                self.ende(stapel, "abgebrochen", "Scan verworfen: %d Blatt gescannt, nichts abgelegt." % stapel.blatt)
        elif akt == "redo" and ZEIT.match(a.get("stapel") or "") and not self.offen:
            self.neu(a["stapel"], person)
        else:
            log("auftrag %s: nichts zu tun (offen: %s)" % (akt, bool(self.offen)))

    def neu(self, zeit, person):
        try:
            with open(os.path.join(self.cfg.data, zeit, "stapel.json")) as f:
                raw = json.load(f)
            stapel = Stapel(**{k: v for k, v in raw.items() if k in Stapel.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            melde(self.cfg, person or self.cfg.notify, "Den Stapel %s gibt es im Archiv nicht (mehr)." % zeit)
            return
        stapel.person, stapel.abgelegt = person or stapel.person, []
        self.verarbeiten(stapel)

    # way in A: the inbox folder

    def eingang_fertig(self):
        """the next inbox file whose size held still for stable_s, or None; (path, person)"""
        now = time.monotonic()
        da = set()
        for wurzel, ordner, dateien in os.walk(self.cfg.eingang):
            ordner[:] = [o for o in ordner if not o.startswith(".")]
            for name in sorted(dateien):
                if name.startswith((".", "~")) or name.lower().endswith(UNFERTIG):
                    continue
                pfad = os.path.join(wurzel, name)
                try:
                    st = os.stat(pfad)
                except OSError:
                    continue
                da.add(pfad)
                alt = self.gesehen.get(pfad)
                if not alt or alt[:2] != (st.st_size, st.st_mtime):
                    self.gesehen[pfad] = (st.st_size, st.st_mtime, now)
                elif st.st_size > 0 and now - alt[2] >= self.cfg.stable_s:
                    rel = os.path.relpath(pfad, self.cfg.eingang).split(os.sep)
                    person = rel[0].lower() if len(rel) > 1 and PERSON.match(rel[0].lower()) else ""
                    del self.gesehen[pfad]
                    return pfad, person
        self.gesehen = {p: v for p, v in self.gesehen.items() if p in da}
        return None

    def ordner_stapel(self, datei):
        pfad, person = datei
        stapel = self.neuer_stapel("ordner", person, os.path.basename(pfad))
        endung = os.path.splitext(pfad)[1].lower()
        # into the archive first: the inbox keeps nothing, a crash loses nothing
        shutil.move(pfad, os.path.join(self.ordner(stapel), "original" + endung))
        self.protokoll(stapel, "aus dem Eingang: %s (%s)" % (stapel.datei, person or "ohne Person"))
        if endung not in ENDUNGEN:
            self.ende(stapel, "fehler", "Scan fehlgeschlagen: %s kann ich nicht lesen (nur PDF, TIFF, JPG, PNG). Die Datei liegt im Archiv %s." % (stapel.datei, stapel.zeit))
            return
        self.verarbeiten(stapel)

    # way in B: the SANE scanner

    def scanimage(self, stapel, start):
        roh = os.path.join(self.ordner(stapel), "roh")
        os.makedirs(roh, exist_ok=True)
        w, h = self.cfg.area
        return [self.cfg.scanimage, "-d", self.cfg.scanner, "--source", "Automatic Document Feeder",
                "--adf-mode", "Duplex" if self.cfg.duplex else "Simplex", "--resolution", str(self.cfg.dpi),
                "--mode", self.cfg.mode, "-x", "%g" % w, "-y", "%g" % h, "--auto-eject=yes", "--format=pnm",
                "--batch=" + os.path.join(roh, "bild-%04d.pnm"), "--batch-start=%d" % start]

    def scanner_stapel(self, stapel):
        """scans until the feeder is empty; a stop mid-stack keeps the pages and waits for the rest"""
        weiter = bool(stapel.bilder)
        if weiter and self.cfg.duplex and len(stapel.bilder) % 2:
            stapel.bilder = stapel.bilder[:-1]   # never half a sheet: it is scanned again
        start = len(stapel.bilder) + 1
        roh = os.path.join(self.ordner(stapel), "roh")
        os.makedirs(roh, exist_ok=True)
        for pfad in glob.glob(os.path.join(roh, "bild-*.pnm")):
            if pfad not in stapel.bilder:
                os.unlink(pfad)   # half a sheet, or what followed a broken page: scanned again
        self.zustand("scannt", "scannt Seite %d" % start, stapel, seite=start)
        fehler_log = os.path.join(self.ordner(stapel), "scanimage-%d.log" % start)
        try:
            with open(fehler_log, "w") as err:
                p = subprocess.Popen(self.scanimage(stapel, start), stdout=subprocess.DEVNULL, stderr=err)
                n = 0
                while p.poll() is None:
                    neu = len(glob.glob(os.path.join(roh, "bild-*.pnm")))
                    if neu != n:
                        n = neu
                        self.zustand("scannt", "scannt Seite %d" % (n + 1), stapel, seite=n + 1)
                    time.sleep(0.5)
        except OSError as e:
            self.ende(stapel, "fehler", "Scan fehlgeschlagen: der Scanner lässt sich nicht starten (%s). In Paperless liegt nichts davon." % e)
            return
        with open(fehler_log) as f:
            stderr = f.read()
        self.protokoll(stapel, "scanimage ab Bild %d: %s" % (start, stderr.strip().replace("\n", " | ")[-500:]))
        bilder = []
        for pfad in sorted(glob.glob(os.path.join(roh, "bild-*.pnm"))):
            if pfad in stapel.bilder:
                bilder.append(pfad)
                continue
            try:
                lies_pnm(pfad)
                bilder.append(pfad)
            except ValueError as e:
                self.protokoll(stapel, "verworfen: %s" % e)
                break   # what follows a broken page is not to be trusted either; the next run removes it
        stapel.bilder = bilder
        # a clean end only when the feeder ran out; "Batch terminated" is every run's last line
        if "out of documents" in stderr.lower():
            if not stapel.bilder:
                self.ende(stapel, "abgebrochen", "Scan abgebrochen: kein Blatt im Einzug, nichts abgelegt.")
                return
            self.verarbeiten(stapel)
            return
        grund = scanner_grund(stderr)
        if self.cfg.duplex and len(stapel.bilder) % 2:
            os.unlink(stapel.bilder[-1])
            stapel.bilder = stapel.bilder[:-1]
        stapel.blatt = len(stapel.bilder) // 2 if self.cfg.duplex else len(stapel.bilder)
        if not stapel.bilder:
            self.ende(stapel, "fehler", "Scan fehlgeschlagen: der Scanner hat kein Blatt geliefert (Scanner: %s). In Paperless liegt nichts davon." % grund)
            return
        stapel.grund, stapel.seit, stapel.erinnert = grund, self.now(), False
        self.offen = stapel
        self.speichern(stapel)
        text = wartet_text(stapel)
        melde(self.cfg, stapel.person, text)
        self.zustand("wartet-auf-rest", text.splitlines()[0], stapel, blatt=stapel.blatt + 1, grund=grund)

    def warten(self):
        stapel = self.offen
        if not stapel.erinnert and self.now() - stapel.seit >= self.cfg.remind_s:
            stapel.erinnert = True
            self.speichern(stapel)
            melde(self.cfg, stapel.person, "Erinnerung: " + wartet_text(stapel))
        if self.now() - stapel.seit >= self.cfg.drop_s:
            self.offen = None
            self.ende(stapel, "abgebrochen", "Scan verworfen: der Stapel hat einen Tag auf den Rest gewartet (%d Blatt gescannt), nichts abgelegt." % stapel.blatt)

    # one way through

    def seiten(self, stapel):
        """the stack's pages, checked: from the archived original or the raw scans"""
        ordner = self.ordner(stapel)
        original = next(iter(glob.glob(os.path.join(ordner, "original.*"))), "")
        seiten_dir = os.path.join(ordner, "seiten")
        shutil.rmtree(seiten_dir, ignore_errors=True)
        os.makedirs(seiten_dir)
        bilder, texte, quellen = [], [], []
        if original.lower().endswith(".pdf"):
            r = run(["pdftoppm", "-r", str(self.cfg.dpi)] + (["-gray"] if self.cfg.mode == "Gray" else []) + [original, os.path.join(seiten_dir, "s")], timeout=900)
            if r.returncode:
                raise RuntimeError("das PDF lässt sich nicht lesen: " + (r.stderr or "").strip()[-200:])
            bilder = sorted(glob.glob(os.path.join(seiten_dir, "s-*.p[gp]m")), key=lambda p: int(re.search(r"-(\d+)\.p[gp]m$", p).group(1)))
            for i in range(1, len(bilder) + 1):
                t = run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", original, "-"], timeout=120)
                texte.append(t.stdout if t.returncode == 0 else None)
            quellen = list(range(1, len(bilder) + 1))
        elif original:
            from PIL import Image, ImageSequence
            for i, frame in enumerate(ImageSequence.Iterator(Image.open(original)), 1):
                p = os.path.join(seiten_dir, "s-%d.%s" % (i, "pgm" if self.cfg.mode == "Gray" else "ppm"))
                frame.convert("L" if self.cfg.mode == "Gray" else "RGB").save(p)
                bilder.append(p)
            texte, quellen = [None] * len(bilder), [0] * len(bilder)
        else:
            roh = list(stapel.bilder)
            if self.cfg.duplex and self.cfg.back_first:
                # each sheet comes back first, then front: front first again
                roh = [b for i in range(0, len(roh) - 1, 2) for b in (roh[i + 1], roh[i])] + (roh[-1:] if len(roh) % 2 else [])
            for i, src in enumerate(roh, 1):
                p = os.path.join(seiten_dir, "s-%d%s" % (i, os.path.splitext(src)[1]))
                shutil.copyfile(src, p)
                bilder.append(p)
            texte, quellen = [None] * len(bilder), [0] * len(bilder)
        out = []
        for i, p in enumerate(bilder):
            self.zustand("trennt", "liest Seite %d von %d" % (i + 1, len(bilder)), stapel)
            out.append(pruefe_seite(p, i + 1, texte[i], quellen[i]))
        return out, original

    def verarbeiten(self, stapel, frage=None):
        try:
            self._verarbeiten(stapel, frage or modell(self.cfg, lambda g: self.zustand("trennt", "wartet auf das Modell: " + g, stapel)))
        except Exception as e:
            log(traceback.format_exc())
            schon = ("Schon in Paperless: %s." % ", ".join(stapel.abgelegt)) if stapel.abgelegt else "In Paperless liegt nichts davon."
            self.ende(stapel, "fehler", "Scan fehlgeschlagen: %s. %s Neu versuchen: „laib scan redo --stapel %s“." % (str(e).rstrip("."), schon, stapel.zeit), stapel.abgelegt)

    def _verarbeiten(self, stapel, frage):
        seiten, original = self.seiten(stapel)
        inhalt = [s for s in seiten if not s.leer]
        if not inhalt:
            self.ende(stapel, "abgebrochen", "Scan abgebrochen: alle %d Seiten sind leer, nichts abgelegt." % len(seiten))
            return
        self.zustand("trennt", "trennt %d Seiten" % len(inhalt), stapel)
        hinweis = ""
        try:
            docs, leer_modell = trennen(inhalt, frage)
        except Exception as e:
            hinweis = "Das Modell konnte nicht trennen (%s): alles liegt als ein Dokument „Stapel unsortiert“ in Paperless." % str(e)[:160]
            for s in inhalt:
                s.leer, s.warum = False, ""
            docs, leer_modell = [Dokument(seiten=inhalt, titel="Stapel unsortiert")], []
        with open(os.path.join(self.ordner(stapel), "plan.json"), "w") as f:
            json.dump({"seiten": [{k: v for k, v in s.__dict__.items() if k != "text"} for s in seiten],
                       "dokumente": [{"seiten": [s.nr for s in d.seiten], "art": d.art, "absender": d.absender, "datum": d.datum,
                                      "titel": d.titel, "name": dateiname(d)} for d in docs], "hinweis": hinweis}, f, ensure_ascii=False, indent=1)
        self.zustand("legt-ab", "legt %d Dokumente ab" % len(docs), stapel)
        vergeben = set()
        ordner = os.path.join(self.cfg.consume, self.cfg.tag)
        for i, d in enumerate(docs, 1):
            pdf = os.path.join(self.ordner(stapel), "dokument-%d.pdf" % i)
            if original.lower().endswith(".pdf"):
                pdf_aus_original(original, d.seiten, pdf)
            else:
                pdf_aus_bildern(self.cfg, [s.bild for s in d.seiten], pdf)
            name = frei(ordner, dateiname(d), vergeben)
            ablegen(self.cfg, pdf, name)
            os.unlink(pdf)
            stapel.abgelegt.append(name)
            self.speichern(stapel)
            self.protokoll(stapel, "abgelegt: %s (Seiten %s)" % (name, ",".join(str(s.nr) for s in d.seiten)))
        leer = len(seiten) - len(inhalt) + len(leer_modell)
        kopf = ("%d Blatt, " % ((len(seiten) + 1) // 2 if self.cfg.duplex else len(seiten))) if stapel.quelle == "scanner" else ("%s: " % stapel.datei)
        text = "Scan fertig: %s%d Seiten, %d leer entfernt, %d Dokumente in Paperless (Tag %s):\n%s" % (
            kopf, len(seiten), leer, len(docs), self.cfg.tag, "\n".join("- " + n[:-4] for n in stapel.abgelegt))
        if hinweis:
            text += "\n" + hinweis
        self.ende(stapel, "fertig", text, stapel.abgelegt, seiten=len(seiten), leer=leer)

    def aufraeumen(self):
        self.aufgeraeumt = self.now()
        grenze = self.now() - self.cfg.keep_days * 86400
        for pfad in glob.glob(os.path.join(self.cfg.data, "*")):
            zeit = os.path.basename(pfad)
            if not ZEIT.match(zeit) or (self.offen and self.offen.zeit == zeit) or self.status.get("stapel") == zeit:
                continue
            try:
                alt = os.path.getmtime(os.path.join(pfad, "stapel.json")) < grenze
            except OSError:
                alt = os.path.getmtime(pfad) < grenze
            if alt:
                shutil.rmtree(pfad, ignore_errors=True)


def scanner_grund(stderr):
    """the scanner's own reason, the last error line that is not the closing "Batch terminated" """
    zeilen = [z.strip() for z in stderr.splitlines() if z.strip() and "batch terminated" not in z.lower()
              and re.search(r"error|jam|fail|sane_|invalid|cover|refused", z, re.I)]
    grund = zeilen[-1] if zeilen else "ohne Angabe"
    return re.sub(r"^scanimage:\s*", "", grund)[:200]


def wartet_text(stapel):
    n = stapel.blatt + 1
    return ("Der Scanner hat bei Blatt %d abgebrochen (Scanner: %s). %d Blatt sind gescannt. Stau lösen, den Rest ab Blatt %d "
            "einlegen, dann „weiter“ – oder „abschliessen“ (nur die %d Blatt) bzw. „verwerfen“." % (n, stapel.grund, stapel.blatt, n, stapel.blatt))


def sane_netz(scanner, etc="/etc/sane.d"):
    """a network scanner named backend:net:host is in its backend's config, as SANE finds it"""
    m = re.fullmatch(r"([a-z0-9_]+):net:([\w.:-]+)", scanner or "")
    conf = m and os.path.join(etc, m.group(1) + ".conf")
    if not conf or not os.path.exists(conf):
        return
    with open(conf) as f:
        if re.search(r"(?m)^net\s+%s\s*$" % re.escape(m.group(2)), f.read()):
            return
    with open(conf, "a") as f:
        f.write("\nnet %s\n" % m.group(2))


def main(argv):
    cfg = Config.from_env(os.environ)
    if argv[1:2] == ["redo"] and len(argv) == 3 and ZEIT.match(argv[2]):
        # the CLI in the container: the same order laib scan writes
        ziel = os.path.join(cfg.data, "auftrag", "%d.json" % time.time_ns())
        with open(ziel + ".tmp", "w") as f:
            json.dump({"aktion": "redo", "stapel": argv[2], "von": "cli"}, f)
        os.rename(ziel + ".tmp", ziel)
        return 0
    if argv[1:]:
        print("usage: scan.py [redo <JJJJ-MM-TTThh-mm-ss>]", file=sys.stderr)
        return 1
    for d in (cfg.data, cfg.eingang):
        os.makedirs(d, exist_ok=True)
    sane_netz(cfg.scanner)
    log("bereit: Eingang %s, Scanner %s, Modell %s" % (cfg.eingang, cfg.scanner or "keiner", cfg.model_name or "keins"))
    Dienst(cfg).schleife()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
