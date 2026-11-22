"""Catálogos abiertos de libros, vídeo, audio y software.

Catálogos y archivos abiertos (Internet Archive, Project Gutenberg, Open Library, Wikimedia Commons) y fichas de
series (TVmaze, sin vídeo). Gutenberg y Commons solo alojan obras libres; Internet Archive aloja subidas de usuarios. Cada registro trae `extra.files`
con enlaces directos cuando el catálogo los ofrece.
"""
from __future__ import annotations

import asyncio
import re

from .resources import on_topic
from .sources import SOURCES, Ctx, Emit, q_exact, rec, strip_html

# extensiones que interesan por tipo de Internet Archive, en orden de preferencia
IA_PREF = {
    "movies": ("mp4", "webm", "ogv", "mkv", "avi", "mpeg", "mpg", "mov"),
    "texts": ("pdf", "epub", "txt", "djvu", "mobi"),
    "audio": ("mp3", "ogg", "flac", "m4a", "zip"),
    "software": ("zip", "7z", "iso", "tar", "gz", "exe", "msi", "apk", "rar"),
}
RECENT_DAYS = 3650  # con una ventana menor se filtra por fecha de subida; mayor = todo el catálogo por popularidad


def _ia_files(meta: dict, ident: str, pref: tuple) -> list:
    out = []
    for f in (meta or {}).get("files", []) or []:
        n = f.get("name", "")
        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
        if ext not in pref or n.endswith(("_meta.xml", "_files.xml", ".torrent")) or "_thumb" in n:
            continue
        size = int(f["size"]) if str(f.get("size", "")).isdigit() else None
        out.append({"url": f"https://archive.org/download/{ident}/{n}", "name": n, "size": size,
                    "_rank": (pref.index(ext), 0 if f.get("source") == "original" else 1, size or 0)})
    out.sort(key=lambda x: x["_rank"])
    for x in out:
        x.pop("_rank")
    return out[:8]


