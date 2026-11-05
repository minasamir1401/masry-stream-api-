import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, Response
from starlette.requests import Request

from app.services import admin_auth


class AdminLoginTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return Request({"type": "http", "scheme": "http", "server": ("127.0.0.1", 3000), "path": "/api/admin/login", "headers": [(b"origin", b"http://127.0.0.1:3000")]})

    async def test_rate_limit_returns_remaining_wait_and_does_not_query_credentials(self):
        db = AsyncMock()
        with patch.object(admin_auth, "login_failures", deque([0, 2, 3, 4, 5])), patch.object(admin_auth, "monotonic", return_value=45.2):
            with self.assertRaises(HTTPException) as error:
                await admin_auth.login("admin", "example", self.request(), Response(), db)
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], "15")
        db.execute.assert_not_awaited()

    async def test_expired_limit_allows_a_new_attempt_and_keeps_invalid_password_unauthorized(self):
        db = AsyncMock()
        result = Mock()
        result.scalar_one_or_none.return_value = SimpleNamespace(username="admin", salt="test-salt", password_hash=admin_auth.hash_password("correct", "test-salt"))
        db.execute.return_value = result
        failures = deque([0, 1, 2, 3, 4])
        with patch.object(admin_auth, "login_failures", failures), patch.object(admin_auth, "monotonic", return_value=65):
            with self.assertRaises(HTTPException) as error:
                await admin_auth.login("admin", "incorrect", self.request(), Response(), db)
        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(list(failures), [65])
        db.execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
