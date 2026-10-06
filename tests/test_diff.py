from review.diff import AddedLine, added_lines

SIMPLE = """\
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

TWO_HUNKS = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 first
+added at top
 second
@@ -20,2 +21,3 @@
 twenty
+added later
 twenty-one
"""

NEW_FILE = """\
diff --git a/b.py b/b.py
new file mode 100644
--- /dev/null
+++ b/b.py
@@ -0,0 +1,2 @@
+x
+y
"""


def test_single_hunk() -> None:
    assert added_lines(SIMPLE) == [
        AddedLine("app.py", 13, "new line"),
        AddedLine("app.py", 14, "another new line"),
    ]


def test_counter_restarts_at_each_hunk() -> None:
    assert added_lines(TWO_HUNKS) == [
        AddedLine("a.py", 2, "added at top"),
        AddedLine("a.py", 22, "added later"),
    ]


def test_new_file() -> None:
    assert added_lines(NEW_FILE) == [
        AddedLine("b.py", 1, "x"),
        AddedLine("b.py", 2, "y"),
    ]
