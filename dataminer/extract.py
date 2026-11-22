"""Extracción CPU-intensiva: se ejecuta en un ProcessPoolExecutor (todas las CPUs) para no bloquear la red."""
from __future__ import annotations

import io
import re
from urllib.parse import urljoin, urlsplit

from .util import fold, relevance, sha1

ARCHIVE_EXT = {"zip", "gz", "tgz", "tar", "7z", "rar", "bz2", "xz", "zst"}
IMG_EXT = {"jpg", "jpeg", "png", "gif", "webp", "svg", "tif", "tiff", "bmp", "avif"}
DOC_EXT = ARCHIVE_EXT | {"sql", "sqlite", "db", "jsonl", "mobi", "azw3", "djvu", "tex", "bib", "mp3", "ogg", "flac", "wav",
                         "mp4", "webm", "mkv", "py", "ipynb", "sh", "ps1", "yaml", "yml", "toml", "ini", "log"} | {"pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "csv", "tsv", "ppt", "pptx", "odp",
           "json", "xml", "txt", "md", "epub", "ipynb", "parquet", "geojson", "kml"}
SKIP_EXT = {"ico", "css", "js", "woff", "woff2", "ttf", "eot", "map"}


CT_EXT = {"application/pdf": "pdf", "text/csv": "csv", "application/json": "json", "application/xml": "xml",
          "text/xml": "xml", "text/plain": "txt", "text/markdown": "md", "application/msword": "doc",
          "application/vnd.ms-excel": "xls", "application/vnd.ms-powerpoint": "ppt", "application/rtf": "rtf",
          "application/epub+zip": "epub", "application/rss+xml": "xml", "application/atom+xml": "xml",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
          "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
          "application/vnd.oasis.opendocument.text": "odt", "application/vnd.oasis.opendocument.spreadsheet": "ods",
          "application/zip": "zip", "application/x-zip-compressed": "zip", "application/gzip": "gz", "application/x-gzip": "gz",
          "application/x-tar": "tar", "application/x-7z-compressed": "7z", "application/vnd.rar": "rar",
          "application/x-rar-compressed": "rar", "application/x-bzip2": "bz2", "application/x-xz": "xz",
          "application/sql": "sql", "application/x-sqlite3": "sqlite", "application/vnd.apache.parquet": "parquet",
          "application/geo+json": "geojson", "application/ld+json": "json", "text/tab-separated-values": "tsv",
          "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
          "image/tiff": "tif", "image/avif": "avif", "audio/mpeg": "mp3", "audio/ogg": "ogg", "video/mp4": "mp4",
          "video/webm": "webm", "application/x-ipynb+json": "ipynb", "text/x-python": "py", "application/x-yaml": "yaml"}


def url_ext(u: str) -> str:
    path = urlsplit(u).path.lower()
    m = re.search(r"\.([a-z][a-z0-9]{1,7})$", path)
    return m.group(1) if m else ""


def extract_html(raw: bytes, url: str, tokens: list, want_links: bool, want_images: bool = False) -> dict:
    import trafilatura
    from trafilatura.utils import decode_file
    from selectolax.lexbor import LexborHTMLParser

    html = decode_file(raw) if isinstance(raw, (bytes, bytearray)) else raw
    out = {"ok": False}
    try:
        tree = trafilatura.load_html(html)
        if tree is None:
            return out
        meta = trafilatura.extract_metadata(tree, default_url=url)
        md = trafilatura.extract(tree, url=url, output_format="markdown", include_tables=True, include_links=False,
                                 include_comments=False, favor_recall=True, deduplicate=True) or ""
    except Exception as e:  # páginas patológicas
        return {"ok": False, "error": repr(e)[:200]}
    m = meta.as_dict() if meta is not None and hasattr(meta, "as_dict") else (meta.__dict__ if meta else {})
    title = m.get("title") or ""
    desc = m.get("description") or ""
    if not md:
        md = desc
    body = f"{title}\n{desc}\n{md}"
    out.update(ok=True, title=title, author=m.get("author"), date=m.get("date"), sitename=m.get("sitename"),
               description=desc, image=m.get("image"), tags=m.get("tags") or m.get("categories"),
               lang=m.get("language"), markdown=md, text_len=len(md), relevance=relevance(body, tokens),
               sha1=sha1(re.sub(r"\W+", "", fold(md))[:20000]) if len(md) > 200 else None)
    if want_links:
        links, docs, feeds, seen = [], [], [], set()
        try:
            t = LexborHTMLParser(html)
            for ln in t.css("link[rel=alternate]"):
                ty = (ln.attributes.get("type") or "")
                if "rss" in ty or "atom" in ty:
                    feeds.append(urljoin(url, ln.attributes.get("href") or ""))
            for a in t.css("a[href]"):
                h = (a.attributes.get("href") or "").strip()
                if not h or h.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                    continue
                u = urljoin(url, h).split("#")[0]
                if not u.startswith("http") or u in seen:
                    continue
                seen.add(u)
                ext = url_ext(u)
                if ext in SKIP_EXT or (ext in IMG_EXT and not want_images):
                    continue
                if ext in IMG_EXT:
                    docs.append((u, "", 0, ext))
                    continue
                anchor = re.sub(r"\s+", " ", a.text() or "")[:200]
                sc = relevance(anchor + " " + re.sub(r"[-_/.+%]", " ", u), tokens)
                if ext in DOC_EXT:
                    docs.append((u, anchor, sc, ext))
                else:
                    links.append((u, anchor, sc))
                if len(seen) > 3000:
                    break
            if want_images:
                for im in t.css("img[src]"):
                    u = urljoin(url, (im.attributes.get("src") or "").strip())
                    if u.startswith("http") and u not in seen and not u.startswith("data:"):
                        seen.add(u)
                        docs.append((u, im.attributes.get("alt") or "", 0, url_ext(u) or "img"))
        except Exception:
            pass
        out.update(links=links, docs=docs, feeds=feeds)
    return out


def extract_pdf(raw: bytes, tokens: list, max_pages: int = 40) -> dict:
    try:
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(raw))
        info = r.metadata or {}
        parts = []
        for i, p in enumerate(r.pages):
            if i >= max_pages:
                break
            try:
                parts.append(p.extract_text() or "")
            except Exception:
                pass
        text = "\n\n".join(parts).strip()
        return {"ok": True, "title": str(info.get("/Title") or ""), "author": str(info.get("/Author") or ""),
                "date": str(info.get("/CreationDate") or "")[2:16], "pages": len(r.pages), "markdown": text,
                "text_len": len(text), "relevance": relevance(text[:200000], tokens)}
    except Exception as e:
        return {"ok": False, "error": repr(e)[:200]}
