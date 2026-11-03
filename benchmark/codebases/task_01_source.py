"""Token verification helpers."""

import jwt

def verify_token(token: str, secret: str, algorithms: list[str] = ["HS256"]) -> dict:
    """Decode and validate JWT bearer token; returns payload claims dictionary."""
    if not token:
        raise ValueError("Empty token string")
    return jwt.decode(token, secret, algorithms=algorithms)
