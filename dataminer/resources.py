"""Fuentes de RECURSOS descargables: datasets, ficheros, código. Cada registro trae `extra.files`
(URLs directas de descarga) que el rastreador baja a output/<run>/files/."""
from __future__ import annotations

import asyncio
import re

from .sources import Ctx, Emit, lang_parts, q_exact, rec, strip_html
from .util import make_tokens, parse_date, relevance


def on_topic(ctx: Ctx, *texts: str) -> bool:
    """El término debe aparecer en el título/nombre del propio recurso, no solo en la colección."""
    toks = getattr(ctx, "_toks", None)
    if toks is None:
        toks = ctx._toks = make_tokens(ctx.terms)
    return relevance(" ".join(t or "" for t in texts), toks) > 0

DATA_EXT = re.compile(r"\.(csv|tsv|json|jsonl|parquet|xlsx?|ods|xml|geojson|zip|gz|txt|pdf|docx?|sql|sqlite|db|arrow|feather|h5|npz)$", re.I)


def _pick(d, langs):
    """data.europa.eu devuelve textos multilingües {'es': ..., 'en': ...}."""
    if isinstance(d, dict):
        for l in langs + ["en", "es"]:
            if d.get(l):
                return d[l]
        return next(iter(d.values()), "")
    return d or ""


async def zenodo(ctx: Ctx, emit: Emit):
    """Zenodo (CERN): datasets, software, papers y presentaciones con ficheros descargables."""
    cutoff = ctx.cutoff
    for t in ctx.terms:
        for page in range(1, ctx.pages + 1):
            j = await ctx.fetch.json("https://zenodo.org/api/records", params={
                "q": q_exact(t), "sort": "mostrecent", "size": "25", "page": str(page)})
            hits = ((j or {}).get("hits") or {}).get("hits", [])
            if not hits:
                break
            for h in hits:
                m = h.get("metadata", {})
                d = parse_date(h.get("created") or m.get("publication_date"))
                if d and d < cutoff:
                    return
                files = [{"url": f["links"]["self"], "name": f.get("key"), "size": f.get("size")}
                         for f in h.get("files", []) if (f.get("links") or {}).get("self")]
                if not await emit(rec("zenodo", (h.get("links") or {}).get("self_html") or f"https://zenodo.org/records/{h.get('id')}",
                                      m.get("title"), strip_html(m.get("description", ""))[:800], d, t,
                                      type=(m.get("resource_type") or {}).get("type"), files=files[:20], no_fetch=True)):
                    return


async def figshare(ctx: Ctx, emit: Emit):
    """Figshare: datasets y ficheros de investigación."""
    sem = asyncio.Semaphore(6)
    for t in ctx.terms:
        for page in range(1, min(ctx.pages, 3) + 1):
            j = await _figshare_search(ctx, t, page)
            if not j:
                break

            async def one(a):
                async with sem:
                    det = await ctx.fetch.json(f"https://api.figshare.com/v2/articles/{a['id']}")
                files = [{"url": f.get("download_url"), "name": f.get("name"), "size": f.get("size")}
                         for f in (det or {}).get("files", []) if f.get("download_url")]
                return await emit(rec("figshare", a.get("url_public_html") or a.get("url"), a.get("title"),
                                      strip_html((det or {}).get("description", ""))[:800], a.get("published_date"), t,
                                      doi=a.get("doi"), files=files[:20], no_fetch=True))

            res = await asyncio.gather(*(one(a) for a in j))
            if not all(res) or len(j) < 50:
                break


