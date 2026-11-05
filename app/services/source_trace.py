"""Request-local source diagnostics, without signed URLs or provider tokens."""
from collections import Counter
from contextvars import ContextVar
from urllib.parse import urlsplit
import time
import httpx

_issues = ContextVar("source_issues", default=None)
_cooldowns = {}


def begin():
    issues = Counter()
    _issues.set(issues)
    return issues


def record(reason, url):
    issues = _issues.get()
    if issues is not None:
        issues[(reason, urlsplit(str(url)).hostname or "unknown")] += 1


async def response_hook(response):
    if response.status_code >= 400:
        record(f"http_{response.status_code}", response.url)
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "30")
        seconds = min(120, max(5, int(retry))) if retry.isdigit() else 30
        _cooldowns[response.url.host] = time.monotonic() + seconds


async def request_hook(request):
    if _cooldowns.get(request.url.host, 0) > time.monotonic():
        record("rate_limited", request.url)
        raise httpx.RequestError("Source requested a cooldown", request=request)


def summary(issues):
    return [{"reason": reason, "host": host, "count": count}
            for (reason, host), count in issues.items()]
