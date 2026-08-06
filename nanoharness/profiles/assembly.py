"""Staged extension assembly for hosts with derived runtime services."""

from dataclasses import dataclass
from typing import Callable, Dict, List

from nanoharness.extensions import ExtensionContext, ExtensionManager
from nanoharness.profiles.builder import HarnessBuilder
from nanoharness.profiles.models import HarnessSpec, HarnessValidation


HostBinder = Callable[[ExtensionContext, ExtensionManager], None]


class AssemblyPlanError(ValueError):
    pass


@dataclass(frozen=True)
class AssemblyPlan:
    bootstrap: List[str]
    runtime: List[str]

    @classmethod
    def from_spec(cls, spec: HarnessSpec) -> "AssemblyPlan":
        raw = spec.metadata.get("assembly") or {}
        if not isinstance(raw, dict):
            raise AssemblyPlanError("metadata.assembly must be an object")
        return cls(
            bootstrap=list(raw.get("bootstrap_extensions") or []),
            runtime=list(raw.get("runtime_extensions") or []),
        )

    def validate(self, spec: HarnessSpec) -> None:
        active = {item.name for item in spec.extensions if item.enabled}
        bootstrap = set(self.bootstrap)
        runtime = set(self.runtime)
        duplicates = bootstrap & runtime
        if duplicates:
            raise AssemblyPlanError(
                f"Extensions cannot appear in both assembly phases: {sorted(duplicates)}"
            )
        planned = bootstrap | runtime
        missing = active - planned
        unknown = planned - active
        if missing or unknown:
            raise AssemblyPlanError(
                f"Assembly plan mismatch; missing={sorted(missing)}, "
                f"unknown_or_disabled={sorted(unknown)}"
            )
        if len(self.bootstrap) != len(bootstrap) or len(self.runtime) != len(runtime):
            raise AssemblyPlanError("Assembly phases cannot contain duplicate extensions")


@dataclass
class StagedAssembly:
    spec: HarnessSpec
    manager: ExtensionManager
    validation: HarnessValidation
    phases: Dict[str, List[str]]

    @property
    def context(self) -> ExtensionContext:
        return self.manager.context

    def inspect(self) -> dict:
        return {
            "profile": self.spec.name,
            "phases": self.phases,
            "runtime": self.manager.inspect(),
        }

    def close(self) -> None:
        self.manager.close()


class StagedAssembler:
    """Install bootstrap extensions, bind the host, then install runtime edges."""

    def __init__(self, builder: HarnessBuilder | None = None):
        self.builder = builder or HarnessBuilder()

    def assemble(
        self,
        spec: HarnessSpec,
        context: ExtensionContext,
        bind_host: HostBinder,
        *,
        plan: AssemblyPlan | None = None,
    ) -> StagedAssembly:
        selected = plan or AssemblyPlan.from_spec(spec)
        selected.validate(spec)
        validation = self.builder.validate(spec)
        if not validation.valid:
            details = "; ".join(issue.message for issue in validation.errors)
            raise AssemblyPlanError(f"Invalid HarnessSpec {spec.name!r}: {details}")
        requests = {
            item.name: item for item in spec.extensions if item.enabled
        }
        manager = ExtensionManager(context)
        phases = {"bootstrap": [], "runtime": []}
        try:
            self._install_phase(
                manager,
                requests,
                validation.installation_order,
                set(selected.bootstrap),
                phases["bootstrap"],
            )
            bind_host(context, manager)
            self._verify_host_bindings(spec, context)
            self._install_phase(
                manager,
                requests,
                validation.installation_order,
                set(selected.runtime),
                phases["runtime"],
            )
        except Exception:
            try:
                manager.close()
            except Exception:
                pass
            raise
        return StagedAssembly(
            spec=spec,
            manager=manager,
            validation=validation,
            phases=phases,
        )

    def _install_phase(
        self,
        manager,
        requests,
        installation_order,
        selected,
        receipt,
    ) -> None:
        for name in installation_order:
            if name not in selected:
                continue
            request = requests[name]
            manager.install(self.builder.catalog.create(name), request.config)
            receipt.append(name)

    @staticmethod
    def _verify_host_bindings(spec, context) -> None:
        missing_capabilities = sorted(
            set(spec.host.capabilities) - context.capabilities
        )
        missing_services = sorted(
            set(spec.host.services) - set(context.services)
        )
        if missing_capabilities or missing_services:
            raise AssemblyPlanError(
                "Host binder did not satisfy Profile declarations; "
                f"capabilities={missing_capabilities}, services={missing_services}"
            )
