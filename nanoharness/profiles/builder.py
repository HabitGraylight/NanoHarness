"""Dependency-aware planning, validation, explanation, and construction."""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions import (
    EXTENSION_PROTOCOL_VERSION,
    ExtensionContext,
    ExtensionManager,
)
from nanoharness.extensions.base import ExtensionProtocol
from nanoharness.core.engine import NanoEngine
from nanoharness.profiles.catalog import ExtensionCatalog, UnknownExtensionError
from nanoharness.profiles.models import (
    HARNESS_SPEC_VERSION,
    DependencyEdge,
    HarnessExplanation,
    HarnessIssue,
    HarnessSpec,
    HarnessValidation,
    PlannedExtension,
)


class HarnessSpecError(ValueError):
    def __init__(self, validation: HarnessValidation):
        self.validation = validation
        details = "; ".join(issue.message for issue in validation.errors)
        super().__init__(f"Invalid harness profile {validation.spec_name!r}: {details}")


class HarnessBuildError(RuntimeError):
    pass


@dataclass
class HarnessBuild:
    spec: HarnessSpec
    manager: ExtensionManager
    validation: HarnessValidation
    engine: Optional[NanoEngine] = None

    @property
    def context(self) -> ExtensionContext:
        return self.manager.context

    def inspect(self) -> Dict[str, Any]:
        return {
            "profile": _redact(self.spec.model_dump(mode="json")),
            "validation": self.validation.model_dump(mode="json"),
            "runtime": self.manager.inspect(),
            "engine": (
                {
                    "type": type(self.engine).__name__,
                    "max_steps": self.engine.max_steps,
                    "session_id": self.engine.session_id,
                }
                if self.engine is not None
                else None
            ),
        }

    def close(self) -> None:
        self.manager.close()


@dataclass
class _ResolvedExtension:
    request_index: int
    extension: ExtensionProtocol
    config: Dict[str, Any]


