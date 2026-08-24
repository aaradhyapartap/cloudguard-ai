"""AWS Lambda entrypoint.

Mangum translates between API Gateway's event shape and ASGI, so the same
FastAPI application runs unchanged under uvicorn locally and under Lambda in
AWS. One application, two transports — no "works locally, different code in
production" divergence.

``lifespan="off"``: Lambda freezes and thaws execution contexts rather than
running a clean process lifecycle, so ASGI lifespan events do not map onto it
cleanly. Startup work that matters must be import-time or per-request.
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from app.core.config import get_settings
from app.core.container import build_container
from app.core.logging import configure_logging
from app.main import create_app

_settings = get_settings()
configure_logging(_settings)

# Executed once per cold start, not per invocation: the container and its
# adapters are reused across warm invocations.
_container = build_container(_settings)
_app = create_app(_settings)

handler: Any = Mangum(_app, lifespan="off")
