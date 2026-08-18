"""Gemini FunctionDeclarations for dummy tools.

Only declarations bound to *loaded* skills are sent to the model. That is
the second half of dynamic context: unused tools never appear in the
function-calling schema for the turn.
"""

from __future__ import annotations

from google.genai import types

FETCH_USER_PROFILE = types.FunctionDeclaration(
    name="fetch_user_profile",
    description=(
        "Look up a customer profile from POSTGRES_DATABASE_1. "
        "Returns dummy profile fields (name, email, loyalty tier)."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "Customer id, e.g. usr_1001",
            }
        },
        "required": ["user_id"],
    },
)

FETCH_ORDER_DETAILS = types.FunctionDeclaration(
    name="fetch_order_details",
    description=(
        "Look up purchase history and tracking from POSTGRES_DATABASE_1. "
        "Returns dummy order status, items, total, and tracking number."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Order id such as 4401 or 9928",
            }
        },
        "required": ["order_id"],
    },
)

PROCESS_REFUND = types.FunctionDeclaration(
    name="process_refund",
    description=(
        "Submit a refund to STRIPE_PAYMENT_GATEWAY_1. Always call this for a "
        "refund request. Dummy payment write. Over-threshold amounts are "
        "rejected by the guardrail in the tool result — do not skip the call."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order to refund"},
            "amount": {
                "type": "number",
                "description": "Refund amount in USD",
            },
            "reason": {
                "type": "string",
                "description": "Customer-facing reason for the refund",
            },
        },
        "required": ["order_id", "amount", "reason"],
    },
)

CREATE_SUPPORT_TICKET = types.FunctionDeclaration(
    name="create_support_ticket",
    description=(
        "Open a Zendesk ticket on ZENDESK_API_1 for manager review. "
        "Use after a policy-blocked refund or when the caller needs a human."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "Customer id"},
            "issue_summary": {
                "type": "string",
                "description": "What the specialist needs to know",
            },
        },
        "required": ["user_id", "issue_summary"],
    },
)

DECLARATIONS: dict[str, types.FunctionDeclaration] = {
    "fetch_user_profile": FETCH_USER_PROFILE,
    "fetch_order_details": FETCH_ORDER_DETAILS,
    "process_refund": PROCESS_REFUND,
    "create_support_ticket": CREATE_SUPPORT_TICKET,
}


def tools_for_skills(tool_names: list[str]) -> list[types.Tool]:
    decls = [DECLARATIONS[name] for name in tool_names if name in DECLARATIONS]
    if not decls:
        return []
    return [types.Tool(function_declarations=decls)]
