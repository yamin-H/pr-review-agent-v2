import re
from dataclasses import dataclass

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class AddedLine:
    path: str
    line: int
    text: str


def added_lines(diff_text: str) -> list[AddedLine]:
    """Return every added line with its real line number in the new file."""
    result: list[AddedLine] = []
    path: str | None = None
    new_line = 0
    old_left = 0  
    new_left = 0  

    for raw in diff_text.splitlines():
        if old_left > 0 or new_left > 0:  
            if raw.startswith("+"):
                if path is not None:
                    result.append(AddedLine(path, new_line, raw[1:]))
                new_line += 1
                new_left -= 1
            elif raw.startswith("-"):
                old_left -= 1
            elif raw.startswith("\\"): 
                pass
            else: 
                new_line += 1
                new_left -= 1
                old_left -= 1
            continue

        if raw.startswith("+++ "):
            target = raw[4:]
            path = None if target == "/dev/null" else target.removeprefix("b/")
        elif match := HUNK_HEADER.match(raw):
            old_left = int(match.group(1) or 1)
            new_line = int(match.group(2))
            new_left = int(match.group(3) or 1)

    return result