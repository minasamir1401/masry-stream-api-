import asyncio
import html
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from app.services.source_trace import record


BASE_URL = "https://animetna.com"
SHARE4MAX_HOSTS = {"share4max.com", "share4max.org"}
MP4UPLOAD_HOSTS = {"mp4upload.com", "www.mp4upload.com"}
KRAKEN_HOSTS = {"krakenfiles.com", "www.krakenfiles.com"}
SENDVID_HOSTS = {"sendvid.com", "www.sendvid.com"}
MIXDROP_HOSTS = {"mixdrop.ag", "mixdrop.co", "mixdrop.to", "mxdrop.click"}
DIRECT_PLAYER_HOSTS = MP4UPLOAD_HOSTS | KRAKEN_HOSTS | SENDVID_HOSTS
ANIME4UP_HOSTS = {"4c.c8ywrhg.shop", "4s.g22jrec.shop"}
VIDARA_HOSTS = {"vidaraa.cc", "vidara.so"}
UQLOAD_HOSTS = {"uqload.com", "www.uqload.com", "uqload.is", "www.uqload.is", "uqload.vc", "www.uqload.vc"}
HLS_MEDIA_SUFFIXES = (".friwduh.shop", ".s1q2105.com", ".97bf1.com", ".cloudwindow-route.com", ".tnmr.org")
VOE_HOSTS = {"voe.sx", "shannoncoldalready.com"}
LULUSTREAM_HOSTS = {"lulustream.com", "luluvdo.com"}
MP4_MEDIA_SUFFIXES = (".mp4upload.com", ".krakencloud.net", ".sendvid.com", ".vid3rb.com", ".mxcontent.net")
_episode_indexes = {}


