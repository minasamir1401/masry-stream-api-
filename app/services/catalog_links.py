"""In-memory view of immutable public paths stored in CatalogEntry."""
_paths = {}


def default_path(anime_id, media_type="SERIES"):
    return f"/{'movie' if media_type == 'MOVIE' else 'series'}/{int(anime_id)}"


def known_path(anime_id):
    return _paths.get(int(anime_id)) if anime_id else None


def register_path(anime_id, path):
    if path in (default_path(anime_id), default_path(anime_id, "MOVIE")):
        _paths[int(anime_id)] = path


def public_path(item):
    anime_id = item.get("anime_id") or item.get("id")
    stored = known_path(anime_id)
    candidate = item.get("canonical_path")
    if candidate not in (default_path(anime_id), default_path(anime_id, "MOVIE")):
        candidate = None
    return stored or candidate or default_path(anime_id, item.get("media_type"))
