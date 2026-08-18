"""Mock clients for tokenized external systems.

Each function prints a structured execution log showing the interface token
and the simulated SQL/HTTP operation. Nothing here opens a real socket.
Swap these implementations for production drivers that read the same
environment tokens from `config.settings`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import settings
from src.models import (
    MutationKind,
    OrderDetails,
    OrderItem,
    RefundResult,
    SupportTicket,
    UserProfile,
)

console = Console()

_REFUND_SEQ = 1000
_TICKET_SEQ = 8000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory stand-ins for POSTGRES_DATABASE_1 tables
# ---------------------------------------------------------------------------

USERS: dict[str, UserProfile] = {
    "usr_1001": UserProfile(
        user_id="usr_1001",
        full_name="Alice Chen",
        email="alice.chen@example.com",
        account_status="active",
        loyalty_tier="gold",
        created_at=datetime(2023, 4, 12, 15, 30, tzinfo=timezone.utc),
    ),
    "usr_1002": UserProfile(
        user_id="usr_1002",
        full_name="Ben Okonkwo",
        email="ben.okonkwo@example.com",
        account_status="active",
        loyalty_tier="silver",
        created_at=datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc),
    ),
}

ORDERS: dict[str, OrderDetails] = {
    "4401": OrderDetails(
        order_id="4401",
        user_id="usr_1001",
        items=[
            OrderItem(
                sku="WB-2048",
                name="Merino Travel Sweater",
                quantity=1,
                unit_price=89.99,
            )
        ],
        total_amount=89.99,
        status="shipped",
        tracking_number="TRK-998877",
        carrier="UPS",
        created_at=datetime(2026, 8, 10, 18, 42, tzinfo=timezone.utc),
    ),
    "9928": OrderDetails(
        order_id="9928",
        user_id="usr_1001",
        items=[
            OrderItem(
                sku="AV-1100",
                name="Noise-Cancelling Headphones",
                quantity=1,
                unit_price=249.00,
            )
        ],
        total_amount=249.00,
        status="delivered",
        tracking_number="TRK-441120",
        carrier="FedEx",
        created_at=datetime(2026, 8, 2, 11, 5, tzinfo=timezone.utc),
    ),
}


def normalize_order_id(order_id: str) -> str:
    cleaned = order_id.strip().upper().lstrip("#")
    if cleaned.startswith("ORD-"):
        cleaned = cleaned[4:]
    return cleaned


def _log_call(
    *,
    tool_name: str,
    token: str,
    endpoint: str,
    mutation: MutationKind,
    operation: str,
    payload: dict[str, Any],
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold cyan", min_width=12)
    table.add_column()
    table.add_row("tool", tool_name)
    table.add_row("token", token)
    table.add_row("endpoint", endpoint)
    table.add_row("mutation", mutation.value)
    table.add_row("operation", operation)
    for key, value in payload.items():
        table.add_row(key, str(value))
    console.print(
        Panel(
            table,
            title=f"[bold]MOCK {mutation.value}[/bold]  {token}",
            border_style="cyan" if mutation == MutationKind.READ else "magenta",
        )
    )


def fetch_user_profile(user_id: str) -> UserProfile:
    """Simulated SELECT against POSTGRES_DATABASE_1.customers."""
    _log_call(
        tool_name="fetch_user_profile",
        token=settings.TOKEN_POSTGRES,
        endpoint=settings.postgres_database_1,
        mutation=MutationKind.READ,
        operation="SELECT * FROM customers WHERE user_id = :user_id",
        payload={"user_id": user_id},
    )
    profile = USERS.get(user_id)
    if profile is None:
        raise LookupError(f"No customer found for user_id={user_id!r}")
    return profile


def fetch_order_details(order_id: str) -> OrderDetails:
    """Simulated SELECT against POSTGRES_DATABASE_1.orders."""
    oid = normalize_order_id(order_id)
    _log_call(
        tool_name="fetch_order_details",
        token=settings.TOKEN_POSTGRES,
        endpoint=settings.postgres_database_1,
        mutation=MutationKind.READ,
        operation="SELECT * FROM orders WHERE order_id = :order_id",
        payload={"order_id": oid},
    )
    order = ORDERS.get(oid)
    if order is None:
        raise LookupError(f"No order found for order_id={oid!r}")
    return order


def process_refund(order_id: str, amount: float, reason: str) -> RefundResult:
    """Simulated POST against STRIPE_PAYMENT_GATEWAY_1 refunds API."""
    global _REFUND_SEQ
    oid = normalize_order_id(order_id)
    _REFUND_SEQ += 1
    refund_id = f"re_{_REFUND_SEQ}"
    _log_call(
        tool_name="process_refund",
        token=settings.TOKEN_STRIPE,
        endpoint=settings.stripe_payment_gateway_1,
        mutation=MutationKind.WRITE,
        operation="POST /v1/refunds",
        payload={
            "order_id": oid,
            "amount": f"{amount:.2f}",
            "reason": reason,
            "refund_id": refund_id,
        },
    )
    order = ORDERS.get(oid)
    if order is None:
        raise LookupError(f"No order found for order_id={oid!r}")
    order.refunded_amount = round(order.refunded_amount + amount, 2)
    if order.refunded_amount >= order.total_amount:
        order.status = "refunded"
    return RefundResult(
        refund_id=refund_id,
        order_id=oid,
        amount=amount,
        reason=reason,
        status="succeeded",
        gateway=settings.TOKEN_STRIPE,
        processed_at=_utcnow(),
    )


def create_support_ticket(user_id: str, issue_summary: str) -> SupportTicket:
    """Simulated POST against ZENDESK_API_1, authenticated by ZENDESK_API_TOKEN."""
    global _TICKET_SEQ
    _TICKET_SEQ += 1
    ticket_id = f"TCK-{_TICKET_SEQ}"
    _log_call(
        tool_name="create_support_ticket",
        token=settings.TOKEN_ZENDESK,
        endpoint=settings.zendesk_api_1,
        mutation=MutationKind.WRITE,
        operation="POST /api/v2/tickets",
        payload={
            "user_id": user_id,
            "issue_summary": issue_summary,
            "auth": f"Bearer {settings.zendesk_api_token[:12]}…",
            "ticket_id": ticket_id,
        },
    )
    return SupportTicket(
        ticket_id=ticket_id,
        user_id=user_id,
        issue_summary=issue_summary,
        priority="high",
        status="open",
        queue="finance_review",
        created_at=_utcnow(),
    )


TOOL_ENDPOINTS: dict[str, str] = {
    "fetch_user_profile": settings.TOKEN_POSTGRES,
    "fetch_order_details": settings.TOKEN_POSTGRES,
    "process_refund": settings.TOKEN_STRIPE,
    "create_support_ticket": settings.TOKEN_ZENDESK,
}

TOOL_MUTATIONS: dict[str, MutationKind] = {
    "fetch_user_profile": MutationKind.READ,
    "fetch_order_details": MutationKind.READ,
    "process_refund": MutationKind.WRITE,
    "create_support_ticket": MutationKind.WRITE,
}

# Postgres is a read model for this agent. Money moves through Stripe;
# cases move through Zendesk. Any WRITE targeting Postgres is unauthorized.
POSTGRES_WRITES_AUTHORIZED = False


def reset_stores() -> None:
    """Restore seed data so demos and tests start from a known ledger."""
    global _REFUND_SEQ, _TICKET_SEQ
    _REFUND_SEQ = 1000
    _TICKET_SEQ = 8000
    for order in ORDERS.values():
        if order.order_id == "4401":
            order.status = "shipped"
            order.refunded_amount = 0.0
        elif order.order_id == "9928":
            order.status = "delivered"
            order.refunded_amount = 0.0
