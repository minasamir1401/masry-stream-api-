"""Keep each playback URL bound to one file across catalog cache refreshes."""
import hashlib

from app.services.db_cache import db_cache
from app.services.animetna import resolve_player


class PlaybackSources:
    TTL = 6 * 60 * 60

    @staticmethod
    def key(anime_id, episode, token):
        return f"playback:{anime_id}:{episode}:{token}"

    async def register(self, anime_id, episode, server):
        token = hashlib.sha256(f"{anime_id}:{episode}:{server['url']}".encode()).hexdigest()[:24]
        key = self.key(anime_id, episode, token)
        if await db_cache.get(key) is None:
            await db_cache.set(key, dict(server), self.TTL)
        return token

    async def get(self, anime_id, episode, token):
        return await db_cache.get(self.key(anime_id, episode, token))

    async def renew(self, client, anime_id, episode, token, server):
        origin = server.get("source_url")
        if not origin:
            return None
        # Resolve the same published file, never another server's list position.
        results = await resolve_player(client, {"type": "embed", "url": origin, "name": server["name"]}, None, episode)
        replacement = next((item for item in results if item.get("type") == "direct"), None)
        if replacement:
            await db_cache.set(self.key(anime_id, episode, token), replacement, self.TTL)
        return replacement


playback_sources = PlaybackSources()
