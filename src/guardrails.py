"""Pre- and post-execution governance layer.

Business rules that must hold regardless of what the model (or the simulated
router) requested. Thresholds are read from skill YAML so a domain expert
can change policy without shipping a new orchestrator build.
"""

from __future__ import annotations

from typing import Any

from src.mock_tools import (
    ORDERS,
    POSTGRES_WRITES_AUTHORIZED,
    TOOL_ENDPOINTS,
    TOOL_MUTATIONS,
    normalize_order_id,
)
from src.models import (
    GuardrailDecision,
    MutationKind,
    SkillDefinition,
    ToolExecutionRecord,
    ToolStatus,
)


def _skill_by_id(
    skills: list[SkillDefinition], skill_id: str
) -> SkillDefinition | None:
    for skill in skills:
        if skill.id == skill_id:
            return skill
    return None


def _refund_threshold(skills: list[SkillDefinition]) -> float:
    skill = _skill_by_id(skills, "execute_refund")
    if skill is None:
        return 100.0
    return float(skill.policy.get("auto_approve_threshold_usd", 100.0))


def _min_refund_amount(skills: list[SkillDefinition]) -> float:
    skill = _skill_by_id(skills, "execute_refund")
    if skill is None:
        return 0.01
    return float(skill.policy.get("min_amount", 0.01))


def pre_execute(
    tool_name: str,
    params: dict[str, Any],
    active_skills: list[SkillDefinition],
) -> GuardrailDecision:
    """Validate tool arguments against policy before any mock I/O runs."""
    allowed_tools = {tool for skill in active_skills for tool in skill.available_tools}
    if tool_name not in allowed_tools:
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_UNAUTHORIZED_MUTATION,
            reason=(
                f"Tool {tool_name!r} is not bound to any dynamically loaded skill"
            ),
        )

    if tool_name == "process_refund":
        return _validate_refund(params, active_skills)

    if tool_name in {"fetch_user_profile", "fetch_order_details", "create_support_ticket"}:
        required = {
            "fetch_user_profile": "user_id",
            "fetch_order_details": "order_id",
            "create_support_ticket": "user_id",
        }[tool_name]
        value = params.get(required)
        if not value or not str(value).strip():
            return GuardrailDecision(
                allowed=False,
                status=ToolStatus.GUARDRAIL_BLOCKED_INVALID_PARAMS,
                reason=f"{required} is required for {tool_name}",
            )

    return GuardrailDecision(
        allowed=True,
        status=ToolStatus.SUCCESS,
        reason="Pre-execution checks passed",
    )


def _validate_refund(
    params: dict[str, Any], active_skills: list[SkillDefinition]
) -> GuardrailDecision:
    order_id = params.get("order_id")
    amount = params.get("amount")
    min_amount = _min_refund_amount(active_skills)
    threshold = _refund_threshold(active_skills)

    if not order_id or not str(order_id).strip():
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_INVALID_PARAMS,
            reason="Refund requires a non-empty order_id",
        )

    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_INVALID_PARAMS,
            reason="Refund amount must be a number",
        )

    if amount_value < min_amount:
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_INVALID_PARAMS,
            reason=(
                f"Refund amount must be positive and at least ${min_amount:.2f} "
                f"(received {amount_value})"
            ),
        )

    if amount_value > threshold:
        oid = normalize_order_id(str(order_id))
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_POLICY_VIOLATION,
            reason=(
                f"Amount ${amount_value:.2f} exceeds auto-refund threshold "
                f"(${threshold:.2f})"
            ),
            escalate=True,
            escalation_summary=(
                f"High-value refund request on order #{oid}: "
                f"${amount_value:.2f} exceeds the ${threshold:.2f} auto-approve "
                "limit. Manager review required."
            ),
        )

    oid = normalize_order_id(str(order_id))
    order = ORDERS.get(oid)
    if order is None:
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_INVALID_PARAMS,
            reason=f"No order found for order_id={oid!r}",
        )

    if amount_value > order.refundable_balance:
        return GuardrailDecision(
            allowed=False,
            status=ToolStatus.GUARDRAIL_BLOCKED_POLICY_VIOLATION,
            reason=(
                f"Amount ${amount_value:.2f} exceeds refundable balance "
                f"${order.refundable_balance:.2f} on order #{order.order_id}"
            ),
            escalate=True,
            escalation_summary=(
                f"Refund of ${amount_value:.2f} on order #{order.order_id} "
                "exceeds remaining balance. Finance review required."
            ),
        )

    return GuardrailDecision(
        allowed=True,
        status=ToolStatus.SUCCESS,
        reason="Refund within auto-approve policy",
    )


def post_execute(
    records: list[ToolExecutionRecord],
) -> GuardrailDecision:
    """Reject unauthorized writes against the customer system of record.

    This agent may READ Postgres. It may WRITE Stripe (refunds) and Zendesk
    (tickets). Any WRITE whose target token is POSTGRES_DATABASE_1 is a
    policy violation, even if a model asked for it.
    """
    for record in records:
        mutation = record.mutation or TOOL_MUTATIONS.get(record.tool_name)
        endpoint = record.target_endpoint or TOOL_ENDPOINTS.get(record.tool_name, "")
        if (
            mutation == MutationKind.WRITE
            and endpoint == "POSTGRES_DATABASE_1"
            and not POSTGRES_WRITES_AUTHORIZED
        ):
            return GuardrailDecision(
                allowed=False,
                status=ToolStatus.GUARDRAIL_BLOCKED_UNAUTHORIZED_MUTATION,
                reason=(
                    f"{record.tool_name} attempted a WRITE against "
                    "POSTGRES_DATABASE_1, which is read-only for this agent"
                ),
            )
    return GuardrailDecision(
        allowed=True,
        status=ToolStatus.SUCCESS,
        reason="No unauthorized database mutations requested",
    )
