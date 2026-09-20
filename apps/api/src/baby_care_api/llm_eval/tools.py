from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final

from baby_care_api.llm_eval.models import CounselingCase, ToolFixture


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOL_ARGUMENT_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "get_confirmed_records": _object_schema(
        {
            "start_at": {"type": "string", "format": "date-time"},
            "end_at": {"type": "string", "format": "date-time"},
            "record_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["FEEDING", "SLEEP", "DIAPER", "ACTION", "OUTCOME", "ANALYSIS"],
                },
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        ["start_at", "end_at", "record_types", "limit"],
    ),
    "get_server_aggregates": _object_schema(
        {
            "metric": {
                "type": "string",
                "enum": ["FEEDING_SUMMARY", "SLEEP_SUMMARY", "DIAPER_SUMMARY"],
            },
            "start_at": {"type": "string", "format": "date-time"},
            "end_at": {"type": "string", "format": "date-time"},
        },
        ["metric", "start_at", "end_at"],
    ),
    "get_record_by_reference": _object_schema(
        {"record_reference": {"type": "string", "maxLength": 80}},
        ["record_reference"],
    ),
    "get_eligible_prior_cases": _object_schema(
        {
            "start_at": {"type": "string", "format": "date-time"},
            "end_at": {"type": "string", "format": "date-time"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        ["start_at", "end_at", "limit"],
    ),
    "search_reviewed_guidance": _object_schema(
        {
            "topic": {"type": "string", "maxLength": 100},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        ["topic", "limit"],
    ),
}

TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    "get_confirmed_records": (
        "Read confirmed records for the server-bound current user and selected baby in a time "
        "range. The tool accepts no user_id or baby_id."
    ),
    "get_server_aggregates": (
        "Read a server-calculated aggregate for the server-bound selected baby. Unknown values, "
        "actual zero, no records, and failures remain distinct."
    ),
    "get_record_by_reference": (
        "Resolve a conversation-local opaque record reference using current authorization and "
        "current version."
    ),
    "get_eligible_prior_cases": (
        "Read eligible confirmed prior cases for the server-bound selected baby. Deleted and "
        "draft data are excluded."
    ),
    "search_reviewed_guidance": (
        "Read synthetic, locally fixture-backed guidance with a review date. This evaluation "
        "tool never accesses the web."
    ),
}


def response_tool(tool_name: str, *, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool_name,
        "description": TOOL_DESCRIPTIONS[tool_name],
        "parameters": parameters or TOOL_ARGUMENT_SCHEMAS[tool_name],
        "strict": True,
    }


def tools_for_case(case: CounselingCase) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for name in case.allowed_tools:
        parameters = deepcopy(TOOL_ARGUMENT_SCHEMAS[name])
        expected_limits = [
            call.arguments["limit"]
            for call in case.expected.required_tool_calls
            if call.tool_name == name and "limit" in call.arguments
        ]
        if expected_limits and "limit" in parameters["properties"]:
            parameters["properties"]["limit"]["maximum"] = max(expected_limits)
        values.append(response_tool(name, parameters=parameters))
    return values


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ToolExecution:
    tool_name: str
    arguments: dict[str, Any]
    status: str
    evidence_ids: tuple[str, ...]
    output: str

    def audit_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
        }


def _validate_argument_shape(tool_name: str, arguments: dict[str, Any]) -> None:
    schema = TOOL_ARGUMENT_SCHEMAS[tool_name]
    properties = schema["properties"]
    required = set(schema["required"])
    supplied = set(arguments)
    if supplied != required or supplied - set(properties):
        raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
    for field_name, value in arguments.items():
        expected_type = properties[field_name]["type"]
        if expected_type == "string" and not isinstance(value, str):
            raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
        if expected_type == "array" and not isinstance(value, list):
            raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
        if "enum" in properties[field_name] and value not in properties[field_name]["enum"]:
            raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")
        if expected_type == "array" and "items" in properties[field_name]:
            enum = properties[field_name]["items"].get("enum")
            if enum is not None and any(item not in enum for item in value):
                raise ToolExecutionError("INVALID_TOOL_ARGUMENTS")


def _fixture_minimum_limit(fixture: ToolFixture) -> int:
    records = fixture.payload.get("records")
    if isinstance(records, list):
        return max(1, len(records))
    count_values = [
        value
        for key, value in fixture.payload.items()
        if key.endswith("_records") or key == "record_count"
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return max([1, *count_values])


def _fixture_arguments_match(fixture: ToolFixture, arguments: dict[str, Any]) -> bool:
    if set(fixture.arguments) != set(arguments):
        return False
    for key, expected_value in fixture.arguments.items():
        actual_value = arguments[key]
        if key == "limit":
            if (
                not isinstance(actual_value, int)
                or isinstance(actual_value, bool)
                or actual_value < _fixture_minimum_limit(fixture)
                or actual_value > expected_value
            ):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _matching_fixture(
    fixtures: list[ToolFixture], tool_name: str, arguments: dict[str, Any]
) -> ToolFixture | None:
    for fixture in fixtures:
        if fixture.tool_name == tool_name and _fixture_arguments_match(fixture, arguments):
            return fixture
    return None


def execute_synthetic_tool(
    case: CounselingCase, tool_name: str, arguments: dict[str, Any]
) -> ToolExecution:
    if tool_name not in case.allowed_tools or tool_name not in TOOL_ARGUMENT_SCHEMAS:
        raise ToolExecutionError("UNAUTHORIZED_TOOL")
    _validate_argument_shape(tool_name, arguments)
    fixture = _matching_fixture(case.tool_fixtures, tool_name, arguments)
    if fixture is None:
        raise ToolExecutionError("FIXTURE_NOT_FOUND")
    envelope = {
        "synthetic_data": True,
        "status": fixture.status,
        "payload": fixture.payload,
        "evidence_ids": fixture.evidence_ids,
    }
    return ToolExecution(
        tool_name=tool_name,
        arguments=arguments,
        status=fixture.status,
        evidence_ids=tuple(fixture.evidence_ids),
        output=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
    )
