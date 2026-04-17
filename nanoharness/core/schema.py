from pydantic import BaseModel
from typing import List, Dict, Any, Optional


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


class StepResult(BaseModel):
    """单步执行的结果记录"""
    step_id: int
    thought: str
    action: Optional[Dict] = None
    observation: Optional[str] = None
    status: str = "success"  # success, error, terminated
