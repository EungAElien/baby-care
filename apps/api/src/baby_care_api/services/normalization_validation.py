from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from baby_care_api.models.b04 import Choice
from baby_care_api.models.normalization import (
    DraftAction,
    DraftOutcome,
    DraftState,
    Evidence,
    NormalizedContent,
)

BLOCKING_UNRESOLVED = frozenset({"CONFLICT", "UNSUPPORTED_CODE", "MISSING_EVIDENCE"})
_MUTUALLY_EXCLUSIVE_STATES = (
    frozenset({"CRYING", "CALM"}),
    frozenset({"ASLEEP", "AWAKE"}),
)
_VISUAL_PRIORITY = (
    "CRYING",
    "FUSSING",
    "ASLEEP",
    "SLEEPY_APPEARING",
    "CALM",
    "AWAKE",
    "CHEERFUL_APPEARING",
)


@dataclass(frozen=True)
class SemanticIssue:
    field: str
    code: str
    message: str


def _all_evidence(content: NormalizedContent) -> Iterable[tuple[str, Evidence]]:
    for index, action in enumerate(content.actions):
        for evidence in action.evidence:
            yield f"actions.{index}.evidence", evidence
    for index, state in enumerate(content.states):
        for evidence in state.evidence:
            yield f"states.{index}.evidence", evidence
    for index, outcome in enumerate(content.outcomes):
        for evidence in outcome.evidence:
            yield f"outcomes.{index}.evidence", evidence
    for index, interpretation in enumerate(content.caregiver_interpretations):
        for evidence in interpretation.evidence:
            yield f"caregiver_interpretations.{index}.evidence", evidence


def _evidence_issue(
    *,
    field: str,
    evidence: Evidence,
    raw_text: str | None,
    choice_ids: set[str],
    allow_user_correction: bool,
) -> SemanticIssue | None:
    if evidence.source == "TEXT":
        if (
            raw_text is None
            or evidence.choice_id is not None
            or evidence.span_start is None
            or evidence.span_end is None
            or evidence.quote is None
            or evidence.span_start > evidence.span_end
            or evidence.span_end > len(raw_text)
            or raw_text[evidence.span_start : evidence.span_end] != evidence.quote
        ):
            return SemanticIssue(
                field,
                "EVIDENCE_TEXT_MISMATCH",
                "Text evidence must match the original Unicode code-point slice.",
            )
        return None
    if evidence.source == "CHOICE":
        if (
            evidence.choice_id not in choice_ids
            or evidence.span_start is not None
            or evidence.span_end is not None
            or evidence.quote is not None
        ):
            return SemanticIssue(
                field,
                "EVIDENCE_CHOICE_MISMATCH",
                "Choice evidence must reference one submitted choice and no text span.",
            )
        return None
    if (
        not allow_user_correction
        or evidence.choice_id is not None
        or evidence.span_start is not None
        or evidence.span_end is not None
        or evidence.quote is None
        or not evidence.quote.strip()
    ):
        return SemanticIssue(
            field,
            "EVIDENCE_CORRECTION_INVALID",
            "User correction evidence requires a non-empty note and no source coordinates.",
        )
    return None


def _check_time(item: DraftAction | DraftState | DraftOutcome, field: str) -> SemanticIssue | None:
    value = item.occurred_at if isinstance(item, DraftAction) else item.observed_at
    if item.time_precision == "EXACT" and value is None:
        return SemanticIssue(field, "TIME_REQUIRED", "EXACT time requires an absolute timestamp.")
    if item.time_precision == "UNKNOWN" and value is not None:
        return SemanticIssue(
            field, "UNKNOWN_TIME_HAS_VALUE", "UNKNOWN time cannot include an absolute timestamp."
        )
    if isinstance(item, DraftAction):
        if item.time_precision == "RELATIVE" and not (item.relative_time or "").strip():
            return SemanticIssue(
                field,
                "RELATIVE_TIME_REQUIRED",
                "A relative action time must preserve its source expression.",
            )
        if item.time_precision != "RELATIVE" and item.relative_time is not None:
            return SemanticIssue(
                field,
                "RELATIVE_TIME_UNEXPECTED",
                "relative_time is only valid for RELATIVE precision.",
            )
    return None


