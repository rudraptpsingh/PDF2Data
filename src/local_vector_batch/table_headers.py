"""Conservative header proposals; numeric continuation rows remain unresolved."""
import re

_NUMBER = re.compile(r"[+\-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)%?")


def first_row_header_candidate(cells):
    if not cells:
        return None
    values = [str(v).strip() for v in cells[0] if v is not None and str(v).strip()]
    if not values:
        return None
    for value in values:
        if value.startswith('(') and value.endswith(')'):
            value = value[1:-1].strip()
        if _NUMBER.fullmatch(value):
            return None
    return 0
