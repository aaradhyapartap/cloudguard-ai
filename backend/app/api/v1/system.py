"""Authenticated system introspection.

``/me`` exists so the frontend can render navigation from server-authoritative
permissions rather than from its own copy of the rules. Two copies of an
authorization matrix drift; one does not.

``/system/config`` is admin-only and returns which adapters and feature flags
are active — the fastest way to answer "why is it behaving like that?" without
shell access. It exposes selections, never secrets.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import ContainerDep, PrincipalDep, requires
from app.security.authz import ROLE_PERMISSIONS, Permission

router = APIRouter(tags=["system"])


class MeResponse(BaseModel):
    user_id: str
    organization_id: str
    email: str
    role: str
    department: str | None
    permissions: list[str]
    visible_confidentiality_levels: list[str]


@router.get("/me", response_model=MeResponse, summary="Current caller")
async def me(principal: PrincipalDep) -> MeResponse:
    return MeResponse(
        user_id=str(principal.user_id),
        organization_id=str(principal.organization_id),
        email=principal.email,
        role=principal.role.value,
        department=principal.department,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[principal.role]),
        visible_confidentiality_levels=[
            level.value for level in principal.visible_confidentiality_levels
        ],
    )


class SystemConfigResponse(BaseModel):
    environment: str
    llm_provider: str
    vector_store: str
    document_store: str
    event_publisher: str
    chat_model: str
    embedding_model: str
    feature_flags: dict[str, bool]


@router.get(
    "/system/config",
    response_model=SystemConfigResponse,
    summary="Active adapters and feature flags",
    dependencies=[requires(Permission.SETTINGS_MANAGE)],
)
async def system_config(container: ContainerDep) -> SystemConfigResponse:
    settings = container.settings
    return SystemConfigResponse(
        environment=settings.environment.value,
        llm_provider=settings.llm_provider,
        vector_store=settings.vector_store,
        document_store=settings.document_store,
        event_publisher=settings.event_publisher,
        chat_model=container.llm.chat_model_id,
        embedding_model=container.embeddings.embedding_model_id,
        feature_flags=settings.features.model_dump(),
    )
