"""Persistencia en output/<consulta>_<fecha>/ : JSONL en vivo, markdown por página, ficheros, CSV, stats y resumen."""
from __future__ import annotations

import csv
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

from .util import domain_of, safe_filename, slug


class Store:
    def __init__(self, out: Path):
        self.out = out
        (out / "pages").mkdir(parents=True, exist_ok=True)
        (out / "files").mkdir(exist_ok=True)
        self._fh = {n: open(out / f"{n}.jsonl", "a", encoding="utf-8", buffering=1) for n in ("results", "pages", "files")}
        self.stats = {
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "hits": 0, "pages": 0, "files": 0,
            "dup_hits": 0, "old_hits_dropped": 0, "undated_hits": 0, "per_source": defaultdict(Counter),
            "pages_stale": 0, "pages_dup": 0, "pages_irrelevant": 0, "pages_failed": 0, "robots_blocked": 0,
            "status": "running",
        }
        self._n = self._count_existing()
        for k, n in (("hits", "results"), ("pages", "pages"), ("files", "files")):  # totales acumulados al reanudar
            try:
                self.stats[k] = sum(1 for _ in open(out / f"{n}.jsonl", encoding="utf-8"))
            except OSError:
                pass

    def _count_existing(self) -> int:
        try:
            return sum(1 for _ in open(self.out / "pages.jsonl", encoding="utf-8"))
        except OSError:
            return 0

    def load_seen(self, name: str, key: str) -> set:
        s = set()
        p = self.out / f"{name}.jsonl"
        if p.exists():
            for line in open(p, encoding="utf-8"):
                try:
                    v = json.loads(line).get(key)
                    if v:
                        s.add(v)
                except ValueError:
                    pass
        return s

    def write(self, name: str, obj: dict):
        self._fh[name].write(json.dumps(obj, ensure_ascii=False) + "\n")

    def save_markdown(self, url: str, title: str, body: str, front: dict) -> str:
        self._n += 1
        dom = safe_filename(domain_of(url) or "unknown", 60)
        d = self.out / "pages" / dom
        d.mkdir(exist_ok=True)
        name = safe_filename(f"{self._n:05d}_{slug(title or url, 70)}.md")
        fm = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items() if v not in (None, "", []))
        (d / name).write_text(f"---\n{fm}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")
        return f"pages/{dom}/{name}"

    def place_file(self, url: str, tmp, ext: str, name: str | None = None) -> str:
        """Mueve a su sitio definitivo un fichero ya descargado a disco (descarga por trozos)."""
        p = self._file_path(url, ext, name)
        os.replace(tmp, p)
        return str(p.relative_to(self.out)).replace("\\", "/")

    def save_file(self, url: str, content: bytes, ext: str, name: str | None = None) -> str:
        p = self._file_path(url, ext, name)
        p.write_bytes(content)
        return str(p.relative_to(self.out)).replace("\\", "/")

    def _file_path(self, url: str, ext: str, name: str | None = None) -> Path:
        dom = safe_filename(domain_of(url) or "unknown", 60)
        d = self.out / "files" / dom
        d.mkdir(exist_ok=True)
        raw = name or unquote(os.path.basename(url.split("?")[0]))
        if raw in ("content", "download", "", "file"):
            raw = unquote(url.rstrip("/").split("/")[-2]) if url.count("/") > 3 else "file"
        base = safe_filename(os.path.basename(raw) or "file", 100)
        if not base.lower().endswith("." + ext):
            base += f".{ext}"
        p = d / base
        i = 1
        while p.exists():
            stem, _, e = base.rpartition(".")
            p = d / f"{stem}_{i}.{e}"
            i += 1
        return p

    def flush_stats(self, extra: dict | None = None):
        s = dict(self.stats)
        s["per_source"] = {k: dict(v) for k, v in self.stats["per_source"].items()}
        if extra:
            s.update(extra)
        tmp = self.out / "stats.json.tmp"
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.out / "stats.json")

    def _read(self, name):
        p = self.out / f"{name}.jsonl"
        rows = []
        if p.exists():
            for line in open(p, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        return rows

    def finalize(self, meta: dict, extra: dict):
        for f in self._fh.values():
            f.close()
        hits, pages, files = self._read("results"), self._read("pages"), self._read("files")
        with open(self.out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "source", "title", "url", "query", "snippet"])
            for h in sorted(hits, key=lambda h: h.get("date") or "", reverse=True):
                w.writerow([h.get("date"), h.get("source"), h.get("title"), h.get("url"), h.get("query"), h.get("snippet")])
        with open(self.out / "pages.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "relevance", "depth", "title", "url", "sitename", "author", "text_len", "markdown"])
            for p in sorted(pages, key=lambda p: -p.get("relevance", 0)):
                w.writerow([p.get("date"), p.get("relevance"), p.get("depth"), p.get("title"), p.get("url"),
                            p.get("sitename"), p.get("author"), p.get("text_len"), p.get("md")])
        if self.stats["status"] == "running":
            self.stats["status"] = "done"
        self.stats["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.flush_stats(extra)
        self._summary(meta, hits, pages, files)

    def _summary(self, meta, hits, pages, files):
        st = self.stats
        L = [f"# DataMiner · {meta['terms']}", "",
             f"- Ventana: últimos **{meta['since']}** (desde {meta['cutoff']})  ·  profundidad **{meta['depth']}**",
             f"- Resultados únicos: **{len(hits)}**  ·  páginas extraídas: **{len(pages)}**  ·  ficheros: **{len(files)}**",
             f"- Descartados por antiguos: {st['old_hits_dropped']}  ·  duplicados: {st['dup_hits']}  ·  sin fecha (marcados): {st['undated_hits']}",
             f"- Páginas: obsoletas {st['pages_stale']} · duplicadas {st['pages_dup']} · irrelevantes {st['pages_irrelevant']} · "
             f"fallidas {st['pages_failed']} · bloqueadas por robots.txt {st['robots_blocked']} · "
             f"omitidas por tope de dominio {st.get('domain_cap_skips', 0)} · resultados irrelevantes filtrados {st.get('irrelevant_hits', 0)}", "",
             "## Fuentes", "", "| fuente | aceptados | antiguos | duplicados | irrelevantes | estado |", "|---|---:|---:|---:|---:|---|"]
        for s, c in sorted(st["per_source"].items(), key=lambda kv: -kv[1].get("kept", 0)):
            L.append(f"| {s} | {c.get('kept', 0)} | {c.get('old', 0)} | {c.get('dup', 0)} | {c.get('irrelevant', 0)} | {meta['source_status'].get(s, '')} |")
        doms = Counter(domain_of(h["url"]) for h in hits)
        L += ["", "## Dominios principales", ""] + [f"- {d} — {n}" for d, n in doms.most_common(30)]
        L += ["", "## Lo más reciente", ""]
        for h in sorted((h for h in hits if h.get("date")), key=lambda h: h["date"], reverse=True)[:40]:
            L.append(f"- `{h['date'][:16]}` [{h['source']}] [{h['title'][:110] or h['url']}]({h['url']})")
        if pages:
            L += ["", "## Páginas más relevantes", ""]
            for p in sorted(pages, key=lambda p: -p.get("relevance", 0))[:40]:
                L.append(f"- ({p.get('relevance')}) [{(p.get('title') or p['url'])[:110]}]({p['url']}) → `{p.get('md')}`")
        if files:
            L += ["", "## Ficheros descargados", ""]
            for f in files[:60]:
                L.append(f"- `{f['path']}` ({f['size'] // 1024} KB) ← {f['url']}")
        (self.out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
