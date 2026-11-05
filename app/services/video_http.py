"""Reuse upstream video connections across the browser's byte-range requests."""
import httpx

_client = None


def get_video_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(12, connect=7, pool=5),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20, keepalive_expiry=60),
            follow_redirects=True,
        )
    return _client


async def close_video_client():
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
