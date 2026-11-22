"""Adaptadores de fuentes. Cada fuente es `async def fuente(ctx, emit)`; llama a `await emit(registro)`
(devuelve False cuando la fuente alcanzó su cupo) y filtra por fecha en origen siempre que la API lo permita."""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import os
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import quote, quote_plus, unquote

import feedparser
from selectolax.lexbor import LexborHTMLParser

from .net import Fetcher
from .util import bing_unwrap, clean_ws, iso, now, parse_date, relative_date

Emit = Callable[[dict], Awaitable[bool]]


@dataclass
class Ctx:
    fetch: Fetcher
    terms: list[str]
    since: dt.timedelta
    langs: list[str]                 # ['es-ES', 'en-US']
    pages: int = 5                   # páginas por consulta en buscadores
    max_per_source: int = 1000
    variants: list[str] = field(default_factory=list)   # consultas avanzadas para buscadores web
    log: Callable[[str], None] = print

    @property
    def cutoff(self) -> dt.datetime:
        return now() - self.since

    @property
    def days(self) -> int:
        return max(1, int(self.since.total_seconds() // 86400) + (1 if self.since.total_seconds() % 86400 else 0))

    @property
    def date_str(self) -> str:
        return self.cutoff.strftime("%Y-%m-%d")


def demojibake(s: str) -> str:
    if s and ("Ã" in s or "â€" in s):
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def rec(source: str, url: str, title: str = "", snippet: str = "", date=None, query: str = "", **extra) -> dict:
    d = parse_date(date) if not isinstance(date, dt.datetime) else date
    return {
        "url": url,
        "title": clean_ws(demojibake(html.unescape(title or "")), 500),
        "snippet": clean_ws(demojibake(html.unescape(snippet or "")), 1500),
        "date": iso(d),
        "source": source,
        "query": query,
        **({"extra": extra} if extra else {}),
    }


def strip_html(s: str) -> str:
    if not s:
        return ""
    try:
        return clean_ws(LexborHTMLParser(s).text(separator=" "))
    except Exception:
        return clean_ws(re.sub(r"<[^>]+>", " ", s))


def html_links(s: str) -> list[str]:
    if not s:
        return []
    out = []
    for a in LexborHTMLParser(s).css("a[href]"):
        cls = a.attributes.get("class") or ""
        h = a.attributes.get("href") or ""
        if h.startswith("http") and "mention" not in cls and "hashtag" not in cls and "/tags/" not in h:
            out.append(h)
    return out


def q_exact(t: str) -> str:
    return f'"{t}"' if " " in t.strip() else t


def lang_parts(lang: str) -> tuple[str, str]:
    l, _, c = lang.partition("-")
    return l.lower(), (c or l).upper()


# =========================================================================== buscadores web
def yahoo_unwrap(h: str) -> str:
    m = re.search(r"/RU=([^/]+)/R[KS]=", h or "")
    return unquote(m.group(1)) if m else h


async def yahoo_web(ctx: Ctx, emit: Emit):
    """Yahoo (índice de Bing) con filtro de frescura btf y operadores avanzados (intitle:, filetype:, site:...)."""
    days = ctx.since.total_seconds() / 86400
    btf = "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 31 else None
    blocked = asyncio.Event()
    jobs = [(q, None) for q in ctx.variants]
    for lang in ctx.langs:
        l, _ = lang_parts(lang)
        jobs += [(q_exact(t), f"lang_{l}") for t in ctx.terms]

    base = {q_exact(t) for t in ctx.terms} | set(ctx.terms)

    async def one(q: str, vl):
        seen = set()
        # consultas base: todas las páginas; variantes con operadores: 2 páginas (Yahoo limita el ritmo)
        for p in range(ctx.pages if (q in base or vl) else min(ctx.pages, 2)):
            if blocked.is_set():
                return
            params = {"p": q, "b": str(p * 7 + 1)}
            if btf:
                params["btf"] = btf
            if vl:
                params["vl"] = vl
            r = await ctx.fetch.get("https://search.yahoo.com/search", params=params, retries=3)
            if r is None or not r.ok:
                if p == 0 and (q, vl) not in failed:
                    failed.append((q, vl))        # se reintenta al final, tras una pausa
                return
            items = LexborHTMLParser(r.text).css("div.algo")
            if not items:
                if "captcha" in r.text.lower():
                    blocked.set()
                    ctx.log("[yahoo_web] captcha detectado: fuente pausada")
                return
            new = 0
            for d in items:
                a = d.css_first(".compTitle a")
                if not a:
                    continue
                url = yahoo_unwrap(a.attributes.get("href") or "")
                if not url.startswith("http") or url in seen or "yahoo.com" in url.split("/")[2]:
                    continue
                seen.add(url)
                new += 1
                h3 = a.css_first("h3")
                cap = d.css_first(".compText")
                snip = cap.text(separator=" ") if cap else ""
                date = None
                m = re.match(r"\s*([^·•]{3,30}?)\s*[·•]", snip)
                if m:
                    date = relative_date(m.group(1)) or parse_date(m.group(1))
                    snip = snip[m.end():]
                if not await emit(rec("yahoo_web", url, (h3 or a).text(), snip, date, q, rank=p * 7 + new)):
                    return
            if new == 0:
                return

    failed: list = []
    await asyncio.gather(*(one(q, vl) for q, vl in jobs))
    if failed and not blocked.is_set():           # segunda vuelta: Yahoo devuelve 500 a ráfagas y se recupera solo
        again, n0 = list(failed), len(failed)
        ctx.log(f"[yahoo_web] {n0} consultas fallaron: pausa de 20 s y segundo intento, de una en una")
        await asyncio.sleep(20)
        for q, vl in again:
            await one(q, vl)
        ctx.log("[yahoo_web] segundo intento terminado")


async def ddg_meta(ctx: Ctx, emit: Emit):
    """Metabuscador `ddgs` (rota entre Google/Bing/Yandex/Mullvad/...): texto + noticias."""
    from ddgs import DDGS

    tl = "d" if ctx.since <= dt.timedelta(days=1) else "w" if ctx.since <= dt.timedelta(days=7) \
        else "m" if ctx.since <= dt.timedelta(days=31) else "y"

    def run(q, kind, region):
        out = []
        # ddgs 9.16 ya no trae mullvad_google ni bing: se prueban los motores de texto que sí existen
        backends = ["duckduckgo", "brave", "mojeek", "startpage"] if kind == "text" else ["auto"]
        for b in backends:  # en ddgs una lista combinada falla entera si un motor falla: uno a uno
            try:
                d = DDGS(timeout=15)
                f = d.text if kind == "text" else d.news
                out += f(q, region=region, timelimit=tl, max_results=min(ctx.max_per_source, 100), backend=b) or []
            except Exception:
                pass
        return out

    jobs = []
    for lang in ctx.langs:
        l, c = lang_parts(lang)
        region = f"{c.lower()}-{l}"
        for q in ctx.variants[:6]:
            jobs.append((q, "text", region))
        for t in ctx.terms:
            jobs.append((q_exact(t), "news", region))
    sem = asyncio.Semaphore(4)

    async def job(q, kind, region):
        async with sem:
            res = await asyncio.to_thread(run, q, kind, region)
        for r in res:
            url = r.get("href") or r.get("url")
            if url and not await emit(rec(f"ddg_{kind}", url, r.get("title"), r.get("body"), r.get("date"), q,
                                          publisher=r.get("source"))):
                return

    await asyncio.gather(*(job(*j) for j in jobs))


# =========================================================================== noticias
async def bing_news(ctx: Ctx, emit: Emit):
    h = ctx.since.total_seconds() / 3600
    interval = "4" if h <= 1 else "7" if h <= 24 else "8" if h <= 168 else "9"
    for lang in ctx.langs:
        l, c = lang_parts(lang)
        for t in ctx.terms:
            seen = set()
            for p in range(max(1, ctx.pages // 2)):
                r = await ctx.fetch.get("https://www.bing.com/news/search", params={
                    "q": q_exact(t), "format": "rss", "count": "100", "first": str(p * 100 + 1),
                    "qft": f'interval="{interval}"', "setlang": l, "cc": c, "sortbydate": "1"})
                if r is None or not r.ok:
                    break
                feed = feedparser.parse(r.content)
                new = 0
                for e in feed.entries:
                    url = bing_unwrap(e.get("link", ""))
                    if url in seen:
                        continue
                    seen.add(url)
                    new += 1
                    if not await emit(rec("bing_news", url, e.get("title"), strip_html(e.get("summary", "")),
                                          e.get("published"), t, publisher=e.get("news_source"), lang=lang)):
                        return
                if new == 0:
                    break


async def google_news(ctx: Ctx, emit: Emit):
    h = ctx.since.total_seconds() / 3600
    when = f"{max(1, int(h))}h" if h <= 48 else f"{ctx.days}d"
    for lang in ctx.langs:
        l, c = lang_parts(lang)
        for t in ctx.terms:
            for q in (q_exact(t), f"intitle:{q_exact(t)}"):
                r = await ctx.fetch.get(
                    f"https://news.google.com/rss/search?q={quote_plus(q + ' when:' + when)}&hl={l}-{c}&gl={c}&ceid={c}:{l}")
                if r is None or not r.ok:
                    continue
                for e in feedparser.parse(r.content).entries:
                    src = e.get("source") or {}
                    if not await emit(rec("google_news", e.get("link", ""), e.get("title"), "", e.get("published"), q,
                                          publisher=src.get("title"), publisher_url=src.get("href"), lang=lang,
                                          no_fetch=True)):
                        return


async def gdelt(ctx: Ctx, emit: Emit):
    """GDELT DOC 2.0: medios de todo el mundo, en 65 idiomas, actualizado cada 15 min."""
    h = ctx.since.total_seconds() / 3600
    span = f"{max(1, int(h))}h" if h <= 72 else f"{min(ctx.days, 90)}d"
    # una sola consulta OR (GDELT limita a 1 petición / 5 s por IP) con presupuesto total acotado
    query = " OR ".join(q_exact(t) for t in ctx.terms)
    if len(ctx.terms) > 1:
        query = f"({query})"
    for t in [query]:
        try:
            r = await asyncio.wait_for(ctx.fetch.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": t, "mode": "artlist", "maxrecords": "250", "format": "json",
                "timespan": span, "sort": "datedesc"}, retries=3), timeout=90)
        except asyncio.TimeoutError:
            ctx.log("[gdelt] sin respuesta en 90 s (límite por IP)")
            return
        if r is None or not r.ok:
            ctx.log(f"[gdelt] HTTP {r.status if r else 'error'} (límite por IP)")
            return
        try:
            arts = r.json().get("articles", [])
        except ValueError:
            continue
        for a in arts:
            if not await emit(rec("gdelt", a.get("url"), a.get("title"), "", a.get("seendate"), t,
                                  domain=a.get("domain"), lang=a.get("language"), country=a.get("sourcecountry"))):
                return


# =========================================================================== comunidades / redes
async def hackernews(ctx: Ctx, emit: Emit):
    ts = int(ctx.cutoff.timestamp())
    for t in ctx.terms:
        for tags in ("story", "comment"):
            page = 0
            while True:
                j = await ctx.fetch.json("https://hn.algolia.com/api/v1/search_by_date", params={
                    "query": t, "tags": tags, "numericFilters": f"created_at_i>{ts}", "hitsPerPage": "200", "page": str(page)})
                if not j:
                    break
                for hh in j.get("hits", []):
                    item = f"https://news.ycombinator.com/item?id={hh.get('objectID')}"
                    if tags == "story":
                        r = rec("hackernews", hh.get("url") or item, hh.get("title"), strip_html(hh.get("story_text") or ""),
                                hh.get("created_at"), t, hn=item, points=hh.get("points"), comments=hh.get("num_comments"))
                    else:
                        body = hh.get("comment_text") or ""
                        r = rec("hackernews_comment", item, f"HN: {hh.get('story_title') or ''}", strip_html(body),
                                hh.get("created_at"), t, links=html_links(body), no_fetch=True)
                    if not await emit(r):
                        return
                page += 1
                if page >= j.get("nbPages", 0) or page >= 20:
                    break


async def mastodon(ctx: Ctx, emit: Emit):
    instances = ["mastodon.social", "fosstodon.org", "infosec.exchange", "hachyderm.io", "mstdn.social",
                 "mastodon.online", "techhub.social", "masto.es"]
    tags = {re.sub(r"[^\w]", "", t.lower()) for t in ctx.terms}  # 'copias de seguridad' -> #copiasdeseguridad
    cutoff = ctx.cutoff

    async def one(inst, tag):
        max_id = None
        for _ in range(ctx.pages * 2):
            params = {"limit": "40"}
            if max_id:
                params["max_id"] = max_id
            j = await ctx.fetch.json(f"https://{inst}/api/v1/timelines/tag/{quote(tag)}", params=params)
            if not j or not isinstance(j, list):
                return
            for s in j:
                d = parse_date(s.get("created_at"))
                if d and d < cutoff:
                    return
                content = s.get("content", "")
                acct = (s.get("account") or {}).get("acct", "")
                links = html_links(content)
                card = s.get("card") or {}
                if card.get("url"):
                    links.append(card["url"])
                text = strip_html(content)
                if not await emit(rec("mastodon", s.get("url") or s.get("uri"), f"@{acct}: {text[:90]}", text, d, f"#{tag}",
                                      instance=inst, links=links, reblogs=s.get("reblogs_count"),
                                      favs=s.get("favourites_count"), no_fetch=True)):
                    return
            max_id = j[-1].get("id") if j else None
            if not max_id:
                return

    await asyncio.gather(*(one(i, tg) for i in instances for tg in tags if tg))


async def bluesky(ctx: Ctx, emit: Emit):
    since = ctx.cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    for t in ctx.terms:
        cursor = None
        for _ in range(ctx.pages * 3):
            params = {"q": q_exact(t), "sort": "latest", "limit": "100", "since": since}
            if cursor:
                params["cursor"] = cursor
            j = await ctx.fetch.json("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts", params=params)
            if not j:
                break
            for p in j.get("posts", []):
                a = p.get("author", {})
                rk = p.get("uri", "").rsplit("/", 1)[-1]
                r = p.get("record", {})
                links = [f.get("uri") for fc in r.get("facets", []) or [] for f in fc.get("features", [])
                         if f.get("uri")]
                ext = ((p.get("embed") or {}).get("external") or {}).get("uri")
                if ext:
                    links.append(ext)
                text = r.get("text", "")
                if not await emit(rec("bluesky", f"https://bsky.app/profile/{a.get('handle')}/post/{rk}",
                                      f"@{a.get('handle')}: {text[:90]}", text, r.get("createdAt") or p.get("indexedAt"), t,
                                      links=links, likes=p.get("likeCount"), reposts=p.get("repostCount"), no_fetch=True)):
                    return
            cursor = j.get("cursor")
            if not cursor:
                break


async def stackexchange(ctx: Ctx, emit: Emit):
    sites = ["stackoverflow", "serverfault", "superuser", "unix", "askubuntu", "security", "dba",
             "softwareengineering", "datascience", "devops", "es.stackoverflow"]
    fromdate = int(ctx.cutoff.timestamp())
    key = os.environ.get("STACKEXCHANGE_KEY")

    async def one(site):
        for t in ctx.terms:
            if await one_term(site, t) is False:
                return

    async def one_term(site, t):
        for page in range(1, 3):
            params = {"q": t, "site": site, "sort": "creation", "order": "desc",
                      "fromdate": str(fromdate), "pagesize": "100", "page": str(page)}
            if key:
                params["key"] = key
            j = await ctx.fetch.json("https://api.stackexchange.com/2.3/search/advanced", params=params)
            if not j:
                return
            for it in j.get("items", []):
                if not await emit(rec("stackexchange", it.get("link"), it.get("title"), ", ".join(it.get("tags", [])),
                                      it.get("creation_date"), site, score=it.get("score"),
                                      answers=it.get("answer_count"), answered=it.get("is_answered"))):
                    return False
            if j.get("backoff"):
                await asyncio.sleep(int(j["backoff"]))
            if not j.get("has_more"):
                return

    await asyncio.gather(*(one(s) for s in sites))


async def youtube(ctx: Ctx, emit: Emit):
    h = ctx.since.total_seconds() / 3600
    sp = "EgIIAQ%3D%3D" if h <= 1 else "EgIIAg%3D%3D" if h <= 24 else "EgIIAw%3D%3D" if h <= 168 \
        else "EgIIBA%3D%3D" if h <= 744 else "EgIIBQ%3D%3D"
    for lang in ctx.langs[:2]:
        l, c = lang_parts(lang)
        for t in ctx.terms:
            r = await ctx.fetch.get(f"https://www.youtube.com/results?search_query={quote_plus(t)}&sp={sp}&hl={l}&gl={c}",
                                    headers={"Cookie": "CONSENT=YES+1; SOCS=CAI"})
            if r is None or not r.ok:
                continue
            m = re.search(r"ytInitialData\s*=\s*(\{.+?\});\s*</script>", r.text, re.S)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
            except ValueError:
                continue
            stack = [data]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    v = o.get("videoRenderer")
                    if v and v.get("videoId"):
                        title = "".join(x.get("text", "") for x in v.get("title", {}).get("runs", []))
                        desc = "".join(x.get("text", "") for x in (v.get("detailedMetadataSnippets") or [{}])[0]
                                       .get("snippetText", {}).get("runs", []))
                        when = (v.get("publishedTimeText") or {}).get("simpleText", "")
                        ch = "".join(x.get("text", "") for x in v.get("ownerText", {}).get("runs", []))
                        if not await emit(rec("youtube", f"https://www.youtube.com/watch?v={v['videoId']}", title, desc,
                                              relative_date(when), t, channel=ch, published=when,
                                              views=(v.get("viewCountText") or {}).get("simpleText"), no_fetch=True)):
                            return
                        continue
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)


# =========================================================================== código / modelos / paquetes
async def github(ctx: Ctx, emit: Emit):
    hdr = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        hdr["Authorization"] = f"Bearer {tok}"
        ctx.fetch.set_interval("api.github.com", 2.1)
    d = ctx.date_str
    for t in ctx.terms:
        for kind, q, sort in (("repositories", f"{q_exact(t)} pushed:>={d}", "updated"),
                              ("issues", f"{q_exact(t)} created:>={d}", "created")):
            for page in range(1, (min(ctx.pages, 5) if tok else 2) + 1):  # sin token: 10 búsquedas/min
                j = await ctx.fetch.json(f"https://api.github.com/search/{kind}", headers=hdr, params={
                    "q": q, "sort": sort, "order": "desc", "per_page": "100", "page": str(page)}, retries=1)
                if not j or not j.get("items"):
                    break
                for it in j["items"]:
                    if kind == "repositories":
                        r = rec("github_repo", it.get("html_url"), it.get("full_name"), it.get("description") or "",
                                it.get("pushed_at"), q, stars=it.get("stargazers_count"), lang=it.get("language"),
                                topics=it.get("topics"))
                    else:
                        r = rec("github_issue", it.get("html_url"), it.get("title"), (it.get("body") or "")[:1000],
                                it.get("created_at"), q, state=it.get("state"), comments=it.get("comments"))
                    if not await emit(r):
                        return
                if len(j["items"]) < 100:
                    break


async def gitlab(ctx: Ctx, emit: Emit):
    for t in ctx.terms:
        for page in range(1, ctx.pages + 1):
            j = await ctx.fetch.json("https://gitlab.com/api/v4/projects", params={
                "search": t, "order_by": "last_activity_at", "sort": "desc", "per_page": "100", "page": str(page),
                "last_activity_after": ctx.cutoff.isoformat(), "simple": "true"})
            if not j:
                break
            for p in j:
                if not await emit(rec("gitlab", p.get("web_url"), p.get("path_with_namespace"), p.get("description") or "",
                                      p.get("last_activity_at"), t, stars=p.get("star_count"))):
                    return
            if len(j) < 100:
                break


async def huggingface(ctx: Ctx, emit: Emit):
    cutoff = ctx.cutoff
    for t in ctx.terms:
        for kind, prefix in (("models", ""), ("datasets", "datasets/"), ("spaces", "spaces/")):
            j = await ctx.fetch.json(f"https://huggingface.co/api/{kind}", params={
                "search": t, "sort": "lastModified", "direction": "-1", "limit": "200"})
            for it in j or []:
                d = parse_date(it.get("lastModified") or it.get("createdAt"))
                if d and d < cutoff:
                    break
                if not await emit(rec(f"hf_{kind}", f"https://huggingface.co/{prefix}{it.get('id')}", it.get("id"),
                                      ", ".join((it.get("tags") or [])[:12]), d, t, likes=it.get("likes"),
                                      downloads=it.get("downloads"))):
                    return


async def npm(ctx: Ctx, emit: Emit):
    cutoff = ctx.cutoff
    for t in ctx.terms:
        for frm in range(0, 750, 250):
            j = await ctx.fetch.json("https://registry.npmjs.org/-/v1/search", params={"text": t, "size": "250", "from": str(frm)})
            objs = (j or {}).get("objects", [])
            for o in objs:
                p = o.get("package", {})
                d = parse_date(p.get("date"))
                if d and d < cutoff:
                    continue
                if not await emit(rec("npm", (p.get("links") or {}).get("npm") or f"https://www.npmjs.com/package/{p.get('name')}",
                                      f"{p.get('name')} {p.get('version', '')}", p.get("description") or "", d, t,
                                      keywords=p.get("keywords"), weekly=(o.get("downloads") or {}).get("weekly"))):
                    return
            if len(objs) < 250:
                break


# =========================================================================== conocimiento / academia / archivo
async def wikipedia(ctx: Ctx, emit: Emit):
    cutoff = ctx.cutoff
    for l in dict.fromkeys(lang_parts(x)[0] for x in ctx.langs):
        for t in ctx.terms:
            j = await ctx.fetch.json(f"https://{l}.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": q_exact(t), "srsort": "last_edit_desc", "srlimit": "100",
                "srprop": "timestamp|snippet|size", "format": "json"})
            for s in ((j or {}).get("query") or {}).get("search", []):
                d = parse_date(s.get("timestamp"))
                if d and d < cutoff:
                    break
                if not await emit(rec("wikipedia", f"https://{l}.wikipedia.org/wiki/{quote(s['title'].replace(' ', '_'))}",
                                      s.get("title"), strip_html(s.get("snippet", "")), d, t, lang=l, edited=True)):
                    return


