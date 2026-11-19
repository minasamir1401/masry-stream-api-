"""Share source connections while keeping diagnostics scoped to each request."""
import httpx

from app.services import source_trace

_client = None


def get_source_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=7,
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=40, keepalive_expiry=60),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            },
            event_hooks={"request": [source_trace.request_hook], "response": [source_trace.response_hook]},
        )
    return _client


async def close_source_client():
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
