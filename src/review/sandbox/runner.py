"""Subprocess-based isolated sandbox runner for executing Python reproduction tests."""

import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from review.sandbox.models import ExecutionResult

logger = logging.getLogger("review.sandbox.runner")

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_OUTPUT_BYTES = 50_000

# Environment variables that must NOT leak into the untrusted execution sandbox
SENSITIVE_ENV_VARS = {
    "GROQ_API_KEY",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "DATABASE_URL",
}


class SandboxRunner:
    """Executes reproduction tests in an ephemeral directory with strict timeout boundaries."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        python_executable: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.python_executable = python_executable or sys.executable

    def _build_safe_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Construct sanitized environment variables without sensitive API keys."""
        safe_env = {
            k: v for k, v in os.environ.items() if k not in SENSITIVE_ENV_VARS
        }
        safe_env["PYTHONDONTWRITEBYTECODE"] = "1"
        safe_env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            safe_env.update(extra_env)
        return safe_env

    def run_script(
        self,
        script_content: str,
        repo_root: Path | None = None,
        script_filename: str = "test_reproduce.py",
        use_pytest: bool = False,
    ) -> ExecutionResult:
        """Write script to an ephemeral directory and execute under sandbox constraints."""
        with tempfile.TemporaryDirectory(prefix="agent_sandbox_") as tmpdir:
            tmp_path = Path(tmpdir)
            script_path = tmp_path / script_filename
            script_path.write_text(script_content, encoding="utf-8")

            # Determine command to run
            if use_pytest:
                cmd = [self.python_executable, "-m", "pytest", "-v", str(script_path)]
            else:
                cmd = [self.python_executable, str(script_path)]

            # Configure execution working directory & pythonpath
            cwd = str(repo_root) if repo_root and repo_root.exists() else str(tmp_path)
            env = self._build_safe_env()

            # Ensure repo_root or tmp_path is on PYTHONPATH
            existing_ppath = env.get("PYTHONPATH", "")
            added_ppath = str(repo_root) if repo_root else str(tmp_path)
            env["PYTHONPATH"] = (
                f"{added_ppath}{os.pathsep}{existing_ppath}" if existing_ppath else added_ppath
            )

            start = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0

                stdout = proc.stdout[: self.max_output_bytes]
                stderr = proc.stderr[: self.max_output_bytes]
                exit_code = proc.returncode

                # Reproduction is confirmed if the test failed with an assertion or exception
                reproduced = exit_code != 0 and (
                    "AssertionError" in (stderr + stdout)
                    or "Exception" in (stderr + stdout)
                    or "Error:" in (stderr + stdout)
                    or "FAILED" in (stderr + stdout)
                )

                # Extract concise error summary from output if available
                error_summary = None
                combined_err = (stderr + "\n" + stdout).strip()
                if exit_code != 0 and combined_err:
                    lines = [line.strip() for line in combined_err.splitlines() if line.strip()]
                    error_summary = lines[-1] if lines else f"Process exited with code {exit_code}"

                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    timed_out=False,
                    duration_ms=elapsed_ms,
                    reproduced=reproduced,
                    error_summary=error_summary,
                )

            except subprocess.TimeoutExpired as e:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                stdout_val = e.stdout if isinstance(e.stdout, str) else ""
                stderr_val = e.stderr if isinstance(e.stderr, str) else ""
                stdout = stdout_val[: self.max_output_bytes]
                stderr = stderr_val[: self.max_output_bytes]
                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=-1,
                    timed_out=True,
                    duration_ms=elapsed_ms,
                    reproduced=False,
                    error_summary=f"Execution timed out after {self.timeout_seconds}s.",
                )

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return ExecutionResult(
                    stdout="",
                    stderr=str(e),
                    exit_code=-1,
                    timed_out=False,
                    duration_ms=elapsed_ms,
                    reproduced=False,
                    error_summary=f"Sandbox execution error: {e}",
                )
