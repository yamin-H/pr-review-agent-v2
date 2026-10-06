"""Data models for test reproduction and isolated sandbox execution."""

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    """Result of running a test or reproduction script in the execution sandbox."""

    stdout: str = Field(default="", description="Standard output from execution")
    stderr: str = Field(default="", description="Standard error from execution")
    exit_code: int = Field(default=0, description="Process return code")
    timed_out: bool = Field(
        default=False, description="Whether process exceeded timeout threshold"
    )
    duration_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    reproduced: bool = Field(
        default=False, description="Whether execution confirmed the target bug"
    )
    error_summary: str | None = Field(
        default=None, description="Concise error or failure summary from execution"
    )
