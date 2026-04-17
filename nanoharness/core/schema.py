import time
from enum import Enum

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


class StepResult(BaseModel):
    """单步执行的结果记录"""
    step_id: int
    thought: str
    action: Optional[Dict] = None
    observation: Optional[str] = None
    status: str = "success"  # success, error, terminated


# ── Permission ──

class PermissionLevel(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class PermissionRule(BaseModel):
    tool_name: str  # supports glob: "git_*", "*"
    level: PermissionLevel = PermissionLevel.ALLOW
    blocked_params: Dict[str, List[Any]] = {}  # param_name -> blocked values


# ── Memory ──

class MemoryEntry(BaseModel):
    key: str
    content: str
    timestamp: float = 0.0
    metadata: Dict[str, Any] = {}