class HarnessBuilder:
    """Turn a HarnessSpec into an inspectable extension installation plan."""

    def __init__(self, catalog: Optional[ExtensionCatalog] = None):
        self.catalog = catalog or ExtensionCatalog.builtins()

    def validate(
        self,
        spec: HarnessSpec,
        context: Optional[ExtensionContext] = None,
    ) -> HarnessValidation:
        validation, _ = self._analyze(spec, context=context)
        return validation

    def explain(
        self,
        spec: HarnessSpec,
        context: Optional[ExtensionContext] = None,
    ) -> HarnessExplanation:
        validation, resolved = self._analyze(spec, context=context)
        order_indexes = {
            name: index for index, name in enumerate(validation.installation_order)
        }
        resolved_by_index = {
            item.request_index: item for item in resolved
        }
        extensions = []
        for request_index, request in enumerate(spec.extensions):
            try:
                extension = self.catalog.create(request.name)
            except UnknownExtensionError:
                continue
            resolved_item = resolved_by_index.get(request_index)
            if resolved_item is not None:
                config = resolved_item.config
            else:
                config = request.config
            manifest = extension.manifest
            extensions.append(
                PlannedExtension(
                    name=manifest.name,
                    enabled=request.enabled,
                    version=manifest.version,
                    description=manifest.description,
                    requested_index=request_index,
                    installation_index=(
                        order_indexes.get(manifest.name)
                        if resolved_item is not None
                        else None
                    ),
                    provides=list(manifest.provides),
                    requires=list(manifest.requires),
                    conflicts=list(manifest.conflicts),
                    config=_redact(config),
                    config_schema=extension.config_schema(),
                )
            )

        providers: Dict[str, List[str]] = {
            capability: ["host"] for capability in spec.host.capabilities
        }
        for item in resolved:
            for capability in item.extension.manifest.provides:
                providers.setdefault(capability, []).append(
                    item.extension.manifest.name
                )
        return HarnessExplanation(
            schema_version=spec.schema_version,
            name=spec.name,
            description=spec.description,
            valid=validation.valid,
            host=spec.host,
            engine=spec.engine,
            installation_order=validation.installation_order,
            extensions=extensions,
            capabilities={
                capability: names for capability, names in sorted(providers.items())
            },
            dependencies=validation.dependencies,
            errors=validation.errors,
            warnings=validation.warnings,
        )

    def build(
        self,
        spec: HarnessSpec,
        context: Optional[ExtensionContext] = None,
    ) -> HarnessBuild:
        context = context or ExtensionContext(tools=DictToolRegistry())
        validation, resolved = self._analyze(spec, context=context)
        if not validation.valid:
            raise HarnessSpecError(validation)

        resolved_by_name = {
            item.extension.manifest.name: item for item in resolved
        }
        manager = ExtensionManager(context)
        try:
            for name in validation.installation_order:
                item = resolved_by_name[name]
                manager.install(item.extension, item.config)
        except Exception as error:
            cleanup_error = None
            try:
                manager.close()
            except Exception as close_error:
                cleanup_error = close_error
            message = f"Failed to build harness profile {spec.name!r}: {error}"
            if cleanup_error is not None:
                message += f"; cleanup also failed: {cleanup_error}"
            raise HarnessBuildError(message) from error
        engine = None
        if spec.engine is not None:
            binding = spec.engine
            try:
                engine = NanoEngine(
                    llm_client=context.services[binding.llm_service],
                    tools=context.tools,
                    context=context.services[binding.context_service],
                    state=context.services[binding.state_service],
                    hooks=context.services[binding.hooks_service],
                    evaluator=context.services[binding.evaluator_service],
                    max_steps=binding.max_steps,
                    event_sink=(
                        context.services[binding.event_sink_service]
                        if binding.event_sink_service else None
                    ),
                    session_id=binding.session_id,
                    policy=(
                        context.services[binding.policy_service]
                        if binding.policy_service else None
                    ),
                    approval_broker=(
                        context.services[binding.approval_broker_service]
                        if binding.approval_broker_service else None
                    ),
                    executor=(
                        context.services[binding.executor_service]
                        if binding.executor_service else None
                    ),
                )
                engine.extension_manager = manager
            except Exception as error:
                try:
                    manager.close()
                except Exception:
                    pass
                raise HarnessBuildError(
                    f"Failed to bind NanoEngine for profile {spec.name!r}: {error}"
                ) from error
        return HarnessBuild(
            spec=spec,
            manager=manager,
            validation=validation,
            engine=engine,
        )

    def _analyze(
        self,
        spec: HarnessSpec,
        *,
        context: Optional[ExtensionContext],
    ) -> Tuple[HarnessValidation, List[_ResolvedExtension]]:
        errors: List[HarnessIssue] = []
        warnings: List[HarnessIssue] = []
        if spec.schema_version != HARNESS_SPEC_VERSION:
            errors.append(HarnessIssue(
                code="unsupported_spec_version",
                message=(
                    f"HarnessSpec version {spec.schema_version!r} is unsupported; "
                    f"expected {HARNESS_SPEC_VERSION!r}"
                ),
                field="schema_version",
            ))

        active = [request for request in spec.extensions if request.enabled]
        counts = Counter(request.name for request in active)
        for name in sorted(name for name, count in counts.items() if count > 1):
            errors.append(HarnessIssue(
                code="duplicate_extension",
                message=f"Extension {name!r} is requested more than once",
                extension=name,
            ))

        if not active:
            warnings.append(HarnessIssue(
                code="empty_profile",
                message="Harness profile has no enabled extensions",
            ))
        if context is None and (spec.host.capabilities or spec.host.services):
            warnings.append(HarnessIssue(
                code="offline_host_assumptions",
                message=(
                    "Host requirements are treated as declared providers during "
                    "offline validation; build() checks the real bindings"
                ),
            ))

        if context is not None:
            for capability in sorted(
                set(spec.host.capabilities) - context.capabilities
            ):
                errors.append(HarnessIssue(
                    code="missing_host_capability",
                    message=f"Host is missing declared capability {capability!r}",
                    capability=capability,
                ))
            for service in sorted(
                set(spec.host.services) - set(context.services)
            ):
                errors.append(HarnessIssue(
                    code="missing_host_service",
                    message=f"Host is missing declared service {service!r}",
                    field=f"host.services.{service}",
                ))

        if spec.engine is not None:
            declared_services = set(spec.host.services)
            for service in spec.engine.service_names():
                if service not in declared_services:
                    errors.append(HarnessIssue(
                        code="undeclared_engine_service",
                        message=(
                            f"Engine binding {service!r} must be declared in "
                            "host.services"
                        ),
                        field="engine",
                    ))

        resolved: List[_ResolvedExtension] = []
        seen = set()
        for request_index, request in enumerate(spec.extensions):
            if not request.enabled or request.name in seen:
                continue
            seen.add(request.name)
            try:
                extension = self.catalog.create(request.name)
            except UnknownExtensionError:
                errors.append(HarnessIssue(
                    code="unknown_extension",
                    message=(
                        f"Unknown extension {request.name!r}; "
                        f"available: {self.catalog.names()}"
                    ),
                    extension=request.name,
                ))
                continue
            manifest = extension.manifest
            if manifest.protocol_version != EXTENSION_PROTOCOL_VERSION:
                errors.append(HarnessIssue(
                    code="extension_protocol_mismatch",
                    message=(
                        f"Extension {manifest.name!r} uses protocol "
                        f"{manifest.protocol_version!r}; expected "
                        f"{EXTENSION_PROTOCOL_VERSION!r}"
                    ),
                    extension=manifest.name,
                ))
                continue
            try:
                parsed = extension.parse_config(request.config)
            except ValidationError as error:
                for detail in error.errors(include_input=False):
                    field = ".".join(str(part) for part in detail["loc"])
                    errors.append(HarnessIssue(
                        code="invalid_extension_config",
                        message=(
                            f"Extension {manifest.name!r} config field "
                            f"{field or '<root>'}: {detail['msg']}"
                        ),
                        extension=manifest.name,
                        field=field or None,
                    ))
                continue
            except Exception as error:
                errors.append(HarnessIssue(
                    code="invalid_extension_config",
                    message=(
                        f"Extension {manifest.name!r} config validation failed "
                        f"({type(error).__name__})"
                    ),
                    extension=manifest.name,
                ))
                continue
            resolved.append(_ResolvedExtension(
                request_index=request_index,
                extension=extension,
                config=parsed.model_dump(mode="json"),
            ))

        host_capabilities = set(spec.host.capabilities)
        provider_map: Dict[str, List[str]] = {
            capability: ["host"] for capability in host_capabilities
        }
        for item in resolved:
            for capability in item.extension.manifest.provides:
                provider_map.setdefault(capability, []).append(
                    item.extension.manifest.name
                )

        declared_capabilities = set(provider_map)
        for item in resolved:
            manifest = item.extension.manifest
            for capability in manifest.requires:
                if capability not in declared_capabilities:
                    errors.append(HarnessIssue(
                        code="missing_capability",
                        message=(
                            f"Extension {manifest.name!r} requires unavailable "
                            f"capability {capability!r}"
                        ),
                        extension=manifest.name,
                        capability=capability,
                    ))

        final_capabilities = set(declared_capabilities)
        if context is not None:
            final_capabilities.update(context.capabilities)
        for item in resolved:
            manifest = item.extension.manifest
            for capability in sorted(set(manifest.conflicts) & final_capabilities):
                errors.append(HarnessIssue(
                    code="capability_conflict",
                    message=(
                        f"Extension {manifest.name!r} conflicts with capability "
                        f"{capability!r}"
                    ),
                    extension=manifest.name,
                    capability=capability,
                ))

        installation_order: List[str] = []
        available = set(host_capabilities)
        remaining = list(resolved)
        while remaining:
            ready_index = next(
                (
                    index
                    for index, item in enumerate(remaining)
                    if set(item.extension.manifest.requires) <= available
                ),
                None,
            )
            if ready_index is None:
                unresolved = {
                    item.extension.manifest.name: sorted(
                        set(item.extension.manifest.requires) - available
                    )
                    for item in remaining
                }
                # Missing providers already have a more specific issue above.
                if all(
                    capability in declared_capabilities
                    for capabilities in unresolved.values()
                    for capability in capabilities
                ):
                    errors.append(HarnessIssue(
                        code="dependency_cycle",
                        message=f"Extension dependency cycle or deadlock: {unresolved}",
                    ))
                break
            item = remaining.pop(ready_index)
            name = item.extension.manifest.name
            installation_order.append(name)
            available.update(item.extension.manifest.provides)

        dependencies = []
        for item in resolved:
            manifest = item.extension.manifest
            for capability in manifest.requires:
                dependencies.append(DependencyEdge(
                    extension=manifest.name,
                    capability=capability,
                    providers=list(provider_map.get(capability, [])),
                ))

        validation = HarnessValidation(
            spec_name=spec.name,
            valid=not errors,
            errors=errors,
            warnings=warnings,
            installation_order=installation_order,
            capabilities=sorted(declared_capabilities),
            dependencies=dependencies,
        )
        return validation, resolved


def validate_harness(
    spec: HarnessSpec,
    *,
    context: Optional[ExtensionContext] = None,
    catalog: Optional[ExtensionCatalog] = None,
) -> HarnessValidation:
    return HarnessBuilder(catalog).validate(spec, context=context)


def explain_harness(
    spec: HarnessSpec,
    *,
    context: Optional[ExtensionContext] = None,
    catalog: Optional[ExtensionCatalog] = None,
) -> HarnessExplanation:
    return HarnessBuilder(catalog).explain(spec, context=context)


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


def _redact(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        if parent_key.lower() == "env":
            return {str(key): "***" for key in value}
        result = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_KEYS or any(
                marker in normalized for marker in ("password", "secret", "token")
            ):
                result[str(key)] = "***"
            else:
                result[str(key)] = _redact(item, parent_key=normalized)
        return result
    if isinstance(value, list):
        return [_redact(item, parent_key=parent_key) for item in value]
    return value
