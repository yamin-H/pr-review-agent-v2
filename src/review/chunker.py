import re
from dataclasses import dataclass, field

from review.filters import should_review

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")


@dataclass(frozen=True)
class DiffHunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]

    def format_numbered(self) -> str:
        """Format the hunk with new-file line numbers prefixed to added and context lines."""
        formatted_lines: list[str] = [self.header]
        current_new_line = self.new_start

        for raw in self.lines:
            if raw.startswith("+"):
                formatted_lines.append(f"{current_new_line:4d} | {raw}")
                current_new_line += 1
            elif raw.startswith(("-", "\\")):
                formatted_lines.append(f"     | {raw}")
            else:
                formatted_lines.append(f"{current_new_line:4d} | {raw}")
                current_new_line += 1

        return "\n".join(formatted_lines)


@dataclass
class DiffChunk:
    file_path: str
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        """Total diff lines across all hunks in this chunk."""
        return sum(len(h.lines) for h in self.hunks)

    def format_for_prompt(self) -> str:
        """Format the chunk with file header and numbered hunks for prompt consumption."""
        parts = [f"File: {self.file_path}"]
        for hunk in self.hunks:
            parts.append(hunk.format_numbered())
        return "\n\n".join(parts)


def parse_diff_hunks(diff_text: str) -> list[tuple[str, list[DiffHunk]]]:
    """Parse a unified diff into reviewable files and their corresponding hunks."""
    files: list[tuple[str, list[DiffHunk]]] = []
    current_file: str | None = None
    current_hunks: list[DiffHunk] = []

    # Current hunk tracking
    active_header: str | None = None
    old_start = 0
    old_count = 0
    new_start = 0
    new_count = 0
    old_left = 0
    new_left = 0
    hunk_lines: list[str] = []

    def flush_hunk() -> None:
        nonlocal active_header, hunk_lines
        if active_header is not None:
            current_hunks.append(
                DiffHunk(
                    header=active_header,
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=list(hunk_lines),
                )
            )
            active_header = None
            hunk_lines = []

    def flush_file() -> None:
        nonlocal current_file, current_hunks
        flush_hunk()
        if current_file is not None and current_hunks and should_review(current_file):
            files.append((current_file, list(current_hunks)))
        current_file = None
        current_hunks = []

    for raw in diff_text.splitlines():
        if old_left > 0 or new_left > 0:
            hunk_lines.append(raw)
            if raw.startswith("+"):
                new_left -= 1
            elif raw.startswith("-"):
                old_left -= 1
            elif raw.startswith("\\"):
                pass
            else:
                new_left -= 1
                old_left -= 1
            continue

        if raw.startswith("diff --git "):
            flush_file()
        elif raw.startswith("+++ "):
            target = raw[4:]
            current_file = None if target == "/dev/null" else target.removeprefix("b/")
        elif match := HUNK_HEADER.match(raw):
            flush_hunk()
            active_header = raw
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            old_left = old_count
            new_left = new_count

    flush_file()
    return files


def chunk_diff(diff_text: str, max_chunk_lines: int = 150) -> list[DiffChunk]:
    """Split a unified diff into bounded DiffChunk objects filtered by should_review.

    Hunks from the same file are grouped up to max_chunk_lines. When adding another
    hunk would exceed max_chunk_lines, a new chunk is started.
    """
    chunks: list[DiffChunk] = []
    file_diffs = parse_diff_hunks(diff_text)

    for file_path, hunks in file_diffs:
        current_chunk = DiffChunk(file_path=file_path)

        for hunk in hunks:
            hunk_len = len(hunk.lines)
            if current_chunk.hunks and (current_chunk.line_count + hunk_len > max_chunk_lines):
                chunks.append(current_chunk)
                current_chunk = DiffChunk(file_path=file_path)

            current_chunk.hunks.append(hunk)

        if current_chunk.hunks:
            chunks.append(current_chunk)

    return chunks
