"""Cliente HTTP asíncrono: HTTP/2, concurrencia global + por host, ritmo por host, reintentos con backoff,
límite de tamaño de descarga y marcado de hosts degradados (403/429/captcha)."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
BOT_UA = "DataMinerSpider/1.0"  # token que buscamos en robots.txt

# segundos mínimos entre peticiones a estos hosts (límites públicos documentados o observados)
HOST_INTERVAL = {
    "api.gdeltproject.org": 5.5,
    "api.github.com": 6.5,  # sin token: 10 búsquedas/min
    "export.arxiv.org": 3.0,
    "www.bing.com": 0.6,
    "search.yahoo.com": 2.2,
    "news.google.com": 0.4,
    "api.stackexchange.com": 0.3,
    "www.youtube.com": 1.0,
    "api.openalex.org": 0.2,
}
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}


@dataclass
class Resp:
    status: int
    url: str
    headers: httpx.Headers
    content: bytes
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def ctype(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        m = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.I)
        enc = m.group(1) if m else "utf-8"
        try:
            return self.content.decode(enc, "replace")
        except LookupError:
            return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


@dataclass
class HostStat:
    requests: int = 0
    ok: int = 0
    errors: int = 0
    statuses: dict = field(default_factory=lambda: defaultdict(int))
    degraded: str | None = None


class Fetcher:
    def __init__(self, concurrency: int = 64, per_host: int = 4, timeout: float = 20.0, proxy: str | None = None):
        self.client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency),
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            proxy=proxy,
        )
        self.timeout = timeout
        self.sem = asyncio.Semaphore(concurrency)
        self.per_host = per_host
        self._host_sem: dict[str, asyncio.Semaphore] = {}
        self._host_next: dict[str, float] = defaultdict(float)
        self._interval = dict(HOST_INTERVAL)
        self.hosts: dict[str, HostStat] = defaultdict(HostStat)
        self.bytes = 0

    def _hs(self, host: str) -> asyncio.Semaphore:
        s = self._host_sem.get(host)
        if s is None:
            s = self._host_sem[host] = asyncio.Semaphore(1 if host in self._interval else self.per_host)
        return s

    async def _pace(self, host: str):
        iv = self._interval.get(host)
        if not iv:
            return
        t = time.monotonic()
        wait = self._host_next[host] - t
        self._host_next[host] = max(t, self._host_next[host]) + iv
        if wait > 0:
            await asyncio.sleep(wait)

    def set_interval(self, host: str, seconds: float):
        self._interval[host] = seconds

    async def get(self, url: str, *, params=None, headers=None, max_bytes: int | None = 6_000_000,
                  retries: int = 2, timeout: float | None = None, method: str = "GET", data=None) -> Resp | None:
        host = urlsplit(url).hostname or ""
        st = self.hosts[host]
        for attempt in range(retries + 1):
            resp = None
            err = None
            async with self.sem, self._hs(host):
                await self._pace(host)
                st.requests += 1
                kw = {"params": params, "headers": headers}
                if isinstance(data, (bytes, str)):
                    kw["content"] = data
                elif data:
                    kw["data"] = data
                if timeout:
                    kw["timeout"] = timeout

                async def _do():
                    async with self.client.stream(method, url, **kw) as r:
                        chunks, n, trunc = [], 0, False
                        limit = max_bytes if r.status_code == 200 else 65536
                        cl = r.headers.get("content-length", "")
                        if limit and cl.isdigit() and int(cl) > limit:  # demasiado grande: ni lo descargamos
                            return Resp(r.status_code, str(r.url), r.headers, b"", True)
                        async for ch in r.aiter_bytes():
                            chunks.append(ch)
                            n += len(ch)
                            if limit and n > limit:
                                trunc = True
                                break
                        self.bytes += n
                        return Resp(r.status_code, str(r.url), r.headers, b"".join(chunks), trunc)

                try:  # plazo TOTAL por petición: evita servidores que gotean bytes indefinidamente
                    resp = await asyncio.wait_for(_do(), timeout=(timeout or self.timeout) * 3)
                except asyncio.TimeoutError as e:
                    err = e
                except (httpx.HTTPError, OSError, ValueError) as e:  # timeouts, TLS, DNS, protocolo...
                    err = e
            if resp is not None:
                st.statuses[resp.status] += 1
                if resp.status == 200:
                    st.ok += 1
                    base = HOST_INTERVAL.get(host)
                    cur = self._interval.get(host)
                    if cur:  # recuperación del ritmo tras un frenazo adaptativo
                        self._interval[host] = max(base, cur * 0.85) if base else (cur * 0.85 if cur > 0.3 else 0)
                        if not base and self._interval[host] == 0:
                            del self._interval[host]
                    return resp
                if resp.status in RETRY_STATUS and attempt < retries:
                    ra = resp.headers.get("retry-after", "")
                    delay = float(ra) if ra.isdigit() else (2 ** (attempt + 1)) + random.random()
                    if resp.status == 429 or host in self._interval:  # frenamos al host de forma adaptativa
                        self._interval[host] = min(max(self._interval.get(host, 0.5) * 1.5, 1.0), 20.0)
                        delay = max(delay, self._interval[host] * 2)
                    await asyncio.sleep(min(delay, 30))
                    continue
                if resp.status in (403, 429) and not st.degraded:
                    st.degraded = f"HTTP {resp.status}"
                return resp
            st.errors += 1
            if attempt < retries:
                await asyncio.sleep(1 + attempt * 2 + random.random())
            else:
                st.statuses[type(err).__name__] += 1
        return None

    async def download(self, url: str, dest, *, max_bytes: int = 0, retries: int = 2, stall: float = 60.0) -> dict | None:
        """Descarga directa a disco, por trozos y con huella SHA-256 al vuelo: el fichero nunca se carga entero en memoria.
        Si un intento se corta, el siguiente continúa con Range desde lo ya escrito. Sin plazo total: solo se abandona
        si el servidor deja de enviar datos durante `stall` segundos. Devuelve None si no se pudo, o un dict con
        status, url final, headers, size, sha256, head (primeros bytes) y truncated (superaba max_bytes)."""
        import hashlib
        import os
        host = urlsplit(url).hostname or ""
        st = self.hosts[host]
        sha, size, head = hashlib.sha256(), 0, b""
        for attempt in range(retries + 1):
            try:
                async with self.sem, self._hs(host):
                    await self._pace(host)
                    st.requests += 1
                    hdr = {"Range": f"bytes={size}-"} if size else None
                    async with self.client.stream("GET", url, headers=hdr,
                                                  timeout=httpx.Timeout(stall, connect=15.0)) as r:
                        st.statuses[r.status_code] += 1
                        if r.status_code == 200 and size:  # el servidor ignora Range: empezar de cero
                            sha, size, head = hashlib.sha256(), 0, b""
                        elif r.status_code not in (200, 206):
                            if r.status_code in RETRY_STATUS and attempt < retries:
                                await asyncio.sleep(2 ** (attempt + 1))
                                continue
                            if r.status_code in (403, 429) and not st.degraded:
                                st.degraded = f"HTTP {r.status_code}"
                            return {"status": r.status_code, "url": str(r.url), "headers": r.headers, "size": 0,
                                    "sha256": "", "head": b"", "truncated": False}
                        cl = r.headers.get("content-length", "")
                        if max_bytes and cl.isdigit() and size + int(cl) > max_bytes:
                            return {"status": 200, "url": str(r.url), "headers": r.headers, "size": size + int(cl),
                                    "sha256": "", "head": b"", "truncated": True}
                        with open(dest, "ab" if size else "wb") as fh:
                            async for ch in r.aiter_bytes(262144):
                                fh.write(ch)
                                sha.update(ch)
                                if len(head) < 8192:
                                    head += ch[:8192 - len(head)]
                                size += len(ch)
                                self.bytes += len(ch)
                                if max_bytes and size > max_bytes:
                                    return {"status": 200, "url": str(r.url), "headers": r.headers, "size": size,
                                            "sha256": "", "head": head, "truncated": True}
                        st.ok += 1
                        return {"status": 200, "url": str(r.url), "headers": r.headers, "size": size,
                                "sha256": sha.hexdigest(), "head": head, "truncated": False}
            except (httpx.HTTPError, OSError, ValueError):
                st.errors += 1
                if attempt < retries:
                    await asyncio.sleep(1 + attempt * 2 + random.random())
        try:
            os.remove(dest)
        except OSError:
            pass
        return None

    async def json(self, url: str, **kw):
        r = await self.get(url, **kw)
        if r is None or not r.ok:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def close(self):
        await self.client.aclose()


class Robots:
    """Caché de robots.txt por host (respetado en el rastreo en profundidad)."""

    def __init__(self, fetcher: Fetcher, enabled: bool = True):
        self.f = fetcher
        self.enabled = enabled
        self.cache: dict[str, RobotFileParser | None] = {}
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        p = urlsplit(url)
        key = f"{p.scheme}://{p.netloc}"
        if key not in self.cache:
            async with self.locks[key]:
                if key not in self.cache:
                    rp = None
                    r = await self.f.get(key + "/robots.txt", retries=0, timeout=8, max_bytes=500_000)
                    if r is not None and r.ok:
                        rp = RobotFileParser()
                        rp.parse(r.text.splitlines())
                    elif r is not None and r.status in (401, 403):
                        rp = RobotFileParser()
                        rp.disallow_all = True
                    self.cache[key] = rp
        rp = self.cache[key]
        if rp is None:
            return True
        try:
            return rp.can_fetch(BOT_UA, url)
        except Exception:
            return True
