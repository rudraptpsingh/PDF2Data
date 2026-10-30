"""Physical JSONL boundaries, preserving Unicode NEL/LS/PS inside strings."""


def jsonl_physical_lines(value: str) -> list[str]:
    # JSON encodes LF/CR inside strings as escapes. NEL, LS and PS are legal
    # literal Unicode characters and must never become record delimiters.
    # Ignore only empty/ASCII-whitespace physical records, not Unicode payloads.
    return [line for line in value.split('\n') if line.strip(' \t\r')]
