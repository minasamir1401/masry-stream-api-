import asyncio
import unittest
from unittest.mock import patch
import httpx

from app.services.source_trace import begin, record, summary
from app.services import source_trace


class SourceTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_prevents_repeated_requests_to_rate_limited_provider(self):
        source_trace._cooldowns.clear()
        request = httpx.Request("GET", "https://provider.example/title")
        with patch("app.services.source_trace.time.monotonic", return_value=100):
            await source_trace.response_hook(httpx.Response(429, headers={"Retry-After": "20"}, request=request))
            with self.assertRaises(httpx.RequestError):
                await source_trace.request_hook(request)
        with patch("app.services.source_trace.time.monotonic", return_value=121):
            await source_trace.request_hook(request)
        source_trace._cooldowns.clear()

    async def test_parallel_viewers_do_not_share_diagnostics_or_expose_signed_urls(self):
        async def viewer(host):
            issues = begin()
            await asyncio.sleep(0)
            record("deleted_media", f"https://{host}/file?token=private")
            return summary(issues)
        first, second = await asyncio.gather(viewer("one.example"), viewer("two.example"))
        self.assertEqual(first, [{"reason": "deleted_media", "host": "one.example", "count": 1}])
        self.assertEqual(second[0]["host"], "two.example")
        self.assertNotIn("private", str(first))


if __name__ == "__main__":
    unittest.main()
