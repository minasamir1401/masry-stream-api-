import unittest

from app.services.source_http import close_source_client, get_source_client


class SourceConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_checks_share_connections_until_shutdown(self):
        self.addAsyncCleanup(close_source_client)
        first = get_source_client()
        self.assertIs(get_source_client(), first)
        await close_source_client()
        self.assertTrue(first.is_closed)
        self.assertIsNot(get_source_client(), first)


if __name__ == "__main__":
    unittest.main()
