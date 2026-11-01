from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, BigInteger, DateTime, ForeignKey, Identity, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
    # Load server-generated values (ids, timestamps) on insert instead of lazily.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    subject: Mapped[str] = mapped_column(unique=True)
    username: Mapped[str]
    email: Mapped[str | None]
    display_name: Mapped[str]
    is_platform_admin: Mapped[bool] = mapped_column(server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[datetime | None]


class Membership(Base):
    """One role or permission flag a user holds in a tenant, mirrored from IdP groups."""

    __tablename__ = "memberships"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    grant_name: Mapped[str] = mapped_column(primary_key=True)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    token_hash: Mapped[str] = mapped_column(unique=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    refresh_token: Mapped[str]
    id_token: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    synced_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    prefix: Mapped[str]
    token_hash: Mapped[str] = mapped_column(unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    at: Mapped[datetime]
    actor: Mapped[str]
    tenant_id: Mapped[UUID | None]
    action: Mapped[str]
    target: Mapped[str]
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str]
    hash: Mapped[str]


class TenantConfigRow(Base):
    __tablename__ = "tenant_config"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[str]


class CaseNumber(Base):
    """Per-tenant counter behind human-readable case numbers."""

    __tablename__ = "case_numbers"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    last: Mapped[int]


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    number: Mapped[int]
    title: Mapped[str]
    description: Mapped[str]
    state: Mapped[str]
    category: Mapped[str]
    impact: Mapped[str]
    urgency: Mapped[str]
    severity: Mapped[str]
    priority: Mapped[str]
    override_justification: Mapped[str | None]
    team: Mapped[str | None]
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str]
    tlp: Mapped[str]
    pap: Mapped[str]
    restricted: Mapped[bool]
    regulated_data: Mapped[list[str]] = mapped_column(ARRAY(Text))
    data_regions: Mapped[list[str]] = mapped_column(ARRAY(Text))
    business_units: Mapped[list[str]] = mapped_column(ARRAY(Text))
    attack_techniques: Mapped[list[str]] = mapped_column(ARRAY(Text))
    custom: Mapped[dict[str, Any]] = mapped_column(JSONB)
    resolution: Mapped[str | None]
    merged_into_id: Mapped[UUID | None] = mapped_column(ForeignKey("cases.id"))
    detected_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]
    acknowledged_at: Mapped[datetime | None]
    triaged_at: Mapped[datetime | None]
    contained_at: Mapped[datetime | None]
    major_incident_at: Mapped[datetime | None]
    major_incident_reason: Mapped[str | None]
    legal_hold: Mapped[bool] = mapped_column(server_default="false")
    privileged: Mapped[bool] = mapped_column(server_default="false")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    source: Mapped[str]
    external_id: Mapped[str | None]
    group_key: Mapped[str | None]
    title: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str]
    name: Mapped[str]
    external_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CaseAsset(Base):
    __tablename__ = "case_assets"

    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    title: Mapped[str]
    description: Mapped[str]
    status: Mapped[str]
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None]
    depends_on: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    waive_reason: Mapped[str | None]
    playbook_key: Mapped[str | None]
    position: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None]


class Notification(Base):
    """Outbox of escalation messages; one row per rule, trigger, and SLA step of a case."""

    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    rule: Mapped[str]
    trigger: Mapped[str]
    step: Mapped[str]
    emails: Mapped[list[str]] = mapped_column(ARRAY(Text))
    webhook: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None]


class Observable(Base):
    __tablename__ = "observables"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    type: Mapped[str]
    value: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CaseObservable(Base):
    __tablename__ = "case_observables"

    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    observable_id: Mapped[UUID] = mapped_column(ForeignKey("observables.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    kind: Mapped[str]  # file, malware, or reference
    name: Mapped[str]
    description: Mapped[str]
    size: Mapped[int | None] = mapped_column(BigInteger)
    location: Mapped[str | None]  # where a reference's evidence is held
    object_key: Mapped[str | None]
    upload_id: Mapped[str | None]
    status: Mapped[str]
    sha256: Mapped[str | None]  # of the evidence itself
    object_sha256: Mapped[str | None]  # of the stored object, checked on every download
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None]


class CustodyEntry(Base):
    """Chain of custody; append-only and hash-chained per evidence item."""

    __tablename__ = "custody_log"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("evidence.id"))
    at: Mapped[datetime]
    actor: Mapped[str]
    action: Mapped[str]
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str]
    hash: Mapped[str]


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    occurred_at: Mapped[datetime]  # when it happened, not when it was recorded
    source: Mapped[str]
    description: Mapped[str]
    milestone: Mapped[str | None]
    observable_id: Mapped[UUID | None] = mapped_column(ForeignKey("observables.id"))
    asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id"))
    evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("evidence.id"))
    recorded_by: Mapped[str]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    body: Mapped[str]  # Markdown, never rendered as raw HTML
    privileged: Mapped[bool]
    revision: Mapped[int]
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NoteRevision(Base):
    """Every version of a note, append-only."""

    __tablename__ = "note_revisions"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    note_id: Mapped[UUID] = mapped_column(ForeignKey("notes.id"))
    revision: Mapped[int]
    body: Mapped[str]
    privileged: Mapped[bool]
    edited_by: Mapped[str]
    edited_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    body: Mapped[str]
    author: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RegulatoryClock(Base):
    """A breach-notification deadline running for a case; met by a logged notification."""

    __tablename__ = "regulatory_clocks"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    regime: Mapped[str]  # regime key
    name: Mapped[str]  # regime name when the clock started
    started_at: Mapped[datetime]
    due_at: Mapped[datetime]
    warn_at: Mapped[datetime]
    met_at: Mapped[datetime | None]


class MaterialityDecision(Base):
    """Whether an incident is material or reportable; append-only."""

    __tablename__ = "materiality_decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    material: Mapped[bool]
    decided_by: Mapped[str]  # the person or body that made the decision
    decided_at: Mapped[datetime]
    rationale: Mapped[str]
    recorded_by: Mapped[str]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Disclosure(Base):
    """A notification sent outside the organization, such as to a regulator; append-only."""

    __tablename__ = "disclosures"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    recipient_type: Mapped[str]
    recipient: Mapped[str]
    sent_at: Mapped[datetime]
    summary: Mapped[str]
    clock_id: Mapped[UUID | None] = mapped_column(ForeignKey("regulatory_clocks.id"))
    evidence_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))  # attachments
    recorded_by: Mapped[str]
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    query: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Pir(Base):
    """A case's post-incident review."""

    __tablename__ = "pirs"

    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    root_cause: Mapped[str]
    contributing_factors: Mapped[str]
    went_well: Mapped[str]
    went_poorly: Mapped[str]
    updated_by: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PirAction(Base):
    """An improvement agreed in a post-incident review, tracked until done."""

    __tablename__ = "pir_actions"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    title: Mapped[str]
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
