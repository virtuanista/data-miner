"""Plataforma web local de DataMiner: lanzar búsquedas, ver el progreso en vivo, explorar y descargar resultados."""
from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import resources, media  # noqa: F401
from .sources import SOURCES
from .util import fold, slug

from .paths import OUT, self_cmd, workdir  # noqa: E402
STATIC = Path(__file__).resolve().parent / "static"
PROCS: dict[str, subprocess.Popen] = {}
_cache: dict[tuple, tuple[float, list]] = {}
_lock = threading.Lock()


def run_dir(rid: str) -> Path | None:
    p = (OUT / rid).resolve()
    return p if p.parent == OUT.resolve() and p.is_dir() else None


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    key = (str(path),)
    m = path.stat().st_mtime
    with _lock:
        c = _cache.get(key)
        if c and c[0] == m:
            return c[1]
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    with _lock:
        _cache[key] = (m, rows)
    return rows


def run_info(p: Path) -> dict:
    try:
        s = json.loads((p / "stats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {"status": "starting", "hits": 0, "pages": 0, "files": 0}
    try:
        q = json.loads((p / "query.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        q = {}
    proc = PROCS.get(p.name)
    alive = proc is not None and proc.poll() is None
    status = s.get("status", "starting")
    if status in ("running", "starting") and not alive:
        age = time.time() - (p / "stats.json").stat().st_mtime if (p / "stats.json").exists() else 999
        if age > 30 and proc is not None or (proc is None and age > 60):
            status = "stopped"
    return {"id": p.name, "status": status, "alive": alive, "terms": q.get("terms") or [p.name], "since": q.get("since"),
            "depth": q.get("depth"), "started": s.get("started"), "updated": s.get("updated"), "hits": s.get("hits", 0),
            "pages": s.get("pages", 0), "files": s.get("files", 0), "queue": s.get("queue", 0), "mb": s.get("mb", 0),
            "requests": s.get("requests", 0), "per_source": s.get("per_source", {}),
            "source_status": s.get("source_status", {}), "stats": {k: v for k, v in s.items() if isinstance(v, (int, float))},
            "sources": q.get("sources", []), "files_invalid": s.get("files_invalid", 0),
            "disk_limit": bool(s.get("disk_limit_reached"))}


def start_search(body: dict) -> str:
    term = (body.get("term") or "").strip()
    if not term:
        raise ValueError("falta el término")
    rid = f"{slug(term, 50)}_{time.strftime('%Y%m%d-%H%M%S')}"
    out = OUT / rid
    out.mkdir(parents=True, exist_ok=True)
    args = [*self_cmd(), "search", term, "--out", str(out),
            "--since", str(body.get("since") or "7d"), "--depth", str(int(body.get("depth", 1)))]
    for t in body.get("extra") or []:
        if t.strip():
            args += ["-t", t.strip()]
    if body.get("sources"):
        args += ["--sources", ",".join(body["sources"])]
    if body.get("langs"):
        args += ["--langs", body["langs"]]
    for sp in body.get("site_packs") or []:
        args += ["--site-pack", sp]
    for s in body.get("sites") or []:
        if s.strip():
            args += ["--site", s.strip()]
    for r in body.get("raw") or []:
        if r.strip():
            args += ["--raw", r.strip()]
    if body.get("queries") is not None:  # consultas ya compuestas en el constructor: se lanzan tal cual, sin expansión
        args.append("--minimal")
        for r in list(dict.fromkeys(q.strip() for q in body["queries"] if q.strip()))[:40]:
            args += ["--raw", r]
    if body.get("max_file_mb") is not None:
        args += ["--max-file-mb", str(float(body["max_file_mb"]))]
    if body.get("one_format"):
        args.append("--one-format")
    for k, flag in (("max_pages", "--max-pages"), ("per_domain", "--per-domain"), ("pages", "--pages")):
        if body.get(k):
            args += [flag, str(int(body[k]))]
    if body.get("no_files"):
        args.append("--no-files")
    if body.get("keep_all"):
        args.append("--keep-all")
    if body.get("max_gb") is not None:
        args += ["--max-gb", str(float(body["max_gb"]))]
    if body.get("images"):
        args.append("--images")
    if body.get("max_gb") is not None:
        args += ["--max-gb", str(float(body["max_gb"]))]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    lf = open(out / "console.log", "a", encoding="utf-8")
    PROCS[rid] = subprocess.Popen(args, cwd=workdir(), stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, creationflags=flags)
    return rid


def start_crawl(body: dict) -> str:
    urls = [u.strip() for u in body.get("urls") or [] if u.strip()]
    if not urls:
        raise ValueError("faltan URLs")
    rid = f"{slug('web-' + urls[0].split('//')[-1], 50)}_{time.strftime('%Y%m%d-%H%M%S')}"
    out = OUT / rid
    out.mkdir(parents=True, exist_ok=True)
    args = [*self_cmd(), "crawl", *urls, "--out", str(out),
            "--depth", str(int(body.get("depth") or 6)), "--max-pages", str(int(body.get("max_pages") or 5000))]
    if body.get("images"):
        args.append("--images")
    if body.get("no_files"):
        args.append("--no-files")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    lf = open(out / "console.log", "a", encoding="utf-8")
    PROCS[rid] = subprocess.Popen(args, cwd=workdir(), stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, creationflags=flags)
    return rid


def filt(rows, qs, fields):
    q = fold(qs.get("q", [""])[0])
    src = qs.get("source", [""])[0]
    kind = qs.get("kind", [""])[0]
    counts = {}
    for r in rows:
        k = r.get("kind")
        if k:
            counts[k] = counts.get(k, 0) + 1
    if src:
        rows = [r for r in rows if r.get("source") == src]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if q:
        rows = [r for r in rows if q in fold(" ".join(str(r.get(f) or "") for f in fields))]
    sort = qs.get("sort", ["date"])[0]
    if sort == "size":
        rows = sorted(rows, key=lambda r: -(r.get("size") or 0))
    elif sort == "relevance":
        rows = sorted(rows, key=lambda r: -(r.get("relevance") or 0))
    else:
        rows = sorted(rows, key=lambda r: r.get("date") or r.get("fetched") or "", reverse=True)
    dom = qs.get("domain", [""])[0]
    if dom:
        from .util import domain_of
        rows = [r for r in rows if domain_of(r.get("url") or "") == dom]
    off = int(qs.get("offset", ["0"])[0])
    lim = min(int(qs.get("limit", ["100"])[0]), 1000)
    return {"total": len(rows), "items": rows[off:off + lim], "kinds": counts,
            "bytes": sum(r.get("size") or 0 for r in rows)}


from .hub import BUILD, INTERVALS, Platform  # noqa: E402

PLAT = Platform(OUT, read_jsonl, run_info, start_search, PROCS)
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon", ".html": "text/html; charset=utf-8",
                ".woff2": "font/woff2"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _platform_get(self, parts, qs):
        """Rutas de la plataforma (panel, catálogo, búsqueda global, vigilancias, ajustes). None si no es suya."""
        if parts[0] == "static" and len(parts) >= 2:
            f = (STATIC / "/".join(parts[1:])).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file() or f.suffix.lower() not in STATIC_TYPES:
                return self._send(404, {"error": "no existe"})
            return self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix.lower()])
        if parts[0] != "api":
            return None
        p = parts[1:]
        if p == ["overview"]:
            return self._send(200, PLAT.overview())
        if p == ["catalog"]:
            return self._send(200, PLAT.sources_catalog(SOURCES))
        if p == ["find"]:
            return self._send(200, PLAT.find(qs.get("q", [""])[0], qs.get("type", [""])[0]))
        if p == ["activity"]:
            return self._send(200, PLAT.activity())
        if p == ["graph"]:
            by = qs.get("by", ["topic"])[0]
            rd = run_dir(qs.get("run", [""])[0]) if qs.get("run", [""])[0] else None
            kind = "files" if qs.get("kind", [""])[0] == "files" else "results"
            return self._send(200, PLAT.graph(rd.name if rd else "", by if by in ("topic", "domain", "source", "type") else "topic",
                                              kind=kind))
        if p == ["templates"]:
            return self._send(200, PLAT.templates())
        if p == ["builder"]:
            return self._send(200, PLAT.builder(SOURCES))
        if p == ["library"]:
            return self._send(200, filt(PLAT.library_rows(), qs, ("path", "title", "url", "source", "run")))
        if p == ["ping"]:
            return self._send(200, {"build": BUILD, "sync": {"configured": bool(PLAT.settings().get("sync_url")), "connected": False}})
        if p == ["system"]:
            return self._send(200, PLAT.system())
        if p == ["settings"]:
            return self._send(200, PLAT.settings())
        if p == ["watches"]:
            return self._send(200, {"items": PLAT.watches(), "intervals": INTERVALS})
        if len(p) == 3 and p[0] == "watch" and p[2] == "news":
            return self._send(200, PLAT.watch_news(p[1]))
        if len(p) == 3 and p[0] == "run" and p[2] == "answer":
            d = run_dir(p[1])
            return self._send(200, PLAT.answer(d, qs.get("q", [""])[0])) if d else self._send(404, {"error": "no existe"})
        if len(p) == 3 and p[0] == "run" and p[2] == "insights":
            d = run_dir(p[1])
            return self._send(200, PLAT.insights(d)) if d else self._send(404, {"error": "no existe"})
        return None

    def _platform_post(self, parts, body):
        if parts == ["api", "settings"]:
            return self._send(200, PLAT.save_settings(body))
        if parts == ["api", "watches"]:
            return self._send(200, PLAT.save_watch(body))
        if parts == ["api", "templates"]:
            return self._send(200, PLAT.save_template(body))
        if len(parts) == 4 and parts[:2] == ["api", "template"] and parts[3] == "delete":
            return self._send(200, PLAT.delete_template(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "watch"] and parts[3] in ("delete", "toggle", "run"):
            return self._send(200, PLAT.watch_action(parts[2], parts[3]))
        if parts == ["api", "open-output"]:
            if os.name == "nt":
                subprocess.Popen(["explorer", str(OUT)])
            return self._send(200, {"ok": True})
        return None

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
                                                     if not isinstance(body, str) else body.encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        return True

    def _local(self) -> bool:
        """Solo atiende a la propia interfaz: rechaza peticiones que lleguen con otro nombre de host (DNS rebinding)
        o lanzadas desde otra web abierta en el navegador (Origin ajeno o Sec-Fetch-Site cross-site)."""
        ok = ("127.0.0.1", "localhost", "[::1]")
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        if host not in ok or (origin and (urlsplit(origin).hostname or "") not in ("127.0.0.1", "localhost", "::1")) \
                or (self.headers.get("Sec-Fetch-Site") == "cross-site" and self.path.startswith("/api")):
            self._send(403, {"error": "petición ajena a DataMiner"})
            return False
        return True

    def do_GET(self):
        if not self._local():
            return
        u = urlsplit(self.path)
        qs = parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        try:
            if not parts:
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if self._platform_get(parts, qs):
                return
            if parts == ["api", "sources"]:
                return self._send(200, [{"id": k, "desc": v[1]} for k, v in SOURCES.items()])
            if parts == ["api", "runs"]:
                OUT.mkdir(parents=True, exist_ok=True)
                runs = sorted((p for p in OUT.iterdir() if p.is_dir() and not p.name.startswith("_")),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                return self._send(200, [run_info(p) for p in runs[:100]])
            if len(parts) >= 3 and parts[:2] == ["api", "run"]:
                p = run_dir(parts[2])
                if not p:
                    return self._send(404, {"error": "no existe"})
                what = parts[3] if len(parts) > 3 else "info"
                if what == "info":
                    return self._send(200, run_info(p))
                if what == "results":
                    return self._send(200, filt(read_jsonl(p / "results.jsonl"), qs, ("title", "snippet", "url")))
                if what == "pages":
                    return self._send(200, filt(read_jsonl(p / "pages.jsonl"), qs, ("title", "description", "url", "sitename")))
                if what == "files":
                    return self._send(200, filt(read_jsonl(p / "files.jsonl"), qs, ("url", "path", "title", "content_type", "source")))
                if what in ("head", "zip"):
                    rel = qs.get("path", [""])[0]
                    f = (p / rel).resolve()
                    if p not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no existe"})
                    if what == "zip":  # solo listado: nunca se extrae nada
                        import zipfile
                        try:
                            with zipfile.ZipFile(f) as z:
                                items = [{"name": i.filename, "size": i.file_size} for i in z.infolist()[:800]]
                                return self._send(200, {"items": items, "count": len(z.infolist())})
                        except zipfile.BadZipFile:
                            return self._send(200, {"items": [], "count": 0, "error": "no es un zip válido"})
                    n = min(int(qs.get("bytes", ["200000"])[0]), 2_000_000)
                    with open(f, "rb") as fh:
                        data = fh.read(n)
                    return self._send(200, {"text": data.decode("utf-8", "replace"), "size": f.stat().st_size,
                                            "truncated": f.stat().st_size > n})
                if what == "log":
                    t = (p / "run.log").read_text(encoding="utf-8")[-20000:] if (p / "run.log").exists() else ""
                    return self._send(200, {"log": t})
                if what == "file":
                    rel = qs.get("path", [""])[0]
                    f = (p / rel).resolve()
                    if p not in f.parents or not f.is_file():
                        return self._send(404, {"error": "no existe"})
                    ct = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if f.suffix.lower() in (".md", ".jsonl", ".log", ".json", ".txt", ".csv", ".tsv", ".py", ".sh", ".yaml", ".yml", ".xml", ".sql"):
                        ct = "text/plain; charset=utf-8"
                    from urllib.parse import quote as _q
                    dl = {"Content-Disposition": f"attachment; filename*=UTF-8''{_q(f.name)}"} if qs.get("dl") else {}
                    if f.suffix.lower() in (".html", ".htm", ".svg", ".xhtml"):  # nunca renderizar HTML/SVG descargado
                        ct = "text/plain; charset=utf-8"
                    dl["X-Content-Type-Options"] = "nosniff"
                    if f.suffix.lower() != ".pdf":  # el visor PDF de Chrome no funciona en un documento sandbox
                        dl["Content-Security-Policy"] = "sandbox"
                    return self._send(200, f.read_bytes(), ct, dl)
            return self._send(404, {"error": "ruta"})
        except Exception as e:
            return self._send(500, {"error": repr(e)})

    def do_POST(self):
        if not self._local():
            return
        parts = [p for p in urlsplit(self.path).path.split("/") if p]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        try:
            if self._platform_post(parts, body):
                return
            if parts == ["api", "search"]:
                return self._send(200, {"id": start_search(body)})
            if parts == ["api", "crawl"]:
                return self._send(200, {"id": start_crawl(body)})
            if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "stop":
                proc = PROCS.get(parts[2])
                if proc and proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                    else:
                        proc.terminate()
                return self._send(200, {"ok": True})
            if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "delete":
                import shutil
                p = run_dir(parts[2])
                if not p:
                    return self._send(404, {"error": "no existe"})
                proc = PROCS.pop(p.name, None)
                if proc and proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                    else:
                        proc.terminate()
                    time.sleep(1)
                shutil.rmtree(p, ignore_errors=True)
                with _lock:
                    for k in [k for k in _cache if str(p) in k[0]]:
                        _cache.pop(k, None)
                return self._send(200, {"ok": not p.exists()})
            if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "open":
                p = run_dir(parts[2])
                rel = body.get("path") or ""
                target = (p / rel).resolve() if rel else p
                if p and (target == p or p in target.parents) and target.exists():
                    if os.name == "nt":  # mostrar en el Explorador (nunca abrir/ejecutar el fichero)
                        subprocess.Popen(["explorer", "/select,", str(target)] if target.is_file() else ["explorer", str(target)])
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "no existe"})
            if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "resume":
                p = run_dir(parts[2])
                q = json.loads((p / "query.json").read_text(encoding="utf-8"))
                argv = q.get("argv", [])
                args = [*self_cmd(), *[a for a in argv if a != "--bg"]]
                if "--resume" not in argv:
                    args += ["--resume", str(p)]
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                lf = open(p / "console.log", "a", encoding="utf-8")
                PROCS[p.name] = subprocess.Popen(args, cwd=workdir(), stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                                                 creationflags=flags)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "ruta"})
        except Exception as e:
            return self._send(400, {"error": str(e)})


