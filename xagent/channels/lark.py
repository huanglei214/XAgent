from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from typing import Any

from xagent.bus import InboundMessage, OutboundEvent, StreamKind
from xagent.channels.base import BaseChannel
from xagent.config import LarkChannelConfig


class LarkSdkAdapter:
    """官方 lark-oapi SDK 的薄适配层，便于 channel 单测注入 fake SDK。"""

    def __init__(self) -> None:
        import lark_oapi as lark  # type: ignore[import-untyped]
        from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            CreateMessageRequest,
            CreateMessageRequestBody,
            Emoji,
        )

        self.lark = lark
        self.create_message_request = CreateMessageRequest
        self.create_message_request_body = CreateMessageRequestBody
        self.create_message_reaction_request = CreateMessageReactionRequest
        self.create_message_reaction_request_body = CreateMessageReactionRequestBody
        self.emoji = Emoji

    def domain_for(self, domain: str) -> str:
        if domain == "lark":
            return str(self.lark.LARK_DOMAIN)
        return str(self.lark.FEISHU_DOMAIN)

    def log_level_for(self, log_level: str) -> Any:
        name = log_level.strip().upper()
        return getattr(self.lark.LogLevel, name, self.lark.LogLevel.INFO)

    def build_client(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
        log_level: Any,
    ) -> Any:
        return (
            self.lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .domain(domain)
            .log_level(log_level)
            .build()
        )

    def build_event_handler(
        self,
        *,
        encrypt_key: str | None,
        verification_token: str | None,
        log_level: Any,
        callback: Any,
    ) -> Any:
        return (
            self.lark.EventDispatcherHandler.builder(
                encrypt_key or "",
                verification_token or "",
                log_level,
            )
            .register_p2_im_message_receive_v1(callback)
            .build()
        )

    def build_ws_client(
        self,
        *,
        app_id: str,
        app_secret: str,
        event_handler: Any,
        log_level: Any,
        domain: str,
        auto_reconnect: bool,
    ) -> Any:
        return self.lark.ws.Client(
            app_id=app_id,
            app_secret=app_secret,
            event_handler=event_handler,
            log_level=log_level,
            domain=domain,
            auto_reconnect=auto_reconnect,
        )

    def run_ws_client(self, ws_client: Any) -> None:
        loop = self._ensure_ws_loop(ws_client)
        try:
            try:
                loop.run_until_complete(ws_client._connect())
            except Exception:
                loop.run_until_complete(ws_client._disconnect())
                if getattr(ws_client, "_auto_reconnect", False):
                    loop.run_until_complete(ws_client._reconnect())
                else:
                    raise
            loop.create_task(ws_client._ping_loop())
            loop.run_forever()
        finally:
            self._cleanup_ws_loop(loop)

    def stop_ws_client(self, ws_client: Any, *, timeout: float = 5.0) -> None:
        loop = self._ensure_ws_loop(ws_client)
        with contextlib.suppress(Exception):
            ws_client._auto_reconnect = False
        if not loop.is_running():
            return

        async def shutdown() -> None:
            with contextlib.suppress(Exception):
                await ws_client._disconnect()
            current = asyncio.current_task(loop)
            tasks = [task for task in asyncio.all_tasks(loop) if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
        try:
            future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def get_bot_open_id(self, client: Any) -> str:
        request = (
            self.lark.BaseRequest.builder()
            .http_method(self.lark.HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({self.lark.AccessTokenType.TENANT})
            .build()
        )
        response = client.request(request)
        if hasattr(response, "success") and not response.success():
            raise RuntimeError(f"Failed to get Lark bot info: {response.code} {response.msg}")
        payload = self._raw_json(response)
        bot = payload.get("bot") or payload.get("data", {}).get("bot") or payload.get("data") or {}
        open_id = bot.get("open_id") if isinstance(bot, dict) else None
        if not open_id:
            raise RuntimeError("Lark bot info response does not contain bot.open_id")
        return str(open_id)

    def send_text(self, client: Any, *, chat_id: str, text: str) -> None:
        body = (
            self.create_message_request_body.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            self.create_message_request.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        response = client.im.v1.message.create(request)
        if hasattr(response, "success") and not response.success():
            raise RuntimeError(f"Failed to send Lark message: {response.code} {response.msg}")

    def add_reaction(self, client: Any, *, message_id: str, emoji_type: str) -> None:
        reaction_type = self.emoji.builder().emoji_type(emoji_type).build()
        body = (
            self.create_message_reaction_request_body.builder()
            .reaction_type(reaction_type)
            .build()
        )
        request = (
            self.create_message_reaction_request.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = client.im.v1.message_reaction.create(request)
        if hasattr(response, "success") and not response.success():
            raise RuntimeError(f"Failed to add Lark reaction: {response.code} {response.msg}")

    @staticmethod
    def _ensure_ws_loop(ws_client: Any) -> asyncio.AbstractEventLoop:
        start = getattr(ws_client, "start", None)
        func = getattr(start, "__func__", None)
        globals_map = getattr(func, "__globals__", {})
        loop = globals_map.get("loop") if isinstance(globals_map, dict) else None
        if loop is None:
            raise RuntimeError("Cannot locate lark-oapi websocket event loop")
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            globals_map["loop"] = loop
        return loop

    @staticmethod
    def _cleanup_ws_loop(loop: asyncio.AbstractEventLoop) -> None:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    @staticmethod
    def _raw_json(response: Any) -> dict[str, Any]:
        raw = getattr(response, "raw", None)
        content = getattr(raw, "content", b"") if raw is not None else b""
        if isinstance(content, bytes):
            text = content.decode("utf-8")
        else:
            text = str(content)
        payload = json.loads(text or "{}")
        return payload if isinstance(payload, dict) else {}


class LarkChannel(BaseChannel):
    def __init__(
        self,
        *,
        config: LarkChannelConfig,
        bus: Any,
        sdk: Any | None = None,
    ) -> None:
        super().__init__(name="lark", bus=bus)
        self.config = config
        self.sdk = sdk or LarkSdkAdapter()
        self.bot_open_id: str | None = None
        self._client: Any | None = None
        self._ws_client: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._stopping = False

    def describe(self) -> str:
        return (
            f"lark domain={self.config.domain} "
            f"require_mention={self.config.require_mention} "
            f"streaming={self.supports_streaming}"
        )

    async def start(self) -> None:
        if self._started:
            return
        app_id = self.config.app_id
        app_secret = self.config.app_secret
        if not app_id:
            raise RuntimeError("Lark channel requires channels.lark.app_id")
        if not app_secret:
            raise RuntimeError("Lark channel requires channels.lark.app_secret")

        self._loop = asyncio.get_running_loop()
        domain = self.sdk.domain_for(self.config.domain)
        log_level = self.sdk.log_level_for(self.config.log_level)
        self._client = self.sdk.build_client(
            app_id=app_id,
            app_secret=app_secret,
            domain=domain,
            log_level=log_level,
        )
        self.bot_open_id = self.sdk.get_bot_open_id(self._client)
        event_handler = self.sdk.build_event_handler(
            encrypt_key=self.config.encrypt_key,
            verification_token=self.config.verification_token,
            log_level=log_level,
            callback=self._handle_sdk_event,
        )
        self._ws_client = self.sdk.build_ws_client(
            app_id=app_id,
            app_secret=app_secret,
            event_handler=event_handler,
            log_level=log_level,
            domain=domain,
            auto_reconnect=self.config.auto_reconnect,
        )
        self._started = True
        self._stopping = False

    async def run(self) -> None:
        if self._ws_client is None:
            raise RuntimeError("Lark channel must be started before run()")
        try:
            run_ws_client = getattr(self.sdk, "run_ws_client", None)
            if callable(run_ws_client):
                await asyncio.to_thread(run_ws_client, self._ws_client)
            else:
                await asyncio.to_thread(self._ws_client.start)
        except RuntimeError:
            if self._stopping:
                return
            raise

    async def handle_message(self, message: Any) -> InboundMessage | None:
        incoming = _extract_incoming(message)
        if incoming is None:
            return None
        if incoming.message_type != "text":
            return None
        if not incoming.chat_id:
            return None
        if self.bot_open_id and incoming.sender_id == self.bot_open_id:
            return None
        if incoming.sender_type == "app":
            return None

        text = _extract_text(incoming.content)
        if not text.strip():
            return None
        if self.config.require_mention and incoming.chat_type != "p2p":
            if not self.bot_open_id or not _mentions_open_id(incoming.mentions, self.bot_open_id):
                return None
        if self.config.strip_mention and self.bot_open_id:
            text = _strip_bot_mention(text, incoming.mentions, self.bot_open_id)
        text = text.strip()
        if not text:
            return None

        inbound = InboundMessage(
            content=text,
            channel=self.name,
            chat_id=incoming.chat_id,
            sender_id=incoming.sender_id or "unknown",
            external_message_id=incoming.message_id,
            metadata={
                "chat_type": incoming.chat_type,
                "message_type": incoming.message_type,
                "tenant_key": incoming.tenant_key,
            },
        )
        await self.bus.publish_inbound(inbound)
        await self._add_reaction(incoming.message_id, self.config.working_reaction)
        return inbound

    async def send(self, event: OutboundEvent) -> None:
        if event.stream is not None and event.stream.kind == StreamKind.DELTA:
            return
        if self._client is None:
            raise RuntimeError("Lark channel must be started before send()")
        text = event.content.strip()
        if not text:
            return
        await asyncio.to_thread(self.sdk.send_text, self._client, chat_id=event.chat_id, text=text)
        if not event.metadata.get("progress"):
            message_id = event.metadata.get("external_message_id")
            await self._add_reaction(
                str(message_id) if message_id else None,
                self.config.done_reaction,
            )

    async def stop(self) -> None:
        self._stopping = True
        ws_client = self._ws_client
        if ws_client is None:
            return
        with contextlib.suppress(Exception):
            if hasattr(ws_client, "_auto_reconnect"):
                ws_client._auto_reconnect = False
        stop_ws_client = getattr(self.sdk, "stop_ws_client", None)
        if callable(stop_ws_client):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(stop_ws_client, ws_client)
            return
        for method_name in ("stop", "close", "disconnect", "_disconnect"):
            method = getattr(ws_client, method_name, None)
            if not callable(method):
                continue
            with contextlib.suppress(Exception):
                result = method()
                if inspect.isawaitable(result):
                    await result
                return

    def _handle_sdk_event(self, event: Any) -> None:
        if self._stopping:
            return
        if self._loop is None:
            raise RuntimeError("Lark channel event loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(self.handle_message(event), self._loop)
        future.add_done_callback(_consume_callback_result)

    async def _add_reaction(self, message_id: str | None, emoji_type: str) -> None:
        if not self.config.reactions_enabled:
            return
        if self._client is None or not message_id or not emoji_type:
            return
        add_reaction = getattr(self.sdk, "add_reaction", None)
        if not callable(add_reaction):
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                add_reaction,
                self._client,
                message_id=message_id,
                emoji_type=emoji_type,
            )


def _consume_callback_result(future: Future[Any]) -> None:
    with contextlib.suppress(Exception):
        future.result()


class _IncomingMessage:
    def __init__(
        self,
        *,
        message_id: str | None,
        chat_id: str | None,
        chat_type: str,
        message_type: str,
        content: str | None,
        mentions: list[Any],
        sender_id: str,
        sender_type: str,
        tenant_key: str | None,
    ) -> None:
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat_type = chat_type
        self.message_type = message_type
        self.content = content or ""
        self.mentions = mentions
        self.sender_id = sender_id
        self.sender_type = sender_type
        self.tenant_key = tenant_key


def _extract_incoming(payload: Any) -> _IncomingMessage | None:
    event = _field(payload, "event") or payload
    sender = _field(event, "sender")
    message = _field(event, "message")
    if sender is None or message is None:
        return None
    sender_id = _user_open_id(_field(sender, "sender_id"))
    mentions = _field(message, "mentions") or []
    if not isinstance(mentions, list):
        mentions = list(mentions)
    return _IncomingMessage(
        message_id=_field(message, "message_id"),
        chat_id=_field(message, "chat_id"),
        chat_type=str(_field(message, "chat_type") or ""),
        message_type=str(_field(message, "message_type") or ""),
        content=_field(message, "content"),
        mentions=mentions,
        sender_id=sender_id or "",
        sender_type=str(_field(sender, "sender_type") or ""),
        tenant_key=_field(sender, "tenant_key"),
    )


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _user_open_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    open_id = _field(value, "open_id")
    if open_id:
        return str(open_id)
    return None


def _extract_text(content: str) -> str:
    with contextlib.suppress(json.JSONDecodeError):
        payload = json.loads(content)
        if isinstance(payload, dict):
            return str(payload.get("text") or "")
    return content


def _mentions_open_id(mentions: list[Any], open_id: str) -> bool:
    return any(_mention_open_id(mention) == open_id for mention in mentions)


def _mention_open_id(mention: Any) -> str | None:
    mention_id = _field(mention, "id")
    if isinstance(mention_id, str):
        return mention_id
    return _user_open_id(mention_id)


def _strip_bot_mention(text: str, mentions: list[Any], bot_open_id: str) -> str:
    stripped = text
    stripped = re.sub(
        rf"<at\s+user_id=[\"']?{re.escape(bot_open_id)}[\"']?.*?</at>",
        "",
        stripped,
    )
    for mention in mentions:
        if _mention_open_id(mention) != bot_open_id:
            continue
        key = _field(mention, "key")
        name = _field(mention, "name")
        if key:
            stripped = stripped.replace(str(key), "")
        if name:
            stripped = re.sub(rf"^@?{re.escape(str(name))}\s*", "", stripped)
    return stripped.strip(" \t\r\n,，:：")
