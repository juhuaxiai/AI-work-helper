from collections.abc import Callable
from pathlib import Path

import pytest

from super_ai.organization import OrganizationBinding, OrganizationDirectory, OrganizationTenant


@pytest.fixture
def bind_oncall(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    members: list[OrganizationBinding] = []
    sources = ("test-provider", "local", "local-alertmanager")

    def resolve_directory(_config_path: Path | str | None) -> OrganizationDirectory:
        return OrganizationDirectory(
            tenants=(OrganizationTenant(
                tenant_key="test-tenant", oncall_department_id="oncall", alert_sources=sources,
            ),),
            members=tuple(members),
            cls_tenant_key="test-tenant",
        )

    def bind(user_id: str) -> None:
        members.append(OrganizationBinding(
            user_id=user_id, tenant_key="test-tenant", open_id=user_id, department_ids=("oncall",)
        ))

    monkeypatch.setattr("super_ai.api.app.load_organization_directory", resolve_directory)
    return bind
