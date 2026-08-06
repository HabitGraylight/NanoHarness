"""Automatic ETCSLV, capability, extension, and policy matrices."""

from typing import Dict, List, Sequence

from pydantic import BaseModel, ConfigDict, Field

from nanoharness.profiles.builder import HarnessBuilder
from nanoharness.profiles.models import HarnessExplanation, HarnessSpec


class MatrixRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    item: str
    values: Dict[str, str] = Field(default_factory=dict)


class HarnessMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: List[str] = Field(default_factory=list)
    rows: List[MatrixRow] = Field(default_factory=list)
    valid: Dict[str, bool] = Field(default_factory=dict)
    errors: Dict[str, List[str]] = Field(default_factory=dict)


def build_profile_matrix(
    specs: Sequence[HarnessSpec],
    *,
    builder: HarnessBuilder | None = None,
) -> HarnessMatrix:
    builder = builder or HarnessBuilder()
    labels = _profile_labels(specs)
    explanations = [builder.explain(spec) for spec in specs]
    rows = []

    for item, getter in (
        ("E: execution", lambda exp: "NanoEngine" if exp.engine else "unbound"),
        ("T: tools", _tool_summary),
        ("C: context", lambda exp: exp.engine.context_service if exp.engine else "unbound"),
        ("S: state", lambda exp: exp.engine.state_service if exp.engine else "unbound"),
        ("L: hooks", lambda exp: exp.engine.hooks_service if exp.engine else "unbound"),
        ("V: evaluator", lambda exp: exp.engine.evaluator_service if exp.engine else "unbound"),
    ):
        rows.append(_row("ETCSLV", item, labels, explanations, getter))

    for item, getter in (
        ("tool policy", lambda exp: _engine_value(exp, "policy_service", "engine_default")),
        ("approval broker", lambda exp: _engine_value(exp, "approval_broker_service", "none")),
        ("tool executor", lambda exp: _engine_value(exp, "executor_service", "registry")),
        ("event sink", lambda exp: _engine_value(exp, "event_sink_service", "run-local")),
    ):
        rows.append(_row("Policy", item, labels, explanations, getter))

    capabilities = sorted({
        capability
        for explanation in explanations
        for capability in explanation.capabilities
    })
    for capability in capabilities:
        rows.append(MatrixRow(
            category="Capability",
            item=capability,
            values={
                label: ", ".join(explanation.capabilities.get(capability, [])) or "—"
                for label, explanation in zip(labels, explanations)
            },
        ))

    extensions = sorted({
        extension.name
        for explanation in explanations
        for extension in explanation.extensions
        if extension.enabled
    })
    for extension_name in extensions:
        rows.append(MatrixRow(
            category="Extension",
            item=extension_name,
            values={
                label: _extension_value(explanation, extension_name)
                for label, explanation in zip(labels, explanations)
            },
        ))

    host_services = sorted({
        service
        for explanation in explanations
        for service in explanation.host.services
    })
    for service in host_services:
        rows.append(MatrixRow(
            category="Host Service",
            item=service,
            values={
                label: "required" if service in explanation.host.services else "—"
                for label, explanation in zip(labels, explanations)
            },
        ))

    return HarnessMatrix(
        profiles=labels,
        rows=rows,
        valid={
            label: explanation.valid
            for label, explanation in zip(labels, explanations)
        },
        errors={
            label: [issue.message for issue in explanation.errors]
            for label, explanation in zip(labels, explanations)
            if explanation.errors
        },
    )


def _row(category, item, labels, explanations, getter) -> MatrixRow:
    return MatrixRow(
        category=category,
        item=item,
        values={
            label: str(getter(explanation))
            for label, explanation in zip(labels, explanations)
        },
    )


def _tool_summary(explanation: HarnessExplanation) -> str:
    capabilities = sorted(
        capability
        for capability in explanation.capabilities
        if capability.startswith("tools.")
    )
    return ", ".join(capabilities) if capabilities else "host registry"


def _engine_value(
    explanation: HarnessExplanation,
    field: str,
    fallback: str,
) -> str:
    if explanation.engine is None:
        return "unbound"
    return str(getattr(explanation.engine, field) or fallback)


def _extension_value(
    explanation: HarnessExplanation,
    name: str,
) -> str:
    for extension in explanation.extensions:
        if extension.enabled and extension.name == name:
            return f"v{extension.version} @ {extension.installation_index}"
    return "—"


def _profile_labels(specs: Sequence[HarnessSpec]) -> List[str]:
    counts: Dict[str, int] = {}
    labels = []
    for spec in specs:
        counts[spec.name] = counts.get(spec.name, 0) + 1
        occurrence = counts[spec.name]
        labels.append(spec.name if occurrence == 1 else f"{spec.name}#{occurrence}")
    return labels
