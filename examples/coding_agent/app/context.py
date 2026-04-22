"""Three-layer context optimization.

Layer 1: Spill — large tool results → disk, keep preview in context
Layer 2: Compress — old tool observations → short placeholders
Layer 3: Summarize — entire history too long → LLM continuity summary

Usage:
    # In builder: wrap SimpleContextManager
    context = ManagedContext(SimpleContextManager(system_prompt), scratch_dir, llm)

    # In REPL: between rounds
    context.compress_old()
    context.summarize_if_needed()
"""

import os
import time
from typing import List, Optional

from nanoharness.components.context.simple_context import SimpleContextManager
from nanoharness.core.base import BaseContextManager
from nanoharness.core.schema import AgentMessage
from nanoharness.utils.token_counter import count_messages_tokens


# ── Defaults ──

_SPILL_THRESHOLD = 2000    # chars — larger observations get spilled to disk
_PREVIEW_LINES = 15        # lines kept as preview after spill
_COMPRESS_CHARS = 300      # chars — old observations compressed to this
_TOKEN_LIMIT = 8000        # tokens — trigger Layer 3 summarize above this
_KEEP_RECENT = 6           # messages — always kept intact during summarize


class ManagedContext(BaseContextManager):
    """Three-layer context: spill → compress → summarize.

    Wraps SimpleContextManager. The engine sees the same BaseContextManager
    interface — no engine changes needed.
    """

    def __init__(
        self,
        inner: SimpleContextManager,
        scratch_dir: str,
        llm_client=None,
        spill_threshold: int = _SPILL_THRESHOLD,
        preview_lines: int = _PREVIEW_LINES,
        compress_chars: int = _COMPRESS_CHARS,
        token_limit: int = _TOKEN_LIMIT,
    ):
        self._inner = inner
        self._scratch_dir = scratch_dir
        self._llm = llm_client
        self._spill_threshold = spill_threshold
        self._preview_lines = preview_lines
        self._compress_chars = compress_chars
        self._token_limit = token_limit

        os.makedirs(scratch_dir, exist_ok=True)

    # ── BaseContextManager interface ──

    def add_message(self, msg: AgentMessage):
        """Intercept large tool results (Layer 1: spill)."""
        if msg.role == "tool" and len(msg.content) > self._spill_threshold:
            preview = _spill_to_disk(msg.content, self._scratch_dir, self._preview_lines)
            msg = AgentMessage(role="tool", content=preview)
        self._inner.add_message(msg)

    def get_full_context(self) -> List[dict]:
        return self._inner.get_full_context()

    def reset(self):
        self._inner.reset()

    # Expose inner messages for direct access (used by compress/summarize)
    @property
    def _messages(self):
        return self._inner._messages

    @_messages.setter
    def _messages(self, value):
        self._inner._messages = value

    # ── Layer 2: compress old observations ──

    def compress_old(self):
        """Compress tool observations from completed rounds.

        Replaces old (not current round) tool messages longer than
        compress_chars with a short placeholder preserving the first
        few lines and total size info.
        """
        messages = self._messages
        if not messages:
            return

        # Find where current round starts (last user message)
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                last_user_idx = i
                break

        new_messages = []
        for i, msg in enumerate(messages):
            if msg.role == "tool" and (last_user_idx is None or i < last_user_idx):
                if len(msg.content) > self._compress_chars:
                    new_messages.append(AgentMessage(
                        role="tool",
                        content=_make_placeholder(msg.content, self._compress_chars),
                    ))
                else:
                    new_messages.append(msg)
            else:
                new_messages.append(msg)

        self._messages = new_messages

    # ── Layer 3: summarize history ──

    def summarize_if_needed(self):
        """If context exceeds token limit, summarize old history via LLM.

        Keeps system prompt + recent messages intact.
        Replaces old messages with a single continuity summary.
        Falls back to trimming if no LLM client available.
        """
        if not self._messages:
            return

        current_tokens = count_messages_tokens(self.get_full_context())
        if current_tokens <= self._token_limit:
            return

        if self._llm is None:
            _trim_oldest(self)
            return

        messages = self._messages

        # Find cutoff — keep system prompt + recent messages
        cutoff = max(1, len(messages) - _KEEP_RECENT)  # at least skip system

        # Don't summarize if there's barely any history
        if cutoff <= 1:
            return

        old_messages = messages[1:cutoff]  # skip system prompt at [0]
        if not old_messages:
            return

        # Generate summary
        summary_text = _generate_summary(self._llm, old_messages)

        # Rebuild: system + summary + recent
        self._messages = (
            [messages[0]]  # system prompt
            + [AgentMessage(
                role="system",
                content=f"[Conversation Summary]\n{summary_text}",
            )]
            + messages[cutoff:]
        )


