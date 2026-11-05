import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.db_cache import DBCache


class StreamCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_cache_expiry_is_converted_to_monotonic_time(self):
        cache = DBCache()
        cache._l1 = {}
        session = MagicMock()
        session.__aenter__ = AsyncMock()
        session.__aexit__ = AsyncMock()
        with patch("app.services.db_cache.AsyncSessionLocal", return_value=session):
            with patch.object(cache, "_db_get", new_callable=AsyncMock, return_value={"expires_at": time.time() + 120, "value": {"servers": ["signed-url"]}}):
                await cache.get("stream:test")
        remaining = cache._l1["stream:test"][0] - time.monotonic()
        self.assertGreater(remaining, 118)
        self.assertLessEqual(remaining, 120)


if __name__ == "__main__":
    unittest.main()