async def _figshare_search(ctx: Ctx, t: str, page: int):
    import json as _json
    r = await ctx.fetch.get("https://api.figshare.com/v2/articles/search", method="POST",
                            headers={"Content-Type": "application/json"},
                            data=_json.dumps({"search_for": q_exact(t), "order": "published_date", "order_direction": "desc",
                                              "page_size": 50, "page": page,
                                              "published_since": ctx.date_str}).encode())
    if r is None or not r.ok:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def eu_open_data(ctx: Ctx, emit: Emit):
    """data.europa.eu: 1,6M+ datasets de toda la UE (incluye datos.gob.es, data.gouv.fr, govdata.de...)."""
    cutoff = ctx.cutoff
    langs = [lang_parts(l)[0] for l in ctx.langs]
    for t in ctx.terms:
        for page in range(0, ctx.pages):
            j = await ctx.fetch.json("https://data.europa.eu/api/hub/search/search", params={
                "q": t, "filters": "dataset", "limit": "100", "page": str(page)})
            res = ((j or {}).get("result") or {}).get("results", [])
            if not res:
                break
            for x in res:
                d = parse_date(x.get("modified") or x.get("issued"))
                if d and d < cutoff:
                    continue
                files = []
                for di in x.get("distributions", []) or []:
                    u = di.get("download_url") or di.get("access_url")
                    u = u[0] if isinstance(u, list) and u else u
                    if isinstance(u, str) and u.startswith("http"):
                        files.append({"url": u, "name": _pick(di.get("title"), langs) or None,
                                      "format": (di.get("format") or {}).get("id") if isinstance(di.get("format"), dict) else di.get("format")})
                title = _pick(x.get("title"), langs)
                if not await emit(rec("eu_open_data", f"https://data.europa.eu/data/datasets/{x.get('id')}", title,
                                      strip_html(_pick(x.get("description"), langs))[:800], d, t,
                                      catalog=(x.get("catalog") or {}).get("id"), files=files[:15], no_fetch=True)):
                    return


async def uk_open_data(ctx: Ctx, emit: Emit):
    """data.gov.uk (CKAN)."""
    for t in ctx.terms:
        j = await ctx.fetch.json("https://ckan.publishing.service.gov.uk/api/3/action/package_search", params={
            "q": t, "rows": "200", "sort": "metadata_modified desc",
            "fq": f"metadata_modified:[{ctx.cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')} TO NOW]"})
        for p in ((j or {}).get("result") or {}).get("results", []):
            files = [{"url": r.get("url"), "name": r.get("name"), "format": r.get("format")}
                     for r in p.get("resources", []) if (r.get("url") or "").startswith("http")]
            if not await emit(rec("uk_open_data", f"https://www.data.gov.uk/dataset/{p.get('id')}/{p.get('name')}",
                                  p.get("title"), p.get("notes") or "", p.get("metadata_modified"), t,
                                  org=(p.get("organization") or {}).get("title"), files=files[:15], no_fetch=True)):
                return


