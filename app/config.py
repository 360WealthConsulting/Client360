import os

ENVIRONMENT = os.getenv("CLIENT360_ENVIRONMENT", "development").lower()
SESSION_SECRET = os.getenv("SESSION_SECRET")
if ENVIRONMENT == "production" and not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is required in production")
SESSION_SECRET = SESSION_SECRET or "development-only-change-me"
SESSION_HTTPS_ONLY = ENVIRONMENT == "production"
