"""Scripted multi-turn demo: Gemini chooses skills, then dummy tools.

The point of the demo is dynamic context, not live integrations.

Phase 1 — Gemini sees a thin skill catalog (no YAML prompt fragments).
Phase 2 — Only the chosen skills' fragments + dummy tools are injected.
Phase 3 — Guardrails still block policy violations; dummy data answers the user.

Usage:
    export GEMINI_API_KEY=...
    python demo.py
    python demo.py --interactive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from config import settings
from src.gemini_client import GeminiNotConfiguredError
from src.mock_tools import reset_stores
from src.models import TurnRequest
from src.orchestrator import Orchestrator
from src.skill_registry import SkillRegistry

console = Console()

DEMO_PERMISSIONS = ["tier_1_support", "tier_2_support"]
DEMO_USER = "usr_1001"

SCRIPT = [
    (
        1,
        "What's the status of order 4401?",
        "Gemini should load order_status and read dummy POSTGRES data.",
    ),
    (
        2,
        "I want a $50 refund on order #9928",
        "Gemini should load execute_refund and call dummy Stripe (≤ $100).",
    ),
    (
        3,
        "I want a $200 refund on order #9928",
        "Guardrail blocks Stripe; Gemini should open a dummy Zendesk ticket.",
    ),
]


def _require_key() -> None:
    if settings.gemini_api_key.strip():
        return
    console.print(
        Panel(
            "GEMINI_API_KEY is not set.\n\n"
            "1. Copy [bold].env.example[/bold] to [bold].env[/bold]\n"
            "2. Paste your Gemini API key on the GEMINI_API_KEY line\n"
            "3. Re-run [bold]python demo.py[/bold]",
            title="Missing Gemini key",
            border_style="red",
        )
    )
    raise SystemExit(1)


def _print_trace(trace) -> None:
    saved = max(0, trace.full_context_chars - trace.dynamic_context_chars)
    pct = (
        int(100 * saved / trace.full_context_chars)
        if trace.full_context_chars
        else 0
    )
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("model", trace.gemini_model)
    table.add_row("gemini calls", str(trace.gemini_calls))
    table.add_row("session", trace.session_id)
    table.add_row("loaded skills", ", ".join(trace.dynamically_loaded_skills) or "(none)")
    table.add_row("catalog (router)", f"{trace.catalog_chars} chars — no prompt fragments")
    table.add_row(
        "naive context",
        f"{trace.full_context_chars} chars — all 4 skill fragments",
    )
    table.add_row(
        "injected this turn",
        f"{trace.dynamic_context_chars} chars — selected skills only (−{pct}%)",
    )
    if trace.skill_selection_rationale:
        table.add_row("why these skills", trace.skill_selection_rationale)
    if trace.blocked_skills_reason:
        blocked = "; ".join(
            f"{item.skill_id}: {item.reason}" for item in trace.blocked_skills_reason
        )
        table.add_row("blocked skills", blocked)
    console.print(Panel(table, title="Dynamic context", border_style="green"))

    payload = json.dumps(trace.model_dump(mode="json"), indent=2)
    console.print(
        Panel(
            Syntax(payload, "json", theme="monokai", word_wrap=True),
            title="Audit Execution Trace",
            border_style="yellow",
        )
    )
    console.print(
        Panel(trace.final_response, title="Gemini response", border_style="blue")
    )


def _run_engine() -> Orchestrator:
    reset_stores()
    return Orchestrator(skill_registry=SkillRegistry())


def run_scripted() -> None:
    _require_key()
    engine = _run_engine()
    session_id: str | None = None

    console.print(
        Panel(
            "[bold]Wonderful AI[/bold] — Gemini skill router\n"
            "Catalog in → fragments out → dummy tools → audit trace",
            border_style="white",
        )
    )

    for turn, query, caption in SCRIPT:
        console.rule(f"[bold]Turn {turn}[/bold] — {caption}")
        console.print(f"[bold cyan]user>[/bold cyan] {query}")
        try:
            trace = engine.handle_turn(
                TurnRequest(
                    query=query,
                    user_id=DEMO_USER,
                    user_permissions=DEMO_PERMISSIONS,
                    session_id=session_id,
                )
            )
        except GeminiNotConfiguredError as exc:
            console.print(f"[red]{exc}[/red]")
            raise SystemExit(1) from exc
        session_id = trace.session_id
        _print_trace(trace)


def run_interactive() -> None:
    _require_key()
    engine = _run_engine()
    session_id: str | None = None
    console.print(
        "Interactive Gemini mode. Ask for order status, a small refund, then a "
        "large refund. Empty line or 'quit' to exit.\n"
    )
    while True:
        try:
            query = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not query or query.lower() in {"quit", "exit"}:
            return
        trace = engine.handle_turn(
            TurnRequest(
                query=query,
                user_id=DEMO_USER,
                user_permissions=DEMO_PERMISSIONS,
                session_id=session_id,
            )
        )
        session_id = trace.session_id
        _print_trace(trace)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wonderful AI Gemini demo")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Type queries instead of running the three scripted turns",
    )
    args = parser.parse_args()
    if args.interactive:
        run_interactive()
    else:
        run_scripted()


if __name__ == "__main__":
    main()
