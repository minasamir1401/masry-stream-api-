"""Reuse upstream video connections across the browser's byte-range requests."""
import httpx

_client = None


def get_video_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0, pool=15.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=120),
            follow_redirects=True,
        )
    return _client


async def close_video_client():
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
