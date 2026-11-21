import re


def property_group(name: str) -> str | None:
    match = re.fullmatch(r"([a-z]+)\s*[-#]?\s*\d+[a-z]?(?:-\d+[a-z]?)?", name.strip(), re.IGNORECASE)
    if match is None:
        return None
    group = match.group(1).casefold()
    return None if group in {"apartment", "apt", "home", "house", "property", "unit"} else group
