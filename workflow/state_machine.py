# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from identity.schema import Principal


@dataclass(frozen=True)
class TransitionRule:
    from_states: frozenset[str]
    to_state: str
    actor_requirement: str


TRANSITIONS = {
    "claim": TransitionRule(frozenset({"pool"}), "assigned", "matching_role"),
    "assign": TransitionRule(frozenset({"pool"}), "assigned", "manager"),
    "reassign": TransitionRule(
        frozenset({"assigned", "in_progress", "waiting_review", "returned"}),
        "assigned",
        "manager",
    ),
    "start": TransitionRule(
        frozenset({"assigned", "returned"}), "in_progress", "assignee"
    ),
    "submit": TransitionRule(
        frozenset({"in_progress"}), "waiting_review", "assignee"
    ),
    "return": TransitionRule(
        frozenset({"waiting_review"}), "returned", "manager"
    ),
    "complete": TransitionRule(
        frozenset({"in_progress", "waiting_review"}), "completed", "manager"
    ),
    "cancel": TransitionRule(
        frozenset(
            {"pool", "assigned", "in_progress", "waiting_review", "returned"}
        ),
        "cancelled",
        "manager",
    ),
}


class WorkflowStateMachine:
    states = frozenset(
        {
            "pool",
            "assigned",
            "in_progress",
            "waiting_review",
            "returned",
            "completed",
            "cancelled",
        }
    )
    transitions = TRANSITIONS

    def transition(
        self, task: dict[str, Any], action: str, actor: Principal | dict[str, Any]
    ) -> str:
        rule = self.transitions.get(action)
        if rule is None:
            raise ValueError(f"unknown workflow action: {action}")
        status = task.get("status")
        if status not in rule.from_states:
            raise ValueError(f"cannot {action} a task in {status!r} state")
        self._check_actor(rule.actor_requirement, task, actor)
        return rule.to_state

    @staticmethod
    def _check_actor(
        requirement: str,
        task: dict[str, Any],
        actor: Principal | dict[str, Any],
    ) -> None:
        actor_id = _actor_value(actor, "user_id", "id")
        role_codes = set(_actor_value(actor, "role_codes") or ())
        permissions = set(_actor_value(actor, "permissions") or ())
        is_admin = "system_admin" in role_codes or "*" in permissions
        if requirement == "matching_role":
            if task.get("role_code") not in role_codes:
                raise PermissionError("actor does not have the task pool role")
            return
        if requirement == "assignee":
            if not is_admin and actor_id != task.get("assignee_user_id"):
                raise PermissionError("only the task assignee may perform this action")
            return
        if requirement == "manager" and not (
            is_admin or "project_owner" in role_codes
        ):
            raise PermissionError("only a project owner or system administrator may act")


def _actor_value(actor: Principal | dict[str, Any], *names: str) -> Any:
    for name in names:
        if isinstance(actor, dict) and name in actor:
            return actor[name]
        if hasattr(actor, name):
            return getattr(actor, name)
    return None
