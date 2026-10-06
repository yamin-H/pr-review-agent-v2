from pathlib import PurePosixPath

LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "go.sum"}

SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "__snapshots__", ".venv"}

SKIP_SUFFIXES = (
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
    ".snap",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".pdf",
)

SKIP_NAME_PARTS = ("_pb2.py", ".generated.")


def should_review(path: str) -> bool:
    """Return False for files that are not worth sending to the model."""
    p = PurePosixPath(path)
    name = p.name

    if name in LOCKFILES:
        return False
    if any(part in SKIP_DIRS for part in p.parts[:-1]):
        return False
    if name.endswith(SKIP_SUFFIXES):
        return False
    if any(part in name for part in SKIP_NAME_PARTS):
        return False
    return True