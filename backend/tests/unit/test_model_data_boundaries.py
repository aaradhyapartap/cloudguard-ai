"""Keep all current generation paths behind the shared model-data checks."""

from __future__ import annotations

import ast
from pathlib import Path

from app.security import sensitive_data


def test_all_generation_paths_use_shared_input_and_output_boundaries() -> None:
    root = Path(__file__).parents[2] / "app" / "services"
    totals = {"GenerationRequest": 0, "build_isolated_user_prompt": 0,
              "require_model_response_allowed": 0}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        counts = dict.fromkeys(totals, 0)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in counts:
                    counts[node.func.id] += 1
        assert len(set(counts.values())) == 1, path.name
        for name, count in counts.items():
            totals[name] += count
    assert set(totals.values()) == {7}


def test_redaction_audit_contains_decisions_but_no_sensitive_values() -> None:
    from unittest.mock import Mock, patch

    logger = Mock()
    with patch.object(sensitive_data, "logger", logger):
        result = sensitive_data.minimize_model_text(
            "Contact alice@example.test", surface="REFERENCE_EVIDENCE"
        )
    assert result == "Contact [REDACTED]"
    logger.info.assert_called_once()
    assert "alice@example.test" not in repr(logger.info.call_args)
    assert logger.info.call_args.kwargs["policy_version"] == "model-data-v1"
    assert logger.info.call_args.kwargs["categories"] == [{"category": "EMAIL", "count": 1}]
