from pydantic import BaseModel
from typing import List, Dict, Any, Optional


# ── LLM interaction ──

class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]


class LLMResponse(BaseModel):
    content: str
    tool_calls: Optional[List[ToolCall]] = None


class AgentMessage(BaseModel):
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: Optional[List[ToolCall]] = None


class StopSignal(BaseModel):
    """Mid-loop evaluation: should the engine stop early?"""
    should_stop: bool = False
    reason: str = ""
    stop_category: str = ""  # "error_loop" | "spinning" | "stagnation" | ""


class EvaluationResult(BaseModel):
    """Post-loop evaluation: did the agent achieve the task?"""
    achieved: bool = False
    confidence: float = 0.0
    explanation: str = ""
    evidence: List[str] = []


class StepResult(BaseModel):
    """单步执行的结果记录"""
    step_id: int
    thought: str
    action: Optional[Dict] = None
    observation: Optional[str] = None
    status: str = "success"  # success, error, terminated
    stop_signal: Optional[StopSignal] = None