def _archive(mediatype: str, name: str):
    async def src(ctx: Ctx, emit: Emit):
        sem = asyncio.Semaphore(6)
        recent = ctx.since.days < RECENT_DAYS
        for t in ctx.terms:
            q = f"({q_exact(t)}) AND mediatype:({mediatype})"
            if recent:
                q += f" AND publicdate:[{ctx.date_str} TO 9999-12-31]"
            j = await ctx.fetch.json("https://archive.org/advancedsearch.php", params=[
                ("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "publicdate"), ("fl[]", "description"),
                ("fl[]", "downloads"), ("fl[]", "year"), ("fl[]", "creator"),
                ("sort[]", "publicdate desc" if recent else "downloads desc"), ("rows", "60"), ("output", "json")])
            docs = ((j or {}).get("response") or {}).get("docs", [])

            async def one(d, t=t):
                ident = d.get("identifier")
                if not ident or not on_topic(ctx, d.get("title"), ident):
                    return True
                async with sem:
                    meta = await ctx.fetch.json(f"https://archive.org/metadata/{ident}")
                desc = d.get("description")
                desc = " ".join(desc) if isinstance(desc, list) else desc or ""
                creator = d.get("creator")
                creator = ", ".join(creator[:3]) if isinstance(creator, list) else creator
                return await emit(rec(name, f"https://archive.org/details/{ident}", d.get("title"), strip_html(desc)[:800],
                                      d.get("publicdate"), t, mediatype=mediatype, downloads=d.get("downloads"),
                                      year=d.get("year"), author=creator, files=_ia_files(meta, ident, IA_PREF[mediatype]),
                                      no_fetch=True))

            await asyncio.gather(*(one(d) for d in docs))
    return src


async def gutenberg(ctx: Ctx, emit: Emit):
    """Project Gutenberg vía Gutendex: libros de dominio público con EPUB, Kindle y texto plano."""
    want = (("application/epub+zip", "epub"), ("application/x-mobipocket-ebook", "mobi"), ("text/plain", "txt"))
    for t in ctx.terms:
        j = await ctx.fetch.json("https://gutendex.com/books", params={"search": t}, timeout=30, retries=1)
        for b in (j or {}).get("results", [])[:60]:
            authors = ", ".join(a.get("name", "") for a in b.get("authors", [])[:3])
            if not on_topic(ctx, b.get("title"), authors, " ".join(b.get("subjects", []))):
                continue
            base = re.sub(r"[^\w\- ]+", "", b.get("title") or f"pg{b.get('id')}")[:80].strip() or f"pg{b.get('id')}"
            files = []
            for mime, ext in want:
                u = next((v for k, v in (b.get("formats") or {}).items() if k.startswith(mime)), None)
                if u:
                    files.append({"url": u, "name": f"{base}.{ext}"})
            if not await emit(rec("gutenberg", f"https://www.gutenberg.org/ebooks/{b.get('id')}", b.get("title"),
                                  "; ".join(b.get("subjects", [])[:6]), None, t, author=authors,
                                  lang=", ".join(b.get("languages", [])), downloads=b.get("download_count"),
                                  files=files, no_fetch=True)):
                return


async def openlibrary(ctx: Ctx, emit: Emit):
    """Open Library: ediciones con texto completo de lectura pública (enlaza al ejemplar en Internet Archive)."""
    for t in ctx.terms:
        j = await ctx.fetch.json("https://openlibrary.org/search.json", params={
            "q": t, "has_fulltext": "true", "limit": "60",
            "fields": "key,title,author_name,first_publish_year,ia,ebook_access,language"})
        for d in (j or {}).get("docs", []):
            if d.get("ebook_access") != "public" or not d.get("ia"):
                continue
            authors = ", ".join((d.get("author_name") or [])[:3])
            if not on_topic(ctx, d.get("title"), authors):
                continue
            if not await emit(rec("openlibrary", f"https://archive.org/details/{d['ia'][0]}", d.get("title"),
                                  f"{authors} · primera edición {d.get('first_publish_year') or 's. f.'}", None, t,
                                  author=authors, year=d.get("first_publish_year"),
                                  catalog=f"https://openlibrary.org{d.get('key')}", no_fetch=True)):
                return


async def commons_media(ctx: Ctx, emit: Emit):
    """Wikimedia Commons: vídeo y audio con licencia libre, con enlace directo al fichero."""
    for t in ctx.terms:
        for kind in ("video", "audio"):
            j = await ctx.fetch.json("https://commons.wikimedia.org/w/api.php", params={
                "action": "query", "generator": "search", "gsrsearch": f"{t} filetype:{kind}", "gsrnamespace": "6",
                "gsrlimit": "40", "prop": "imageinfo", "iiprop": "url|size|mime|timestamp", "format": "json"})
            for p in (((j or {}).get("query") or {}).get("pages") or {}).values():
                ii = (p.get("imageinfo") or [{}])[0]
                title = (p.get("title") or "").removeprefix("File:")
                if not ii.get("url") or not on_topic(ctx, title):
                    continue
                if not await emit(rec("commons_media", ii.get("descriptionurl") or ii["url"], title,
                                      f"{kind} · {ii.get('mime', '')}", ii.get("timestamp"), t, type=kind,
                                      files=[{"url": ii["url"], "name": title, "size": ii.get("size")}], no_fetch=True)):
                    return


async def tvmaze(ctx: Ctx, emit: Emit):
    """TVmaze: fichas de series (estreno, cadena, géneros, sinopsis). Solo información, no vídeo."""
    for t in ctx.terms:
        j = await ctx.fetch.json("https://api.tvmaze.com/search/shows", params={"q": t})
        for it in j if isinstance(j, list) else []:
            s = it.get("show") or {}
            net = (s.get("network") or s.get("webChannel") or {}).get("name")
            if not await emit(rec("tvmaze", s.get("url"), s.get("name"), strip_html(s.get("summary") or "")[:800], None, t,
                                  published=s.get("premiered"), state=s.get("status"), channel=net,
                                  type=", ".join(s.get("genres") or []), lang=s.get("language"), no_fetch=True)):
                return


async def europepmc(ctx: Ctx, emit: Emit):
    """Europe PMC: artículos científicos en acceso abierto, con enlace directo al PDF."""
    for t in ctx.terms:
        q = f'{q_exact(t)} OPEN_ACCESS:y AND FIRST_PDATE:[{ctx.date_str} TO 2100-01-01]'
        j = await ctx.fetch.json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={
            "query": q, "format": "json", "resultType": "core", "pageSize": "60", "sort": "FIRST_PDATE_D desc"})
        for x in ((j or {}).get("resultList") or {}).get("result", []):
            urls = (x.get("fullTextUrlList") or {}).get("fullTextUrl", [])
            pdf = next((u.get("url") for u in urls if u.get("documentStyle") == "pdf"), None)
            page = next((u.get("url") for u in urls if u.get("documentStyle") == "html"), None) or \
                f"https://europepmc.org/article/{x.get('source')}/{x.get('id')}"
            base = re.sub(r"[^\w\- ]+", "", x.get("title") or x.get("id") or "articulo")[:80].strip()
            if not await emit(rec("europepmc", page, x.get("title"), strip_html(x.get("abstractText") or "")[:800],
                                  x.get("firstPublicationDate"), t, author=x.get("authorString"), doi=x.get("doi"),
                                  publisher=(x.get("journalInfo") or {}).get("journal", {}).get("title"),
                                  files=[{"url": pdf, "name": f"{base}.pdf"}] if pdf else [], no_fetch=True)):
                return


