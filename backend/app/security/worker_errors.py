"""Sanitize task failures before Lambda/Step Functions can serialize them."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from app.core.logging import get_logger

logger = get_logger(__name__)


class WorkerTaskError(RuntimeError):
    """Stable sanitized task failure; existing States.ALL catches still apply."""


def sanitize_worker_failures[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Preserve success results; never serialize raw input/exception messages."""
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            logger.error("worker_task_failed", error_type=type(exc).__name__)
            raise WorkerTaskError("Workflow task failed.") from None

    return wrapped
