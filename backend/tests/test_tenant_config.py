from typing import Any

import pytest
from pydantic import ValidationError

from app.tenant_config import DEFAULT_CONFIG, CustomField, TenantConfig

DEFAULT = DEFAULT_CONFIG.model_dump(mode="json")


def config(**changes: Any) -> TenantConfig:
    return TenantConfig.model_validate(DEFAULT | changes)


def severity(**changes: Any) -> dict[str, Any]:
    return DEFAULT["severity"] | changes


def sla(a: int = 1, t: int = 2, c: int = 3, r: int = 4) -> dict[str, int]:
    return {
        "acknowledge_minutes": a,
        "triage_minutes": t,
        "contain_minutes": c,
        "resolve_minutes": r,
    }


def test_default_config_is_valid() -> None:
    assert TenantConfig.model_validate(DEFAULT) == DEFAULT_CONFIG
    assert DEFAULT_CONFIG.state_keys == [
        "new",
        "triage",
        "analysis",
        "containment",
        "eradication",
        "recovery",
        "post_incident",
        "closed",
    ]


@pytest.mark.parametrize(
    ("impact", "urgency", "expected"),
    [
        ("high", "high", "critical"),
        ("high", "medium", "high"),
        ("high", "low", "medium"),
        ("medium", "high", "high"),
        ("medium", "medium", "medium"),
        ("medium", "low", "low"),
        ("low", "high", "medium"),
        ("low", "medium", "low"),
        ("low", "low", "low"),
    ],
)
def test_default_severity_matrix(impact: str, urgency: str, expected: str) -> None:
    assert DEFAULT_CONFIG.severity.severity(impact, urgency) == expected


def test_custom_matrix_lookup_and_priority() -> None:
    custom = config(
        severity={
            "levels": [{"key": "sev1", "sla": sla()}, {"key": "sev2", "sla": sla()}],
            "impacts": ["wide", "narrow"],
            "urgencies": ["now", "later"],
            "matrix": [["sev1", "sev1"], ["sev2", "sev1"]],
        },
        templates=[{"key": "t", "name": "T", "impact": "wide", "urgency": "now"}],
    ).severity
    assert custom.severity("narrow", "now") == "sev2"
    assert custom.severity("narrow", "later") == "sev1"
    assert [custom.priority(k) for k in custom.keys] == ["P1", "P2"]


def test_unknown_matrix_axis_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in list"):
        DEFAULT_CONFIG.severity.severity("extreme", "high")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"states": [{"key": "only", "name": "Only", "terminal": True}]}, "at least 2"),
        (
            {"states": [{"key": "a", "name": "A", "terminal": True}, {"key": "b", "name": "B"}]},
            "first state cannot be terminal",
        ),
        ({"states": [{"key": "a", "name": "A"}, {"key": "b", "name": "B"}]}, "must be terminal"),
        (
            {"states": [{"key": "a", "name": "A"}, {"key": "a", "name": "B", "terminal": True}]},
            "duplicate state keys",
        ),
        ({"states": [{"key": "Bad Key", "name": "A"}, *DEFAULT["states"]]}, "pattern"),
        ({"states": [{"key": "a", "name": " "}, *DEFAULT["states"]]}, "at least 1 character"),
        (
            {"custom_fields": [{"key": "x", "label": "X", "type": "picklist"}]},
            "needs options",
        ),
        (
            {"custom_fields": [{"key": "x", "label": "X", "type": "text", "options": ["a"]}]},
            "only picklist",
        ),
        (
            {
                "custom_fields": [
                    {"key": "x", "label": "X", "type": "picklist", "options": ["a", "a"]}
                ]
            },
            "duplicate options",
        ),
        (
            {"custom_fields": [{"key": "x", "label": "X", "type": "colour"}]},
            "Input should be",
        ),
        (
            {
                "custom_fields": [
                    {"key": "x", "label": "X", "type": "text"},
                    {"key": "x", "label": "Y", "type": "date"},
                ]
            },
            "duplicate custom field keys",
        ),
        (
            {
                "custom_fields": [
                    {"key": "x", "label": "X", "type": "text", "required_from": "nowhere"}
                ]
            },
            "required from unknown state",
        ),
        ({"severity": severity(levels=[])}, "at least 1"),
        (
            {"severity": severity(impacts=["high", "medium"])},
            "one row per impact",
        ),
        (
            {"severity": severity(urgencies=["high", "medium", "low", "later"])},
            "one column per urgency",
        ),
        ({"severity": severity(impacts=["high", "high", "low"])}, "duplicate impacts"),
        (
            {"severity": severity(matrix=[["critical"] * 3, ["high"] * 3, ["severe"] * 3])},
            "unknown severities in matrix: severe",
        ),
        (
            {"severity": severity(levels=[{"key": "critical", "sla": sla(5, 4, 6, 7)}])},
            "must not decrease",
        ),
        (
            {"severity": severity(levels=[{"key": "critical", "sla": sla(0, 1, 2, 3)}])},
            "greater than 0",
        ),
        (
            {"escalation_rules": [{"name": "r", "trigger": "sla_breach"}]},
            "needs an email or a webhook",
        ),
        (
            {
                "escalation_rules": [
                    {"name": "r", "trigger": "sla_breach", "notify_emails": ["not-an-email"]}
                ]
            },
            "valid email",
        ),
        (
            {
                "escalation_rules": [
                    {"name": "r", "trigger": "sla_breach", "webhook_url": "http://hooks.test/x"}
                ]
            },
            "must use https",
        ),
        (
            {
                "escalation_rules": [
                    {
                        "name": "r",
                        "trigger": "sla_breach",
                        "severities": ["apocalyptic"],
                        "notify_emails": ["soc@example.com"],
                    }
                ]
            },
            "unknown severities",
        ),
        (
            {
                "escalation_rules": [
                    {"name": "r", "trigger": "whenever", "notify_emails": ["soc@example.com"]}
                ]
            },
            "Input should be",
        ),
    ],
)
def test_invalid_config_is_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        config(**changes)


