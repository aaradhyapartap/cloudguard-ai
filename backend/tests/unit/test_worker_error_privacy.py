"""Task failures must not let runtime error serialization disclose raw input."""

from __future__ import annotations

import ast
import traceback
from pathlib import Path

import pytest
from app.security.worker_errors import WorkerTaskError, sanitize_worker_failures
from pydantic import BaseModel, ValidationError


def test_worker_wrapper_preserves_success_result_identity() -> None:
    result = {"executed": False}

    @sanitize_worker_failures
    def worker(event: dict[str, object]) -> dict[str, bool]:
        return result

    assert worker({}) is result


@pytest.mark.parametrize("kind", ["raw", "pydantic", "chained"])
def test_worker_failure_and_formatted_traceback_are_sanitized(kind: str) -> None:
    class Payload(BaseModel):
        count: int

    @sanitize_worker_failures
    def worker() -> None:
        if kind == "pydantic":
            Payload.model_validate({"count": "SYNTHETIC-SENSITIVE"})
        if kind == "chained":
            try:
                raise ValueError("SYNTHETIC-SENSITIVE")
            except ValueError as exc:
                raise RuntimeError("SYNTHETIC-SENSITIVE") from exc
        raise RuntimeError("SYNTHETIC-SENSITIVE")

    with pytest.raises(WorkerTaskError, match=r"^Workflow task failed\.$") as caught:
        worker()
    assert not isinstance(caught.value, ValidationError)
    assert caught.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(caught.value))
    assert "SYNTHETIC-SENSITIVE" not in rendered


def test_all_current_worker_entrypoints_have_the_failure_boundary() -> None:
    root = Path(__file__).parents[2] / "app"
    count = 0
    for path in root.glob("*worker.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "handler":
                assert any(isinstance(decorator, ast.Name)
                           and decorator.id == "sanitize_worker_failures"
                           for decorator in node.decorator_list), path.name
                count += 1
    assert count == 8
