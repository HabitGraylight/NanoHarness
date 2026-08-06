"""Terminal UI for the coding agent.

Provides a REPL loop that keeps the agent running between tasks.
Uses readline (when available) for proper line editing — arrow keys,
backspace, history all work correctly.
"""

import sys
import os

# Enable readline for line editing (arrow keys, history, etc.)
try:
    import readline

    # Persist history across sessions
    _histfile = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox", ".history")
    os.makedirs(os.path.dirname(_histfile), exist_ok=True)

    try:
        readline.read_history_file(_histfile)
    except FileNotFoundError:
        pass

    readline.set_history_length(200)

    def _save_history():
        try:
            readline.write_history_file(_histfile)
        except Exception:
            pass

    import atexit
    atexit.register(_save_history)
except ImportError:
    pass

# ANSI color codes
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


# Prompt for input() — \x01/\x02 wrappers tell readline to ignore
# ANSI sequences when calculating cursor position.
_PROMPT = f"\n\x01{_BOLD}\x02\x01{_GREEN}\x02❯ \x01{_RESET}\x02"

BANNER = f"""{_BOLD}{_CYAN}
  ╔══════════════════════════════════╗
  ║       NanoHarness Coding Agent   ║
  ╚══════════════════════════════════╝{_RESET}

  {_DIM}Type a coding task and press Enter.{_RESET}
  {_DIM}Commands: /quit, /clear, /help{_RESET}
"""

HELP_TEXT = f"""{_BOLD}Commands:{_RESET}
  {_CYAN}/quit{_RESET}   Exit the agent
  {_CYAN}/clear{_RESET}  Clear conversation context
  {_CYAN}/help{_RESET}   Show this help

{_BOLD}Tips:{_RESET}
  - Be specific about what you want changed
  - The agent will read files, make edits, and run tests
  - Destructive operations (push, reset) require your approval
"""


def read_input() -> str | None:
    """Read user input. Returns None on EOF or /quit."""
    try:
        line = input(_PROMPT).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if not line:
        return ""

    if line == "/quit":
        return None
    if line == "/clear":
        return "/clear"
    if line == "/help":
        print(HELP_TEXT)
        return ""

    return line
