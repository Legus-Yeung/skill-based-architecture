"""Dynamic skill loader and role-based permission manager.

Skills live in YAML under `skills/`. Domain experts can edit prompts,
permissions, tools, and policy thresholds without touching the orchestrator.

Gemini only sees a thin catalog (id / name / description) until a skill is
selected. Full `system_prompt_fragment` text is injected after authorization.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from config import settings
from src.models import BlockedSkillReason, SkillDefinition, SkillResolution


class SkillRegistry:
    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or settings.skills_dir
        self._skills: dict[str, SkillDefinition] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, SkillDefinition] = {}
        for path in sorted(self.skills_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            skill = SkillDefinition.model_validate(raw)
            loaded[skill.id] = skill
        if not loaded:
            raise FileNotFoundError(f"No skill YAML files found in {self.skills_dir}")
        self._skills = loaded

    def all_skills(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def catalog_cards(self) -> str:
        """Compact index for the skill router. No prompt fragments, no policy body."""
        blocks: list[str] = []
        for skill in self._skills.values():
            perms = ", ".join(skill.required_permissions) or "(none)"
            tools = ", ".join(skill.available_tools) or "(none)"
            blocks.append(
                f"- id: {skill.id}\n"
                f"  name: {skill.name}\n"
                f"  description: {skill.description.strip()}\n"
                f"  required_permissions: {perms}\n"
                f"  available_tools: {tools}"
            )
        return "\n".join(blocks)

    def _normalize_skill_id(self, raw: str) -> str:
        key = raw.strip().lower().replace(" ", "_").replace("-", "_")
        if key in self._skills:
            return key
        lowered = raw.strip().lower()
        for skill in self._skills.values():
            if skill.name.lower() == lowered or skill.id.lower() == lowered:
                return skill.id
        return raw.strip()

    def _missing_permissions(
        self, skill: SkillDefinition, user_permissions: list[str]
    ) -> list[str]:
        granted = set(user_permissions)
        return [perm for perm in skill.required_permissions if perm not in granted]

    def authorize_skills(
        self,
        requested_ids: list[str],
        user_permissions: list[str],
    ) -> SkillResolution:
        """Load only requested skills the caller is allowed to use.

        RBAC is enforced here, not by the model. A selected skill the caller
        cannot hold is recorded in ``blocked`` and never enters context.
        """
        blocked: list[BlockedSkillReason] = []
        loaded: list[SkillDefinition] = []
        seen: set[str] = set()

        for raw_id in requested_ids:
            skill_id = self._normalize_skill_id(raw_id)
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skill = self._skills.get(skill_id)
            if skill is None:
                blocked.append(
                    BlockedSkillReason(
                        skill_id=skill_id,
                        reason="Unknown skill id (not in YAML catalog)",
                    )
                )
                continue
            missing = self._missing_permissions(skill, user_permissions)
            if missing:
                blocked.append(
                    BlockedSkillReason(
                        skill_id=skill.id,
                        reason=(
                            "Missing required permission"
                            + ("s" if len(missing) > 1 else "")
                            + f": {', '.join(missing)}"
                        ),
                    )
                )
                continue
            loaded.append(skill)

        return SkillResolution(loaded=loaded, blocked=blocked, matched_intents=list(seen))

    def build_dynamic_context(self, skills: list[SkillDefinition]) -> str:
        """Assemble the turn's system prompt from loaded skill fragments only.

        Unloaded skills contribute zero tokens — that is the core context-
        engineering property this PoC is meant to demonstrate.
        """
        if not skills:
            return (
                "You are the Wonderful AI Enterprise Support Agent. "
                "No skills were authorized for this turn. Explain the "
                "limitation. Do not invent tool results."
            )

        header = (
            "You are the Wonderful AI Enterprise Support Agent for an "
            "e-commerce customer hub. Only the skills below are in scope "
            "for this turn. Do not claim capabilities from skills that "
            "were not loaded.\n"
            "When the user asks to look up or change something, you MUST "
            "call the matching tool. Do not answer from memory or from "
            "policy text alone. After tools return, answer in concise, "
            "professional language.\n"
            "If a tool comes back GUARDRAIL_BLOCKED_POLICY_VIOLATION and "
            "create_support_ticket is available, call it. Do not retry the "
            "blocked write.\n"
        )
        parts = [header]
        for skill in skills:
            perms = ", ".join(skill.required_permissions) or "(none)"
            tools = ", ".join(skill.available_tools) or "(none)"
            parts.append(
                f"## Skill: {skill.name} (`{skill.id}`)\n"
                f"Required permissions: {perms}\n"
                f"Available tools: {tools}\n\n"
                f"{skill.system_prompt_fragment.strip()}\n"
            )
        return "\n".join(parts)


registry = SkillRegistry()
