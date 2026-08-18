"""Pydantic v2 schemas for skills, tool I/O, requests, and audit traces."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolStatus(str, Enum):
    SUCCESS = "SUCCESS"
    GUARDRAIL_BLOCKED_POLICY_VIOLATION = "GUARDRAIL_BLOCKED_POLICY_VIOLATION"
    GUARDRAIL_BLOCKED_INVALID_PARAMS = "GUARDRAIL_BLOCKED_INVALID_PARAMS"
    GUARDRAIL_BLOCKED_UNAUTHORIZED_MUTATION = (
        "GUARDRAIL_BLOCKED_UNAUTHORIZED_MUTATION"
    )
    TOOL_ERROR = "TOOL_ERROR"


class MutationKind(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


# ---------------------------------------------------------------------------
# Skill catalog (loaded from YAML — never from prompt strings)
# ---------------------------------------------------------------------------


class SkillDefinition(BaseModel):
    """Declarative skill contract authored by domain experts in YAML."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str
    system_prompt_fragment: str
    required_permissions: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    intent_triggers: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    always_load: bool = False


class BlockedSkillReason(BaseModel):
    skill_id: str
    reason: str


class SkillResolution(BaseModel):
    loaded: list[SkillDefinition]
    blocked: list[BlockedSkillReason]
    matched_intents: list[str] = Field(default_factory=list)


class SkillSelection(BaseModel):
    """Gemini's phase-1 output: which YAML skills to inject this turn."""

    skill_ids: list[str] = Field(
        default_factory=list,
        description="Catalog skill ids to load this turn, e.g. order_status",
    )
    rationale: str = Field(
        default="",
        description="One or two sentences explaining why these skills and not the others",
    )


# ---------------------------------------------------------------------------
# Mock system-of-record payloads
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    user_id: str
    full_name: str
    email: str
    account_status: str
    loyalty_tier: str
    created_at: datetime


class OrderItem(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_price: float


class OrderDetails(BaseModel):
    order_id: str
    user_id: str
    items: list[OrderItem]
    total_amount: float
    currency: str = "USD"
    status: str
    tracking_number: str | None = None
    carrier: str | None = None
    created_at: datetime
    refunded_amount: float = 0.0

    @property
    def refundable_balance(self) -> float:
        return round(self.total_amount - self.refunded_amount, 2)


class RefundResult(BaseModel):
    refund_id: str
    order_id: str
    amount: float
    currency: str = "USD"
    reason: str
    status: str
    gateway: str
    processed_at: datetime


class SupportTicket(BaseModel):
    ticket_id: str
    user_id: str
    issue_summary: str
    priority: str
    status: str
    queue: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Guardrails & execution records
# ---------------------------------------------------------------------------


class GuardrailDecision(BaseModel):
    allowed: bool
    status: ToolStatus
    reason: str
    escalate: bool = False
    escalation_summary: str | None = None


class ToolExecutionRecord(BaseModel):
    tool_name: str
    target_endpoint: str
    input: dict[str, Any]
    status: ToolStatus
    reason: str | None = None
    output: dict[str, Any] | None = None
    mutation: MutationKind | None = None


class AuditExecutionTrace(BaseModel):
    """Complete observability payload emitted on every agent turn."""

    session_id: str
    user_query: str
    user_permissions: list[str]
    dynamically_loaded_skills: list[str]
    blocked_skills_reason: list[BlockedSkillReason] = Field(default_factory=list)
    tools_executed: list[ToolExecutionRecord] = Field(default_factory=list)
    final_response: str
    skill_selection_rationale: str = ""
    catalog_chars: int = 0
    full_context_chars: int = 0
    dynamic_context_chars: int = 0
    gemini_model: str = ""
    gemini_calls: int = 0
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# HTTP / CLI request surface
# ---------------------------------------------------------------------------


class TurnRequest(BaseModel):
    query: str
    user_id: str = "usr_1001"
    user_permissions: list[str] = Field(
        default_factory=lambda: ["tier_1_support", "tier_2_support"]
    )
    session_id: str | None = None

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        return stripped


class SessionSnapshot(BaseModel):
    session_id: str
    user_id: str
    user_permissions: list[str]
    last_order_id: str | None = None
    turn_count: int = 0
    transcript: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
