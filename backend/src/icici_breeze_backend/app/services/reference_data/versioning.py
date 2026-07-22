"""Shared reference-data Redis version bump and purge."""
from __future__ import annotations

from icici_breeze_backend.app.db.redis_client import cache_delete_pattern, get_redis
from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY, version_prefix


def bump_refdata_version(new_ver: int) -> None:
    """Set active refdata version and purge the previous generation from Redis."""
    from icici_breeze_backend.app.services.reference_data.scrip_index import current_version

    prev = current_version()
    get_redis().set(CURRENT_VERSION_KEY, str(new_ver))
    if prev and prev != new_ver:
        cache_delete_pattern(f"{version_prefix(prev)}:*")
