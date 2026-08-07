import pytest
from pydantic import ValidationError

import nanoharness
from nanoharness.components.tools import DictToolRegistry
from nanoharness.extensions import ExtensionContext, ExtensionManager
from nanoharness.extensions.channels import (
    ChannelExtension,
    ChannelExtensionConfig,
    DurableChannelGateway,
    DurableChannelStore,
    MockChannelAdapter,
    OutboundEnvelope,
    OutboxStatus,
)
from nanoharness.profiles import ExtensionCatalog, HarnessBuilder, HarnessSpec


def context():
    return ExtensionContext(tools=DictToolRegistry())


def test_channel_extension_config_is_strict():
    with pytest.raises(ValidationError, match="extra"):
        ChannelExtensionConfig(unknown=True)
    with pytest.raises(ValidationError, match="unique"):
        ChannelExtensionConfig(mock_channels=["mock", "mock"])
    with pytest.raises(ValidationError, match="invalid"):
        ChannelExtensionConfig(mock_channels=["bad/channel"])
    with pytest.raises(ValidationError):
        ChannelExtensionConfig(claim_lease_seconds=0)


def test_channel_extension_installs_service_without_global_tools(tmp_path):
    active = context()
    extension = ChannelExtension()
    installation = extension.install(
        active,
        ChannelExtensionConfig(
            persist_path=str(tmp_path / "state.json"),
            mock_channels=["mock", "console"],
        ),
    )
    service = active.services["channels"]
    assert isinstance(service, DurableChannelGateway)
    assert service.channels == ["console", "mock"]
    assert installation.tools == []
    assert installation.metadata["registers_tools"] is False
    assert active.tool_names() == set()


def test_channel_extension_rejects_service_conflict(tmp_path):
    active = context()
    active.services["channels"] = object()
    with pytest.raises(ValueError, match="service conflicts"):
        ChannelExtension().install(
            active,
            ChannelExtensionConfig(persist_path=str(tmp_path / "state.json")),
        )


def test_channel_extension_requires_its_own_config_type():
    with pytest.raises(TypeError, match="ChannelExtensionConfig"):
        ChannelExtension().install(context(), HarnessSpec(name="wrong"))


def test_extension_manager_exposes_capabilities_and_closes_adapters(tmp_path):
    active = context()
    manager = ExtensionManager(active)
    installation = manager.install(
        ChannelExtension(),
        {
            "persist_path": str(tmp_path / "state.json"),
            "mock_channels": ["mock"],
        },
    )
    service = active.services["channels"]
    adapter = service._adapters["mock"]
    assert set(installation.capabilities) == {
        "channels.adapter-protocol",
        "channels.gateway",
        "channels.inbox",
        "channels.outbox",
    }
    assert set(installation.capabilities) <= active.capabilities
    manager.close()
    manager.close()
    assert service.closed is True
    assert adapter.closed is True


def test_extension_can_use_custom_service_and_event_paths(tmp_path):
    active = context()
    installation = ChannelExtension().install(
        active,
        ChannelExtensionConfig(
            persist_path=str(tmp_path / "runtime" / "state.json"),
            events_path=str(tmp_path / "audit" / "channels.jsonl"),
            service_name="gateway.channels",
        ),
    )
    service = active.services["gateway.channels"]
    service.ingest({
        "message_id": "one",
        "channel": "mock",
        "account_id": "main",
        "conversation_id": "conversation",
        "sender_id": "user",
        "content": "Hello",
    })
    assert installation.services == ["gateway.channels"]
    assert (tmp_path / "audit" / "channels.jsonl").exists()


def test_extension_recovery_can_be_disabled(tmp_path):
    path = tmp_path / "state.json"
    store = DurableChannelStore(str(path))
    queued, _ = store.queue_outbound(
        OutboundEnvelope(
            channel="mock",
            account_id="main",
            conversation_id="conversation",
            content="Reply",
        ),
        idempotency_key="key",
    )
    store.approve_outbox(queued.id)
    store.begin_delivery(queued.id)

    active = context()
    ChannelExtension().install(
        active,
        ChannelExtensionConfig(persist_path=str(path), recover=False),
    )
    assert active.services["channels"].store.get_outbox(queued.id).status == (
        OutboxStatus.SENDING
    )


def test_builtin_catalog_discovers_channel_extension():
    catalog = ExtensionCatalog.builtins()
    assert "channels.durable" in catalog.names()
    assert catalog.manifest("channels.durable").provides == [
        "channels.adapter-protocol",
        "channels.gateway",
        "channels.inbox",
        "channels.outbox",
    ]


def test_harness_builder_validates_and_explains_channel_profile(tmp_path):
    spec = HarnessSpec.model_validate({
        "name": "channel-profile",
        "extensions": [{
            "name": "channels.durable",
            "config": {
                "persist_path": str(tmp_path / "state.json"),
                "mock_channels": ["mock"],
            },
        }],
    })
    builder = HarnessBuilder()
    validation = builder.validate(spec)
    explanation = builder.explain(spec)
    assert validation.valid is True
    assert validation.installation_order == ["channels.durable"]
    assert explanation.extensions[0].name == "channels.durable"
    assert explanation.extensions[0].config_schema["additionalProperties"] is False


def test_public_top_level_exports_channel_contracts():
    assert nanoharness.ChannelExtension is ChannelExtension
    assert nanoharness.DurableChannelGateway is DurableChannelGateway
    assert nanoharness.MockChannelAdapter is MockChannelAdapter
