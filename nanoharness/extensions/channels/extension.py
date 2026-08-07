"""Schema-first durable Channel Gateway extension."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nanoharness.extensions.base import (
    BaseExtension,
    ExtensionContext,
    ExtensionInstallation,
    ExtensionManifest,
)
from nanoharness.extensions.channels.adapter import MockChannelAdapter
from nanoharness.extensions.channels.gateway import DurableChannelGateway
from nanoharness.extensions.channels.store import DurableChannelStore


class ChannelExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist_path: str = ".channels/state.json"
    events_path: Optional[str] = None
    service_name: str = "channels"
    mock_channels: list[str] = Field(default_factory=list)
    claim_lease_seconds: float = Field(default=300.0, gt=0, le=86_400)
    recover: bool = True

    @field_validator("mock_channels")
    @classmethod
    def validate_mock_channels(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("mock_channels must be unique")
        for channel in value:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", channel):
                raise ValueError("mock channel name is invalid")
        return value


class ChannelExtension(BaseExtension):
    manifest = ExtensionManifest(
        name="channels.durable",
        version="1.0.0",
        description=(
            "Durable transport-neutral channel inbox, outbox, and adapter service."
        ),
        provides=[
            "channels.adapter-protocol",
            "channels.gateway",
            "channels.inbox",
            "channels.outbox",
        ],
    )
    config_model = ChannelExtensionConfig

    def install(
        self,
        context: ExtensionContext,
        config: BaseModel,
    ) -> ExtensionInstallation:
        if not isinstance(config, ChannelExtensionConfig):
            raise TypeError("ChannelExtension requires ChannelExtensionConfig")
        if config.service_name in context.services:
            raise ValueError(
                f"ChannelExtension service conflicts: {config.service_name!r}"
            )
        store = DurableChannelStore(
            config.persist_path,
            events_path=config.events_path,
            claim_lease_seconds=config.claim_lease_seconds,
        )
        gateway = DurableChannelGateway(
            store,
            [MockChannelAdapter(channel) for channel in config.mock_channels],
            recover=config.recover,
        )
        context.provide_service(config.service_name, gateway)
        return ExtensionInstallation(
            name=self.manifest.name,
            version=self.manifest.version,
            capabilities=list(self.manifest.provides),
            services=[config.service_name],
            config=config.model_dump(mode="json"),
            metadata={
                "persist_path": str(store.path),
                "events_path": str(store.events_path),
                "channels": gateway.channels,
                "registers_tools": False,
            },
        )

    def close(
        self,
        context: ExtensionContext,
        installation: ExtensionInstallation,
    ) -> None:
        for service_name in installation.services:
            service = context.services.get(service_name)
            if isinstance(service, DurableChannelGateway):
                service.close()
