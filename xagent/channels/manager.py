from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChannelManager:
    """Placeholder manager for future external chat channels."""

    channels: dict[str, object]

    async def serve_forever(self) -> None:
        if not self.channels:
            raise RuntimeError("No channels are configured yet.")