TEMPLATE = {"key": "t", "name": "T", "impact": "high", "urgency": "low"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"templates": []}, "at least 1"),
        ({"templates": [TEMPLATE, TEMPLATE]}, "duplicate template keys"),
        ({"templates": [TEMPLATE | {"impact": "cosmic"}]}, "unknown impact"),
        ({"templates": [TEMPLATE | {"urgency": "never"}]}, "unknown urgency"),
        ({"templates": [TEMPLATE | {"team": "ghosts"}]}, "unknown team"),
        ({"templates": [TEMPLATE | {"tlp": "purple"}]}, "Input should be"),
        ({"teams": [{"key": "a", "name": "A"}, {"key": "a", "name": "B"}]}, "duplicate team keys"),
    ],
)
def test_invalid_teams_and_templates(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        config(**changes)


def test_valid_escalation_rule() -> None:
    rule = {
        "name": "Critical breach",
        "trigger": "sla_breach",
        "severities": ["critical"],
        "notify_emails": ["soc-leads@example.com"],
        "webhook_url": "https://hooks.example.test/pager",
    }
    assert config(escalation_rules=[rule]).escalation_rules[0].name == "Critical breach"


FIELDS = [
    {"key": "notes", "label": "Notes", "type": "text"},
    {"key": "vector", "label": "Vector", "type": "picklist", "options": ["email", "web"]},
    {"key": "found_on", "label": "Found on", "type": "date"},
    {"key": "handler", "label": "Handler", "type": "user"},
    {"key": "records", "label": "Records", "type": "number"},
    {"key": "root_cause", "label": "Root cause", "type": "text", "required_from": "recovery"},
]
WITH_FIELDS = config(custom_fields=FIELDS)


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("notes", "free text", True),
        ("notes", "x" * 10_001, False),
        ("notes", 5, False),
        ("vector", "email", True),
        ("vector", "Email", False),
        ("vector", ["email"], False),
        ("found_on", "2026-10-31", True),
        ("found_on", "2026-13-01", False),
        ("found_on", "yesterday", False),
        ("found_on", 20261031, False),
        ("handler", "jdoe", True),
        ("handler", 7, False),
        ("records", 12, True),
        ("records", 1.5, True),
        ("records", "12", False),
        ("records", True, False),
        ("records", float("inf"), False),
    ],
)
def test_field_values_are_checked_by_type(field: str, value: Any, valid: bool) -> None:
    errors = WITH_FIELDS.validate_values({field: value}, "new")
    assert (field not in errors) is valid


def test_unknown_field_is_reported() -> None:
    assert WITH_FIELDS.validate_values({"mystery": 1}, "new") == {"mystery": "unknown field"}


@pytest.mark.parametrize(
    ("state", "required"),
    [
        ("new", False),
        ("eradication", False),
        ("recovery", True),
        ("post_incident", True),
        ("closed", True),
    ],
)
def test_required_from_state_onward(state: str, required: bool) -> None:
    errors = WITH_FIELDS.validate_values({}, state)
    assert (errors == {"root_cause": "required"}) is required
    assert WITH_FIELDS.validate_values({"root_cause": ""}, state) == errors
    assert WITH_FIELDS.validate_values({"root_cause": "phished admin"}, state) == {}


def test_picklist_options_are_trimmed() -> None:
    field = CustomField(key="x", label=" X ", type="picklist", options=[" a "])  # pyright: ignore[reportArgumentType]
    assert (field.label, field.options) == ("X", ["a"])
