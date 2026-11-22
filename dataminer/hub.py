"""Capa de plataforma: panel global, catálogo de fuentes, búsqueda entre recolecciones, vigilancias programadas y ajustes.

Todo el estado propio vive en output/_platform/ (JSON). Las recolecciones siguen siendo carpetas de output/.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .util import domain_of, fold

BUILD = __version__

GROUPS = {
    "Web": ["yahoo_web", "ddg_meta"],
    "Noticias": ["google_news", "bing_news", "gdelt"],
    "Social y foros": ["hackernews", "mastodon", "bluesky", "stackexchange", "youtube", "devto"],
    "Código y paquetes": ["github", "gitlab", "huggingface", "npm", "pypi", "github_releases", "code_archives"],
    "Ciencia": ["arxiv", "openalex", "crossref", "hal", "oai_pmh", "openaire", "europepmc", "doaj", "wikipedia"],
    "Datos abiertos": ["zenodo", "figshare", "socrata", "eu_open_data", "uk_open_data", "hf_dataset_files"],
    "Libros, vídeo y audio": ["gutenberg", "openlibrary", "archive_books", "archive_video", "archive_audio", "commons_media",
                              "librivox", "nasa_media", "tvmaze"],
    "Archivos": ["internet_archive", "archive_files", "archive_software"],
}
# «Qué buscas»: cada preset elige fuentes y, para los buscadores web, los filetype: que añade a las consultas
PRESETS = [
    {"id": "docs", "label": "Documentos", "desc": "PDF, Word y presentaciones", "icon": "file", "filetypes": ["pdf", "docx", "pptx"],
     "sources": ["yahoo_web", "hal", "zenodo", "openaire", "arxiv", "oai_pmh", "figshare", "europepmc"]},
    {"id": "data", "label": "Datos", "desc": "Hojas de cálculo, CSV y datasets", "icon": "sources", "filetypes": ["xlsx", "csv"],
     "sources": ["yahoo_web", "zenodo", "figshare", "eu_open_data", "uk_open_data", "socrata", "hf_dataset_files", "openaire"]},
    {"id": "archives", "label": "Comprimidos y software", "desc": "ZIP, instaladores e imágenes de disco", "icon": "download",
     "filetypes": ["zip"], "sources": ["yahoo_web", "github_releases", "code_archives", "archive_software", "pypi", "zenodo",
                                       "archive_files"]},
    {"id": "code", "label": "Código y modelos", "desc": "Repositorios, paquetes y modelos", "icon": "depth", "filetypes": [],
     "sources": ["github", "gitlab", "huggingface", "npm", "pypi", "code_archives", "stackexchange"]},
    {"id": "books", "label": "Libros", "desc": "Open Library e Internet Archive", "icon": "page", "filetypes": ["pdf"],
     "sources": ["openlibrary", "archive_books", "yahoo_web"], "catalog": True},
    {"id": "video", "label": "Vídeo", "desc": "Archivos de vídeo y fichas de series", "icon": "play", "filetypes": [],
     "sources": ["archive_video", "commons_media", "nasa_media", "youtube", "tvmaze"], "catalog": True},
    {"id": "audio", "label": "Audio", "desc": "Música y audiolibros archivados", "icon": "activity", "filetypes": [],
     "sources": ["archive_audio", "librivox", "commons_media", "nasa_media"], "catalog": True},
    {"id": "news", "label": "Web y noticias", "desc": "Páginas, prensa y enciclopedia", "icon": "globe", "filetypes": [],
     "sources": ["yahoo_web", "google_news", "bing_news", "gdelt", "wikipedia"]},
    {"id": "social", "label": "Comunidades", "desc": "Foros, redes y preguntas técnicas", "icon": "hit", "filetypes": [],
     "sources": ["hackernews", "mastodon", "bluesky", "stackexchange", "devto", "youtube"]},
]
# Operador filetype: en el buscador web. «web» refleja la sonda del 22-11-2026 contra Yahoo (índice de Bing):
# ok = devolvió ficheros de ese tipo · partial = devolvió otros empaquetados · untested = sin comprobar · no = 0 resultados
FILETYPES = [
    {"group": "Documentos", "items": [["pdf", "ok"], ["docx", "ok"], ["pptx", "ok"], ["doc", "untested"], ["ppt", "untested"],
                                      ["odt", "untested"], ["rtf", "untested"], ["txt", "untested"]]},
    {"group": "Datos", "items": [["xlsx", "ok"], ["csv", "untested"], ["xls", "untested"], ["json", "no"], ["xml", "untested"]]},
    {"group": "Comprimidos", "items": [["zip", "partial"], ["rar", "untested"], ["7z", "untested"]]},
    {"group": "Libros y multimedia", "items": [["epub", "no"], ["mp4", "no"], ["mp3", "untested"]]},
]
SITE_PACKS = {
    "gdocs": {"label": "Google Docs y Drive públicos", "sites": ["docs.google.com", "drive.google.com"]},
    "docs": {"label": "Repositorios de documentos", "sites": ["slideshare.net", "scribd.com", "researchgate.net", "academia.edu"]},
    "dev": {"label": "Desarrollo", "sites": ["github.com", "stackoverflow.com", "gitlab.com", "dev.to"]},
    "social": {"label": "Redes y blogs", "sites": ["reddit.com", "x.com", "linkedin.com", "medium.com", "substack.com"]},
    "gov": {"label": "Administración y universidad", "sites": ["gov", "gob.es", "europa.eu", "edu"]},
    "archive": {"label": "Archivos abiertos", "sites": ["archive.org", "commons.wikimedia.org", "gutenberg.org"]},
}
GROUP_OF = {s: g for g, ss in GROUPS.items() for s in ss}
RESOURCE_SOURCES = ["yahoo_web", "ddg_meta", "zenodo", "figshare", "eu_open_data", "uk_open_data", "socrata", "archive_files",
                    "hf_dataset_files", "github_releases", "code_archives", "openaire", "hal", "oai_pmh", "pypi", "arxiv",
                    "openalex", "github", "huggingface", "gitlab", "npm"]
METHOD = {"yahoo_web": "HTML", "ddg_meta": "Metabuscador", "google_news": "RSS", "bing_news": "RSS"}

DEFAULT_SETTINGS = {"since": "7d", "depth": 1, "langs": "es-ES,en-US", "max_pages": 3000, "per_domain": 40, "max_gb": 5.0,
                    "scope": "res", "images": False, "sync_url": ""}
INTERVALS = {60: "Cada hora", 360: "Cada 6 horas", 720: "Cada 12 horas", 1440: "Cada día", 10080: "Cada semana"}

import re as _re

_TOK = _re.compile(r"[a-z0-9]{3,}")
_STOP = set("""
the and for with that this from are was were you your yours not but have has had can could will would should shall may might must
all any its our ours out new how what when where who whom why which while more most less least than then them they their theirs
there here into onto over under about above below also been being one two three four five first second last next use used uses
using via per get gets got getting make makes made making like likes just only some many much very really still even ever never
always often now today yesterday tomorrow time times year years month months week weeks day days hour hours minute minutes ago
want wants wanted need needs needed know known knows think thinks thought look looks looking work works working worked way ways
thing things something anything nothing everything someone anyone everyone people person good best better great well back down
off own same other others another each every both few several such these those does doing done did don doesn didn isn aren wasn
won can't cannot let lets say says said see sees seen seeing take takes took taken come comes came going goes gone give gives
given find finds found show shows shown ask asks asked tell tells told try tries tried keep keeps kept put puts run runs running
set sets add adds added start starts started help helps helped part parts lot lots bit kind sort actually probably maybe simply
please thanks thank yes yeah sure okay right left long short high low big small old early late full free real true false
because since until unless though although however therefore thus between among within without against during before after
through across around along upon toward towards again once twice already yet soon later ago per via etc com www http https org net
html htm php pdf www2 index page pages post posts comment comments reply replies thread read reading reads write writes written
video videos watch watching episode channel official news update updates updated latest release released version guide tutorial
introduction intro overview review reviews blog article articles story stories show tell ask hn points point
que los las del una uno unos unas por para con sin sobre como mas pero sus este esta estos estas ese esa esos esas eso esto son
fue fueron ser sido siendo estar esta estan estaba han hay haber habia muy ya entre desde hasta cuando donde quien quienes cual
cuales tambien todo toda todos todas otro otra otros otras cada solo solamente asi puede pueden podria hace hacen hacer hecho
segun tras ante bajo les nos mis tus nuestro nuestra vuestro ellos ellas usted ustedes tiene tienen tener tenia aqui alli ahora
hoy ayer manana siempre nunca tanto tan poco mucho muchos muchas algo alguien nada nadie mismo misma bien mal mejor peor nuevo
nueva nuevos nuevas gran grande primer primera ultimo ultima parte forma manera vez veces ano anos dia dias mes meses semana
porque aunque mientras durante contra hacia mediante ademas embargo pues entonces luego despues antes como cuanto
der die das und ist von mit auf den dem ein eine nicht les des est pour dans avec sur par une aux que qui pas plus
jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october november
december enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre""".split())
_lock = threading.RLock()
_ov_cache: dict = {}


class Platform:
    def __init__(self, out: Path, read_jsonl, run_info, start_search, procs):
        self.out = out
        self.dir = out / "_platform"
        self.read_jsonl = read_jsonl
        self.run_info = run_info
        self.start_search = start_search
        self.procs = procs
        self._sched: threading.Thread | None = None

    # ---------- almacenamiento ----------
    def _load(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save(self, name: str, data):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.dir / name)

    def runs(self) -> list[Path]:
        self.out.mkdir(parents=True, exist_ok=True)
        return sorted((p for p in self.out.iterdir() if p.is_dir() and not p.name.startswith("_")),
                      key=lambda p: p.stat().st_mtime, reverse=True)

    # ---------- ajustes y sistema ----------
    def settings(self) -> dict:
        return {**DEFAULT_SETTINGS, **self._load("settings.json", {})}

    def save_settings(self, body: dict) -> dict:
        cur = self.settings()
        for k, default in DEFAULT_SETTINGS.items():
            if k in body:
                v = body[k]
                try:
                    cur[k] = type(default)(v) if not isinstance(default, bool) else bool(v)
                except (TypeError, ValueError):
                    pass
        with _lock:
            self._save("settings.json", cur)
        return cur

    def install_id(self) -> str:
        with _lock:
            d = self._load("install.json", {})
            if not d.get("id"):
                d = {"id": uuid.uuid4().hex, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
                self._save("install.json", d)
            return d["id"]

    def system(self) -> dict:
        import sys
        runs = self.runs()
        size = 0
        for p in runs:
            for root, _d, files in os.walk(p):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        free = None
        try:
            import shutil
            free = shutil.disk_usage(self.out).free
        except OSError:
            pass
        return {"build": BUILD, "engine": __version__, "python": sys.version.split()[0], "output": str(self.out),
                "runs": len(runs), "disk_bytes": size, "disk_free": free, "install_id": self.install_id(),
                "sync": {"configured": bool(self.settings().get("sync_url")), "connected": False},
                "scheduler": bool(self._sched and self._sched.is_alive())}

    # ---------- panel ----------
    def overview(self) -> dict:
        runs = self.runs()[:200]
        key = tuple((p.name, (p / "stats.json").stat().st_mtime if (p / "stats.json").exists() else 0) for p in runs)
        with _lock:
            c = _ov_cache.get("ov")
            if c and c[0] == key and time.time() - c[2] < 20:
                return c[1]
        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        by_day = {d: {"hits": 0, "files": 0, "pages": 0} for d in days}
        doms, kinds, kind_bytes = Counter(), Counter(), Counter()
        src: dict[str, dict] = {}
        tot = {"hits": 0, "pages": 0, "files": 0, "bytes": 0, "requests": 0, "mb": 0.0}
        infos = []
        for p in runs:
            info = self.run_info(p)
            infos.append(info)
            tot["requests"] += info.get("requests") or 0
            tot["mb"] += info.get("mb") or 0
            hits = self.read_jsonl(p / "results.jsonl")
            pages = self.read_jsonl(p / "pages.jsonl")
            files = self.read_jsonl(p / "files.jsonl")
            tot["hits"] += len(hits)
            tot["pages"] += len(pages)
            tot["files"] += len(files)
            for h in hits:
                d = (h.get("found") or "")[:10]
                if d in by_day:
                    by_day[d]["hits"] += 1
                dm = domain_of(h.get("url") or "")
                if dm:
                    doms[dm] += 1
            for pg in pages:
                d = (pg.get("fetched") or "")[:10]
                if d in by_day:
                    by_day[d]["pages"] += 1
            for f in files:
                d = (f.get("fetched") or "")[:10]
                if d in by_day:
                    by_day[d]["files"] += 1
                k = f.get("kind") or "otro"
                kinds[k] += 1
                kind_bytes[k] += f.get("size") or 0
                tot["bytes"] += f.get("size") or 0
            ss = info.get("source_status") or {}
            ps = info.get("per_source") or {}
            for s in set(ss) | set(ps):
                a = src.setdefault(s, {"id": s, "runs": 0, "kept": 0, "dup": 0, "irrelevant": 0, "old": 0, "ok": 0, "empty": 0,
                                       "error": 0, "last": "", "last_run": ""})
                v = ps.get(s) or {}
                a["runs"] += 1
                for k in ("kept", "dup", "irrelevant", "old"):
                    a[k] += v.get(k, 0)
                st = ss.get(s, "")
                cls = classify(st, v.get("kept", 0))
                if cls in ("ok", "empty", "error"):
                    a[cls] += 1
                if not a["last_run"]:  # runs van de más reciente a más antigua
                    a["last"], a["last_run"], a["last_class"] = st, info["id"], cls
        res = {"totals": tot, "runs": len(runs), "active": [i for i in infos if i.get("alive")],
               "recent": infos[:8], "by_day": [{"day": d, **v} for d, v in by_day.items()],
               "domains": [{"domain": d, "n": n} for d, n in doms.most_common(10)],
               "kinds": [{"kind": k, "n": n, "bytes": kind_bytes[k]} for k, n in kinds.most_common()],
               "sources": sorted(src.values(), key=lambda a: -a["kept"]),
               "status": dict(Counter(i["status"] for i in infos))}
        with _lock:
            _ov_cache["ov"] = (key, res, time.time())
        return res

    def sources_catalog(self, registry: dict) -> list:
        agg = {a["id"]: a for a in self.overview()["sources"]}
        out = []
        for sid, v in registry.items():
            a = agg.get(sid, {})
            out.append({"id": sid, "desc": v[1], "group": GROUP_OF.get(sid, "Otros"), "resource": sid in RESOURCE_SOURCES,
                        "method": METHOD.get(sid, "API"), "runs": a.get("runs", 0), "kept": a.get("kept", 0),
                        "dup": a.get("dup", 0), "irrelevant": a.get("irrelevant", 0), "old": a.get("old", 0),
                        "ok": a.get("ok", 0), "empty": a.get("empty", 0), "error": a.get("error", 0),
                        "last": a.get("last", ""), "last_class": a.get("last_class", "unused"), "last_run": a.get("last_run", "")})
        return out

    # ---------- análisis de una recolección ----------
    def insights(self, p: Path) -> dict:
        hits = self.read_jsonl(p / "results.jsonl")
        pages = self.read_jsonl(p / "pages.jsonl")
        files = self.read_jsonl(p / "files.jsonl")
        info = self.run_info(p)
        day = Counter((h.get("date") or "")[:10] for h in hits if h.get("date") and h.get("dated", True))
        days = sorted(d for d in day if len(d) == 10)[-30:]
        doms = Counter(domain_of(h.get("url") or "") for h in hits)
        doms.pop("", None)
        kinds, kb = Counter(), Counter()
        for f in files:
            kinds[f.get("kind") or "otro"] += 1
            kb[f.get("kind") or "otro"] += f.get("size") or 0
        ps = info.get("per_source") or {}
        raw = sum(sum(v.get(k, 0) for k in ("kept", "dup", "old", "irrelevant")) for v in ps.values())
        st = info.get("stats") or {}
        return {"by_day": [{"day": d, "n": day[d]} for d in days],
                "undated": sum(1 for h in hits if not h.get("dated", True) or not h.get("date")),
                "domains": [{"domain": d, "n": n} for d, n in doms.most_common(10)], "n_domains": len(doms),
                "kinds": [{"kind": k, "n": n, "bytes": kb[k]} for k, n in kinds.most_common()],
                "by_source": sorted(({"source": s, "n": v.get("kept", 0)} for s, v in ps.items() if v.get("kept")),
                                    key=lambda x: -x["n"])[:10],
                "langs": [{"lang": k, "n": n} for k, n in Counter((pg.get("lang") or "?") for pg in pages).most_common(6)],
                "funnel": {"raw": max(raw, len(hits)), "kept": len(hits), "pages": len(pages), "files": len(files)},
                "discard": {"Duplicados": st.get("dup_hits", 0), "Fuera de la ventana": st.get("old_hits_dropped", 0),
                            "Resultados irrelevantes": st.get("irrelevant_hits", 0),
                            "Páginas irrelevantes": st.get("pages_irrelevant", 0), "Páginas duplicadas": st.get("pages_dup", 0),
                            "Páginas antiguas": st.get("pages_stale", 0), "Páginas fallidas": st.get("pages_failed", 0),
                            "Bloqueadas por robots.txt": st.get("robots_blocked", 0),
                            "Descargas no válidas": st.get("files_invalid", 0),
                            "Ficheros por encima del tamaño máximo": st.get("files_too_big", 0)},
                "top_pages": sorted(({"title": pg.get("title"), "url": pg.get("url"), "relevance": pg.get("relevance") or 0}
                                     for pg in pages), key=lambda x: -x["relevance"])[:6]}

    # ---------- constructor de búsquedas y biblioteca ----------
    def builder(self, registry: dict) -> dict:
        known = set(registry)
        return {"presets": [{**p, "sources": [s for s in p["sources"] if s in known]} for p in PRESETS],
                "filetypes": FILETYPES, "site_packs": SITE_PACKS}

    def library_rows(self) -> list:
        key = tuple((p.name, (p / "files.jsonl").stat().st_mtime if (p / "files.jsonl").exists() else 0)
                    for p in self.runs()[:200])
        with _lock:
            c = _ov_cache.get("lib")
            if c and c[0] == key:
                return c[1]
        rows = []
        for name, m in key:
            if m:
                rows += [{**f, "run": name} for f in self.read_jsonl(self.out / name / "files.jsonl")]
        with _lock:
            _ov_cache["lib"] = (key, rows)
        return rows

    # ---------- mapa: agrupación de hallazgos o ficheros para la vista de navegación ----------
    def graph(self, run: str = "", by: str = "topic", limit: int = 4000, kind: str = "results") -> dict:
        """Elementos agrupados en dos niveles (grupo → subgrupo → elemento), enlaces entre grupos y aristas de
        parecido entre elementos. kind = results (hallazgos) | files (ficheros descargados).
        by = topic (k-medias sobre TF-IDF del título y el resumen) | domain | source | type (solo ficheros)."""
        import math
        files = kind == "files"
        name = "files" if files else "results"
        dirs = [self.out / run] if run and (self.out / run).is_dir() else self.runs()[:60]
        key = (kind, by, limit, tuple((d.name, (d / f"{name}.jsonl").stat().st_mtime if (d / f"{name}.jsonl").exists() else 0)
                                      for d in dirs))
        with _lock:
            c = _ov_cache.get("graph")
            if c and c[0] == key:
                return c[1]
        rows, seen, qterms = [], set(), set()
        for d in dirs:
            try:
                for q in json.loads((d / "query.json").read_text(encoding="utf-8")).get("terms", []):
                    qterms.update(_TOK.findall(fold(q)))
            except (OSError, ValueError):
                pass
            for r in self.read_jsonl(d / f"{name}.jsonl"):
                if files:
                    fn = (r.get("path") or "").rsplit("/", 1)[-1]
                    r = {"title": r.get("title") or fn, "snippet": fn if r.get("title") else "", "url": r.get("url"),
                         "source": r.get("source"), "date": r.get("date") or r.get("fetched"), "found": r.get("fetched"),
                         "canon": r.get("path"), "_f": r}
                k = (d.name, r.get("canon")) if files else (r.get("canon") or r.get("url"))
                if k and k not in seen:
                    seen.add(k)
                    rows.append((d.name, r))
        rows.sort(key=lambda x: x[1].get("found") or "", reverse=True)
        total, rows = len(rows), rows[:limit]
        n = len(rows)
        doms = [domain_of(r.get("url") or "") or "sin dominio" for _, r in rows]
        X, vocab, forms = self._vectors(rows, doms, qterms)
        show = lambda term: " ".join(forms[w].most_common(1)[0][0] if w in forms else w for w in term.split())  # noqa: E731

        if by == "domain":
            top = [d for d, _ in Counter(doms).most_common(16)]
            assign = [d if d in top else "otros dominios" for d in doms]
            sub = [(r.get("source") or "?") for _, r in rows]
        elif by == "source":
            assign = [(r.get("source") or "?") for _, r in rows]
            sub = doms
        elif by == "type" and files:
            assign = [(r["_f"].get("kind") or "otro") for _, r in rows]
            sub = ["." + (r["_f"].get("ext") or "?") for _, r in rows]
        elif X is None or n < 8:
            assign, sub = ["otros temas"] * n, doms
        else:
            import numpy as np
            df = (X > 0).sum(axis=0)
            order = np.argsort(-(df * np.log(1 + n / np.maximum(df, 1)) * np.array([1.8 if " " in v else 1.0 for v in vocab])))
            k = max(5, min(20, int(math.sqrt(n) / 1.6) + 3))
            seeds, member = [], []
            for j in order[:300]:                      # semillas: términos frecuentes cuyos documentos apenas se pisan
                if len(seeds) >= k:
                    break
                m = X[:, j] > 0
                if m.sum() < max(3, n // 250):
                    continue
                if all((m & o).sum() / max(1, (m | o).sum()) < 0.35 and not (set(vocab[j].split()) & set(vocab[s].split()))
                       for s, o in zip(seeds, member)):
                    seeds.append(j)
                    member.append(m)
            C = np.stack([X[m].mean(axis=0) for m in member]) if member else np.zeros((0, X.shape[1]), dtype=np.float32)
            lab = np.full(n, -1)
            for it in range(14):                       # k-medias esférico: cada elemento va al centro con el que más coseno tiene
                if not len(C):
                    break
                C /= np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-9)
                S = X @ C.T
                new = S.argmax(axis=1)
                new[S.max(axis=1) < 0.09] = -1         # sin parecido suficiente con ningún centro
                sizes = np.bincount(new[new >= 0], minlength=len(C))
                keep = sizes >= max(3, n // 300)
                if not keep.all() and it < 12:         # los grupos diminutos se disuelven y se reparte de nuevo
                    C = C[keep]
                    continue
                if (new == lab).all():
                    break
                lab = new
                C = np.stack([X[lab == j].mean(axis=0) if (lab == j).any() else C[j] for j in range(len(C))])
            names, used = [], set()
            for j in range(len(C)):
                top = [vocab[i] for i in np.argsort(-C[j])[:6] if C[j][i] > 0]
                first = next((w for w in top if w not in used), top[0] if top else f"grupo {j + 1}")
                used.add(first)
                second = next((w for w in top if w != first and not (set(w.split()) & set(first.split()))
                               and C[j][vocab.index(w)] >= 0.55 * C[j][vocab.index(first)]), None)
                names.append(show(first) + (" · " + show(second) if second else ""))
            assign = [names[j] if j >= 0 else "otros temas" for j in lab]
            sub = doms

        groups: dict[str, dict] = {}
        for i, g in enumerate(assign):
            G = groups.setdefault(g, {"label": g, "n": 0, "subs": {}, "doms": Counter()})
            G["n"] += 1
            G["doms"][doms[i]] += 1
            G["subs"].setdefault(sub[i], []).append(i)
        order = sorted(groups.values(), key=lambda G: (G["label"].startswith("otros"), -G["n"]))
        clusters, nodes, pos = [], [], {}
        for ci, G in enumerate(order):
            subs = sorted(G["subs"].items(), key=lambda kv: -len(kv[1]))
            head, tail = subs[:9], [i for _, ids in subs[9:] for i in ids]
            if tail:
                head.append(("otros", tail))
            cs = []
            for si, (sl, ids) in enumerate(head):
                cs.append({"label": sl, "n": len(ids)})
                for i in ids:
                    rn, r = rows[i]
                    pos[i] = len(nodes)
                    nd = {"c": ci, "s": si, "t": r.get("title") or r.get("url"), "u": r.get("url"),
                          "sn": (r.get("snippet") or "")[:260], "src": r.get("source"), "d": doms[i],
                          "date": r.get("date"), "run": rn, "q": r.get("query")}
                    if files:
                        nd["f"] = {k: r["_f"].get(k) for k in ("path", "ext", "kind", "size", "sha256", "content_type",
                                                              "fetched", "md", "title", "source", "url")}
                    nodes.append(nd)
            clusters.append({"label": G["label"], "n": G["n"], "subs": cs,
                             "top": [d for d, _ in G["doms"].most_common(3)], "_doms": set(G["doms"])})
        links = []
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                w = len(clusters[x]["_doms"] & clusters[y]["_doms"])
                if w >= 2:
                    links.append([x, y, w])
        for c_ in clusters:
            c_.pop("_doms")
        links.sort(key=lambda x: -x[2])
        edges = []
        if X is not None and n >= 3:                   # aristas: los dos elementos más parecidos a cada uno (coseno ≥ 0,45)
            import numpy as np
            best: dict[tuple, float] = {}
            for s0 in range(0, n, 512):
                S = X[s0:s0 + 512] @ X.T
                S[np.arange(S.shape[0]), np.arange(s0, s0 + S.shape[0])] = -1
                idx = np.argpartition(-S, 2, axis=1)[:, :2]
                for r_ in range(S.shape[0]):
                    for j in idx[r_]:
                        v = float(S[r_, j])
                        if 0.45 <= v < 0.995:           # ≥ 0,995 suele ser el mismo texto repetido
                            e = (min(s0 + r_, int(j)), max(s0 + r_, int(j)))
                            best[e] = max(best.get(e, 0), v)
            edges = [[pos[i], pos[j], round(v, 2)] for (i, j), v in sorted(best.items(), key=lambda kv: -kv[1])[:6000]]
        res = {"by": by, "kind": kind, "total": total, "shown": n, "clusters": clusters, "nodes": nodes, "links": links[:60],
               "edges": edges}
        with _lock:
            _ov_cache["graph"] = (key, res)
        return res

    def _vectors(self, rows, doms, qterms):
        """Matriz TF-IDF normalizada (elementos × términos), su vocabulario y las formas originales de cada raíz."""
        try:
            import numpy as np
        except ImportError:
            return None, [], {}
        import math
        forms: dict[str, Counter] = {}

        def toks(text, dom):
            skip = set(_TOK.findall(dom))
            out = []
            for w in _TOK.findall(fold(text)):
                if w in qterms or w in _STOP:
                    continue
                raw = w
                if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):  # plural simple: «models» y «model» son un tema
                    w = w[:-1]
                if len(w) >= 4 and w not in _STOP and w not in qterms and w not in skip and not w.isdigit():
                    out.append(w)
                    forms.setdefault(w, Counter())[raw] += 1
            return out

        docs = []
        for (_, r), dom in zip(rows, doms):
            tw = toks(r.get("title") or "", dom + " " + (r.get("source") or ""))
            sw = toks(r.get("snippet") or "", dom)
            c = Counter()
            for w in tw + [f"{x} {y}" for x, y in zip(tw, tw[1:])]:
                c[w] += 2.0                               # el título pesa el doble que el resumen
            for w in sw + [f"{x} {y}" for x, y in zip(sw, sw[1:])]:
                c[w] += 1.0
            docs.append(c)
        n = len(docs)
        if not n:
            return None, [], forms
        df = Counter(w for d in docs for w in d)
        lo, hi = max(2, n // 400), max(3, int(n * 0.35))
        vocab = [w for w, c in sorted(df.items(), key=lambda kv: -(kv[1] * math.log(1 + n / kv[1]))) if lo <= c <= hi][:2500]
        if not vocab:
            return None, [], forms
        ix = {w: i for i, w in enumerate(vocab)}
        X = np.zeros((n, len(vocab)), dtype=np.float32)
        for i, d in enumerate(docs):
            for w, tf in d.items():
                j = ix.get(w)
                if j is not None:
                    X[i, j] = (1 + math.log(tf)) * math.log(1 + n / df[w])
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        return X, vocab, forms

    # ---------- respuesta con fuentes: extractos literales ordenados por relevancia ----------
    def answer(self, p: Path, question: str = "") -> dict:
        import math
        try:
            q = json.loads((p / "query.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            q = {}
        question = (question or " ".join(q.get("terms") or [])).strip()
        qt = [w for w in dict.fromkeys(_TOK.findall(fold(question))) if w not in _STOP]
        if not qt:
            return {"question": question, "passages": [], "sources": [], "scanned": 0}
        cands = []                                         # (texto, fuente)
        for pg in self.read_jsonl(p / "pages.jsonl"):
            src = {"title": pg.get("title") or pg.get("url"), "url": pg.get("url"), "date": pg.get("date"),
                   "site": pg.get("sitename") or domain_of(pg.get("url") or ""), "kind": "página"}
            try:
                text = (p / pg["md"]).read_text(encoding="utf-8", errors="replace")[:200_000] if pg.get("md") else ""
            except OSError:
                text = ""
            text = _re.sub(r"^---[\s\S]*?\n---\s*", "", text)
            for par in _re.split(r"\n\s*\n", text):
                par = _re.sub(r"\s+", " ", _re.sub(r"[#*`>|_]+|\[([^\]]+)\]\([^)]*\)", r"\1", par)).strip()
                if 110 <= len(par) <= 900 and par.count("http") < 3:
                    cands.append((par, src))
        for f in self.read_jsonl(p / "files.jsonl"):
            if not f.get("md"):
                continue
            src = {"title": f.get("title") or (f.get("path") or "").rsplit("/", 1)[-1], "url": f.get("url"),
                   "date": f.get("date"), "site": domain_of(f.get("url") or ""), "kind": "documento"}
            try:
                text = (p / f["md"]).read_text(encoding="utf-8", errors="replace")[:300_000]
            except OSError:
                continue
            for par in _re.split(r"\n\s*\n", _re.sub(r"^---[\s\S]*?\n---\s*", "", text)):
                par = _re.sub(r"\s+", " ", par).strip()
                if 110 <= len(par) <= 900:
                    cands.append((par, src))
        for r in self.read_jsonl(p / "results.jsonl"):
            sn = (r.get("snippet") or "").strip()
            if len(sn) >= 90:
                cands.append((sn[:700], {"title": r.get("title") or r.get("url"), "url": r.get("url"), "date": r.get("date"),
                                         "site": domain_of(r.get("url") or ""), "kind": "resumen del resultado"}))
        n = len(cands)
        toks = [_TOK.findall(fold(t)) for t, _ in cands]
        df = Counter(w for ws in toks for w in set(ws) if w in qt)
        phrase = fold(question) if " " in question else ""
        scored = []
        for (text, src), ws in zip(cands, toks):
            tf = Counter(w for w in ws if w in df)
            if not tf:
                continue
            s = sum(math.log(1 + n / df[w]) * c / (c + 1.2) for w, c in tf.items()) * (len(tf) / len(qt)) ** 1.5
            if phrase and phrase in fold(text):
                s *= 1.6
            digits = sum(ch.isdigit() for ch in text) / len(text)
            if digits > 0.1 or text.count("|") > 4:
                s *= 0.45                                  # tablas, listados de cifras y registros automáticos
            if any(w in fold(text[:110]) for w in qt):
                s *= 1.2                                   # el término abre el párrafo: suele ser donde se explica
            if text.rstrip()[-1:] in ".!?»\"":
                s *= 1.1
            if src["kind"] != "resumen del resultado":
                s *= 1.25                                  # el texto de la página pesa más que el resumen del buscador
            scored.append((s, text, src))
        scored.sort(key=lambda x: -x[0])
        out, per, seen_text, sources = [], Counter(), set(), []
        for s, text, src in scored:
            sig = fold(text)[:80]
            if per[src["url"]] >= 2 or sig in seen_text:
                continue
            seen_text.add(sig)
            per[src["url"]] += 1
            if src not in sources:
                sources.append(src)
            out.append({"text": text, "score": round(s, 2), "ref": sources.index(src) + 1})
            if len(out) >= 12:
                break
        return {"question": question, "passages": out, "sources": sources, "scanned": n, "terms": qt}

    # ---------- plantillas de consulta ----------
    def templates(self) -> list:
        return self._load("templates.json", [])

    def save_template(self, body: dict) -> dict:
        name = (body.get("name") or "").strip()[:60]
        if not name:
            raise ValueError("falta el nombre de la plantilla")
        with _lock:
            ts = [x for x in self._load("templates.json", []) if x.get("name") != name]
            item = {"id": uuid.uuid4().hex[:10], "name": name, "data": body.get("data") or {}}
            ts.append(item)
            self._save("templates.json", ts)
        return item

    def delete_template(self, tid: str) -> dict:
        with _lock:
            self._save("templates.json", [x for x in self._load("templates.json", []) if x.get("id") != tid])
        return {"ok": True}

    # ---------- búsqueda global ----------
    def find(self, q: str, typ: str = "", limit: int = 150) -> dict:
        qf = fold(q.strip())
        if len(qf) < 2:
            return {"items": [], "total": 0, "counts": {}}
        spec = (("results", "hallazgo", ("title", "snippet", "url")),
                ("pages", "página", ("title", "description", "url", "sitename")),
                ("files", "recurso", ("path", "title", "url")))
        items, counts = [], Counter()
        for p in self.runs()[:100]:
            for name, label, fields in spec:
                for r in self.read_jsonl(p / f"{name}.jsonl"):
                    if qf in fold(" ".join(str(r.get(f) or "") for f in fields)):
                        counts[name] += 1
                        if (not typ or typ == name) and len(items) < limit:
                            items.append({"type": name, "label": label, "run": p.name,
                                          "title": r.get("title") or (r.get("path") or "").split("/")[-1] or r.get("url"),
                                          "url": r.get("url"), "snippet": (r.get("snippet") or r.get("description") or "")[:240],
                                          "date": r.get("date") or r.get("fetched") or r.get("found"),
                                          "source": r.get("source"), "path": r.get("path"), "md": r.get("md"),
                                          "kind": r.get("kind"), "ext": r.get("ext"), "size": r.get("size")})
        return {"items": items, "total": sum(counts.values()), "counts": dict(counts)}

    def activity(self, limit: int = 400) -> list:
        rows = []
        for p in self.runs()[:25]:
            f = p / "run.log"
            if not f.exists():
                continue
            try:
                day = time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime))
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                    if len(line) > 9 and line[2] == ":" and line[5] == ":":
                        msg = line[9:]
                        lvl = "warn" if ("captcha" in msg or "timeout" in msg or "0 resultados" in msg) else \
                            "error" if msg.lower().startswith("error") or "] error" in msg else \
                            "ok" if msg.startswith(("FIN", "inicio", "cosecha")) else "info"
                        rows.append({"t": f"{day}T{line[:8]}", "run": p.name, "msg": msg, "level": lvl})
            except OSError:
                pass
        rows.sort(key=lambda r: r["t"], reverse=True)
        return rows[:limit]

    # ---------- vigilancias ----------
    def watches(self) -> list:
        ws = self._load("watches.json", [])
        for w in ws:
            rid = (w.get("runs") or [None])[-1]
            w["last_info"] = None
            if rid and (self.out / rid).is_dir():
                i = self.run_info(self.out / rid)
                w["last_info"] = {k: i.get(k) for k in ("id", "status", "alive", "hits", "pages", "files", "started")}
            w["interval_label"] = INTERVALS.get(w.get("every"), f"Cada {w.get('every')} min")
        return ws

    def save_watch(self, body: dict) -> dict:
        term = (body.get("term") or "").strip()
        if not term:
            raise ValueError("falta el término")
        every = int(body.get("every") or 1440)
        if every < 15:
            raise ValueError("el intervalo mínimo es de 15 minutos")
        with _lock:
            ws = self._load("watches.json", [])
            w = next((x for x in ws if x["id"] == body.get("id")), None)
            if not w:
                w = {"id": uuid.uuid4().hex[:10], "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": [], "new_last": None,
                     "next": time.time() + 5 if body.get("run_now") else time.time() + every * 60}
                ws.append(w)
            w.update(name=(body.get("name") or term).strip(), term=term,
                     extra=[t.strip() for t in body.get("extra") or [] if t.strip()], since=str(body.get("since") or "7d"),
                     depth=int(body.get("depth", 1)), scope=body.get("scope") or "res", every=every,
                     enabled=bool(body.get("enabled", True)))
            self._save("watches.json", ws)
        return w

    def watch_action(self, wid: str, action: str) -> dict:
        with _lock:
            ws = self._load("watches.json", [])
            w = next((x for x in ws if x["id"] == wid), None)
            if not w:
                raise ValueError("no existe")
            if action == "delete":
                ws = [x for x in ws if x["id"] != wid]
            elif action == "toggle":
                w["enabled"] = not w.get("enabled", True)
                if w["enabled"]:
                    w["next"] = time.time() + w["every"] * 60
            elif action == "run":
                self._launch(w)
            self._save("watches.json", ws)
        return {"ok": True}

    def _launch(self, w: dict):
        s = self.settings()
        body = {"term": w["term"], "extra": w.get("extra"), "since": w.get("since"), "depth": w.get("depth", 1),
                "langs": s["langs"], "max_pages": s["max_pages"], "per_domain": s["per_domain"], "max_gb": s["max_gb"],
                "sources": RESOURCE_SOURCES if w.get("scope") == "res" else None}
        rid = self.start_search(body)
        w.setdefault("runs", []).append(rid)
        w["runs"] = w["runs"][-30:]
        w["last"] = time.time()
        w["next"] = time.time() + w["every"] * 60
        w["new_last"] = None

    def watch_news(self, wid: str) -> dict:
        w = next((x for x in self._load("watches.json", []) if x["id"] == wid), None)
        if not w:
            raise ValueError("no existe")
        runs = [r for r in w.get("runs", []) if (self.out / r).is_dir()]
        if not runs:
            return {"items": [], "total": 0, "history": []}
        seen: set = set()
        history = []
        new_items: list = []
        for rid in runs:
            rows = self.read_jsonl(self.out / rid / "results.jsonl")
            fresh = [r for r in rows if (r.get("canon") or r.get("url")) not in seen]
            seen.update((r.get("canon") or r.get("url")) for r in rows)
            info = self.run_info(self.out / rid)
            history.append({"run": rid, "started": info.get("started"), "status": info.get("status"), "total": len(rows),
                            "new": len(fresh)})
            new_items = fresh
        if len(runs) == 1:
            history[0]["new"] = history[0]["total"]
        new_items = sorted(new_items, key=lambda r: r.get("date") or "", reverse=True)
        return {"items": new_items[:200], "total": len(new_items), "history": history[::-1], "last_run": runs[-1]}

    def start_scheduler(self):
        if self._sched and self._sched.is_alive():
            return

        def loop():
            while True:
                time.sleep(20)
                try:
                    with _lock:
                        ws = self._load("watches.json", [])
                        changed = False
                        for w in ws:
                            if not w.get("enabled", True) or (w.get("next") or 0) > time.time():
                                continue
                            last = (w.get("runs") or [None])[-1]
                            proc = self.procs.get(last) if last else None
                            if proc is not None and proc.poll() is None:
                                continue  # la ejecución anterior sigue en curso
                            self._launch(w)
                            changed = True
                        if changed:
                            self._save("watches.json", ws)
                except Exception:
                    pass

        self._sched = threading.Thread(target=loop, daemon=True, name="dm-scheduler")
        self._sched.start()


def classify(st: str, kept: int = 0) -> str:
    """Estado de una fuente en una recolección: ok | empty | error | running | pending."""
    if not st or st == "pendiente":
        return "pending"
    if st == "running":
        return "running"
    if st.startswith("error"):
        return "error"
    if st.startswith("timeout"):
        return "ok" if kept else "error"
    if "0 resultados" in st:
        return "empty"
    return "ok"
