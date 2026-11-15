"""Conservative header proposals; numeric continuation rows remain unresolved."""
import re

_NUMBER = re.compile(r"[+\-−]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)%?")
_CONTINUATION_VALUE = re.compile(
    r"(?:[<>≤≥]\s*)?(?:(?:₹|Rs\.?|INR)\s*)?" + _NUMBER.pattern
    + r"(?:\s*(?:kV|MWh|MW|kWh|kW|Hz|lakh|crore|Rs/kWh|INR/kWh))?",
    re.I,
)
OPEN_TOP_CONTINUATION_ROW = 'source_rulings_open_top_continuation_row'
_COLUMN_SYMBOL = re.compile(r'\(([A-Z])\)(?:\s*=\s*(\S[\s\S]*))?')


def numeric_continuation_cell(value):
    return bool(_CONTINUATION_VALUE.fullmatch(value.strip()))


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


def table_header_candidate(table):
    cells = table.get('cells', [])
    if (table.get('detection_method') == OPEN_TOP_CONTINUATION_ROW and cells
            and any(isinstance(value, str) and numeric_continuation_cell(value) for value in cells[0])):
        return None
    return first_row_header_candidate(cells)


def formula_header_symbols(table):
    """Recognize a secondary column header such as (A), (B), (C)=(A+B)."""
    rows = table.get('cells', [])
    if len(rows) < 3 or table_header_candidate(table) != 0:
        return {}
    heading, symbols = rows[:2]
    if len(heading) != len(symbols) or len(symbols) < 4 or symbols[0]:
        return {}
    matches = [_COLUMN_SYMBOL.fullmatch(str(value or '').strip()) for value in symbols[1:]]
    if not all(matches) or not all(heading[1:]):
        return {}
    if [m[1] for m in matches] != [chr(65+i) for i in range(len(matches))]:
        return {}
    if not any(m[2] for m in matches):
        return {}
    return {column: (match[1], bool(match[2])) for column, match in enumerate(matches, 1)}