class Server(ThreadingHTTPServer):
    """En Windows SO_REUSEADDR deja a dos servidores escuchar el MISMO puerto (peticiones repartidas al azar):
    exigimos uso exclusivo para que un segundo lanzamiento detecte el primero."""
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        import socket
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _same_build(port: int) -> bool:
    """True si en ese puerto ya escucha un DataMiner de esta misma versión (se puede reutilizar)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=3) as r:
            return json.loads(r.read()).get("build") == BUILD
    except Exception:
        return False


def bind(port: int = 8787):
    """Devuelve (servidor | None, url). None = ya hay una instancia de esta versión en marcha y se reutiliza.
    Si el puerto lo ocupa otra cosa (o una versión antigua), se prueba con los siguientes."""
    OUT.mkdir(parents=True, exist_ok=True)
    for p in range(port, port + 20):
        try:
            return Server(("127.0.0.1", p), H), f"http://127.0.0.1:{p}/"
        except OSError:
            if _same_build(p):
                return None, f"http://127.0.0.1:{p}/"
    raise OSError("no hay puertos libres para DataMiner")


def serve(port: int = 8787, open_browser: bool = True):
    srv, url = bind(port)
    if srv is None:  # ya hay un DataMiner abierto: lo reutilizamos en vez de fallar
        print(f"DataMiner ya está en marcha en {url}: abriendo el navegador")
        if open_browser:
            webbrowser.open(url)
        return
    PLAT.start_scheduler()
    print(f"DataMiner en {url}  (Ctrl+C para salir)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