async def arxiv(ctx: Ctx, emit: Emit):
    cutoff = ctx.cutoff
    for t in ctx.terms:
        q = "all:" + (f'"{t}"' if " " in t else t)
        r = await ctx.fetch.get(f"https://export.arxiv.org/api/query?search_query={quote(q)}"
                                f"&sortBy=submittedDate&sortOrder=descending&max_results=300")
        if r is None or not r.ok:
            continue
        for e in feedparser.parse(r.content).entries:
            d = parse_date(e.get("published"))
            if d and d < cutoff:
                break
            pdf = next((l.get("href") for l in e.get("links", []) if l.get("type") == "application/pdf"), None)
            if not await emit(rec("arxiv", e.get("link"), e.get("title"), e.get("summary", "")[:1200], d, t,
                                  authors=[a.get("name") for a in e.get("authors", [])], pdf=pdf)):
                return


async def openalex(ctx: Ctx, emit: Emit):
    for t in ctx.terms:
        cursor = "*"
        for _ in range(min(ctx.pages, 4)):
            j = await ctx.fetch.json("https://api.openalex.org/works", params={
                "search": t, "filter": f"from_publication_date:{ctx.date_str}", "sort": "publication_date:desc",
                "per-page": "100", "cursor": cursor})
            if not j:
                break
            for w in j.get("results", []):
                inv = w.get("abstract_inverted_index") or {}
                words = sorted(((p, k) for k, ps in inv.items() for p in ps))
                abstract = " ".join(k for _, k in words[:220])
                loc = w.get("primary_location") or {}
                url = loc.get("landing_page_url") or w.get("doi") or w.get("id")
                if not await emit(rec("openalex", url, w.get("display_name"), abstract, w.get("publication_date"), t,
                                      doi=w.get("doi"), pdf=loc.get("pdf_url"), cited=w.get("cited_by_count"))):
                    return
            cursor = (j.get("meta") or {}).get("next_cursor")
            if not cursor:
                break


