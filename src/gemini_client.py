"""Thin Gemini client: skill routing (JSON) and manual tool-calling turns."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from config import settings
from src.models import SkillSelection

# google-genai logs this once if tools= is passed to Models.generate_content.
# We disable AFC and invoke dummy tools ourselves so guardrails can intercept;
# the warning is SDK deprecation noise, not an application error.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class GeminiNotConfiguredError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing."""


class GeminiRuntime:
    def __init__(self) -> None:
        if not settings.gemini_api_key.strip():
            raise GeminiNotConfiguredError(
                "GEMINI_API_KEY is empty. Copy .env.example to .env and set your key."
            )
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model

    def _generate(self, **kwargs: Any) -> Any:
        """Call Gemini, falling back if the configured model id is unavailable."""
        candidates = [self.model]
        for fallback in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"):
            if fallback not in candidates:
                candidates.append(fallback)
        last_error: Exception | None = None
        for model_id in candidates:
            try:
                kwargs["model"] = model_id
                response = self.client.models.generate_content(**kwargs)
                self.model = model_id
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                message = str(exc).lower()
                if "not found" in message or "not supported" in message or "404" in message:
                    continue
                raise
        raise RuntimeError(
            f"Gemini request failed for models {candidates}: {last_error}"
        ) from last_error

    def select_skills(
        self,
        *,
        query: str,
        catalog: str,
        user_id: str,
        permissions: list[str],
        last_order_id: str | None,
    ) -> SkillSelection:
        """Phase 1: Gemini sees only catalog cards, never skill prompt fragments."""
        session_hint = (
            f"Session memory: last_order_id={last_order_id}"
            if last_order_id
            else "Session memory: none"
        )
        prompt = (
            f"Caller user_id: {user_id}\n"
            f"Caller permissions: {', '.join(permissions) or '(none)'}\n"
            f"{session_hint}\n\n"
            f"User query:\n{query}\n\n"
            f"Skill catalog:\n{catalog}\n"
        )
        system = (
            "You are a skill router for an enterprise support agent.\n"
            "Select the MINIMUM set of skill ids required to handle THIS query.\n"
            "Unused skills must not be selected — their prompt fragments would "
            "waste context tokens.\n"
            "Only return ids that appear in the catalog.\n"
            "You do not call tools in this step. You only choose skills.\n"
            "If a refund is requested, include execute_refund.\n"
            "If the caller asks for a person/manager, include human_escalation.\n"
            "Include identify_user only when identity is actually needed.\n"
        )
        response = self._generate(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=SkillSelection,
            ),
        )
        return _parse_skill_selection(response)

    def generate(
        self,
        *,
        system_instruction: str,
        contents: list[types.Content],
        tools: list[types.Tool] | None,
        force_tool_call: bool = False,
    ) -> Any:
        config_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "temperature": 0.2,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        }
        if tools:
            config_kwargs["tools"] = tools
            if force_tool_call:
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="ANY")
                )
        return self._generate(
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )


def _parse_skill_selection(response: Any) -> SkillSelection:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, SkillSelection):
        return parsed
    if parsed is not None and not isinstance(parsed, SkillSelection):
        try:
            return SkillSelection.model_validate(parsed)
        except Exception:
            pass
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        return SkillSelection(skill_ids=[], rationale="Gemini returned an empty selection")
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return SkillSelection.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        return SkillSelection(skill_ids=[], rationale=f"Unparseable skill selection: {text[:200]}")


def function_calls_of(response: Any) -> list[tuple[str, dict[str, Any]]]:
    extracted: list[tuple[str, dict[str, Any]]] = []
    calls = getattr(response, "function_calls", None) or []
    for item in calls:
        extracted.extend(_one_call(item))
    if extracted:
        return extracted
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            extracted.extend(_one_call(part))
    return extracted


def _one_call(item: Any) -> list[tuple[str, dict[str, Any]]]:
    name = getattr(item, "name", None)
    args = getattr(item, "args", None)
    inner = getattr(item, "function_call", None)
    if inner is not None:
        name = name or getattr(inner, "name", None)
        args = args or getattr(inner, "args", None)
    if not name:
        return []
    return [(str(name), dict(args or {}))]
