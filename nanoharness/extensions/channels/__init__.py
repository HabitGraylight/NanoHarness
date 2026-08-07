"""Reusable durable channel gateway extension."""

from nanoharness.extensions.channels.adapter import (
    ChannelAdapterProtocol,
    ChannelDeliveryError,
    MockChannelAdapter,
)
from nanoharness.extensions.channels.extension import (
    ChannelExtension,
    ChannelExtensionConfig,
)
from nanoharness.extensions.channels.gateway import (
    DurableChannelGateway,
    register_channel_tools,
)
from nanoharness.extensions.channels.models import (
    CHANNEL_SCHEMA_VERSION,
    ChannelStoreState,
    DeliveryAttempt,
    DeliveryAttemptStatus,
    DeliveryReceipt,
    InboxRecord,
    InboxStatus,
    InboundEnvelope,
    OutboundEnvelope,
    OutboxRecord,
    OutboxStatus,
    stable_inbox_id,
    stable_outbox_id,
    stable_tool_idempotency_key,
)
from nanoharness.extensions.channels.store import (
    ChannelConflictError,
    ChannelStateTransitionError,
    ChannelStoreError,
    DurableChannelStore,
)

__all__ = [
    "CHANNEL_SCHEMA_VERSION",
    "ChannelAdapterProtocol",
    "ChannelConflictError",
    "ChannelDeliveryError",
    "ChannelExtension",
    "ChannelExtensionConfig",
    "ChannelStateTransitionError",
    "ChannelStoreError",
    "ChannelStoreState",
    "DeliveryAttempt",
    "DeliveryAttemptStatus",
    "DeliveryReceipt",
    "DurableChannelGateway",
    "DurableChannelStore",
    "InboxRecord",
    "InboxStatus",
    "InboundEnvelope",
    "MockChannelAdapter",
    "OutboundEnvelope",
    "OutboxRecord",
    "OutboxStatus",
    "register_channel_tools",
    "stable_inbox_id",
    "stable_outbox_id",
    "stable_tool_idempotency_key",
]