async def crossref(ctx: Ctx, emit: Emit):
    today = now().strftime("%Y-%m-%d")
    for t in ctx.terms:
        j = await ctx.fetch.json("https://api.crossref.org/works", params={
            "query": t, "filter": f"from-pub-date:{ctx.date_str},until-pub-date:{today}", "sort": "published",
            "order": "desc", "rows": "200"})
        for it in ((j or {}).get("message") or {}).get("items", []):
            dp = ((it.get("published") or it.get("created") or {}).get("date-parts") or [[None]])[0]
            date = "-".join(f"{x:02d}" for x in dp if x) if dp and dp[0] else (it.get("created") or {}).get("date-time")
            if not await emit(rec("crossref", it.get("URL"), (it.get("title") or [""])[0], strip_html(it.get("abstract", "")),
                                  date, t, doi=it.get("DOI"), type=it.get("type"),
                                  publisher=it.get("publisher"))):
                return


async def internet_archive(ctx: Ctx, emit: Emit):
    for t in ctx.terms:
        q = f"({q_exact(t)}) AND publicdate:[{ctx.date_str} TO 9999-12-31]"
        j = await ctx.fetch.json("https://archive.org/advancedsearch.php", params=[
            ("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "publicdate"), ("fl[]", "description"),
            ("fl[]", "mediatype"), ("sort[]", "publicdate desc"), ("rows", "300"), ("output", "json")])
        for d in ((j or {}).get("response") or {}).get("docs", []):
            desc = d.get("description")
            desc = " ".join(desc) if isinstance(desc, list) else desc or ""
            if not await emit(rec("internet_archive", f"https://archive.org/details/{d.get('identifier')}",
                                  d.get("title"), strip_html(desc), d.get("publicdate"), t, mediatype=d.get("mediatype"))):
                return


async def devto(ctx: Ctx, emit: Emit):
    cutoff = ctx.cutoff
    tags = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in ctx.terms}
    for tag in tags:
        for page in range(1, ctx.pages + 1):
            j = await ctx.fetch.json("https://dev.to/api/articles/latest", params={"tag": tag, "per_page": "100", "page": str(page)})
            if not j:
                break
            stop = False
            for a in j:
                d = parse_date(a.get("published_at"))
                if d and d < cutoff:
                    stop = True
                    break
                if not await emit(rec("devto", a.get("url"), a.get("title"), a.get("description"), d, tag,
                                      tags=a.get("tag_list"), reactions=a.get("public_reactions_count"))):
                    return
            if stop or len(j) < 100:
                break


