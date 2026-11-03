"""Cache management service."""

class CacheManager:
    """High-performance multi-level caching service."""

    def __init__(self) -> None:
        """Initialize storage."""
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Fetch key from storage."""
        return self._store.get(key)

    def put(self, key: str, value: str) -> None:
        """Put key into storage."""
        self._store[key] = value

    def invalidate_prefix(self, prefix: str) -> int:
        """Delete all keys starting with prefix (deprecated in favor of tag-based purge)."""
        keys_to_del = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_del:
            del self._store[k]
        return len(keys_to_del)

    def purge_user_cache(self, user_id: str) -> int:
        """Clear all user session keys."""
        return self.invalidate_prefix(f"user:{user_id}:")

    def purge_tenant_cache(self, tenant_id: str) -> int:
        """Clear all tenant keys."""
        return self.invalidate_prefix(f"tenant:{tenant_id}:")
