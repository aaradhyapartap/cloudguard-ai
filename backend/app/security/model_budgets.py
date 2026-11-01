"""Provider-neutral byte/shape ceilings, explicitly not a vendor tokenizer."""

import json
from collections.abc import Sequence

MAX_GENERATION_INPUT_BYTES = 128_000
MAX_GENERATION_OUTPUT_BYTES = 64_000
MAX_GENERATION_OUTPUT_TOKENS = 4096
MAX_GENERATION_MESSAGES = 32
MAX_STOP_SEQUENCES = 8
MAX_STOP_SEQUENCE_CHARACTERS = 128
MAX_EMBEDDING_BATCH = 200
MAX_EMBEDDING_TEXT_BYTES = 8192
MAX_EMBEDDING_BATCH_BYTES = 800_000
MAX_EMBEDDING_RESPONSE_BYTES = 128_000


class ModelBudgetError(ValueError):
    """An input or response exceeds an application-owned resource ceiling."""


def require_text_budget(text: str, *, max_bytes: int) -> int:
    # Check characters first to bound the allocation needed for UTF-8 encoding.
    if len(text) > max_bytes:
        raise ModelBudgetError("The model text exceeds the resource budget.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ModelBudgetError("The model text is not valid UTF-8.") from exc
    if size > max_bytes:
        raise ModelBudgetError("The model text exceeds the resource budget.")
    return size


def require_generation_envelope(payload: dict[str, object], *, max_bytes: int) -> None:
    if not 1 <= max_bytes <= MAX_GENERATION_INPUT_BYTES:
        raise ModelBudgetError("The generation input budget is invalid.")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ModelBudgetError("The model request envelope is invalid.") from exc
    require_text_budget(serialized, max_bytes=max_bytes)


def checked_embedding_inputs(texts: Sequence[str]) -> list[str]:
    if len(texts) > MAX_EMBEDDING_BATCH:
        raise ModelBudgetError("The embedding batch exceeds the resource budget.")
    snapshot = list(texts)
    total = 0
    for text in snapshot:
        total += require_text_budget(text, max_bytes=MAX_EMBEDDING_TEXT_BYTES)
        if total > MAX_EMBEDDING_BATCH_BYTES:
            raise ModelBudgetError("The embedding batch exceeds the resource budget.")
    return snapshot
