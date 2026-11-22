"""DataMiner · araña de búsqueda por criterio: cosecha masiva multi-fuente + rastreo en profundidad."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .crawler import Crawler
from .net import Fetcher
from . import resources, media  # noqa: F401  (registran las fuentes de recursos y los catálogos de medios)
from .sources import SOURCES, Ctx
from .store import Store
from .util import canon, iso, make_tokens, now, parse_date, parse_since, relevance, slug

# fuentes cuya búsqueda es laxa (palabras sueltas o texto no visible): exigimos el término en título/snippet/URL
LOOSE = {"crossref", "openalex", "npm", "gitlab", "devto", "internet_archive", "github_issue", "stackexchange",
         "hf_models", "hf_datasets", "hf_spaces", "ddg_text", "ddg_news", "zenodo", "figshare", "eu_open_data",
         "uk_open_data", "archive_files", "hf_dataset_files", "code_archives", "github_releases", "socrata", "openaire", "hal", "oai_pmh", "pypi"}

from .paths import OUT as OUT_DIR, self_cmd, workdir  # noqa: E402
FILETYPES = ["pdf", "docx", "xlsx", "pptx", "csv", "zip", "epub", "json", "txt", "odt"]
SITE_PACKS = {
    "social": ["reddit.com", "x.com", "linkedin.com", "medium.com", "substack.com", "quora.com", "news.ycombinator.com"],
    "dev": ["github.com", "stackoverflow.com", "dev.to", "gitlab.com", "hashnode.dev"],
    "video": ["youtube.com", "vimeo.com", "tiktok.com"],
    "docs": ["docs.google.com", "slideshare.net", "scribd.com", "researchgate.net", "academia.edu"],
    "gov": ["gov", "gob.es", "europa.eu", "edu"],
}


# catálogos donde los ficheros de un registro son la misma obra en varios formatos
ONE_FORMAT = {"archive_books", "archive_video", "archive_audio", "gutenberg", "nasa_media"}
FORMAT_PREF = ["epub", "pdf", "mobi", "djvu", "txt", "mp4", "webm", "mkv", "ogv", "avi", "mpeg", "mpg", "mov",
               "mp3", "m4a", "ogg", "flac", "zip"]


def best_formats(files: list) -> list:
    """Un solo fichero por obra: agrupa las variantes por nombre base y se queda con el formato preferido."""
    import re as _re
    groups: dict[str, dict] = {}
    for f in files:
        name = (f.get("name") or f.get("url") or "").rsplit("/", 1)[-1]
        stem, _, ext = name.rpartition(".")
        ext = ext.lower()
        stem = _re.sub(r"(_djvu|_text|_bw|_512kb|_64kb|_vbr|~\w+)$", "", (stem or name).lower())
        rank = FORMAT_PREF.index(ext) if ext in FORMAT_PREF else len(FORMAT_PREF)
        cur = groups.get(stem)
        if cur is None or rank < cur["_r"]:
            groups[stem] = {**f, "_r": rank}
    out = sorted(groups.values(), key=lambda x: x["_r"])
    kinds = {x["_r"] for x in out}
    if len(out) > 1 and min(kinds) < len(FORMAT_PREF):   # si hay texto y además su PDF con otro nombre, basta el mejor
        best = min(kinds)
        text_like = set(range(0, 5))
        if best in text_like:
            out = [x for x in out if x["_r"] not in text_like or x["_r"] == best] or out
    return [{k: v for k, v in x.items() if k != "_r"} for x in out]


def build_variants(terms, filetypes, sites, raw, minimal) -> list[str]:
    v = []
    for t in terms:
        e = f'"{t}"' if " " in t else t
        v += [t] if minimal else [t, f'"{t}"', f"intitle:{e}", f"{e} {now().year}",
                                  f"{e} manual filetype:pdf", f"{e} dataset download", f"{e} software download",
                                  f"{e} github release", f"{e} script"]
        if not minimal:
            v += [f"{e} filetype:{ft}" for ft in filetypes]
            v += [f"{e} site:{s}" for s in sites]
    v += raw
    return list(dict.fromkeys(v))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="dataminer", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
ejemplos:
  dm search "copias de seguridad" -t backup -t "disaster recovery" --since 7d --depth 2
  dm search "ransomware" --since 24h --langs es-ES,en-US --bg
  dm search "llm agents" --sources bing_web,hackernews,github,arxiv --site-pack dev --depth 1
  dm search "datos abiertos" --raw 'site:datos.gob.es "datos abiertos"' --filetypes csv,xlsx,json
  dm status output/copias-de-seguridad_20261122-101500
  dm sources""")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="buscar, cosechar y rastrear")
    s.add_argument("term", help="término o criterio principal")
    s.add_argument("-t", "--term-extra", action="append", default=[], help="términos/sinónimos adicionales (repetible)")
    s.add_argument("--since", default="7d", help="ventana de frescura: 90m, 24h, 7d, 2w, 1mo (por defecto 7d)")
    s.add_argument("--depth", type=int, default=1, help="0=solo resultados, 1=extraer cada página, 2+=seguir enlaces relevantes")
    s.add_argument("--langs", default="es-ES,en-US", help="locales, p. ej. es-ES,en-US,pt-BR")
    s.add_argument("--sources", default="all", help="lista separada por comas (ver `dm sources`)")
    s.add_argument("--exclude", default="", help="fuentes a excluir")
    s.add_argument("--pages", type=int, default=5, help="páginas por consulta en buscadores (10 resultados/página)")
    s.add_argument("--max-per-source", type=int, default=2000)
    s.add_argument("--max-pages", type=int, default=3000, help="tope de páginas a descargar en el rastreo")
    s.add_argument("--per-domain", type=int, default=40, help="tope de páginas por dominio")
    s.add_argument("--concurrency", type=int, default=96, help="conexiones simultáneas globales")
    s.add_argument("--per-host", type=int, default=4)
    s.add_argument("--workers", type=int, default=64, help="workers de rastreo")
    s.add_argument("--procs", type=int, default=None, help="procesos de extracción (por defecto: nº de CPUs)")
    s.add_argument("--filetypes", default=",".join(FILETYPES), help="filetype: a buscar con operadores")
    s.add_argument("--site", action="append", default=[], help="añadir variante site:dominio (repetible)")
    s.add_argument("--site-pack", action="append", default=[], choices=list(SITE_PACKS), help="paquetes de sitios")
    s.add_argument("--raw", action="append", default=[], help="consulta avanzada literal (operadores) adicional")
    s.add_argument("--minimal", action="store_true", help="sin expansión de consultas")
    s.add_argument("--no-files", action="store_true", help="no descargar ficheros (pdf, csv, xlsx...)")
    s.add_argument("--all-files", action="store_true", help="descargar todos los ficheros enlazados, no solo los relevantes")
    s.add_argument("--max-file-mb", type=float, default=100, help="tope por fichero en MB (0 = sin límite)")
    s.add_argument("--one-format", action="store_true", help="un solo formato por obra en los catálogos de libros, vídeo y audio")
    s.add_argument("--images", action="store_true", help="descargar también imágenes")
    s.add_argument("--max-gb", type=float, default=5.0, help="límite de disco de ficheros por recolección (0 = sin límite)")
    s.add_argument("--keep-all", action="store_true", help="guardar/seguir páginas aunque no mencionen el término")
    s.add_argument("--keep-stale", action="store_true", help="no descartar páginas con fecha anterior a la ventana")
    s.add_argument("--drop-undated", action="store_true", help="descartar resultados sin fecha")
    s.add_argument("--ignore-robots", action="store_true", help="no consultar robots.txt en el rastreo")
    s.add_argument("--source-timeout", type=int, default=300, help="segundos máximos por fuente")
    s.add_argument("--proxy", default=None, help="proxy HTTP/SOCKS opcional")
    s.add_argument("--out", default=None, help="directorio de salida (por defecto output/<termino>_<fecha>)")
    s.add_argument("--resume", default=None, help="reanudar en un directorio de salida existente")
    s.add_argument("--bg", action="store_true", help="ejecutar en segundo plano (log en run.log)")
    c = sub.add_parser("crawl", help="rastrear webs enteras: todas sus páginas y ficheros")
    c.add_argument("urls", nargs="+", help="URLs o dominios de partida")
    c.add_argument("--depth", type=int, default=6, help="niveles de enlaces a seguir")
    c.add_argument("--max-pages", type=int, default=5000)
    c.add_argument("--images", action="store_true", help="descargar también imágenes")
    c.add_argument("--max-gb", type=float, default=5.0)
    c.add_argument("--no-files", action="store_true")
    c.add_argument("--max-file-mb", type=float, default=100)
    c.add_argument("--concurrency", type=int, default=64)
    c.add_argument("--per-host", type=int, default=6)
    c.add_argument("--workers", type=int, default=48)
    c.add_argument("--procs", type=int, default=None)
    c.add_argument("--ignore-robots", action="store_true")
    c.add_argument("--proxy", default=None)
    c.add_argument("--out", default=None)
    c.add_argument("--resume", default=None)
    c.add_argument("--bg", action="store_true")
    st = sub.add_parser("status", help="estado de una ejecución")
    st.add_argument("dir", nargs="?", help="directorio de salida (por defecto el más reciente)")
    sub.add_parser("sources", help="listar fuentes")
    w = sub.add_parser("web", help="abrir la plataforma web local")
    w.add_argument("--port", type=int, default=8787)
    w.add_argument("--no-browser", action="store_true")
    dk = sub.add_parser("app", help="abrir la aplicación de escritorio (ventana propia)")
    dk.add_argument("--port", type=int, default=8787)
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------------------------- run
class Runner:
    def __init__(self, a, out: Path):
        self.a, self.out = a, out
        self.terms = list(dict.fromkeys([a.term] + a.term_extra))
        self.since = parse_since(a.since)
        self.cutoff = now() - self.since
        self.logf = open(out / "run.log", "a", encoding="utf-8", buffering=1)
        self.tty = sys.stderr.isatty() and not a.bg
        self.source_status: dict[str, str] = {}

    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.logf.write(line + "\n")
        if self.tty:
            print("\r\033[K" + line, file=sys.stderr)

    async def main(self):
        a = self.a
        names = list(SOURCES) if a.sources == "all" else [s.strip() for s in a.sources.split(",") if s.strip()]
        names = [n for n in names if n not in set(a.exclude.split(","))]
        bad = [n for n in names if n not in SOURCES]
        if bad:
            raise SystemExit(f"fuentes desconocidas: {bad}. Usa `dm sources`.")
        sites = a.site + [s for p in a.site_pack for s in SITE_PACKS[p]]
        fts = [x for x in a.filetypes.split(",") if x]
        variants = build_variants(self.terms, fts, sites, a.raw, a.minimal)
        fetch = Fetcher(a.concurrency, a.per_host, proxy=a.proxy)
        store = Store(self.out)
        self.store = store
        seen_hits = store.load_seen("results", "canon")
        tokens = make_tokens(self.terms)
        ctx = Ctx(fetch, self.terms, self.since, [x.strip() for x in a.langs.split(",") if x.strip()],
                  a.pages, a.max_per_source, variants, self.log)
        crawler = None
        if a.depth >= 1:
            crawler = Crawler(fetch, store, tokens, self.cutoff, depth=a.depth, max_pages=a.max_pages,
                              per_domain=a.per_domain, workers=a.workers, download=not a.no_files,
                              max_file_mb=a.max_file_mb, all_files=a.all_files, keep_all=a.keep_all,
                              keep_stale=a.keep_stale, robots=not a.ignore_robots, procs=a.procs, log=self.log,
                              images=a.images, max_gb=a.max_gb)
        self.meta = {"terms": self.terms, "since": a.since, "cutoff": iso(self.cutoff), "depth": a.depth,
                     "sources": names, "variants": variants, "langs": ctx.langs}
        (self.out / "query.json").write_text(json.dumps({**self.meta, "argv": sys.argv[1:]}, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        self.log(f"inicio · términos={self.terms} · ventana={a.since} · {len(names)} fuentes · {len(variants)} variantes · depth={a.depth}")
        st = store.stats
        drop_undated = a.drop_undated

        def make_emit(name):
            ps = st["per_source"][name]

            async def emit(r: dict) -> bool:
                if ps["kept"] >= a.max_per_source:
                    return False
                url = r.get("url")
                if not url or not str(url).startswith("http"):
                    return True
                c = canon(url)
                d = parse_date(r.get("date"))
                if d and d < self.cutoff:
                    ps["old"] += 1
                    st["old_hits_dropped"] += 1
                    return True
                if not d:
                    if drop_undated:
                        return True
                    st["undated_hits"] += 1
                if r["source"] in LOOSE and not a.keep_all and \
                        relevance(f"{r['title']} {r['snippet']} {url}", tokens) <= 0:
                    ps["irrelevant"] += 1
                    st["irrelevant_hits"] = st.get("irrelevant_hits", 0) + 1
                    return True
                if c in seen_hits:
                    ps["dup"] += 1
                    st["dup_hits"] += 1
                    return True
                seen_hits.add(c)
                r["canon"] = c
                r["dated"] = bool(d)
                r["found"] = iso(now())
                store.write("results", r)
                ps["kept"] += 1
                st["hits"] += 1
                if crawler:
                    ex = r.get("extra") or {}
                    if not ex.get("no_fetch"):  # enlace directo a fichero: prioridad de recurso
                        from .extract import DOC_EXT, url_ext
                        is_res = url_ext(url) in DOC_EXT
                        crawler.seed(url, depth=1, score=140 if is_res else 100, hit=r, file=is_res)
                    for ln in ex.get("links") or []:
                        crawler.seed(ln, depth=1, score=60, parent=url, hit={"source": r["source"], "date": r["date"]})
                    if not a.no_files:
                        if ex.get("pdf"):
                            crawler.seed(ex["pdf"], depth=1, score=140, parent=url, hit=r, file=True)
                        files = ex.get("files") or []
                        if a.one_format and r["source"] in ONE_FORMAT:
                            files = best_formats(files)
                        for f in files:  # recursos descargables de las APIs de datasets/código
                            if a.max_file_mb and (f.get("size") or 0) > a.max_file_mb * 1048576:  # el catálogo ya dice que no cabe: ni se pide
                                st["files_too_big"] = st.get("files_too_big", 0) + 1
                                continue
                            crawler.seed(f.get("url"), depth=1, score=150, parent=url, hit=r, file=True, name=f.get("name"))
                return True
            return emit

        async def run_source(name):
            fn = SOURCES[name][0]
            t0 = time.monotonic()
            self.source_status[name] = "running"
            try:
                await asyncio.wait_for(fn(ctx, make_emit(name)), timeout=a.source_timeout)
                self.source_status[name] = f"ok ({time.monotonic() - t0:.0f}s)"
            except asyncio.TimeoutError:
                self.source_status[name] = f"timeout ({a.source_timeout}s)"
            except Exception as e:
                self.source_status[name] = f"error: {e!r}"[:160]
            kept = st["per_source"][name]["kept"]
            if kept == 0 and self.source_status[name].startswith("ok"):
                dup = st["per_source"][name].get("dup", 0)
                self.source_status[name] = (f"ok, 0 nuevos ({dup} ya vistos)" if dup else
                                            "ok, 0 resultados (posible bloqueo o nada reciente)")
            self.log(f"[{name}] {self.source_status[name]} · {kept} resultados")

        stop = asyncio.Event()

        async def progress():
            last = 0.0
            while not stop.is_set():
                await asyncio.sleep(1)
                extra = self._extra(fetch, crawler)
                if self.tty:
                    running = [k for k, v in self.source_status.items() if v == "running"]
                    print(f"\r\033[K⚡ hits {st['hits']} · páginas {st['pages']} · ficheros {st['files']} · cola "
                          f"{crawler.pending if crawler else 0} · {extra['mb']:.1f} MB · activas: {','.join(running)[:70]}",
                          end="", file=sys.stderr, flush=True)
                if time.monotonic() - last > 5:
                    store.flush_stats(extra)
                    last = time.monotonic()

        if crawler and a.resume:  # re-sembrar resultados ya cosechados que aún no se rastrearon
            n0 = crawler.pending
            for line in open(self.out / "results.jsonl", encoding="utf-8"):
                try:
                    h = json.loads(line)
                except ValueError:
                    continue
                if not (h.get("extra") or {}).get("no_fetch"):
                    crawler.seed(h["url"], depth=1, score=90, hit=h)
            self.log(f"reanudación: {crawler.pending - n0} resultados previos pendientes de rastrear")
        prog = asyncio.create_task(progress())
        ctask = asyncio.create_task(crawler.run()) if crawler else None
        try:
            await asyncio.gather(*(run_source(n) for n in names))
            self.log(f"cosecha terminada: {st['hits']} resultados únicos; rastreando…")
            if crawler:
                crawler.harvest_done.set()
                await ctask
        finally:  # también con Ctrl+C: se escriben CSV, stats y resumen con lo obtenido
            stop.set()
            if ctask and not ctask.done():
                ctask.cancel()
                st["status"] = "interrupted"
            prog.cancel()
            self.meta["source_status"] = self.source_status
            if crawler:
                st["domain_cap_skips"] = crawler.cap_skips
                crawler.pool.shutdown(wait=False, cancel_futures=True)
            store.finalize(self.meta, self._extra(fetch, crawler))
            await fetch.close()
        if self.tty:
            print(file=sys.stderr)
        self.log(f"FIN · {st['hits']} resultados · {st['pages']} páginas · {st['files']} ficheros → {self.out}")

    def _extra(self, fetch, crawler):
        degraded = {h: s.degraded for h, s in fetch.hosts.items() if s.degraded}
        return {"mb": fetch.bytes / 1e6, "queue": crawler.pending if crawler else 0,
                "requests": sum(s.requests for s in fetch.hosts.values()), "hosts": len(fetch.hosts),
                "degraded_hosts": dict(list(degraded.items())[:200]), "source_status": self.source_status,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}


async def run_crawl(a, out: Path):
    """Modo web entera: parte de unas URLs y recorre todo el dominio descargando páginas y ficheros."""
    from .util import domain_of
    urls = [u if u.startswith("http") else f"https://{u}" for u in a.urls]
    logf = open(out / "run.log", "a", encoding="utf-8", buffering=1)

    def log(m):
        logf.write(f"{time.strftime('%H:%M:%S')} {m}\n")

    fetch = Fetcher(a.concurrency, a.per_host, proxy=a.proxy)
    store = Store(out)
    crawler = Crawler(fetch, store, [], now() - parse_since("100y"), depth=a.depth, max_pages=a.max_pages,
                      per_domain=a.max_pages, workers=a.workers, download=not a.no_files, max_file_mb=a.max_file_mb,
                      robots=not a.ignore_robots, procs=a.procs, log=log, images=a.images,
                      site_domains=[domain_of(u) for u in urls], max_gb=a.max_gb)
    meta = {"terms": urls, "since": "-", "cutoff": None, "depth": a.depth, "sources": ["crawl"], "mode": "crawl",
            "source_status": {"crawl": "web entera"}}
    (out / "query.json").write_text(json.dumps({**meta, "argv": sys.argv[1:]}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"inicio rastreo web entera · {urls} · depth={a.depth} · máx {a.max_pages} páginas")
    for u in urls:
        crawler.seed(u, depth=1, score=100, hit={"source": "crawl", "url": u})
    crawler.harvest_done.set()
    stop = asyncio.Event()

    async def progress():
        while not stop.is_set():
            await asyncio.sleep(2)
            store.flush_stats({"mb": fetch.bytes / 1e6, "queue": crawler.pending, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "requests": sum(s.requests for s in fetch.hosts.values()), "source_status": meta["source_status"]})

    prog = asyncio.create_task(progress())
    try:
        await crawler.run()
    finally:
        stop.set()
        prog.cancel()
        crawler.pool.shutdown(wait=False, cancel_futures=True)
        store.finalize(meta, {"mb": fetch.bytes / 1e6, "queue": crawler.pending, "source_status": meta["source_status"],
                              "requests": sum(s.requests for s in fetch.hosts.values()),
                              "updated": time.strftime("%Y-%m-%dT%H:%M:%S")})
        await fetch.close()
        log(f"FIN · {store.stats['pages']} páginas · {store.stats['files']} ficheros → {out}")


def cmd_status(d):
    out = OUT_DIR
    if not d:
        runs = sorted((p for p in out.glob("*/stats.json")), key=lambda p: p.stat().st_mtime)
        if not runs:
            print("sin ejecuciones en output/")
            return
        p = runs[-1].parent
    else:
        p = Path(d)
    s = json.loads((p / "stats.json").read_text(encoding="utf-8"))
    if s["status"] == "running" and (p / "stats.json").stat().st_mtime < time.time() - 60:
        s["status"] = "running? (sin actualizar desde hace más de 60 s: el proceso pudo detenerse; usa --resume)"
    print(f"{p}\n estado: {s['status']} · actualizado {s.get('updated')}\n resultados {s['hits']} · páginas {s['pages']} · "
          f"ficheros {s['files']} · cola {s.get('queue')} · {s.get('mb', 0):.1f} MB · peticiones {s.get('requests')}")
    for k, v in sorted(s.get("per_source", {}).items(), key=lambda kv: -kv[1].get("kept", 0)):
        print(f"  {k:20} {v.get('kept', 0):6}  {s.get('source_status', {}).get(k, '')}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = parse_args(argv)
    if a.cmd == "sources":
        for k, (_, desc) in SOURCES.items():
            print(f"  {k:18} {desc}")
        return
    if a.cmd == "status":
        return cmd_status(a.dir)
    if a.cmd == "web":
        from .web import serve
        return serve(a.port, not a.no_browser)
    if a.cmd == "app":
        from .desktop import run
        return run(a.port)
    name = a.term if a.cmd == "search" else "web-" + a.urls[0].split("//")[-1]
    out = Path(a.resume or a.out or OUT_DIR / f"{slug(name, 50)}_{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    if a.bg:
        args = [x for x in (argv if argv is not None else sys.argv[1:]) if x != "--bg"]
        if not a.resume and not a.out:
            args += ["--out", str(out)]
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        with open(out / "console.log", "a", encoding="utf-8") as lf:
            p = subprocess.Popen([*self_cmd(), *args], cwd=workdir(), stdout=lf, stderr=lf,
                                 stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != "nt")
        print(f"🕷  en segundo plano · PID {p.pid}\n   salida: {out}\n   progreso: dm status \"{out}\"")
        return
    if a.depth >= 1 and os.name == "nt":
        import multiprocessing
        multiprocessing.freeze_support()
    if a.cmd == "crawl":
        asyncio.run(run_crawl(a, out))
    else:
        asyncio.run(Runner(a, out).main())
