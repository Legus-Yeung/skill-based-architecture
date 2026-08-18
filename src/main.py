"""FastAPI REST surface and optional CLI chat loop."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

from config import settings
from src.models import AuditExecutionTrace, TurnRequest
from src.orchestrator import orchestrator
from src.skill_registry import registry

app = FastAPI(
    title="Wonderful AI — Enterprise Support Hub",
    description=(
        "Skill-based context engineering PoC. Skills are YAML. Systems are "
        "tokenized. Every turn emits an audit execution trace."
    ),
    version="0.1.0",
)


class HealthResponse(BaseModel):
    status: str
    skills_loaded: list[str]
    gemini_configured: bool
    gemini_model: str


class SkillCatalogItem(BaseModel):
    id: str
    name: str
    required_permissions: list[str]
    available_tools: list[str]
    policy: dict[str, object] = Field(default_factory=dict)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        skills_loaded=[skill.id for skill in registry.all_skills()],
        gemini_configured=bool(settings.gemini_api_key.strip()),
        gemini_model=settings.gemini_model,
    )


@app.get("/v1/skills", response_model=list[SkillCatalogItem])
def list_skills() -> list[SkillCatalogItem]:
    return [
        SkillCatalogItem(
            id=skill.id,
            name=skill.name,
            required_permissions=skill.required_permissions,
            available_tools=skill.available_tools,
            policy=skill.policy,
        )
        for skill in registry.all_skills()
    ]


@app.post("/v1/agent/turn", response_model=AuditExecutionTrace)
def agent_turn(request: TurnRequest) -> AuditExecutionTrace:
    return orchestrator.handle_turn(request)


def _cli_loop(permissions: list[str], user_id: str) -> None:
    session_id: str | None = None
    print("Wonderful AI support hub (CLI). Empty line or 'quit' to exit.\n")
    while True:
        try:
            query = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not query or query.lower() in {"quit", "exit"}:
            return
        trace = orchestrator.handle_turn(
            TurnRequest(
                query=query,
                user_id=user_id,
                user_permissions=permissions,
                session_id=session_id,
            )
        )
        session_id = trace.session_id
        print(trace.final_response)
        print(json.dumps(trace.model_dump(mode="json"), indent=2))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Wonderful AI support hub")
    parser.add_argument("--cli", action="store_true", help="Interactive CLI chat")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--permissions",
        default="tier_1_support,tier_2_support",
        help="Comma-separated authorization tokens for CLI mode",
    )
    parser.add_argument("--user-id", default="usr_1001")
    args = parser.parse_args()
    if args.cli:
        _cli_loop(
            permissions=[p.strip() for p in args.permissions.split(",") if p.strip()],
            user_id=args.user_id,
        )
        return
    uvicorn.run("src.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