# ── Layer 1: spill helpers ──


def _spill_to_disk(content: str, scratch_dir: str, preview_lines: int) -> str:
    """Write full content to disk, return a preview + reference."""
    timestamp = int(time.time() * 1000)
    filename = f"spill_{timestamp}.txt"
    path = os.path.join(scratch_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    lines = content.splitlines()
    preview = "\n".join(lines[:preview_lines])
    remaining = len(lines) - preview_lines
    total_chars = len(content)

    result = preview
    if remaining > 0:
        result += f"\n... [{remaining} more lines, {total_chars} chars total]"
    result += f"\n[Full output saved to {path} — use file_read to access]"

    return result


# ── Layer 2: placeholder helpers ──


def _make_placeholder(content: str, max_chars: int) -> str:
    """Build a short placeholder from a long observation."""
    lines = content.splitlines()
    # Keep first few lines that fit
    kept = []
    used = 0
    for line in lines[:5]:
        if used + len(line) > max_chars:
            break
        kept.append(line)
        used += len(line) + 1

    total_lines = len(lines)
    total_chars = len(content)

    placeholder = "\n".join(kept)
    if total_lines > len(kept):
        placeholder += f"\n... [{total_lines - len(kept)} more lines, {total_chars} chars]"
    placeholder += " [compressed]"
    return placeholder


# ── Layer 3: summary helpers ──


def _generate_summary(llm_client, messages: list) -> str:
    """Ask the LLM to summarize a chunk of conversation history."""
    # Build compact representation
    parts = []
    for msg in messages:
        role = msg.role
        content = (msg.content or "")[:200]
        if role == "tool":
            # For tool messages, just note the first line
            first_line = content.split("\n")[0][:100]
            parts.append(f"  [tool] {first_line}")
        else:
            parts.append(f"  [{role}] {content}")

    history_text = "\n".join(parts)

    prompt = (
        "Summarize the following conversation history in 3-5 sentences.\n"
        "Focus on: what was asked, what was done, what was concluded.\n"
        "Preserve any important file paths, function names, or decisions.\n\n"
        f"{history_text}"
    )

    try:
        response = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        return (response.content or "").strip()
    except Exception:
        # If LLM call fails, fall back to a simple extraction
        return _extract_key_facts(messages)


def _extract_key_facts(messages: list) -> str:
    """Fallback: extract key facts without LLM."""
    facts = []
    for msg in messages:
        if msg.role == "user" and msg.content:
            facts.append(f"User asked: {msg.content[:100]}")
        elif msg.role == "assistant" and msg.content:
            facts.append(f"Assistant: {msg.content[:100]}")
    return " | ".join(facts[:5]) if facts else "Previous conversation history."


def _trim_oldest(context, keep: int = 3):
    """Fallback: drop oldest messages when no LLM available for summarization."""
    while (count_messages_tokens(context.get_full_context()) > context._token_limit
           and len(context._messages) > keep):
        for i, msg in enumerate(context._messages):
            if msg.role != "system":
                context._messages.pop(i)
                break
        else:
            break


# ── Goal verification (unchanged) ──


def verify_goal(llm_client, original_query, report):
    """Post-run verification: ask the LLM if the goal was achieved.

    Makes a single LLM call with the original task and a trajectory summary.
    Returns (achieved: bool, explanation: str).
    """
    steps_summary = []
    for i, step in enumerate(report.get("trajectory", [])):
        action = step.get("action", {})
        tool_name = action.get("name", "none") if action else "none"
        obs = (step.get("observation") or "")[:200]
        steps_summary.append(f"  Step {i} [{step['status']}]: tool={tool_name}, obs={obs}")

    trajectory_text = "\n".join(steps_summary)

    prompt = f"""You are evaluating whether an AI agent achieved its goal.

Original task: {original_query}

Agent trajectory:
{trajectory_text}

Final answer from agent: {(report.get('trajectory', [{}])[-1].get('thought', ''))[:500]}

Did the agent achieve the original task goal?
Reply with EXACTLY one of:
- ACHIEVED: <one sentence explanation>
- NOT_ACHIEVED: <one sentence explanation>"""

    response = llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
    )

    content = (response.content or "").strip()
    achieved = content.upper().startswith("ACHIEVED")
    return achieved, content
