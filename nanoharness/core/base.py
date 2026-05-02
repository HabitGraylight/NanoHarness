import abc
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

from nanoharness.core.schema import (
    AgentMessage,
    EvaluationResult,
    LLMResponse,
    StepResult,
    StopSignal,
)


class HookStage(str, Enum):
    ON_TASK_START = "on_task_start"
    ON_THOUGHT_READY = "on_thought_ready"
    ON_STEP_END = "on_step_end"
    ON_TASK_END = "on_task_end"


class LLMProtocol(Protocol):
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        ...


class BaseComponent(abc.ABC):
    @abc.abstractmethod
    def reset(self):
        pass


class BaseToolRegistry(BaseComponent):
    @abc.abstractmethod
    def get_tool_schemas(self) -> List[Dict]:
        pass

    @abc.abstractmethod
    def call(self, name: str, args: Dict) -> Any:
        pass


class BaseContextManager(BaseComponent):
    @abc.abstractmethod
    def add_message(self, msg: AgentMessage):
        pass

    @abc.abstractmethod
    def get_full_context(self) -> List[Dict]:
        pass


class BaseStateStore(BaseComponent):
    @abc.abstractmethod
    def save_state(self, state: Dict):
        pass

    @abc.abstractmethod
    def load_state(self) -> Dict:
        pass


class BaseEvaluator(BaseComponent):
    @abc.abstractmethod
    def log_step(self, step: StepResult):
        pass

    @abc.abstractmethod
    def get_report(self) -> Dict:
        pass

    def should_stop(self, trajectory: List[StepResult]) -> StopSignal:
        """Mid-loop evaluation: should the engine stop early?

        Default never stops. Override in app-layer evaluators to
        detect spinning, error loops, stagnation, etc.
        """
        return StopSignal()

    def evaluate_success(self, query: str, trajectory: List[StepResult]) -> EvaluationResult:
        """Post-loop evaluation: did the agent achieve the task?

        Default uses legacy logic: any terminated step = achieved.
        Override in app-layer evaluators for real verification.
        """
        achieved = any(s.status == "terminated" for s in trajectory)
        return EvaluationResult(
            achieved=achieved,
            confidence=1.0 if achieved else 0.0,
            explanation="Legacy: terminated step found" if achieved else "No terminated step",
        )


class BaseHookManager(BaseComponent):
    @abc.abstractmethod
    def register(self, stage: str, hook):
        pass

    @abc.abstractmethod
    def trigger(self, stage: str, data: Any):
        pass
