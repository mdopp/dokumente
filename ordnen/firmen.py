"""Firmen: whether two names are one firm (owner 2026-11-09).

A backfill of vertraege.py made 25 household contacts, many of them one firm
twice („Union Krankenversicherung“ and „… Aktiengesellschaft“) or OCR damage
(„BKK rmus“ for BKK firmus, „Dr. Stefan Himmeke“ for Hümmeke). vertraege.py
asks this before kontakt_ergaenzen makes a card; kontakte_aufraeumen.py groups
the cards made before by the same rules. The rules, strongest first:

- gleich: the same words once case, umlauts, punctuation, the legal form at
  the end (AG = Aktiengesellschaft, GmbH, KG, e. V., …) and ordnen's ALIASE
  are folded („DKV“ = „Deutsche Krankenversicherung“).
- ocr: the same words but one, and that one (6 letters or more, the name 8 or
  more) differs once where OCR errs: an umlaut, ß, rn/m, cl/d, li/h, i/l/1,
  o/0. Any other near miss of one letter (two from 10 letters: „Schmidt“,
  „Schmitt“) only when the two share a number, mail, address or IBAN.
- anfang: one's words start the other's: the shorter, less specific name is
  the longer one („Allianz“ → „Allianz Versicherungs-AG“); a longer name
  matches a shorter one only when the rest is a suffix like „& Partner“
  („Allianz Lebensversicherungs-AG“ is never „Allianz Versicherungs-AG“).

ligaturen() puts back fi, fl, ff, ffi, ffl that OCR lost (Paperless' text drops
them: „nance“ for „finance“), but only into a word that a known name or the
text elsewhere has with it. Stdlib only; ordnen.py beside it for ALIASE and
falten."""
import importlib.util, os, re, unicodedata


