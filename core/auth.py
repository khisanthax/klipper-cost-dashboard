"""Bounded credential overlap for recoverable API key rotation."""
import hmac
import json
import os
import time
from core.config import SECRET_FILE


def valid_api_key(supplied, current):
    if not supplied or not current:
        return False
    if hmac.compare_digest(supplied.encode(), current.encode()):
        return True
    # An environment key is deliberately authoritative over file credentials.
    if os.environ.get("KCD_API_KEY"):
        return False
    try:
        with open(SECRET_FILE) as stream:
            credentials = json.load(stream)
        return any(isinstance(item, dict) and float(item.get("expires_at", 0)) > time.time()
                   and hmac.compare_digest(supplied.encode(), str(item.get("key", "")).encode())
                   for item in credentials.get("previous_api_keys", [])[:4])
    except (OSError, ValueError, TypeError):
        return False
