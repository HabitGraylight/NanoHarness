"""Per-round context management for the coding agent.

Two responsibilities:
1. Compress tool observations after each round — keep assistant thinking,
   truncate verbose tool outputs so context doesn't blow up.
2. Verify goal completion — after the engine loop terminates, ask the LLM
   whether the original goal was actually achieved.
"""

from nanoharness.core.schema import AgentMessage
from nanoharness.utils.token_counter import count_messages_tokens

# Tool observations longer than this get truncated after a round completes.
_MAX_OBS_CHARS = 500

# If total context exceeds this (approximate tokens), force-compress old rounds.
_CONTEXT_TOKEN_LIMIT = 8000


def compress_completed_rounds(context, max_obs_chars=_MAX_OBS_CHARS):
    """Compress tool observations from completed rounds.

    Strategy:
    - System / user / assistant messages: kept as-is
    - Tool observations: truncated to max_obs_chars with a [...truncated] marker
    - Only compresses tool messages from previous rounds (not the latest)

    This runs after each engine.run() in the REPL, before the next user input.
    """
    messages = context._messages
    if not messages:
        return

    # Find where the last round starts (last user message)
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            last_user_idx = i
            break

    new_messages = []
    for i, msg in enumerate(messages):
        if msg.role == "tool" and (last_user_idx is None or i < last_user_idx):
            # Tool message from a previous round — compress
            if len(msg.content) > max_obs_chars:
                new_messages.append(AgentMessage(
                    role="tool",
                    content=msg.content[:max_obs_chars] + "\n...[truncated]"
                ))
            else:
                new_messages.append(msg)
        else:
            new_messages.append(msg)

    context._messages = new_messages


def trim_to_token_limit(context, token_limit=_CONTEXT_TOKEN_LIMIT):
    """If context exceeds token limit, drop oldest messages (keep system prompt).

    This is a safety net — if even after compression the context is too long,
    we remove the oldest non-system messages.
    """
    messages = context._messages
    if not messages:
        return

    while count_messages_tokens(context.get_full_context()) > token_limit and len(context._messages) > 3:
        # Remove the oldest non-system message
        for i, msg in enumerate(context._messages):
            if msg.role != "system":
                context._messages.pop(i)
                break
        else:
            break


def verify_goal(llm_client, original_query, report):
    """Post-run verification: ask the LLM if the goal was achieved.

    Makes a single LLM call with the original task and a trajectory summary.
    Returns (achieved: bool, explanation: str).
    """
    # Build a compact trajectory summary
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
        tools=None,  # No tools — just a direct answer
    )

    content = (response.content or "").strip()
    achieved = content.upper().startswith("ACHIEVED")
    return achieved, content
