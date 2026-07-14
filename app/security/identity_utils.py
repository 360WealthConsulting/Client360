def normalize_email(value):
    return (value or "").strip().casefold()
