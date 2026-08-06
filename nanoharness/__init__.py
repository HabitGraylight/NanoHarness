# ── Core (ETCSLV: E=Engine, T=Tools, C=Context, S=State, L=Hooks, V=Evaluator) ──

from nanoharness.core.base import (
    ApprovalBrokerProtocol,
    BaseComponent,
    BaseContextManager,
    BaseEvaluator,
    BaseHookManager,
    BaseStateStore,
    BaseToolRegistry,
    EventSinkProtocol,
    HookStage,
    LLMProtocol,
    ToolExecutorProtocol,
    ToolPolicyProtocol,
)
from nanoharness.core.engine import NanoEngine
from nanoharness.core.prompt import PromptManager
from nanoharness.core.runtime import RunControl
from nanoharness.core.schema import (
    AgentMessage,
    ApprovalResult,
    ApprovalStatus,
    CORE_PROTOCOL_VERSION,
    EventType,
    EvaluationResult,
    HarnessEvent,
    LLMResponse,
    PolicyDecision,
    PolicyOutcome,
    PolicyStage,
    RunCheckpoint,
    RunContext,
    RunStatus,
    StepResult,
    StopSignal,
    ToolCall,
    ToolExecution,
    ToolExecutionStatus,
    ToolRequest,
    TokenUsage,
)

# ── Components (ETCSLV implementations) ──

from nanoharness.components.context import SimpleContextManager
from nanoharness.components.evaluator import TraceEvaluator
from nanoharness.components.hooks import SimpleHookManager
from nanoharness.components.lifecycle import (
    AllowAllPolicy,
    CallbackApprovalBroker,
    CompositeToolPolicy,
    ConsoleEventSink,
    EventBus,
    JsonlEventSink,
    RedactingEventSink,
    RegistryToolExecutor,
)
from nanoharness.components.state import JsonStateStore
from nanoharness.components.tools import DictToolRegistry, ScriptToolRegistry

# ── Reusable extensions ──

from nanoharness.extensions import (
    EXTENSION_PROTOCOL_VERSION,
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManager,
    ExtensionManifest,
    ExtensionProtocol,
    NotificationSourceProtocol,
)
from nanoharness.extensions.background import (
    BackgroundExecutor,
    BackgroundExtension,
    BackgroundExtensionConfig,
    BackgroundTask,
)
from nanoharness.extensions.memory import (
    FileMemoryManager,
    MemoryEntry,
    MemoryExtension,
    MemoryExtensionConfig,
)
from nanoharness.extensions.mcp import (
    MCPClient,
    MCPClientPool,
    MCPDependencyError,
    MCPExtension,
    MCPExtensionConfig,
    MCPServerConfig,
    PluginLoader,
)
from nanoharness.extensions.scheduler import (
    Scheduler,
    SchedulerExtension,
    SchedulerExtensionConfig,
    cron_matches,
)
from nanoharness.extensions.skills import (
    SkillEntry,
    SkillRegistry,
    SkillsExtension,
    SkillsExtensionConfig,
)
from nanoharness.extensions.subagents import (
    SubagentContext,
    SubagentExtension,
    SubagentExtensionConfig,
    SubagentRunner,
    build_subagent_context,
    register_subagent_tool,
    register_task_tool,
    run_subagent,
)
from nanoharness.extensions.tasks import (
    TaskBoard,
    TaskExtension,
    TaskExtensionConfig,
    TaskStatus,
    is_claimable,
    is_ready,
    make_task,
)
from nanoharness.extensions.teams import (
    RequestTracker,
    TeamExtension,
    TeamExtensionConfig,
    TeammateManager,
    register_team_tools,
)
from nanoharness.extensions.worktrees import (
    TaskBoardBindingProtocol,
    WorktreeExtension,
    WorktreeExtensionConfig,
    WorktreeRegistry,
)

# ── Harness profiles ──

from nanoharness.profiles import (
    HARNESS_SPEC_VERSION,
    DependencyEdge,
    EngineSpec,
    ExtensionCatalog,
    ExtensionSpec,
    HarnessBuild,
    HarnessBuildError,
    HarnessBuilder,
    HarnessExplanation,
    HarnessIssue,
    HarnessMatrix,
    HarnessSpec,
    HarnessSpecError,
    HarnessValidation,
    HostRequirements,
    MatrixRow,
    PlannedExtension,
    HarnessTrace,
    TraceComparison,
    TraceMetricComparison,
    TraceStep,
    build_profile_matrix,
    compare_traces,
    explain_harness,
    load_harness_spec,
    load_trace,
    summarize_trace,
    validate_harness,
)