def _ordnen():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ordnen.py")
    spec = importlib.util.spec_from_file_location("firmen_ordnen", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_o = _ordnen()
ALIASE, falten = _o.ALIASE, _o.falten
# legal forms, folded, longest first: stripped from a name's end, again and again („GmbH & Co. KG“)
RECHTSFORMEN = sorted([f.split() for f in (
    "ag", "aktiengesellschaft", "gmbh", "mbh", "gesellschaft mit beschraenkter haftung", "kg", "kgaa", "ohg", "gbr", "ug",
    "haftungsbeschraenkt", "ug haftungsbeschraenkt", "co", "e v", "ev", "eg", "se", "partg", "partgmbb", "mbb", "vvag",
    "versicherungsverein auf gegenseitigkeit", "a g", "inc", "ltd", "llc", "sa", "nv", "bv", "plc")], key=len, reverse=True)
# what may follow a name and still be the same firm: a longer name is a shorter one only with these words after it
ZUSATZ = {"partner", "partnerin", "partnern", "partnerschaft", "kollegen", "kolleginnen", "und", "co", "team", "gruppe"}
# what OCR confuses: each pair a letter (or two) read as the other
VERWECHSELT = [("ue", "u"), ("ue", "i"), ("u", "i"), ("oe", "o"), ("ae", "a"), ("ss", "b"), ("rn", "m"), ("cl", "d"), ("li", "h"),
               ("i", "l"), ("l", "1"), ("i", "1"), ("o", "0"), ("e", "c"), ("ii", "u"), ("in", "m")]
LIGATUREN = ("ffi", "ffl", "fi", "fl", "ff")


def worte(name):
    """a name's folded words without the legal form at its end"""
    w = falten(name).split()
    while True:
        f = next((f for f in RECHTSFORMEN if len(w) > len(f) and w[-len(f):] == f), None)
        if not f:
            return w
        w = w[:-len(f)]


def _alias_worte():
    out = {}
    for alias, ziel in ALIASE.items():
        out[" ".join(worte(alias))] = ziel
        out[" ".join(worte(ziel))] = ziel
    return out


def kanonisch(name):
    """the name ALIASE gives it (its fuller name), else None"""
    return _alias_worte().get(" ".join(worte(name)))


def kern(name):
    """what is compared: the words, the alias's when it has one"""
    ziel = kanonisch(name)
    return tuple(worte(ziel if ziel else name))


def abstand(a, b):
    """Levenshtein distance"""
    vor = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        jetzt = [i]
        for j, y in enumerate(b, 1):
            jetzt.append(min(vor[j] + 1, jetzt[j - 1] + 1, vor[j - 1] + (x != y)))
        vor = jetzt
    return vor[-1]


def verwechselt(a, b):
    """whether a and b differ only where OCR errs (VERWECHSELT), once"""
    for x, y in VERWECHSELT:
        for p, q in ((x, y), (y, x)):
            i = a.find(p)
            while i >= 0:
                if a[:i] + q + a[i + len(p):] == b:
                    return True
                i = a.find(p, i + 1)
    return False


def ocr_gleich(a, b, belegt=False):
    """the same words but one, that one near enough to be OCR's misreading (see the module's text)"""
    if len(a) != len(b) or len(" ".join(a)) < 8:
        return False
    anders = [(x, y) for x, y in zip(a, b) if x != y]
    if len(anders) != 1:
        return False
    x, y = anders[0]
    if min(len(x), len(y)) < 6:
        return False
    return verwechselt(x, y) or (belegt and abstand(x, y) <= (2 if min(len(x), len(y)) >= 10 else 1))


def gleich(a, b, belegt=False):
    """why a (the name in hand) is b (a known one): "gleich", "ocr", "anfang", or None. belegt: the two share a
    number, mail, IBAN or address, which lets any small near miss count"""
    ka, kb = kern(a), kern(b)
    if not ka or not kb:
        return None
    if ka == kb or "".join(ka) == "".join(kb):
        return "gleich"
    if ocr_gleich(ka, kb, belegt):
        return "ocr"
    if len(ka) < len(kb) and kb[:len(ka)] == ka and (len(ka) > 1 or len(ka[0]) >= 3):
        return "anfang"   # the shorter, less specific name in hand: the known fuller one
    if len(ka) > len(kb) and ka[:len(kb)] == kb and set(ka[len(kb):]) <= ZUSATZ:
        return "anfang"
    return None


RANG = {"gleich": 0, "ocr": 1, "anfang": 2}


def beste(name, bekannte, belegt=lambda b: False):
    """the known names name is, best reason first: [(reason, name)], all of the best reason only"""
    treffer = [(g, b) for b in bekannte for g in [gleich(name, b, belegt(b))] if g]
    if not treffer:
        return []
    r = min(RANG[g] for g, _ in treffer)
    return [(g, b) for g, b in treffer if RANG[g] == r]


def wortschatz(*texte):
    """the folded words of names and texts, for ligaturen"""
    out = set()
    for t in texte:
        out |= set(falten(t).split())
    return out


def ligaturen(name, bekannt, fremd=frozenset()):
    """name with the fi, fl, ff, ffi, ffl OCR dropped put back, each where exactly one way to put it in gives a
    word bekannt holds; a word bekannt (or fremd: the names known, not the text) holds as it is stays"""
    def ersetze(m):
        w = m.group(0)
        f = falten(w)
        if len(f) < 3 or f in fremd or not f.isalpha():
            return w
        neu = {}
        for lig in LIGATUREN:
            for i in range(len(w) + 1):
                v = w[:i] + (lig.upper() if w.isupper() and len(w) > 1 else lig) + w[i:]
                if falten(v) in bekannt:
                    neu.setdefault(falten(v), v)
        return next(iter(neu.values())) if len(neu) == 1 else w
    return re.sub(r"[^\W\d_]+", ersetze, str(name or ""))


def mit_umlaut(name):
    """whether a name has a letter OCR loses more often than it adds (ä, ö, ü, ß, an accent)"""
    return any(unicodedata.decomposition(c) or c == "ß" for c in str(name))
