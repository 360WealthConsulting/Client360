import re


def normalize_email(email):
    """Normalize email addresses for matching.

    Lowercases the address, trims surrounding whitespace, and removes
    dots from the local part for gmail-style addresses.
    """
    if not email:
        return ""

    email = email.strip().lower()
    if "@" not in email:
        return email

    local_part, _, domain = email.partition("@")
    if domain in {"gmail.com", "googlemail.com"}:
        local_part = local_part.replace(".", "")
        if local_part.endswith("+"):
            local_part = local_part[: local_part.index("+")]
        elif "+" in local_part:
            local_part = local_part[: local_part.index("+")]

    return f"{local_part}@{domain}"
