"""Unit tests for AST static analysis engine and security rules."""

from pathlib import Path

from review.agent.tools.static_analysis import run_static_analysis


def test_static_analysis_clean_file(tmp_path: Path) -> None:
    clean_file = tmp_path / "clean.py"
    clean_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\n"
        "with open('test.txt') as f:\n"
        "    content = f.read()\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "clean.py")
    assert "Status: PASSED" in report
    assert "No security vulnerabilities" in report


def test_static_analysis_sql_injection(tmp_path: Path) -> None:
    sqli_file = tmp_path / "db.py"
    sqli_file.write_text(
        "def query_user(cursor, user_id):\n"
        "    cursor.execute(f'SELECT * FROM users WHERE id = {user_id}')\n"
        "    cursor.execute('SELECT * FROM users WHERE name = %s' + user_id)\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "db.py")
    assert "SEC001_SQLI" in report
    assert "Potential SQL Injection" in report


def test_static_analysis_command_injection(tmp_path: Path) -> None:
    cmd_file = tmp_path / "shell_exec.py"
    cmd_file.write_text(
        "import subprocess\n"
        "def run_command(cmd):\n"
        "    subprocess.run(cmd, shell=True)\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "shell_exec.py")
    assert "SEC002_COMMAND_INJECTION" in report
    assert "shell=True" in report


def test_static_analysis_insecure_pickle_and_eval(tmp_path: Path) -> None:
    vuln_file = tmp_path / "insecure.py"
    vuln_file.write_text(
        "import pickle\n"
        "def restore(payload, expr):\n"
        "    obj = pickle.loads(payload)\n"
        "    val = eval(expr)\n"
        "    return obj, val\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "insecure.py")
    assert "SEC003_INSECURE_DESERIALIZATION" in report
    assert "SEC004_INSECURE_EVAL" in report


def test_static_analysis_hardcoded_secrets(tmp_path: Path) -> None:
    secret_file = tmp_path / "config.py"
    secret_file.write_text(
        "API_KEY = 'sk-live-abcdef1234567890'\n"
        "DATABASE_PASSWORD = 'super_secret_production_password_123'\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "config.py")
    assert "SEC005_HARDCODED_SECRET" in report


def test_static_analysis_defect_antipatterns(tmp_path: Path) -> None:
    buggy_file = tmp_path / "bugs.py"
    buggy_file.write_text(
        "def append_item(item, items=[]):\n"
        "    items.append(item)\n"
        "    f = open('data.log', 'w')\n"
        "    try:\n"
        "        f.write(str(items))\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )

    report = run_static_analysis(tmp_path, "bugs.py")
    assert "BUG001_MUTABLE_DEFAULT" in report
    assert "BUG002_UNCLOSED_RESOURCE" in report
    assert "BUG003_SILENT_EXCEPTION" in report


def test_static_analysis_non_python_file(tmp_path: Path) -> None:
    md_file = tmp_path / "README.md"
    md_file.write_text("# Documentation\n", encoding="utf-8")

    report = run_static_analysis(tmp_path, "README.md")
    assert "only supports Python (.py) files" in report