SOURCES: dict[str, tuple[Callable, str]] = {
    "yahoo_web": (yahoo_web, "Yahoo/Bing web: operadores avanzados + filtro de frescura"),
    "ddg_meta": (ddg_meta, "Metabuscador ddgs (Google/Bing/Yandex/Mullvad...) web + noticias"),
    "bing_news": (bing_news, "Bing News RSS ordenado por fecha"),
    "google_news": (google_news, "Google News RSS con when:Nd/Nh"),
    "gdelt": (gdelt, "GDELT: prensa mundial en 65 idiomas cada 15 min"),
    "hackernews": (hackernews, "Hacker News historias + comentarios (Algolia)"),
    "mastodon": (mastodon, "Fediverso: timelines de hashtag en 8 instancias"),
    "bluesky": (bluesky, "Bluesky búsqueda de posts más recientes"),
    "stackexchange": (stackexchange, "11 sitios de Stack Exchange"),
    "youtube": (youtube, "Vídeos de YouTube por fecha de subida"),
    "github": (github, "GitHub repos activos + issues nuevos"),
    "gitlab": (gitlab, "GitLab proyectos con actividad reciente"),
    "huggingface": (huggingface, "Hugging Face modelos, datasets y spaces"),
    "npm": (npm, "Paquetes npm publicados recientemente"),
    "wikipedia": (wikipedia, "Artículos de Wikipedia editados recientemente"),
    "arxiv": (arxiv, "Preprints de arXiv por fecha de envío"),
    "openalex": (openalex, "OpenAlex: 250M+ trabajos académicos"),
    "crossref": (crossref, "Crossref: publicaciones con DOI"),
    "internet_archive": (internet_archive, "Internet Archive: subidas recientes"),
    "devto": (devto, "Artículos dev.to por etiqueta"),
}
