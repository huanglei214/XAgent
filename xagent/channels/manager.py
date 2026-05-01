from __future__ import annotations

import asyncio
from dataclasses import dataclass

from xagent.bus import MessageBus, OutboundEvent
from xagent.channels.base import BaseChannel


@dataclass
class ChannelManager:
    """管理 channel 生命周期，并把 Bus 出站消息路由回对应 channel。"""

    bus: MessageBus
    channels: dict[str, BaseChannel]

    async def start(self) -> None:
        for channel in self.channels.values():
            await channel.start()

    async def stop(self) -> None:
        for channel in self.channels.values():
            await channel.stop()

    async def dispatch_outbound(self) -> OutboundEvent:
        event = await self.bus.consume_outbound()
        channel = self.channels.get(event.channel)
        if channel is None:
            raise RuntimeError(f"No channel is configured for {event.channel!r}.")
        await channel.send(event)
        return event

    async def _dispatch_outbound_loop(self) -> None:
        while True:
            await self.dispatch_outbound()

    async def run(self) -> None:
        if not self.channels:
            raise RuntimeError("No channels are configured yet.")
        await self.start()
        tasks = [asyncio.create_task(channel.run()) for channel in self.channels.values()]
        tasks.append(asyncio.create_task(self._dispatch_outbound_loop()))
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            await self.stop()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
