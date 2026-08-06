"""Compatibility facade for the reusable NanoHarness team extension."""

from nanoharness.extensions.teams import (
    RequestTracker,
    TeammateManager,
    register_team_tools,
)
from nanoharness.extensions.teams.manager import (
    _inbox_path,
    _load_roster,
    _make_envelope,
    _make_protocol_envelope,
    _make_system_message,
    _read_inbox,
    _roster_member,
    _save_roster,
    _send_to_inbox,
)

__all__ = [
    "RequestTracker",
    "TeammateManager",
    "_inbox_path",
    "_load_roster",
    "_make_envelope",
    "_make_protocol_envelope",
    "_make_system_message",
    "_read_inbox",
    "_roster_member",
    "_save_roster",
    "_send_to_inbox",
    "register_team_tools",
]
