"""Rastreo en profundidad con cola de prioridad por relevancia, límites por dominio, robots.txt,
extracción en paralelo (multiproceso), deduplicación por contenido y descarga de ficheros."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from .extract import CT_EXT, DOC_EXT, IMG_EXT, extract_html, extract_pdf, url_ext
from .net import Fetcher, Robots
from .store import Store
from .util import canon, domain_of, host_of, iso, now, parse_date, relevance

NO_FETCH_HOSTS = {
    "news.google.com", "bsky.app", "x.com", "twitter.com", "facebook.com", "instagram.com", "linkedin.com",
    "tiktok.com", "reddit.com", "youtube.com", "youtu.be", "t.co", "threads.net", "pinterest.com",
}


KINDS = {
    "documento": {"pdf", "doc", "docx", "odt", "rtf", "epub", "mobi", "azw3", "djvu", "ppt", "pptx", "odp", "txt", "md", "tex"},
    "datos": {"csv", "tsv", "xls", "xlsx", "ods", "json", "jsonl", "xml", "geojson", "kml", "parquet", "sql", "sqlite", "db",
              "arrow", "feather", "h5", "npz"},
    "archivo": {"zip", "gz", "tgz", "tar", "7z", "rar", "bz2", "xz", "zst"},
    "código": {"py", "ipynb", "sh", "ps1", "yaml", "yml", "toml", "ini", "js"},
    "software": {"exe", "msi", "dmg", "deb", "rpm", "appimage", "apk", "jar", "pkg", "whl", "nupkg", "snap", "flatpak"},
    "imagen": {"jpg", "jpeg", "png", "gif", "webp", "svg", "tif", "tiff", "bmp", "avif"},
    "multimedia": {"mp3", "ogg", "flac", "wav", "mp4", "webm", "mkv", "ogv", "avi", "mov", "mpeg", "mpg", "m4a", "opus", "m4v"},
}


def _magic_ext(data: bytes) -> str | None:
    """Tipo real por firma binaria (magic bytes), para ficheros servidos sin extensión ni Content-Type útil."""
    try:
        import filetype
        k = filetype.guess(data[:8192])
        return k.extension if k else None
    except Exception:
        return None


_MAGIC = {"pdf": (b"%PDF",), "zip": (b"PK",), "docx": (b"PK",), "xlsx": (b"PK",), "pptx": (b"PK",), "odt": (b"PK",),
          "ods": (b"PK",), "epub": (b"PK",), "jar": (b"PK",), "whl": (b"PK",), "apk": (b"PK",), "gz": (b"\x1f\x8b",),
          "tgz": (b"\x1f\x8b",), "7z": (b"7z\xbc\xaf",), "rar": (b"Rar!",), "png": (b"\x89PNG",), "jpg": (b"\xff\xd8",),
          "jpeg": (b"\xff\xd8",), "gif": (b"GIF8",), "exe": (b"MZ",), "msi": (b"\xd0\xcf",), "parquet": (b"PAR1",)}


def _invalid(data: bytes, ext: str) -> bool:
    """True si el contenido no corresponde a su tipo (firma binaria distinta o HTML disfrazado)."""
    if not data:
        return True
    head = data[:512].lstrip().lower()
    if ext not in ("html", "htm", "xml", "svg", "txt", "md") and head.startswith((b"<!doctype html", b"<html")):
        return True
    sig = _MAGIC.get(ext)
    return bool(sig) and not data.startswith(sig)


def resource_kind(ext: str) -> str:
    return next((k for k, v in KINDS.items() if ext in v), "otro")


DATE_KIND = {"wikipedia": "edited", "internet_archive": "uploaded", "github_repo": "pushed", "gitlab": "activity",
             "hf_models": "modified", "hf_datasets": "modified", "hf_spaces": "modified", "npm": "released"}


def _blocked_host(url: str) -> bool:
    h = domain_of(url)
    return any(h == b or h.endswith("." + b) for b in NO_FETCH_HOSTS)


class Crawler:
    def __init__(self, fetch: Fetcher, store: Store, tokens, cutoff, *, depth=1, max_pages=3000, per_domain=40,
                 workers=48, download=True, max_file_mb=25, all_files=False, keep_all=False, keep_stale=False,
                 robots=True, procs=None, log=print, images=False, site_domains=None, max_gb=5.0):
        self.f, self.store, self.tokens, self.cutoff = fetch, store, tokens, cutoff
        self.depth, self.max_pages, self.per_domain, self.nworkers = depth, max_pages, per_domain, workers
        self.download, self.max_file = download, int(max_file_mb * 1024 * 1024)
        self.all_files, self.keep_all, self.keep_stale = all_files, keep_all, keep_stale
        self.robots = Robots(fetch, robots)
        self.images = images
        self.max_bytes_total = int(max_gb * 1024 ** 3) if max_gb else 0
        self.saved_bytes = sum((f.get("size") or 0) for f in store._read("files"))
        self._disk_warned = False
        self.site_domains = {d.lower().removeprefix("www.") for d in site_domains} if site_domains else None
        if self.site_domains:  # modo web entera: todo lo del dominio, sin filtros de tema ni fecha
            self.keep_all = self.keep_stale = self.all_files = True
            self.per_domain = max_pages
        self.pool = ProcessPoolExecutor(max_workers=procs)
        self.log = log
        self.q: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.cnt = itertools.count()
        self.seen: set[str] = store.load_seen("pages", "canon") | store.load_seen("files", "canon")
        self.hashes: set[str] = store.load_seen("pages", "sha1") | store.load_seen("files", "sha256")
        self.dom = Counter()       # páginas procesadas por dominio (tope --per-domain)
        self.seeded = Counter()    # URLs encoladas por dominio (reparto: ningún dominio acapara la cola)
        self.inflight = Counter()  # peticiones en curso por dominio (evita bloqueo en cabeza de cola)
        self.per_host = fetch.per_host
        self._attempt_path = store.out / "crawled.txt"
        if self._attempt_path.exists():  # reanudación: no repetir lo ya intentado y conservar los topes
            for line in open(self._attempt_path, encoding="utf-8"):
                c = line.strip()
                if c:
                    self.seen.add(c)
                    self.dom[domain_of(c)] += 1
        self._attempts = open(self._attempt_path, "a", encoding="utf-8", buffering=1)
        self.fetched = 0
        self.cap_skips = 0
        self.active = 0
        self.harvest_done = asyncio.Event()

    # ------------------------------------------------------------------ cola
    def in_site(self, url: str) -> bool:
        d = domain_of(url)
        return any(d == s or d.endswith("." + s) for s in self.site_domains)

    def seed(self, url: str, *, depth: int = 1, score: float = 100, parent: str | None = None, hit: dict | None = None,
             file: bool = False, name: str | None = None):
        if not url or not url.startswith("http") or (_blocked_host(url) and not file):
            return
        if file:  # descarga directa de un recurso (API de datasets, zip de repo, adjunto...)
            hit = {**(hit or {}), "_file": True, "_name": name}
        c = canon(url)
        if c in self.seen:
            return
        self.seen.add(c)
        d = domain_of(url)
        self.seeded[d] += 1
        prio = -(score - 2.0 * min(self.seeded[d], 40))  # intercalar dominios
        self.q.put_nowait((prio, depth, next(self.cnt), url, parent, hit))

    @property
    def pending(self) -> int:
        return self.q.qsize()

    # ------------------------------------------------------------------ workers
    async def run(self):
        await asyncio.gather(*(self._worker() for _ in range(self.nworkers)))
        self.pool.shutdown(wait=False, cancel_futures=True)

    async def _worker(self):
        while True:
            try:
                item = await asyncio.wait_for(self.q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if self.harvest_done.is_set() and self.q.empty() and self.active == 0:
                    return
                continue
            if self.fetched >= self.max_pages:
                continue  # vaciar la cola
            neg, depth, _, url, parent, hit = item
            d = domain_of(url)
            if hit and hit.get("_file"):
                cap = self.per_domain * 25  # recursos descargables: tope muy superior
            else:
                cap = self.per_domain * 5 if (depth == 1 and not parent) else self.per_domain  # resultados de búsqueda: tope mayor
            if self.dom[d] >= cap:
                self.cap_skips += 1
                continue
            if self.inflight[d] >= self.per_host:  # dominio saturado: devolver a la cola y coger otro
                self.q.put_nowait((neg + 0.5, depth, next(self.cnt), url, parent, hit))
                await asyncio.sleep(0.02)
                continue
            self.active += 1
            self.inflight[d] += 1
            try:
                self.dom[d] += 1
                self._attempts.write(canon(url) + "\n")
                await self._process(url, depth, parent, hit, -neg)
            except Exception as e:
                self.store.stats["pages_failed"] += 1
                self.log(f"[crawl] error {url}: {e!r}"[:300])
            finally:
                self.active -= 1
                self.inflight[d] -= 1

    async def _process(self, url, depth, parent, hit, score):
        st = self.store.stats
        is_file = bool(hit and hit.get("_file"))
        if is_file and self.max_bytes_total and self.saved_bytes >= self.max_bytes_total:
            if not self._disk_warned:
                self._disk_warned = True
                st["disk_limit_reached"] = True
                self.log(f"[disco] límite de {self.max_bytes_total / 1024 ** 3:.1f} GB alcanzado: no se descargan más ficheros")
            return
        # los enlaces de descarga que devuelve una API pública están pensados para descargarse: sin robots.txt
        if not is_file and not await self.robots.allowed(url):
            st["robots_blocked"] += 1
            return
        if is_file and self.download:  # recurso directo de un catálogo: a disco por trozos, sin tope de memoria
            return await self._download(url, depth, parent, hit)
        ext = url_ext(url)
        is_doc = ext in DOC_EXT or ext in IMG_EXT or is_file
        r = await self.f.get(url, max_bytes=self.max_file if is_doc else 8_000_000, retries=1 if is_file else 0,
                             timeout=60 if is_file else 15)
        self.fetched += 1
        if r is not None and r.truncated and is_doc:
            st["files_too_big"] = st.get("files_too_big", 0) + 1
            return
        if r is None or not r.ok or r.truncated:
            st["pages_failed"] += 1
            return
        ct = r.ctype
        loop = asyncio.get_running_loop()
        is_html = ct in ("text/html", "application/xhtml+xml") or (ct == "" and not is_doc)
        if is_html and not r.content[:5].startswith(b"%PDF"):
            try:  # tope de tiempo por página: los HTML patológicos no bloquean el rastreo
                res = await asyncio.wait_for(loop.run_in_executor(
                    self.pool, extract_html, r.content[:3_000_000], r.url, self.tokens,
                    depth < self.depth or self.download, self.images), timeout=60)
            except asyncio.TimeoutError:
                res = {"ok": False}
            if res.get("ok"):
                await self._page(url, r.url, depth, parent, hit, res)
            else:
                st["pages_failed"] += 1
            return
        # documento / fichero
        if not self.download:
            return
        if ct.startswith("image/") and not (self.images or is_file):
            return
        if ct.startswith("font/") or "javascript" in ct or ct == "text/css":
            return
        sha = hashlib.sha256(r.content).hexdigest()
        if sha in self.hashes:
            st["pages_dup"] += 1
            return
        self.hashes.add(sha)
        if r.content[:5].startswith(b"%PDF"):
            fext = "pdf"
        elif ct in CT_EXT:
            fext = CT_EXT[ct]
        elif ext in DOC_EXT or ext in IMG_EXT:
            fext = ext
        elif is_file:
            nm = (hit or {}).get("_name") or ""
            fext = url_ext("x/" + nm) or url_ext(r.url) or _magic_ext(r.content) or "bin"
        else:
            return  # tipo desconocido: no lo guardamos
        bad = _invalid(r.content, fext)
        if bad:  # p. ej. una página de error HTML servida como .pdf
            st["files_invalid"] = st.get("files_invalid", 0) + 1
            return
        if self.max_bytes_total and self.saved_bytes + len(r.content) > self.max_bytes_total:
            st["disk_limit_reached"] = True
            return
        self.saved_bytes += len(r.content)
        path = self.store.save_file(r.url, r.content, fext, (hit or {}).get("_name"))
        row = {"url": url, "final_url": r.url, "canon": canon(url), "path": path, "size": len(r.content),
               "kind": resource_kind(fext), "ext": fext,
               "source": (hit or {}).get("source"), "from": (hit or {}).get("url") if is_file else None,
               "title": (hit or {}).get("title") if is_file else None,
               "content_type": ct, "sha256": sha, "parent": parent, "depth": depth, "fetched": iso(now())}
        if r.content[:5].startswith(b"%PDF"):
            try:
                pdf = await asyncio.wait_for(loop.run_in_executor(self.pool, extract_pdf, r.content, self.tokens), timeout=90)
            except asyncio.TimeoutError:
                pdf = {}
            if pdf.get("ok") and pdf.get("markdown"):
                raw = pdf.get("date") or ""
                pdate = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8,14}", raw) else None
                row.update(title=pdf.get("title"), pages=pdf.get("pages"), relevance=pdf.get("relevance"), date=pdate)
                row["md"] = self.store.save_markdown(url, pdf.get("title") or path, pdf["markdown"],
                                                     {"url": url, "file": path, "date": pdate, "pages": pdf.get("pages")})
        self.store.write("files", row)
        st["files"] += 1

    async def _download(self, url, depth, parent, hit):
        st = self.store.stats
        part = self.store.out / "files" / f".{hashlib.sha1(url.encode()).hexdigest()[:16]}.part"
        r = await self.f.download(url, part, max_bytes=self.max_file)
        self.fetched += 1

        def drop(counter=None):
            if counter:
                st[counter] = st.get(counter, 0) + 1
            try:
                part.unlink()
            except OSError:
                pass

        if r is None or r["status"] != 200:
            return drop("pages_failed")
        if r["truncated"]:
            return drop("files_too_big")
        head, size, sha = r["head"], r["size"], r["sha256"]
        if sha in self.hashes:
            return drop("pages_dup")
        ct = r["headers"].get("content-type", "").split(";")[0].strip().lower()
        nm = (hit or {}).get("_name") or ""
        ext = url_ext(url)
        if head[:5].startswith(b"%PDF"):
            fext = "pdf"
        elif url_ext("x/" + nm):          # el catálogo dice cómo se llama: su extensión manda
            fext = url_ext("x/" + nm)
        elif ct in CT_EXT:
            fext = CT_EXT[ct]
        elif ext in DOC_EXT or ext in IMG_EXT:
            fext = ext
        else:
            fext = url_ext(r["url"]) or _magic_ext(head) or "bin"
        if _invalid(head, fext):          # p. ej. una página de error HTML servida como .pdf
            return drop("files_invalid")
        if self.max_bytes_total and self.saved_bytes + size > self.max_bytes_total:
            st["disk_limit_reached"] = True
            return drop()
        self.hashes.add(sha)
        self.saved_bytes += size
        path = self.store.place_file(r["url"], part, fext, nm or None)
        row = {"url": url, "final_url": r["url"], "canon": canon(url), "path": path, "size": size,
               "kind": resource_kind(fext), "ext": fext, "source": (hit or {}).get("source"), "from": (hit or {}).get("url"),
               "title": (hit or {}).get("title"), "content_type": ct, "sha256": sha, "parent": parent, "depth": depth,
               "fetched": iso(now())}
        if fext == "pdf" and size <= 40 * 1024 * 1024:  # el texto del PDF solo se extrae de los que caben holgados en memoria
            try:
                data = (self.store.out / path).read_bytes()
                pdf = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self.pool, extract_pdf, data, self.tokens),
                                             timeout=90)
            except (asyncio.TimeoutError, OSError):
                pdf = {}
            if pdf.get("ok") and pdf.get("markdown"):
                raw = pdf.get("date") or ""
                pdate = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8,14}", raw) else None
                row.update(title=pdf.get("title") or row["title"], pages=pdf.get("pages"), relevance=pdf.get("relevance"), date=pdate)
                row["md"] = self.store.save_markdown(url, pdf.get("title") or path, pdf["markdown"],
                                                     {"url": url, "file": path, "date": pdate, "pages": pdf.get("pages")})
        self.store.write("files", row)
        st["files"] += 1

    async def _page(self, url, final, depth, parent, hit, res):
        st = self.store.stats
        pdate = parse_date(res.get("date"))
        hdate = parse_date((hit or {}).get("date"))
        if pdate and pdate < self.cutoff and (parent or not hdate) and not self.keep_stale:
            st["pages_stale"] += 1
            return  # página antigua y el buscador no garantizaba frescura
        if not res.get("sha1"):  # texto corto: huella por dominio + título (evita 100 copias de un muro de login)
            res["sha1"] = "t:" + canon("https://" + domain_of(url) + "/" + (res.get("title") or ""))
        if res["sha1"] in self.hashes:
            st["pages_dup"] += 1
            return
        rel = res.get("relevance", 0)
        hit_rel = relevance(f"{(hit or {}).get('title', '')} {(hit or {}).get('snippet', '')} {url}", self.tokens) if hit else 0
        if rel <= 0 and (depth > 1 or parent or hit_rel <= 0) and not self.keep_all:  # la página o su resultado deben mencionar el término
            st["pages_irrelevant"] += 1
            return
        if res.get("sha1"):
            self.hashes.add(res["sha1"])
        title = res.get("title") or (hit or {}).get("title") or url
        md = self.store.save_markdown(url, title, res.get("markdown", ""), {
            "url": url, "final_url": final if final != url else None, "date": iso(hdate or pdate), "page_date": iso(pdate),
            "author": res.get("author"), "site": res.get("sitename"), "source": (hit or {}).get("source"),
            "depth": depth, "parent": parent, "relevance": rel, "description": res.get("description")})
        self.store.write("pages", {
            "url": url, "final_url": final, "canon": canon(url), "title": title, "date": iso(hdate or pdate),
            "page_date": iso(pdate), "found_date": iso(hdate), "fresh": bool(pdate >= self.cutoff) if pdate else bool(hdate and hdate >= self.cutoff),
            "date_kind": DATE_KIND.get((hit or {}).get("source"), "published"), "author": res.get("author"),
            "sitename": res.get("sitename"), "description": res.get("description"), "image": res.get("image"),
            "tags": res.get("tags"), "lang": res.get("lang"), "relevance": rel, "text_len": res.get("text_len"),
            "sha1": res.get("sha1"), "depth": depth, "parent": parent, "source": (hit or {}).get("source"),
            "md": md, "n_links": len(res.get("links", [])), "docs": [d[0] for d in res.get("docs", [])][:100],
            "feeds": res.get("feeds"), "fetched": iso(now())})
        st["pages"] += 1
        if self.site_domains:  # modo web entera: todo enlace interno, cualquier tema
            if depth < self.depth:
                for u, _anchor, _sc in res.get("links", []):
                    if self.in_site(u):
                        self.seed(u, depth=depth + 1, score=10 - depth, parent=url)
            if self.download:
                for u, _anchor, _sc, _ext in res.get("docs", []):
                    self.seed(u, depth=depth + 1, score=20, parent=url, file=True)
            return
        if depth < self.depth:
            # siguientes niveles: enlaces relevantes primero (externos ligeramente priorizados)
            dom = domain_of(url)
            for u, _anchor, sc in res.get("links", []):
                if sc > 0 or self.keep_all:
                    bonus = 0.5 if domain_of(u) == dom else 1.0
                    self.seed(u, depth=depth + 1, score=sc * bonus + rel * 0.01, parent=url)
        if self.download and depth <= self.depth:  # los ficheros enlazados son hojas: se bajan un nivel más
            for u, _anchor, sc, _ext in res.get("docs", []):
                if sc > 0 or self.all_files or (self.images and _ext in IMG_EXT):
                    self.seed(u, depth=depth + 1, score=50 + sc, parent=url)
