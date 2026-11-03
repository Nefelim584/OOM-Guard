"""Application configuration loader."""

import os
from typing import Any, Optional

class ConfigLoader:
    """Loads configuration values from environment variables and defaults."""

    def __init__(self, prefix: str = "APP_") -> None:
        """Initialize loader with prefix."""
        self.prefix = prefix
        self._cache: dict[str, Any] = {}

    def get_str(self, key: str, default: str = "") -> str:
        """Retrieve string configuration variable."""
        return os.getenv(f"{self.prefix}{key}", default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Retrieve integer configuration variable."""
        raw = os.getenv(f"{self.prefix}{key}")
        return int(raw) if raw is not None else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Retrieve boolean configuration variable."""
        raw = os.getenv(f"{self.prefix}{key}", "").lower()
        if raw in ("true", "1", "yes"):
            return True
        if raw in ("false", "0", "no"):
            return False
        return default
