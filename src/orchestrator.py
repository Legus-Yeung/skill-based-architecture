"""Gemini-backed execution engine with dynamic skill context.

Two model calls, two different payloads:

1. **Skill router** — Gemini sees a thin catalog (id, description, tools).
   It does not see YAML prompt fragments or policy numbers.
2. **Acting agent** — only the selected, authorized skill fragments plus
   those skills' dummy tools are injected. Unused skills cost zero tokens.

Guardrails still wrap every tool call. Dummy tools never leave process memory.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from google.genai import types

from src import guardrails
from src.gemini_client import GeminiRuntime, function_calls_of
from src.mock_tools import (
    TOOL_ENDPOINTS,
    TOOL_MUTATIONS,
    create_support_ticket,
    fetch_order_details,
    fetch_user_profile,
    process_refund,
)
from src.models import (
    AuditExecutionTrace,
    MutationKind,
    SessionSnapshot,
    SkillDefinition,
    ToolExecutionRecord,
    ToolStatus,
    TurnRequest,
)
from src.skill_registry import SkillRegistry, registry as default_registry
from src.tool_schemas import tools_for_skills

TOOL_IMPLS: dict[str, Callable[..., Any]] = {
    "fetch_user_profile": fetch_user_profile,
    "fetch_order_details": fetch_order_details,
    "process_refund": process_refund,
    "create_support_ticket": create_support_ticket,
}

_MAX_TOOL_TURNS = 6


class Orchestrator:
    def __init__(self, skill_registry: SkillRegistry | None = None) -> None:
        self.registry = skill_registry or default_registry
        self.sessions: dict[str, SessionSnapshot] = {}
        self._gemini: GeminiRuntime | None = None

    @property
    def gemini(self) -> GeminiRuntime:
        if self._gemini is None:
            self._gemini = GeminiRuntime()
        return self._gemini

    def get_or_create_session(self, request: TurnRequest) -> SessionSnapshot:
        if request.session_id and request.session_id in self.sessions:
            session = self.sessions[request.session_id]
            session.user_permissions = list(request.user_permissions)
            return session
        session = SessionSnapshot(
            session_id=request.session_id or f"sess_{uuid.uuid4().hex[:8]}",
            user_id=request.user_id,
            user_permissions=list(request.user_permissions),
        )
        self.sessions[session.session_id] = session
        return session

    def handle_turn(self, request: TurnRequest) -> AuditExecutionTrace:
        session = self.get_or_create_session(request)
        session.turn_count += 1
        gemini_calls = 0

        catalog = self.registry.catalog_cards()
        selection = self.gemini.select_skills(
            query=request.query,
            catalog=catalog,
            user_id=session.user_id,
            permissions=request.user_permissions,
            last_order_id=session.last_order_id,
        )
        gemini_calls += 1

        resolution = self.registry.authorize_skills(
            selection.skill_ids, request.user_permissions
        )
        loaded = list(resolution.loaded)
        blocked = list(resolution.blocked)
        loaded_ids = {skill.id for skill in loaded}

        context = self._agent_system_prompt(loaded, session)
        records: list[ToolExecutionRecord] = []
        contents = self._seed_contents(request.query, session)
        tools = self._tools_for(loaded)

        final_text = ""
        for step in range(_MAX_TOOL_TURNS):
            response = self.gemini.generate(
                system_instruction=context,
                contents=contents,
                tools=tools or None,
                force_tool_call=bool(tools) and step == 0,
            )
            gemini_calls += 1
            calls = function_calls_of(response)
            model_content = _model_content(response)

            if not calls:
                final_text = (getattr(response, "text", None) or "").strip()
                break

            if model_content is not None:
                contents.append(model_content)

            response_parts: list[types.Part] = []
            for tool_name, raw_args in calls:
                params = self._coerce_args(tool_name, raw_args, session, request.query)
                record = self._run_tool(tool_name, params, loaded)
                records.append(record)

                extra: dict[str, Any] = {}
                if (
                    record.status == ToolStatus.GUARDRAIL_BLOCKED_POLICY_VIOLATION
                    and "human_escalation" not in loaded_ids
                ):
                    extra = self._try_load_escalation(
                        session=session,
                        loaded=loaded,
                        loaded_ids=loaded_ids,
                        blocked=blocked,
                    )
                    if extra.get("new_skill_loaded"):
                        context = self._agent_system_prompt(loaded, session)
                        tools = self._tools_for(loaded)

                payload = _function_response_payload(record, extra)
                response_parts.append(
                    types.Part.from_function_response(name=tool_name, response=payload)
                )

            contents.append(types.Content(role="tool", parts=response_parts))
        else:
            final_text = (
                "I reached the tool-call limit for this turn. Please retry with "
                "a more specific request."
            )

        if not final_text:
            final_text = (
                "I loaded the selected skills but could not produce a final answer."
            )

        self._remember(session, request.query, records, final_text)

        post = guardrails.post_execute(records)
        if not post.allowed:
            records.append(
                ToolExecutionRecord(
                    tool_name="post_execution_guardrail",
                    target_endpoint="POLICY_ENGINE",
                    input={},
                    status=post.status,
                    reason=post.reason,
                    mutation=MutationKind.READ,
                )
            )

        naive_context = self.registry.build_dynamic_context(self.registry.all_skills())
        injected = self.registry.build_dynamic_context(loaded)
        return AuditExecutionTrace(
            session_id=session.session_id,
            user_query=request.query,
            user_permissions=list(request.user_permissions),
            dynamically_loaded_skills=[skill.id for skill in loaded],
            blocked_skills_reason=blocked,
            tools_executed=records,
            final_response=final_text,
            skill_selection_rationale=selection.rationale,
            catalog_chars=len(catalog),
            full_context_chars=len(naive_context),
            dynamic_context_chars=len(injected),
            gemini_model=self.gemini.model,
            gemini_calls=gemini_calls,
        )

    def _agent_system_prompt(
        self, loaded: list[SkillDefinition], session: SessionSnapshot
    ) -> str:
        skill_block = self.registry.build_dynamic_context(loaded)
        session_block = (
            "\n# Session\n"
            f"- caller user_id: {session.user_id}\n"
            f"- permissions: {', '.join(session.user_permissions) or '(none)'}\n"
            f"- last_order_id: {session.last_order_id or '(none)'}\n"
            "Use these ids when a tool requires them unless the user specified others.\n"
        )
        return skill_block + session_block

    def _seed_contents(
        self, query: str, session: SessionSnapshot
    ) -> list[types.Content]:
        parts: list[str] = []
        if session.transcript:
            parts.append("Prior conversation:")
            for turn in session.transcript[-6:]:
                parts.append(f"{turn['role']}: {turn['text']}")
            parts.append("")
        parts.append(f"Current user query:\n{query}")
        return [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="\n".join(parts))],
            )
        ]

    def _tools_for(self, loaded: list[SkillDefinition]) -> list[types.Tool]:
        names: list[str] = []
        seen: set[str] = set()
        for skill in loaded:
            for tool_name in skill.available_tools:
                if tool_name not in seen:
                    seen.add(tool_name)
                    names.append(tool_name)
        return tools_for_skills(names)

    def _try_load_escalation(
        self,
        *,
        session: SessionSnapshot,
        loaded: list[SkillDefinition],
        loaded_ids: set[str],
        blocked: list[Any],
    ) -> dict[str, Any]:
        extra = self.registry.authorize_skills(
            ["human_escalation"], session.user_permissions
        )
        blocked.extend(extra.blocked)
        if not extra.loaded:
            return {
                "instruction": (
                    "human_escalation could not be loaded. Tell the caller you "
                    "cannot auto-refund and cannot open a ticket with their role."
                )
            }
        skill = extra.loaded[0]
        if skill.id not in loaded_ids:
            loaded.append(skill)
            loaded_ids.add(skill.id)
        return {
            "new_skill_loaded": "human_escalation",
            "new_tool_available": "create_support_ticket",
            "instruction": (
                "Do not retry process_refund. Open a ticket with "
                "create_support_ticket so a manager can review the claim."
            ),
        }

    def _coerce_args(
        self,
        tool_name: str,
        raw: dict[str, Any],
        session: SessionSnapshot,
        query: str,
    ) -> dict[str, Any]:
        params = dict(raw)
        if tool_name == "fetch_user_profile":
            params.setdefault("user_id", session.user_id)
        if tool_name == "create_support_ticket":
            params.setdefault("user_id", session.user_id)
            params.setdefault("issue_summary", query)
        if tool_name == "process_refund":
            params.setdefault("reason", query)
            if params.get("amount") is not None:
                params["amount"] = float(params["amount"])
            if session.last_order_id and not params.get("order_id"):
                params["order_id"] = session.last_order_id
        if tool_name == "fetch_order_details":
            if session.last_order_id and not params.get("order_id"):
                params["order_id"] = session.last_order_id
        return params

    def _run_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        loaded: list[SkillDefinition],
    ) -> ToolExecutionRecord:
        endpoint = TOOL_ENDPOINTS.get(tool_name, "UNKNOWN")
        mutation = TOOL_MUTATIONS.get(tool_name)
        decision = guardrails.pre_execute(tool_name, params, loaded)
        if not decision.allowed:
            return ToolExecutionRecord(
                tool_name=tool_name,
                target_endpoint=endpoint,
                input=params,
                status=decision.status,
                reason=decision.reason,
                mutation=mutation,
            )

        impl = TOOL_IMPLS.get(tool_name)
        if impl is None:
            return ToolExecutionRecord(
                tool_name=tool_name,
                target_endpoint=endpoint,
                input=params,
                status=ToolStatus.TOOL_ERROR,
                reason=f"No dummy implementation registered for {tool_name}",
                mutation=mutation,
            )
        try:
            result = impl(**params)
        except Exception as exc:  # noqa: BLE001 — surface dummy-tool failures in the trace
            return ToolExecutionRecord(
                tool_name=tool_name,
                target_endpoint=endpoint,
                input=params,
                status=ToolStatus.TOOL_ERROR,
                reason=str(exc),
                mutation=mutation,
            )
        return ToolExecutionRecord(
            tool_name=tool_name,
            target_endpoint=endpoint,
            input=params,
            status=ToolStatus.SUCCESS,
            reason="ok",
            output=result.model_dump(mode="json"),
            mutation=mutation,
        )

    def _remember(
        self,
        session: SessionSnapshot,
        query: str,
        records: list[ToolExecutionRecord],
        final_text: str,
    ) -> None:
        for record in records:
            if record.tool_name == "fetch_order_details" and record.input.get("order_id"):
                session.last_order_id = str(record.input["order_id"])
            if record.tool_name == "process_refund" and record.input.get("order_id"):
                session.last_order_id = str(record.input["order_id"])
        session.transcript.append({"role": "user", "text": query})
        session.transcript.append({"role": "assistant", "text": final_text})


def _model_content(response: Any) -> types.Content | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    return getattr(candidates[0], "content", None)


def _function_response_payload(
    record: ToolExecutionRecord, extra: dict[str, Any]
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": record.status.value,
        "target_endpoint": record.target_endpoint,
    }
    if record.reason:
        payload["reason"] = record.reason
    if record.output is not None:
        payload["result"] = record.output
    payload.update(extra)
    return json.loads(json.dumps(payload, default=str))


orchestrator = Orchestrator()
