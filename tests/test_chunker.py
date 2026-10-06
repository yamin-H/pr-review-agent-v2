from review.chunker import DiffChunk, DiffHunk, chunk_diff, parse_diff_hunks

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +12,4 @@
 context line
-old line
+new line
+another new line
 context line
"""

MULTI_HUNK_DIFF = """\
diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,2 +1,3 @@
 first
+added at top
 second
@@ -20,2 +21,3 @@
 twenty
+added later
 twenty-one
"""

MULTI_FILE_WITH_IGNORED = """\
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,2 +1,3 @@
 {
+  "lockfileVersion": 3,
 }
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 start
+added line
 end
"""


def test_parse_single_hunk() -> None:
    file_diffs = parse_diff_hunks(SAMPLE_DIFF)
    assert len(file_diffs) == 1
    path, hunks = file_diffs[0]
    assert path == "app.py"
    assert len(hunks) == 1

    hunk = hunks[0]
    assert hunk.new_start == 12
    assert hunk.new_count == 4
    assert len(hunk.lines) == 5


def test_hunk_format_numbered() -> None:
    hunk = DiffHunk(
        header="@@ -10,3 +12,4 @@",
        old_start=10,
        old_count=3,
        new_start=12,
        new_count=4,
        lines=[
            " context line",
            "-old line",
            "+new line",
            "+another new line",
            " context line",
        ],
    )
    formatted = hunk.format_numbered()
    expected = "\n".join(
        [
            "@@ -10,3 +12,4 @@",
            "  12 |  context line",
            "     | -old line",
            "  13 | +new line",
            "  14 | +another new line",
            "  15 |  context line",
        ]
    )
    assert formatted == expected


def test_chunk_diff_groups_small_hunks() -> None:
    chunks = chunk_diff(MULTI_HUNK_DIFF, max_chunk_lines=50)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.file_path == "service.py"
    assert len(chunk.hunks) == 2
    assert chunk.line_count == 6


def test_chunk_diff_splits_when_limit_exceeded() -> None:
    # max_chunk_lines=4 forces the second 3-line hunk into its own chunk
    chunks = chunk_diff(MULTI_HUNK_DIFF, max_chunk_lines=4)
    assert len(chunks) == 2
    assert chunks[0].file_path == "service.py"
    assert len(chunks[0].hunks) == 1
    assert chunks[1].file_path == "service.py"
    assert len(chunks[1].hunks) == 1


def test_chunk_diff_skips_filtered_files() -> None:
    chunks = chunk_diff(MULTI_FILE_WITH_IGNORED)
    assert len(chunks) == 1
    assert chunks[0].file_path == "app.py"


def test_diff_chunk_format_for_prompt() -> None:
    chunk = DiffChunk(
        file_path="app.py",
        hunks=[
            DiffHunk(
                header="@@ -1,1 +1,2 @@",
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=2,
                lines=[" hello", "+world"],
            )
        ],
    )
    prompt_text = chunk.format_for_prompt()
    assert "File: app.py" in prompt_text
    assert "   1 |  hello" in prompt_text
    assert "   2 | +world" in prompt_text
