"""Reuse upstream video connections across the browser's byte-range requests."""
import httpx

_client = None


def get_video_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=15.0, pool=15.0),
            limits=httpx.Limits(max_connections=500, max_keepalive_connections=200, keepalive_expiry=300),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )
    return _client


async def close_video_client():
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