async def archive_files(ctx: Ctx, emit: Emit):
    """Internet Archive: subidas recientes CON sus ficheros originales (libros, software, audio, datos...)."""
    sem = asyncio.Semaphore(8)
    for t in ctx.terms:
        q = f"({q_exact(t)}) AND publicdate:[{ctx.date_str} TO 9999-12-31]"
        j = await ctx.fetch.json("https://archive.org/advancedsearch.php", params=[
            ("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "publicdate"), ("fl[]", "description"),
            ("fl[]", "mediatype"), ("sort[]", "publicdate desc"), ("rows", "120"), ("output", "json")])
        docs = ((j or {}).get("response") or {}).get("docs", [])

        async def one(d):
            ident = d.get("identifier")
            if not on_topic(ctx, d.get("title"), ident):
                return True
            async with sem:
                meta = await ctx.fetch.json(f"https://archive.org/metadata/{ident}")
            files = []
            for f in (meta or {}).get("files", []) or []:
                n = f.get("name", "")
                if f.get("source") != "original" or n.endswith(("_meta.xml", "_files.xml", "_meta.sqlite", ".torrent")):
                    continue
                files.append({"url": f"https://archive.org/download/{ident}/{n}", "name": n,
                              "size": int(f["size"]) if str(f.get("size", "")).isdigit() else None})
            desc = d.get("description")
            desc = " ".join(desc) if isinstance(desc, list) else desc or ""
            return await emit(rec("archive_files", f"https://archive.org/details/{ident}", d.get("title"), strip_html(desc)[:800],
                                  d.get("publicdate"), t, mediatype=d.get("mediatype"), files=files[:25], no_fetch=True))

        await asyncio.gather(*(one(d) for d in docs))


async def hf_dataset_files(ctx: Ctx, emit: Emit):
    """Hugging Face: ficheros de datos (csv/json/parquet...) de datasets actualizados recientemente."""
    cutoff = ctx.cutoff
    sem = asyncio.Semaphore(6)
    for t in ctx.terms:
        j = await ctx.fetch.json("https://huggingface.co/api/datasets", params={
            "search": t, "sort": "lastModified", "direction": "-1", "limit": "60"})
        items = [it for it in (j or []) if not (parse_date(it.get("lastModified")) or cutoff) < cutoff
                 and on_topic(ctx, it["id"].split("/")[-1].replace("-", " ").replace("_", " "))]

        async def one(it):
            async with sem:
                det = await ctx.fetch.json(f"https://huggingface.co/api/datasets/{it['id']}")
            files = [{"url": f"https://huggingface.co/datasets/{it['id']}/resolve/main/{s['rfilename']}", "name": s["rfilename"]}
                     for s in (det or {}).get("siblings", []) if DATA_EXT.search(s.get("rfilename", ""))]
            return await emit(rec("hf_dataset_files", f"https://huggingface.co/datasets/{it['id']}", it["id"],
                                  ", ".join((it.get("tags") or [])[:10]), it.get("lastModified"), t,
                                  files=files[:8], no_fetch=True))

        await asyncio.gather(*(one(it) for it in items[:40]))


async def code_archives(ctx: Ctx, emit: Emit):
    """Código: repos de GitHub/GitLab con actividad reciente, descargados como .zip (los 40 más relevantes)."""
    import os
    hdr = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    n = 0
    for t in ctx.terms:
        j = await ctx.fetch.json("https://api.github.com/search/repositories", headers=hdr, params={
            "q": f"{q_exact(t)} pushed:>={ctx.date_str}", "sort": "stars", "order": "desc", "per_page": "50"}, retries=1)
        for it in (j or {}).get("items", []):
            if n >= 40 or (it.get("size") or 0) > 60_000:  # tamaño en KB
                continue
            n += 1
            zipu = f"https://codeload.github.com/{it['full_name']}/zip/refs/heads/{it.get('default_branch') or 'main'}"
            if not await emit(rec("code_archives", it["html_url"] + "#zip", it["full_name"], it.get("description") or "",
                                  it.get("pushed_at"), t, stars=it.get("stargazers_count"),
                                  files=[{"url": zipu, "name": it["full_name"].replace("/", "__") + ".zip"}], no_fetch=True)):
                return


async def github_releases(ctx: Ctx, emit: Emit):
    """GitHub Releases: instaladores, binarios y paquetes publicados por los propios proyectos."""
    import os
    hdr = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    budget = 25 if not os.environ.get("GITHUB_TOKEN") else 150  # sin token: 60 llamadas/hora
    sem = asyncio.Semaphore(4)
    for t in ctx.terms:
        j = await ctx.fetch.json("https://api.github.com/search/repositories", headers=hdr, params={
            "q": f"{q_exact(t)} pushed:>={ctx.date_str}", "sort": "stars", "order": "desc", "per_page": "60"}, retries=1)
        repos = [it for it in (j or {}).get("items", []) if it.get("stargazers_count", 0) > 0][:budget]

        async def one(it):
            async with sem:
                rel = await ctx.fetch.json(f"https://api.github.com/repos/{it['full_name']}/releases",
                                           headers=hdr, params={"per_page": "1"}, retries=0)
            if not rel:
                return True
            r0 = rel[0]
            files = [{"url": a["browser_download_url"], "name": a["name"], "size": a.get("size")}
                     for a in r0.get("assets", []) if not a["name"].endswith((".asc", ".sig", ".sha256", ".pem", ".sbom.json"))]
            if not files:
                files = [{"url": r0["zipball_url"], "name": f"{it['full_name'].replace('/', '__')}-{r0.get('tag_name')}.zip"}]
            return await emit(rec("github_releases", r0.get("html_url") or it["html_url"],
                                  f"{it['full_name']} {r0.get('tag_name', '')}", it.get("description") or "",
                                  r0.get("published_at") or it.get("pushed_at"), t, stars=it.get("stargazers_count"),
                                  files=files[:12], no_fetch=True))

        await asyncio.gather(*(one(it) for it in repos))


async def socrata(ctx: Ctx, emit: Emit):
    """Socrata: miles de portales de datos abiertos (ciudades, estados, agencias) con CSV directo."""
    cutoff = ctx.cutoff
    for t in ctx.terms:
        for off in range(0, 100 * ctx.pages, 100):
            j = await ctx.fetch.json("https://api.us.socrata.com/api/catalog/v1", params={
                "q": t, "only": "dataset", "limit": "100", "offset": str(off)})
            res = (j or {}).get("results", [])
            if not res:
                break
            for x in res:
                r, m = x.get("resource", {}), x.get("metadata", {})
                d = parse_date(r.get("data_updated_at") or r.get("updatedAt"))
                if d and d < cutoff:
                    continue
                dom, rid = m.get("domain"), r.get("id")
                files = [{"url": f"https://{dom}/api/views/{rid}/rows.csv?accessType=DOWNLOAD",
                          "name": f"{r.get('name', rid)[:80]}.csv"}]
                if not await emit(rec("socrata", x.get("permalink") or f"https://{dom}/d/{rid}", r.get("name"),
                                      r.get("description") or "", d, t, domain=dom, files=files, no_fetch=True)):
                    return
            if len(res) < 100:
                break


async def openaire(ctx: Ctx, emit: Emit):
    """OpenAIRE Graph: publicaciones, datasets y software científico de toda Europa (acceso abierto)."""
    from .extract import DOC_EXT, url_ext
    for t in ctx.terms:
        for page in range(1, min(ctx.pages, 5) + 1):
            j = await ctx.fetch.json("https://api.openaire.eu/graph/v1/researchProducts", params={
                "search": q_exact(t), "pageSize": "100", "page": str(page), "sortBy": "publicationDate DESC",
                "fromPublicationDate": ctx.date_str})
            res = (j or {}).get("results", [])
            if not res:
                break
            for x in res:
                urls = [u for i in x.get("instances", []) or [] for u in (i.get("urls") or [])]
                if not urls:
                    continue
                files = [{"url": u, "name": None} for u in urls if url_ext(u) in DOC_EXT][:6]
                desc = " ".join(x.get("descriptions") or [])[:800]
                if not await emit(rec("openaire", urls[0], x.get("mainTitle"), strip_html(desc), x.get("publicationDate"), t,
                                      type=x.get("type"), files=files, no_fetch=bool(files))):
                    return
            if len(res) < 100:
                break


async def hal(ctx: Ctx, emit: Emit):
    """HAL (Francia): 1,5M+ documentos científicos con PDF a texto completo."""
    days = ctx.days
    for t in ctx.terms:
        j = await ctx.fetch.json("https://api.archives-ouvertes.fr/search/", params={
            "q": q_exact(t), "fq": f"producedDate_tdate:[NOW-{days}DAYS TO NOW]", "rows": "200", "wt": "json",
            "sort": "producedDate_tdate desc", "fl": "title_s,uri_s,fileMain_s,producedDate_s,abstract_s,docType_s"})
        for d in ((j or {}).get("response") or {}).get("docs", []):
            title = (d.get("title_s") or [""])[0]
            files = [{"url": d["fileMain_s"], "name": None}] if d.get("fileMain_s") else []
            if not await emit(rec("hal", d.get("uri_s"), title, ((d.get("abstract_s") or [""])[0])[:800],
                                  d.get("producedDate_s"), t, type=d.get("docType_s"), files=files, no_fetch=bool(files))):
                return


OAI_ENDPOINTS = {"zenodo": "https://zenodo.org/oai2d", "hal": "https://api.archives-ouvertes.fr/oai/hal/"}


async def oai_pmh(ctx: Ctx, emit: Emit):
    """Cliente OAI-PMH genérico: recorre por fecha los registros nuevos de repositorios académicos y filtra por término."""
    import xml.etree.ElementTree as ET
    ns = {"o": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
    from .extract import DOC_EXT, url_ext

    async def one(name, base):
        params = {"verb": "ListRecords", "metadataPrefix": "oai_dc", "from": ctx.date_str}
        for _ in range(ctx.pages * 10):
            r = await ctx.fetch.get(base, params=params, timeout=40)
            if r is None or not r.ok:
                return
            try:
                root = ET.fromstring(r.content)
            except ET.ParseError:
                return
            for recd in root.iterfind(".//o:record", ns):
                md = recd.find(".//o:metadata", ns)
                if md is None:
                    continue
                title = " ".join(x.text or "" for x in md.iterfind(".//dc:title", ns))
                desc = " ".join(x.text or "" for x in md.iterfind(".//dc:description", ns))
                if not on_topic(ctx, title, desc[:2000]):
                    continue
                ids = [x.text for x in md.iterfind(".//dc:identifier", ns) if (x.text or "").startswith("http")]
                if not ids:
                    continue
                date = next((x.text for x in md.iterfind(".//dc:date", ns)), None)
                files = [{"url": u, "name": None} for u in ids if url_ext(u) in DOC_EXT]
                if not await emit(rec("oai_pmh", ids[0], title, desc[:800], date, f"oai:{name}", repo=name,
                                      files=files[:6], no_fetch=bool(files))):
                    return
            tok = root.find(".//o:resumptionToken", ns)
            if tok is None or not (tok.text or "").strip():
                return
            params = {"verb": "ListRecords", "resumptionToken": tok.text.strip()}

    await asyncio.gather(*(one(n, b) for n, b in OAI_ENDPOINTS.items()))


async def pypi(ctx: Ctx, emit: Emit):
    """PyPI: paquetes nuevos y actualizados recientemente que coinciden con el término, con sus ficheros."""
    import feedparser
    seen = set()
    for feed in ("https://pypi.org/rss/packages.xml", "https://pypi.org/rss/updates.xml"):
        r = await ctx.fetch.get(feed)
        if r is None or not r.ok:
            continue
        for e in feedparser.parse(r.content).entries:
            name = (e.get("title") or "").split(" ")[0]
            if not name or name in seen or not on_topic(ctx, e.get("title"), e.get("summary")):
                continue
            seen.add(name)
            j = await ctx.fetch.json(f"https://pypi.org/pypi/{name}/json")
            if not j:
                continue
            info = j.get("info", {})
            files = [{"url": u["url"], "name": u["filename"], "size": u.get("size")} for u in j.get("urls", [])]
            if not await emit(rec("pypi", info.get("package_url") or f"https://pypi.org/project/{name}/",
                                  f"{name} {info.get('version', '')}", info.get("summary") or "",
                                  e.get("published"), "pypi-rss", files=files[:6], no_fetch=True)):
                return


RESOURCE_SOURCES = {
    "hal": (hal, "HAL: documentos científicos con PDF a texto completo"),
    "oai_pmh": (oai_pmh, "OAI-PMH: registros nuevos de repositorios académicos (Zenodo, HAL)"),
    "pypi": (pypi, "PyPI: paquetes de Python recientes con sus ficheros"),
    "openaire": (openaire, "OpenAIRE: publicaciones, datasets y software científico europeo"),
    "github_releases": (github_releases, "GitHub Releases: instaladores y binarios publicados"),
    "socrata": (socrata, "Socrata: miles de portales de datos abiertos con CSV"),
    "zenodo": (zenodo, "Zenodo: datasets/software/papers con ficheros"),
    "figshare": (figshare, "Figshare: ficheros de investigación"),
    "eu_open_data": (eu_open_data, "data.europa.eu: datos abiertos de toda la UE (incl. datos.gob.es)"),
    "uk_open_data": (uk_open_data, "data.gov.uk: datos abiertos de Reino Unido"),
    "archive_files": (archive_files, "Internet Archive: ficheros originales de subidas recientes"),
    "hf_dataset_files": (hf_dataset_files, "Hugging Face: ficheros de datasets"),
    "code_archives": (code_archives, "GitHub: repos recientes descargados en .zip"),
}

from .sources import SOURCES  # noqa: E402

SOURCES.update(RESOURCE_SOURCES)
