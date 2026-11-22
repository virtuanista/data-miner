"""Utilidades: URLs canónicas, fechas, nombres de fichero seguros, tokens de relevancia."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import re
import unicodedata
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as dparser

UTC = dt.timezone.utc

# --------------------------------------------------------------------------- URLs
_TRACK = re.compile(
    r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|yclid|_hsenc|_hsmi|"
    r"ref_src|ref_url|spm|si|cmpid|ocid|sr_share|share|smid|wt_mc|ncid|guccounter)$",
    re.I,
)


def canon(url: str) -> str:
    """URL canónica para deduplicar: host en minúsculas, sin fragmento ni parámetros de tracking."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url
    if p.scheme not in ("http", "https"):
        return url
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACK.match(k)]
    q.sort()
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit(("https", host, path, urlencode(q, doseq=True), ""))


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_of(url: str) -> str:
    h = host_of(url)
    return h[4:] if h.startswith("www.") else h


def bing_unwrap(href: str) -> str:
    """Decodifica los enlaces de redirección de Bing (ck/a?...&u=a1<base64>, apiclick ...&url=)."""
    if not href:
        return href
    try:
        p = urlsplit(href)
        if "bing.com" in (p.hostname or ""):
            qs = parse_qs(p.query)
            if "u" in qs and qs["u"][0].startswith("a1"):
                b = qs["u"][0][2:]
                b += "=" * (-len(b) % 4)
                return base64.urlsafe_b64decode(b).decode("utf-8", "replace")
            if "url" in qs:
                return qs["url"][0]
    except Exception:
        pass
    return href


# --------------------------------------------------------------------------- fechas
_REL = [
    (re.compile(r"(\d+)\s*(?:min|mins|minute|minutes|minuto|minutos)\b", re.I), "minutes"),
    (re.compile(r"(\d+)\s*(?:h|hr|hrs|hour|hours|hora|horas)\b", re.I), "hours"),
    (re.compile(r"(\d+)\s*(?:d|day|days|día|días|dia|dias)\b", re.I), "days"),
    (re.compile(r"(\d+)\s*(?:w|wk|week|weeks|semana|semanas)\b", re.I), "weeks"),
    (re.compile(r"(\d+)\s*(?:mo|month|months|mes|meses)\b", re.I), "months"),
    (re.compile(r"(\d+)\s*(?:y|yr|year|years|año|años)\b", re.I), "years"),
]


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def parse_since(s: str) -> dt.timedelta:
    """'90m', '24h', '7d', '2w', '3mo' -> timedelta."""
    m = re.fullmatch(r"\s*(\d+)\s*(m|min|h|d|w|mo|y)\s*", s.lower())
    if not m:
        raise ValueError(f"--since inválido: {s!r} (usa 90m, 24h, 7d, 2w, 3mo, 1y)")
    n, u = int(m.group(1)), m.group(2)
    return {
        "m": dt.timedelta(minutes=n), "min": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n),
        "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n), "mo": dt.timedelta(days=30 * n),
        "y": dt.timedelta(days=365 * n),
    }[u]


def relative_date(text: str) -> dt.datetime | None:
    """'3 days ago', 'hace 5 horas', '2h' -> datetime aproximado."""
    if not text:
        return None
    t = text.lower()
    if not re.search(r"ago|hace|\bh\b|\bd\b|\bmin", t) and len(t) > 12:
        return None
    for rx, unit in _REL:
        m = rx.search(t)
        if m:
            n = int(m.group(1))
            if unit == "months":
                return now() - dt.timedelta(days=30 * n)
            if unit == "years":
                return now() - dt.timedelta(days=365 * n)
            return now() - dt.timedelta(**{unit: n})
    if "yesterday" in t or "ayer" in t:
        return now() - dt.timedelta(days=1)
    return None


def parse_date(x) -> dt.datetime | None:
    """Acepta epoch, ISO, RFC822, 'YYYYMMDDTHHMMSSZ', fechas relativas. Devuelve datetime UTC o None."""
    if x is None or x == "":
        return None
    try:
        if isinstance(x, dt.datetime):
            d = x
        elif isinstance(x, (int, float)):
            d = dt.datetime.fromtimestamp(x / 1000 if x > 1e11 else x, UTC)
        else:
            s = str(x).strip()
            if re.fullmatch(r"\d{8}T\d{6}Z", s):
                d = dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            elif re.fullmatch(r"\d{10}(\.\d+)?", s):
                d = dt.datetime.fromtimestamp(float(s), UTC)
            else:
                r = relative_date(s)
                if r:
                    return r
                if re.fullmatch(r"\d{4}(-\d{1,2})?", s):
                    return None  # fecha parcial (año o año-mes): no garantiza frescura -> sin fecha
                base = dt.datetime(1900, 1, 1)  # los campos ausentes NO se rellenan con 'hoy'
                try:
                    d = dparser.parse(s, default=base)
                except (ValueError, OverflowError):
                    d = dparser.parse(s, default=base, fuzzy=True)
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        d = d.astimezone(UTC)
        if d.year < 1990 or d > now() + dt.timedelta(days=2):
            return None
        return d
    except (ValueError, OverflowError, TypeError):
        return None


def iso(d: dt.datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


# --------------------------------------------------------------------------- texto
def fold(s: str) -> str:
    """minúsculas sin acentos."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def slug(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")
    return (s[:maxlen].strip("-")) or "x"


_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str, maxlen: int = 100) -> str:
    name = _WIN_BAD.sub("_", name).strip(" .") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.lower() in _WIN_RESERVED:
        stem = "_" + stem
    ext = ext[:10]
    stem = stem[: maxlen - len(ext) - 1]
    return f"{stem}.{ext}" if ext else stem


STOP = set(
    "a an and are as at be by de del el en es for from how in is it la las los o of on or para por que the to un una y with what why www com http https".split()
)


_PLURAL = re.compile(r"\b([a-z0-9]{4,})s\b")


def stem_text(s: str) -> str:
    """fold + plural ligero: 'copias' -> 'copia', 'backups' -> 'backup', 'desastres' -> 'desastre'."""
    return _PLURAL.sub(r"\1", fold(s))


def make_tokens(terms: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Por término: (frase normalizada, palabras significativas si es compuesto)."""
    out = []
    for t in terms:
        f = re.sub(r"\s+", " ", stem_text(t).strip().strip('"'))
        if not f:
            continue
        words = tuple(w for w in re.findall(r"[a-z0-9][a-z0-9+#.\-]*", f) if len(w) >= 3 and w not in STOP)
        out.append((f, words if len(words) > 1 else ()))
    return out


def relevance(text: str, tokens, cap: int = 50) -> int:
    """Frase exacta x3; las palabras sueltas de un término compuesto solo puntúan si aparecen TODAS."""
    f = stem_text(text)
    score = 0
    for phrase, words in tokens:
        score += 3 * min(f.count(phrase), cap)
        if words and all(w in f for w in words):
            score += min(min(f.count(w) for w in words), cap)
    return score


def sha1(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8", "replace")
    return hashlib.sha1(s).hexdigest()


def clean_ws(s: str | None, maxlen: int | None = None) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:maxlen] if maxlen else s
