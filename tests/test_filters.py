import pytest

from review.filters import should_review


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "frontend/src/index.ts",
        "README.md",
        "tests/test_diff.py",
    ],
)
def test_reviews_normal_source_files(path: str) -> None:
    assert should_review(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "frontend/pnpm-lock.yaml",
        "poetry.lock",
        "go.sum",
        "static/app.min.js",
        "static/app.js.map",
        "node_modules/lib/index.js",
        "dist/bundle.js",
        "src/__snapshots__/button.snap",
        "api/schema_pb2.py",
        "assets/logo.png",
    ],
)
def test_skips_lockfiles_generated_and_binary_files(path: str) -> None:
    assert should_review(path) is False