async def doaj(ctx: Ctx, emit: Emit):
    """DOAJ: artículos de revistas en acceso abierto (enlaza al texto completo en la revista)."""
    from urllib.parse import quote
    for t in ctx.terms:
        j = await ctx.fetch.json(f"https://doaj.org/api/search/articles/{quote(q_exact(t))}",
                                 params={"pageSize": "60", "sort": "created_date:desc"})
        for x in (j or {}).get("results", []):
            b = x.get("bibjson") or {}
            link = next((l.get("url") for l in b.get("link", []) if l.get("url")), None)
            if not link or not on_topic(ctx, b.get("title"), b.get("abstract")):
                continue
            if not await emit(rec("doaj", link, b.get("title"), strip_html(b.get("abstract") or "")[:800],
                                  x.get("created_date"), t, publisher=(b.get("journal") or {}).get("title"),
                                  author=", ".join(a.get("name", "") for a in b.get("author", [])[:3]))):
                return


async def librivox(ctx: Ctx, emit: Emit):
    """LibriVox: audiolibros de dominio público leídos por voluntarios, con el ZIP de MP3 completo."""
    for t in ctx.terms:
        j = await ctx.fetch.json("https://librivox.org/api/feed/audiobooks",
                                 params={"title": "^" + t, "format": "json", "limit": "40", "extended": "1"})
        for b in (j or {}).get("books", []) if isinstance(j, dict) else []:
            authors = ", ".join(f"{a.get('first_name', '')} {a.get('last_name', '')}".strip() for a in b.get("authors", [])[:3])
            base = re.sub(r"[^\w\- ]+", "", b.get("title") or "audiolibro")[:80].strip()
            z = b.get("url_zip_file")
            if not await emit(rec("librivox", b.get("url_librivox") or z, b.get("title"),
                                  strip_html(b.get("description") or "")[:800], None, t, author=authors, lang=b.get("language"),
                                  published=b.get("copyright_year"), files=[{"url": z, "name": f"{base}.zip"}] if z else [],
                                  no_fetch=True)):
                return


async def nasa_media(ctx: Ctx, emit: Emit):
    """Biblioteca de imágenes y vídeo de la NASA: vídeo y audio de uso libre, con sus ficheros."""
    sem = asyncio.Semaphore(6)
    pref = ("~medium.mp4", "~small.mp4", "~mobile.mp4", "~orig.mp4", "~128k.mp3", "~orig.mp3", "~orig.wav")
    for t in ctx.terms:
        j = await ctx.fetch.json("https://images-api.nasa.gov/search", params={"q": t, "media_type": "video,audio"})
        items = (((j or {}).get("collection") or {}).get("items") or [])[:50]

        async def one(it, t=t):
            d = (it.get("data") or [{}])[0]
            if not on_topic(ctx, d.get("title"), d.get("description"), " ".join(d.get("keywords") or [])):
                return True
            async with sem:
                assets = await ctx.fetch.json(it.get("href") or "")
            files = []
            for suf in pref:
                u = next((a for a in assets or [] if isinstance(a, str) and a.endswith(suf)), None)
                if u:
                    files.append({"url": u.replace("http://", "https://").replace(" ", "%20"), "name": u.rsplit("/", 1)[-1]})
                    break
            return await emit(rec("nasa_media", f"https://images.nasa.gov/details/{d.get('nasa_id')}", d.get("title"),
                                  strip_html(d.get("description") or "")[:800], d.get("date_created"), t,
                                  type=d.get("media_type"), org=d.get("center"), files=files, no_fetch=True))

        await asyncio.gather(*(one(it) for it in items))


MEDIA_SOURCES = {
    "europepmc": (europepmc, "Europe PMC: artículos científicos en acceso abierto con su PDF"),
    "doaj": (doaj, "DOAJ: artículos de revistas en acceso abierto"),
    "librivox": (librivox, "LibriVox: audiolibros de dominio público en ZIP de MP3"),
    "nasa_media": (nasa_media, "NASA: vídeo y audio de uso libre, con sus ficheros"),
    "gutenberg": (gutenberg, "Project Gutenberg: libros de dominio público en EPUB, Kindle y texto"),
    "openlibrary": (openlibrary, "Open Library: ediciones de lectura pública con texto completo"),
    "archive_books": (_archive("texts", "archive_books"), "Internet Archive: libros y textos con sus PDF y EPUB"),
    "archive_video": (_archive("movies", "archive_video"), "Internet Archive: películas y vídeo archivados, con sus ficheros"),
    "archive_audio": (_archive("audio", "archive_audio"), "Internet Archive: audio, música y audiolibros archivados"),
    "archive_software": (_archive("software", "archive_software"), "Internet Archive: software e imágenes de disco archivados"),
    "commons_media": (commons_media, "Wikimedia Commons: vídeo y audio con licencia libre"),
    "tvmaze": (tvmaze, "TVmaze: fichas de series (información, sin vídeo)"),
}
SOURCES.update(MEDIA_SOURCES)