async def collect_available(awaitables, timeout=12):
    """Keep completed mirrors even if another host never finishes responding."""
    tasks = [asyncio.create_task(item) for item in awaitables]
    pending = set(tasks)
    completed = set()
    try:
        if not tasks:
            return []
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            done, pending = await asyncio.wait(
                pending,
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            completed.update(done)
            working = sum(
                1 for t in completed
                if not t.cancelled() and t.exception() is None and t.result()
            )
            if working >= 4:
                deadline = min(deadline, time.monotonic() + 1.5)
        return [task.result() for task in tasks
                if task in completed and not task.cancelled() and task.exception() is None]
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def page_episode_index(markup):
    links = {}
    for url, label in SourcePage(markup).links:
        parsed = urlsplit(url)
        if parsed.hostname == "animetna.com" and parsed.path.startswith("/episode/"):
            canonical = f"{BASE_URL}{parsed.path}"
            links[canonical] = links.get(canonical, "") + " " + label
    episodes = {}
    for url, label in links.items():
        number = re.search(r"(?:الحلقة|حلقة|Episode)\s*([0-9]+)", label, re.I) or re.search(r"-episode-([0-9]+)(?:/|$)", url, re.I)
        if number:
            episodes[int(number[1])] = url
        elif len(links) == 1:
            episodes[1] = url
    return episodes


async def get_episode_index(client, id_mal, english, romaji):
    if not id_mal:
        return {}
    cached = _episode_indexes.get(id_mal)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    titles = list(dict.fromkeys(title for title in (english, romaji) if title))
    expected = {re.sub(r"\W+", "", title.casefold()) for title in titles}
    candidates = {}
    for title in titles:
        try:
            response = await client.get(f"{BASE_URL}/wp-json/animetna/v1/search", params={"q": title})
            response.raise_for_status()
            for candidate in response.json().get("items", []):
                url = candidate.get("url", "")
                if urlsplit(url).hostname == "animetna.com" and urlsplit(url).path.startswith("/anime/"):
                    candidates[url] = candidate
        except (httpx.HTTPError, ValueError):
            continue
    ranked = sorted(candidates.values(), key=lambda item: any(re.sub(r"\W+", "", str(item.get(key, "")).casefold()) in expected for key in ("en", "title")), reverse=True)
    for candidate in ranked[:6]:
        try:
            response = await client.get(candidate["url"])
            response.raise_for_status()
            if not re.search(rf"https?://(?:www\.)?myanimelist\.net/anime/{id_mal}(?![0-9])", response.text):
                continue
            episodes = page_episode_index(response.text)
            _episode_indexes[id_mal] = (time.monotonic() + 600, episodes)
            return episodes
        except httpx.HTTPError:
            continue
    return cached[1] if cached else {}


def is_anime4up_host(host):
    return bool(host and re.fullmatch(r"4[a-z]\.[a-z0-9]+\.shop", host))


async def playable_hls(client, url, media_info=None):
    """Check the playlist and a real media segment, rather than an HTML wrapper."""
    for _ in range(3):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(HLS_MEDIA_SUFFIXES):
            return False
        response = await client.get(url, timeout=6)
        response.raise_for_status()
        if not response.text.lstrip().startswith("#EXTM3U"):
            return False
        links = [line.strip() for line in response.text.splitlines() if line.strip() and not line.startswith("#")]
        if not links:
            return False
        url = urljoin(url, links[0])
        if "#EXT-X-STREAM-INF:" in response.text:
            if media_info is not None:
                resolution = re.search(r"RESOLUTION=([0-9]+)x([0-9]+)", response.text)
                if resolution:
                    media_info.update(width=int(resolution[1]), height=int(resolution[2]))
            continue
        duration = sum(float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", response.text))
        # Reject short promotional/paid-plan clips masquerading as an episode.
        if "#EXT-X-ENDLIST" in response.text and duration < 60:
            return False
        segment = urlsplit(url)
        if segment.scheme != "https" or not (segment.hostname or "").endswith(HLS_MEDIA_SUFFIXES):
            return False
        async with client.stream("GET", url, headers={"Range": "bytes=0-63"}, timeout=6) as sample:
            if sample.status_code not in (200, 206):
                return False
            async for chunk in sample.aiter_bytes():
                return bool(chunk) and not chunk.lstrip().lower().startswith((b"<html", b"<!doctype"))
    return False


async def vidara_video(client, url, name):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in VIDARA_HOSTS:
        return None
    match = re.fullmatch(r"/e/([A-Za-z0-9]+)", parsed.path)
    if not match:
        return None
    try:
        response = await client.post(f"https://{parsed.hostname}/api/stream", json={
            "filecode": match[1], "device": "web", "codecs": [],
        }, timeout=7)
        response.raise_for_status()
        media_url = response.json().get("streaming_url", "")
        if await playable_hls(client, media_url):
            return {"name": name, "type": "hls", "url": media_url}
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return None


async def hls_video(client, server, id_mal, episode):
    url = server["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not is_anime4up_host(parsed.hostname):
        return None
    if not re.fullmatch(rf"/Anime4up-S[12]/mal/{id_mal}/{episode}/sub/", parsed.path):
        return None
    try:
        response = await client.get(url, follow_redirects=True, timeout=7)
        response.raise_for_status()
        if not is_anime4up_host(response.url.host):
            return None
        match = re.search(r'(?:let|const|var)\s+streamUrl\s*=\s*("(?:[^"\\]|\\.)*")', response.text)
        if not match:
            if "تم منع تشغيل المشغل" in response.text or "غير مصرح" in response.text:
                record("embedding_denied", url)
            return None
        media_url = json.loads(match[1])
        media = urlsplit(media_url)
        if media.scheme != "https" or not (media.hostname or "").endswith(HLS_MEDIA_SUFFIXES):
            return None
        if not await playable_hls(client, media_url):
            return None
        result = {"name": server["name"], "type": "hls", "url": media_url}
        tracks = re.search(r"const tracks\s*=\s*(\[.*?\]);", response.text, re.S)
        for track in json.loads(tracks[1]) if tracks else []:
            subtitle = urlsplit(track.get("file", ""))
            if subtitle.scheme == "https" and is_anime4up_host(subtitle.hostname) and subtitle.path.endswith(".vtt"):
                result["subtitle_url"] = subtitle.geturl()
                break
        return result
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def direct_video(client, url, name):
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != "https" or host not in DIRECT_PLAYER_HOSTS:
        return None
    if host in MP4UPLOAD_HOSTS and not re.fullmatch(r"/embed-[a-z0-9]+\.html", parsed.path):
        return None
    if host in KRAKEN_HOSTS and not re.fullmatch(r"/embed-video/[A-Za-z0-9]+", parsed.path):
        return None
    if host in SENDVID_HOSTS and not re.fullmatch(r"/embed/[a-z0-9]+", parsed.path):
        return None
    try:
        response = await client.get(url, timeout=7, follow_redirects=True)
        response.raise_for_status()
        if response.url.host not in DIRECT_PLAYER_HOSTS:
            return None
        if host in MP4UPLOAD_HOSTS:
            match = re.search(r'player\.src\(\s*\{[^}]*src:\s*["\x27]([^"\x27]+)', response.text, re.S)
        else:
            match = re.search(r'<source\b[^>]*\ssrc=["\x27]([^"\x27]+)', response.text, re.I)
        if not match:
            record("missing_media_file", url)
            return None
        media_url = html.unescape(match[1])
        if not await playable_mp4(client, media_url):
            return None
        return {"name": name, "type": "direct", "url": media_url, "source_url": url}
    except httpx.HTTPError:
        return None


async def playable_mp4(client, media_url):
    media = urlsplit(media_url)
    suffixes = MP4_MEDIA_SUFFIXES
    if media.scheme != "https" or not (media.hostname or "").endswith(suffixes):
        return False
    async with client.stream("GET", media_url, headers={**media_request_headers(media_url), "Range": "bytes=0-31"}, timeout=6, follow_redirects=True) as response:
        if response.url.scheme != "https" or not (response.url.host or "").endswith(suffixes):
            return False
        if response.status_code != 206:
            record(f"media_http_{response.status_code}", media_url)
            return False
        prefix = b""
        async for chunk in response.aiter_bytes():
            prefix += chunk
            if len(prefix) >= 12:
                break
        return prefix[4:8] == b"ftyp"


def media_request_headers(url):
    # MP4Upload's published player requires its own origin on video requests.
    if (urlsplit(url).hostname or "").endswith(".mp4upload.com"):
        return {"Referer": "https://www.mp4upload.com/", "User-Agent": "Mozilla/5.0"}
    return {}


async def revalidate_server(client, server):
    try:
        if server["type"] == "hls":
            return server if await playable_hls(client, server["url"]) else None
        if server["type"] == "direct":
            return server if await playable_mp4(client, server["url"]) else None
        return await validate_embed(client, server)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return None


def unpack_player_data(markup):
    """Read the player's packed string table without executing third-party JS."""
    pattern = r"\}\('((?:\\.|[^'\\])*)',(\d+),(\d+),'((?:\\.|[^'\\])*)'\.split\('\|'\)"
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for match in re.finditer(pattern, markup):
        payload, radix, count, raw_words = match.groups()
        radix, count = int(radix), int(count)
        if not 2 <= radix <= 62 or count > 5000:
            continue
        words = re.sub(r"\\([\\/'\"])", r"\1", raw_words).split("|")
        table = {}
        for index, word in enumerate(words[:count]):
            number, key = index, ""
            while number:
                key = alphabet[number % radix] + key
                number //= radix
            if word:
                table[key or "0"] = word
        payload = re.sub(r"\\([\\/'\"])", r"\1", payload)
        yield re.sub(r"\b\w+\b", lambda token: table.get(token[0], token[0]), payload)


async def mixdrop_video(client, url, name):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in MIXDROP_HOSTS or not re.fullmatch(r"/e/[A-Za-z0-9]+", parsed.path):
        return None
    try:
        response = await client.get(url, timeout=7, follow_redirects=True)
        response.raise_for_status()
        if response.url.host not in MIXDROP_HOSTS:
            return None
        for data in unpack_player_data(response.text):
            match = re.search(r'MDCore\.wurl\s*=\s*"([^"\n]+)"', data)
            if not match:
                continue
            media_url = urljoin(str(response.url), html.unescape(match[1]))
            if await playable_mp4(client, media_url):
                return {"name": name, "type": "direct", "url": media_url, "source_url": url}
    except (httpx.HTTPError, ValueError):
        pass
    return None


async def lulustream_video(client, url, name):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in LULUSTREAM_HOSTS or not re.fullmatch(r"/e/[a-z0-9]+", parsed.path):
        return None
    try:
        response = await client.get(url, timeout=7, follow_redirects=True)
        response.raise_for_status()
        if response.url.host not in LULUSTREAM_HOSTS:
            return None
        for data in unpack_player_data(response.text):
            match = re.search(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*"([^"\n]+)"', data)
            if not match:
                continue
            media = html.unescape(match[1])
            info = {}
            if await playable_hls(client, media, info):
                if info.get("height"):
                    quality = f"{info['height']}p"
                    name = re.sub(r"[0-9]+p(?: \(source\))?", quality, name)
                return {"name": name, "type": "hls", "url": media, "source_url": url}
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return None


async def share4max_videos(client, url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in SHARE4MAX_HOSTS or not re.fullmatch(r"/iframe/[A-Za-z0-9]+", parsed.path):
        return []
    try:
        response = await client.get(url, follow_redirects=True, timeout=7)
        response.raise_for_status()
        if response.url.host not in SHARE4MAX_HOSTS:
            return []
        state = re.search(r'<script\b[^>]*data-page="app"[^>]*>(.*?)</script>', response.text, re.S)
        if not state:
            return []
        page = json.loads(state[1])
        response = await client.get(str(response.url), headers={
            "X-Inertia": "true",
            "X-Inertia-Version": page["version"],
            "X-Inertia-Partial-Component": page["component"],
            "X-Inertia-Partial-Data": "streams",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }, timeout=7)
        response.raise_for_status()
        streams = response.json()["props"]["streams"]
        candidates = []
        for quality in streams.get("data", []):
            for mirror in quality.get("mirrors", []):
                link = mirror.get("link", "")
                if link.startswith("//"):
                    link = "https:" + link
                if urlsplit(link).hostname in DIRECT_PLAYER_HOSTS:
                    name = f"{mirror['driver']} — {quality.get('label', '')}".strip()
                    candidates.append(direct_video(client, link, name))
                elif urlsplit(link).hostname in VIDARA_HOSTS:
                    candidates.append(vidara_video(client, link, f"Vidara — {quality.get('label', '')}"))
                elif urlsplit(link).hostname in MIXDROP_HOSTS:
                    candidates.append(mixdrop_video(client, link, f"Mixdrop — {quality.get('label', '')}"))
                elif urlsplit(link).hostname in VOE_HOSTS:
                    from app.services.voe import resolve as resolve_voe
                    candidates.append(resolve_voe(client, link, f"VOE — {quality.get('label', '')}"))
                elif urlsplit(link).hostname in LULUSTREAM_HOSTS:
                    candidates.append(lulustream_video(client, link, f"LuluStream — {quality.get('label', '')}"))
        results = await collect_available(candidates, timeout=10)
        return [server for server in results if isinstance(server, dict)]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


SUPPORTED_EMBED_HOSTS = {"ok.ru", "mega.nz", "4shared.com", "www.4shared.com", "uptostream.com", "dsvplay.com"}


async def resolve_player(client, server, id_mal, episode):
    if server.get("type") in ("direct", "hls"):
        result = await revalidate_server(client, server)
        return [result] if result else []
    host = urlsplit(server["url"]).hostname or ""
    if host in SHARE4MAX_HOSTS:
        s4m = await share4max_videos(client, server["url"])
        if s4m:
            return s4m
        valid = await validate_embed(client, server)
        return [valid] if valid else []
    if host in DIRECT_PLAYER_HOSTS:
        result = await direct_video(client, server["url"], server["name"])
        if result:
            return [result]
        valid = await validate_embed(client, server)
        return [valid] if valid else []
    elif is_anime4up_host(host):
        result = await hls_video(client, server, id_mal, episode)
    elif host in VIDARA_HOSTS:
        result = await vidara_video(client, server["url"], server["name"])
    elif host in VOE_HOSTS:
        from app.services.voe import resolve as resolve_voe
        result = await resolve_voe(client, server["url"], server["name"])
    elif host in MIXDROP_HOSTS:
        result = await mixdrop_video(client, server["url"], server["name"])
    elif host in LULUSTREAM_HOSTS:
        result = await lulustream_video(client, server["url"], server["name"])
    elif host in UQLOAD_HOSTS:
        return []
    else:
        result = await validate_embed(client, server)
    return [result] if result else []


class SourcePage(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.links = []
        self.servers = []
        self.link = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.link = [attrs.get("href", ""), ""]
        if tag == "button" and attrs.get("data-src"):
            self.servers.append((attrs["data-src"], attrs.get("data-name", "")))

    def handle_data(self, data):
        if self.link is not None:
            self.link[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.link is not None:
            self.links.append(self.link)
            self.link = None


async def validate_embed(client, server):
    url = server["url"]
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return None
    if host not in {"ok.ru"} | MP4UPLOAD_HOSTS | SHARE4MAX_HOSTS:
        return None
    if host == "ok.ru" and not re.fullmatch(r"/videoembed/[0-9]+", parsed.path):
        return None
    if host in MP4UPLOAD_HOSTS and not re.fullmatch(r"/embed-[a-z0-9]+\.html", parsed.path):
        return None
    if host in SHARE4MAX_HOSTS and not re.fullmatch(r"/iframe/[A-Za-z0-9]+", parsed.path):
        return None
    try:
        response = await client.get(url, timeout=7, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    allowed_redirects = SHARE4MAX_HOSTS if host in SHARE4MAX_HOSTS else {host}
    if response.url.host not in allowed_redirects:
        return None
    if host == "ok.ru":
        if "vp_video_stub" in response.text or "data-module=\"VideoEmbed\"" not in response.text:
            record("deleted_media", url)
            return None
    elif host in SHARE4MAX_HOSTS:
        state = re.search(r'<script\b[^>]*data-page="app"[^>]*>(.*?)</script>', response.text, re.S)
        try:
            video = json.loads(state[1])["props"]["video"] if state else {}
        except (ValueError, KeyError, TypeError):
            return None
        if video.get("status") != "active" or video.get("hashId") != parsed.path.rsplit("/", 1)[-1]:
            return None
    elif not re.search(r'player\.src\(\s*\{[^}]*src:\s*["\x27]https://', response.text, re.S):
        return None
    return server


async def resolve_servers(client, id_mal, english, romaji, episode):
    if not id_mal:
        return []
    cached = _episode_indexes.get(id_mal)
    if cached and cached[0] > time.monotonic() and episode in cached[1]:
        return await resolve_episode_page(client, cached[1][episode], id_mal, episode)
    titles = [title for title in (english, romaji) if title]
    if not titles:
        return []
    queries = list(dict.fromkeys(titles + [re.split(r"\W+", titles[0])[0]]))
    candidates = {}
    for query in queries:
        try:
            response = await client.get(f"{BASE_URL}/wp-json/animetna/v1/search", params={"q": query})
            response.raise_for_status()
            items = response.json().get("items", [])
        except (httpx.HTTPError, ValueError, TypeError):
            continue
        for candidate in items:
            url = candidate.get("url", "")
            if urlsplit(url).hostname == "animetna.com" and urlsplit(url).path.startswith("/anime/"):
                candidates[url] = candidate
        if any(re.sub(r"\W+", "", str(candidate.get("en", "")).casefold()) in {
            re.sub(r"\W+", "", title.casefold()) for title in titles
        } for candidate in candidates.values()):
            break
    title_keys = {re.sub(r"\W+", "", title.casefold()) for title in titles}
    ranked = sorted(candidates.values(), key=lambda item: any(
        re.sub(r"\W+", "", str(item.get(key, "")).casefold()) in title_keys for key in ("en", "title")
    ), reverse=True)
    for candidate in ranked[:4]:
        try:
            response = await client.get(candidate["url"])
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        if not re.search(rf"https?://(?:www\.)?myanimelist\.net/anime/{id_mal}(?![0-9])", response.text):
            continue
        page = SourcePage(response.text)
        _episode_indexes[id_mal] = (time.monotonic() + 600, page_episode_index(response.text))
        episode_links = {}
        for url, label in page.links:
            parsed = urlsplit(url)
            if parsed.hostname != "animetna.com" or not parsed.path.startswith("/episode/"):
                continue
            canonical = f"{BASE_URL}{parsed.path}"
            episode_links[canonical] = episode_links.get(canonical, "") + " " + label
        selected = None
        for url, label in episode_links.items():
            number = re.search(r"(?:الحلقة|حلقة|Episode)\s*([0-9]+)", label, re.I)
            number = number or re.search(r"-episode-([0-9]+)(?:/|$)", url, re.I)
            if number and int(number[1]) == episode:
                selected = url
                break
            if not number and episode == 1 and len(episode_links) == 1:
                selected = url
        if not selected:
            return []
        return await resolve_episode_page(client, selected, id_mal, episode)
    return []


async def resolve_episode_page(client, selected, id_mal, episode):
        if urlsplit(selected).hostname != "animetna.com" or not urlsplit(selected).path.startswith("/episode/"):
            return []
        response = await client.get(selected)
        response.raise_for_status()
        options = []
        for url, name in SourcePage(response.text).servers:
            url = html.unescape(url)
            if url.startswith("//"):
                url = "https:" + url
            host = urlsplit(url).hostname
            if host in {"ok.ru"} | DIRECT_PLAYER_HOSTS | SHARE4MAX_HOSTS | VIDARA_HOSTS | UQLOAD_HOSTS | VOE_HOSTS | MIXDROP_HOSTS or is_anime4up_host(host):
                display_name = "Share4Max" if host in SHARE4MAX_HOSTS else name
                if is_anime4up_host(host):
                    display_name = "بث مباشر " + ("2" if "/Anime4up-S2/" in url else "1")
                options.append({"name": display_name, "type": "embed", "url": html.unescape(url)})
        async def checked_player(server):
            try:
                return await asyncio.wait_for(resolve_player(client, server, id_mal, episode), timeout=20)
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
                return []
        checked = await collect_available((checked_player(server) for server in options))
        return sorted(
            [server for group in checked for server in group],
            key=lambda server: (server["type"] == "embed", not any(q in server["name"] for q in ("1080", "FHD")), "mp4upload" not in server["url"]),
        )


async def latest_catalog(client, limit=28):
    response = await client.get(BASE_URL)
    response.raise_for_status()
    urls = []
    for url, label in SourcePage(response.text).links:
        parsed = urlsplit(url)
        if parsed.hostname == "animetna.com" and re.fullmatch(r"/anime/[^/]+/", parsed.path) and url not in urls:
            urls.append(url)
    semaphore = asyncio.Semaphore(4)
    async def read(url, order):
        async with semaphore:
            try:
                response = await client.get(url)
                response.raise_for_status()
                mal = re.search(r"myanimelist\.net/anime/([0-9]+)", response.text)
                if not mal:
                    return None
                _episode_indexes[int(mal[1])] = (time.monotonic() + 600, page_episode_index(response.text))
                episodes = []
                for link, label in SourcePage(response.text).links:
                    parsed = urlsplit(link)
                    if parsed.hostname != "animetna.com" or not parsed.path.startswith("/episode/"):
                        continue
                    match = re.search(r"(?:الحلقة|حلقة|Episode)\s*([0-9]+)", label, re.I) or re.search(r"-episode-([0-9]+)(?:/|$)", parsed.path)
                    episodes.append((int(match[1]) if match else 1, f"{BASE_URL}{parsed.path}"))
                if not episodes:
                    return None
                number, source = max(episodes, key=lambda entry: entry[0])
                return {"mal": int(mal[1]), "episode": number, "source": source, "order": order}
            except (httpx.HTTPError, ValueError):
                return None
    entries = await asyncio.gather(*(read(url, order) for order, url in enumerate(urls[:limit])))
    return [entry for entry in entries if entry]
