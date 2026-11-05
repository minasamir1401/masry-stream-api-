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
            event_hooks={"request": [source_trace.request_hook], "response": [source_trace.response_hook]},
        )
    return _client


async def close_source_client():
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
