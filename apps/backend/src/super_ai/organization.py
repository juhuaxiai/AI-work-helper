"""Resolve local organization membership from trusted account bindings."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from super_ai.project_config import ProjectConfigurationError, load_project_config

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OrganizationAccessDenied(PermissionError):
    """The current account has no permission to use this organization's diagnostic data."""


class OrganizationTenant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_key: Identifier
    oncall_department_id: Identifier
    alert_sources: tuple[Identifier, ...] = ()


class OrganizationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: Identifier
    tenant_key: Identifier
    open_id: Identifier
    department_ids: tuple[Identifier, ...]


class OrganizationDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenants: tuple[OrganizationTenant, ...] = ()
    members: tuple[OrganizationBinding, ...] = ()
    cls_tenant_key: Identifier | None = None

    @model_validator(mode="after")
    def validate_bindings(self) -> OrganizationDirectory:
        tenant_keys = {tenant.tenant_key for tenant in self.tenants}
        if len(tenant_keys) != len(self.tenants):
            raise ValueError("Organization tenant keys must be unique.")
        if self.cls_tenant_key is not None and self.cls_tenant_key not in tenant_keys:
            raise ValueError("The CLS tenant must exist in the organization directory.")
        user_ids = {member.user_id for member in self.members}
        identities = {(member.tenant_key, member.open_id) for member in self.members}
        if len(user_ids) != len(self.members) or len(identities) != len(self.members):
            raise ValueError("Each account and tenant member must have one organization binding.")
        if any(member.tenant_key not in tenant_keys for member in self.members):
            raise ValueError("Organization members must reference configured tenants.")
        alert_sources = [source for tenant in self.tenants for source in tenant.alert_sources]
        if len(alert_sources) != len(set(alert_sources)):
            raise ValueError("Each alert source must belong to one tenant.")
        return self

    def resolve(self, user_id: str) -> OrganizationMember | None:
        binding = next((member for member in self.members if member.user_id == user_id), None)
        if binding is None:
            return None
        tenant = next(tenant for tenant in self.tenants if tenant.tenant_key == binding.tenant_key)
        return OrganizationMember(
            id=binding.user_id,
            tenant_key=binding.tenant_key,
            open_id=binding.open_id,
            department_ids=binding.department_ids,
            can_diagnose=tenant.oncall_department_id in binding.department_ids,
            alert_sources=tenant.alert_sources,
        )


class OrganizationMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_key: str
    open_id: str
    department_ids: tuple[str, ...]
    can_diagnose: bool
    alert_sources: tuple[str, ...]

    def may_access_diagnosis(self, tenant_key: object) -> bool:
        return self.can_diagnose and self.tenant_key == tenant_key


def load_organization_directory(config_path: Path | str | None) -> OrganizationDirectory:
    try:
        config = load_project_config(config_path)
        return OrganizationDirectory.model_validate(config.get("organizationAccess", {}))
    except (ProjectConfigurationError, ValidationError):
        # Leave ordinary account access available and fail closed for organization resources.
        return OrganizationDirectory()


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    knowledge_base_id: str
    storage_owner_id: str
    name: str
    kind: Literal["personal", "shared", "sop", "cases"] = "personal"
    writable: bool = True
    tenant_key: str | None = None


def knowledge_scopes(user_id: str, member: OrganizationMember | None) -> tuple[KnowledgeScope, ...]:
    personal = KnowledgeScope(f"kb_{user_id}", user_id, "Personal knowledge base")
    if member is None:
        return (personal,)
    shared = _organization_knowledge_scope(
        member.tenant_key, "shared", "企业共享知识", writable=member.can_diagnose
    )
    if not member.can_diagnose:
        return (personal, shared)
    sop = _organization_knowledge_scope(member.tenant_key, "sop", "运维 SOP", writable=True)
    cases = KnowledgeScope(
        case_knowledge_base_id(user_id, member.tenant_key),
        user_id,
        "我的诊断案例",
        kind="cases",
        writable=False,
        tenant_key=member.tenant_key,
    )
    return (personal, shared, sop, cases)


def case_knowledge_base_id(user_id: str, tenant_key: str) -> str:
    return "kb_case_" + sha256(f"{tenant_key}\0{user_id}".encode()).hexdigest()[:40]


def _organization_knowledge_scope(
    tenant_key: str, kind: Literal["shared", "sop"], name: str, *, writable: bool,
) -> KnowledgeScope:
    owner = "orgkb_" + sha256(f"{kind}\0{tenant_key}".encode()).hexdigest()[:40]
    return KnowledgeScope(f"kb_{owner}", owner, name, kind, writable, tenant_key)
