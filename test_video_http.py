import unittest

from app.services.video_http import close_video_client, get_video_client


class VideoConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranges_share_connection_pool_and_shutdown_closes_it(self):
        self.addAsyncCleanup(close_video_client)
        first = get_video_client()
        self.assertIs(get_video_client(), first)
        await close_video_client()
        self.assertTrue(first.is_closed)
        self.assertIsNot(get_video_client(), first)


if __name__ == "__main__":
    unittest.main()
