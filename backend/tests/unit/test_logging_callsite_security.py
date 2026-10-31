"""Static regressions for application logging call sites, including adapters."""

from __future__ import annotations

import ast
from pathlib import Path

APPLICATION_ROOT = Path(__file__).parents[2] / "app"
_FORBIDDEN_FIELDS = frozenset(
    {"error", "detail", "filename", "file_name", "storage_key", "key",
     "transaction_id", "path", "exc_info", "stack_info"}
)


def test_application_logs_do_not_render_sensitive_fields_or_tracebacks() -> None:
    violations: list[str] = []
    for path in sorted(APPLICATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
                continue
            if node.func.attr not in {"info", "warning", "error", "exception", "debug"}:
                continue
            if node.func.attr == "exception":
                violations.append(f"{path.name}:{node.lineno}: traceback logging")
            for keyword in node.keywords:
                if keyword.arg in _FORBIDDEN_FIELDS:
                    violations.append(f"{path.name}:{node.lineno}: {keyword.arg}")
            if not node.args or not isinstance(node.args[0], ast.Constant):
                violations.append(f"{path.name}:{node.lineno}: dynamic event")
    assert violations == []