def _choice_matches_action(choice: Choice, action: DraftAction) -> bool:
    return (
        choice.kind == "ACTION"
        and choice.code == action.action_code
        and (choice.assertion is None or choice.assertion == action.assertion)
    )


def _choice_matches_state(choice: Choice, state: DraftState) -> bool:
    return choice.kind == "STATE" and choice.code in state.state_codes


def _choice_matches_outcome(choice: Choice, outcome: DraftOutcome) -> bool:
    return choice.kind == "RESPONSE" and choice.code == outcome.response_code


def validate_normalized_content(
    content: NormalizedContent,
    *,
    raw_text: str | None,
    choices: list[Choice],
    allow_user_correction: bool,
    confirmation: bool,
    eventless: bool,
) -> list[SemanticIssue]:
    """Validate source grounding and the eventless B-07 persistence boundary."""

    issues: list[SemanticIssue] = []
    choice_by_id = {choice.choice_id: choice for choice in choices}
    if len(choice_by_id) != len(choices):
        issues.append(SemanticIssue("choices", "DUPLICATE_CHOICE_ID", "Choice IDs must be unique."))

    action_refs = [action.action_ref for action in content.actions]
    if len(action_refs) != len(set(action_refs)):
        issues.append(
            SemanticIssue("actions", "DUPLICATE_ACTION_REF", "Action references must be unique.")
        )
    sequences = [action.sequence for action in content.actions]
    if len(sequences) != len(set(sequences)):
        issues.append(
            SemanticIssue(
                "actions", "DUPLICATE_ACTION_SEQUENCE", "Action sequences must be unique."
            )
        )
    action_ref_set = set(action_refs)

    for index, action in enumerate(content.actions):
        field = f"actions.{index}"
        if action.performed_by_user_id is not None:
            issues.append(
                SemanticIssue(
                    f"{field}.performed_by_user_id",
                    "ACTOR_NOT_SUPPORTED",
                    "The eventless first slice cannot assign another performer.",
                )
            )
        time_issue = _check_time(action, f"{field}.occurred_at")
        if time_issue is not None:
            issues.append(time_issue)
        if action.action_code == "FEEDING":
            if (action.amount is None) != (action.unit is None):
                issues.append(
                    SemanticIssue(
                        f"{field}.amount",
                        "QUANTITY_PAIR_REQUIRED",
                        "Feeding amount and unit must be supplied together.",
                    )
                )
            if action.unit == "ML" and action.feeding_mode == "BREAST":
                issues.append(
                    SemanticIssue(
                        f"{field}.unit",
                        "QUANTITY_CONFLICT",
                        "Direct breastfeeding cannot use an mL amount.",
                    )
                )
        elif (
            action.amount is not None or action.unit is not None or action.feeding_mode is not None
        ):
            issues.append(
                SemanticIssue(
                    field,
                    "QUANTITY_NOT_ALLOWED",
                    "Only feeding actions accept amount, unit, or feeding mode.",
                )
            )
        for evidence in action.evidence:
            if evidence.source == "CHOICE":
                choice = choice_by_id.get(evidence.choice_id or "")
                if choice is None or not _choice_matches_action(choice, action):
                    issues.append(
                        SemanticIssue(
                            f"{field}.evidence",
                            "CHOICE_SEMANTIC_MISMATCH",
                            "Choice evidence does not support this action.",
                        )
                    )

    for index, state in enumerate(content.states):
        field = f"states.{index}"
        if len(state.state_codes) != len(set(state.state_codes)):
            issues.append(
                SemanticIssue(
                    f"{field}.state_codes", "DUPLICATE_STATE", "State codes must be unique."
                )
            )
        missing = set(state.linked_action_refs) - action_ref_set
        if missing:
            issues.append(
                SemanticIssue(
                    f"{field}.linked_action_refs",
                    "UNKNOWN_ACTION_REF",
                    "State links must reference actions in the same content.",
                )
            )
        time_issue = _check_time(state, f"{field}.observed_at")
        if time_issue is not None:
            issues.append(time_issue)
        for evidence in state.evidence:
            if evidence.source == "CHOICE":
                choice = choice_by_id.get(evidence.choice_id or "")
                if choice is None or not _choice_matches_state(choice, state):
                    issues.append(
                        SemanticIssue(
                            f"{field}.evidence",
                            "CHOICE_SEMANTIC_MISMATCH",
                            "Choice evidence does not support this state.",
                        )
                    )
        state_set = set(state.state_codes)
        conflict = ("UNKNOWN" in state_set and len(state_set) > 1) or any(
            pair <= state_set for pair in _MUTUALLY_EXCLUSIVE_STATES
        )
        unresolved_conflict = any(
            item.code == "CONFLICT" and item.field.startswith(field) for item in content.unresolved
        )
        if conflict and not unresolved_conflict:
            issues.append(
                SemanticIssue(
                    f"{field}.state_codes",
                    "STATE_CONFLICT",
                    "Mutually exclusive or unknown states require a CONFLICT item.",
                )
            )

    for index, outcome in enumerate(content.outcomes):
        field = f"outcomes.{index}"
        missing = set(outcome.linked_action_refs) - action_ref_set
        if missing:
            issues.append(
                SemanticIssue(
                    f"{field}.linked_action_refs",
                    "UNKNOWN_ACTION_REF",
                    "Outcome links must reference actions in the same content.",
                )
            )
        time_issue = _check_time(outcome, f"{field}.observed_at")
        if time_issue is not None:
            issues.append(time_issue)
        for evidence in outcome.evidence:
            if evidence.source == "CHOICE":
                choice = choice_by_id.get(evidence.choice_id or "")
                if choice is None or not _choice_matches_outcome(choice, outcome):
                    issues.append(
                        SemanticIssue(
                            f"{field}.evidence",
                            "CHOICE_SEMANTIC_MISMATCH",
                            "Choice evidence does not support this outcome.",
                        )
                    )
        if eventless and confirmation:
            issues.append(
                SemanticIssue(
                    field,
                    "EVENT_REQUIRED",
                    "Outcomes require an episode-linked action and cannot be saved by "
                    "the eventless first slice.",
                )
            )

    for field, evidence in _all_evidence(content):
        issue = _evidence_issue(
            field=field,
            evidence=evidence,
            raw_text=raw_text,
            choice_ids=set(choice_by_id),
            allow_user_correction=allow_user_correction,
        )
        if issue is not None:
            issues.append(issue)

    if confirmation:
        for index, unresolved in enumerate(content.unresolved):
            if unresolved.code in BLOCKING_UNRESOLVED:
                issues.append(
                    SemanticIssue(
                        f"unresolved.{index}",
                        "UNRESOLVED_BLOCKING",
                        f"{unresolved.code} must be resolved before confirmation.",
                    )
                )
    return issues


def visual_state_code(
    state_codes: list[str],
) -> Literal[
    "CRYING",
    "FUSSING",
    "CALM",
    "SLEEPY_APPEARING",
    "ASLEEP",
    "AWAKE",
    "CHEERFUL_APPEARING",
    "NEUTRAL",
]:
    values = set(state_codes)
    if "UNKNOWN" in values or any(pair <= values for pair in _MUTUALLY_EXCLUSIVE_STATES):
        return "NEUTRAL"
    for candidate in _VISUAL_PRIORITY:
        if candidate in values:
            return candidate  # type: ignore[return-value]
    return "NEUTRAL"
