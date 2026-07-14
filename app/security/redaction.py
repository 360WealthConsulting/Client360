import re

SENSITIVE = re.compile(r"token|secret|password|tax|ssn|content|body", re.I)

def redact_metadata(value):
    return {key: "[REDACTED]" if SENSITIVE.search(key) else item for key, item in (value or {}).items()}
