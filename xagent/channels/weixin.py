from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from xagent.bus import InboundMessage, OutboundEvent, StreamKind
from xagent.channels.base import BaseChannel
from xagent.config import WeixinChannelConfig, xagent_home

ITEM_TEXT = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
WEIXIN_MAX_MESSAGE_LEN = 4000
WEIXIN_CHANNEL_VERSION = "2.1.1"
ILINK_APP_ID = "bot"
BASE_INFO: dict[str, str] = {"channel_version": WEIXIN_CHANNEL_VERSION}
ERRCODE_SESSION_EXPIRED = -14
SESSION_PAUSE_SECONDS = 60 * 60
MAX_QR_REFRESH_COUNT = 3
POLL_RETRY_DELAY_SECONDS = 2
POLL_BACKOFF_SECONDS = 30
MAX_CONSECUTIVE_FAILURES = 3

logger = logging.getLogger(__name__)


def _build_client_version(version: str) -> int:
    parts = version.split(".")

    def part(index: int) -> int:
        try:
            return int(parts[index])
        except Exception:
            return 0

    return ((part(0) & 0xFF) << 16) | ((part(1) & 0xFF) << 8) | (part(2) & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(WEIXIN_CHANNEL_VERSION)


class WeixinApiAdapter:
    """ilinkai 个人微信 HTTP API 的薄适配层，便于测试注入 fake。"""

    def __init__(
        self,
        *,
        base_url: str,
        route_tag: str | None,
        token: str | None,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.route_tag = route_tag
        self.token = token or ""
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    def configure(
        self,
        *,
        base_url: str | None = None,
        route_tag: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")
        if route_tag is not None:
            self.route_tag = route_tag
        if token is not None:
            self.token = token
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds
            if self._client is not None:
                self._client.timeout = httpx.Timeout(timeout_seconds, connect=30)

    async def open(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30),
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def get_qr_code(self) -> tuple[str, str]:
        data = await self._get(
            "ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
            auth=False,
        )
        qrcode_id = str(data.get("qrcode") or "")
        scan_url = str(data.get("qrcode_img_content") or qrcode_id)
        if not qrcode_id:
            raise RuntimeError(f"Failed to get Weixin QR code: {data}")
        return qrcode_id, scan_url

    async def get_qr_status(self, qrcode_id: str, *, base_url: str | None = None) -> dict[str, Any]:
        return await self._get(
            "ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode_id},
            auth=False,
            base_url=base_url,
        )

    async def get_updates(self, get_updates_buf: str) -> dict[str, Any]:
        return await self._post(
            "ilink/bot/getupdates",
            {
                "get_updates_buf": get_updates_buf,
                "base_info": BASE_INFO,
            },
        )

    async def send_text(self, *, to_user_id: str, text: str, context_token: str) -> dict[str, Any]:
        client_id = f"xagent-{os.urandom(6).hex()}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": BASE_INFO,
        }
        return await self._post("ilink/bot/sendmessage", body)

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Weixin API client is not open")
        url = f"{(base_url or self.base_url).rstrip('/')}/{endpoint}"
        response = await self._client.get(url, params=params, headers=self._headers(auth=auth))
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Weixin API client is not open")
        url = f"{self.base_url}/{endpoint}"
        payload = dict(body)
        payload.setdefault("base_info", BASE_INFO)
        response = await self._client.post(url, json=payload, headers=self._headers(auth=True))
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _headers(self, *, auth: bool) -> dict[str, str]:
        headers = {
            "X-WECHAT-UIN": _random_wechat_uin(),
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.route_tag:
            headers["SKRouteTag"] = str(self.route_tag)
        return headers


class WeixinChannel(BaseChannel):
    def __init__(
        self,
        *,
        config: WeixinChannelConfig,
        bus: Any,
        api: Any | None = None,
    ) -> None:
        super().__init__(name="weixin", bus=bus)
        self.config = config
        self.api = api or WeixinApiAdapter(
            base_url=config.base_url,
            route_tag=config.route_tag,
            token=config.token,
            timeout_seconds=config.poll_timeout_seconds + 10,
        )
        self._token = ""
        self._base_url = config.base_url
        self._get_updates_buf = ""
        self._context_tokens: dict[str, str] = {}
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._running = False
        self._started = False
        self._session_pause_until = 0.0
        self._next_poll_timeout_seconds = config.poll_timeout_seconds
        self.last_send_error: str | None = None

    def describe(self) -> str:
        allow_from = self.config.allow_from
        if "*" in allow_from:
            allow_summary = "*"
        else:
            allow_summary = f"{len(allow_from)} sender(s)"
        return (
            f"weixin mode=long-poll allow_from={allow_summary} "
            f"poll={self.config.poll_timeout_seconds}s state={self._state_path()}"
        )

    async def login(self, *, force: bool = False) -> bool:
        if force:
            self._clear_state()
        elif self.config.token:
            self._token = self.config.token
            return True
        elif self._load_state() and self._token:
            return True

        self._running = True
        await self._open_api(timeout_seconds=60)
        try:
            return await self._qr_login()
        finally:
            self._running = False
            await self._close_api()

    async def start(self) -> None:
        if self._started:
            return
        self._load_state()
        if self.config.token:
            self._token = self.config.token
        if not self._token:
            raise RuntimeError(
                "Weixin channel is not logged in. Run 'xagent channels login weixin' first."
            )
        self._running = True
        self._next_poll_timeout_seconds = self.config.poll_timeout_seconds
        await self._open_api(timeout_seconds=self._next_poll_timeout_seconds + 10)
        self._started = True

    async def run(self) -> None:
        if not self._started:
            raise RuntimeError("Weixin channel must be started before run()")

        failures = 0
        while self._running:
            try:
                await self._poll_once()
                failures = 0
            except httpx.TimeoutException:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - long-poll should keep gateway alive
                if not self._running:
                    break
                failures += 1
                logger.warning("Weixin polling failed: %s", exc)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    failures = 0
                    await asyncio.sleep(POLL_BACKOFF_SECONDS)
                else:
                    await asyncio.sleep(POLL_RETRY_DELAY_SECONDS)

    async def handle_message(self, message: Any) -> InboundMessage | None:
        if not isinstance(message, dict):
            return None
        if _as_int(message.get("message_type")) == MESSAGE_TYPE_BOT:
            return None

        from_user_id = str(message.get("from_user_id") or "").strip()
        if not from_user_id:
            return None
        if not self._is_allowed(from_user_id):
            return None

        message_id = _message_id(message)
        if message_id in self._processed_ids:
            return None
        self._processed_ids[message_id] = None
        while len(self._processed_ids) > 1000:
            self._processed_ids.popitem(last=False)

        text = _extract_text(message).strip()
        if not text:
            return None

        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._context_tokens[from_user_id] = context_token
            self._save_state()

        inbound = InboundMessage(
            content=text,
            channel=self.name,
            chat_id=from_user_id,
            sender_id=from_user_id,
            external_message_id=message_id,
            metadata={"message_type": message.get("message_type")},
        )
        await self.bus.publish_inbound(inbound)
        return inbound

    async def send(self, event: OutboundEvent) -> None:
        if event.stream is not None and event.stream.kind == StreamKind.DELTA:
            return
        self.last_send_error = None
        if not self._started:
            raise RuntimeError("Weixin channel must be started before send()")
        content = event.content.strip()
        if not content:
            return
        context_token = self._context_tokens.get(event.chat_id, "")
        if not context_token:
            self.last_send_error = f"Weixin context_token is missing for chat_id={event.chat_id}"
            logger.warning(self.last_send_error)
            return
        for chunk in _split_message(content, WEIXIN_MAX_MESSAGE_LEN):
            await self.api.send_text(
                to_user_id=event.chat_id,
                text=chunk,
                context_token=context_token,
            )

    async def stop(self) -> None:
        self._running = False
        self._started = False
        await self._close_api()
        self._save_state()

    async def _qr_login(self) -> bool:
        refresh_count = 0
        qrcode_id, scan_url = await self.api.get_qr_code()
        _print_qr_code(scan_url)
        poll_base_url = self._base_url

        while self._running:
            data = await self.api.get_qr_status(qrcode_id, base_url=poll_base_url)
            status = str(data.get("status") or "")
            if status == "confirmed":
                token = str(data.get("bot_token") or "")
                if not token:
                    raise RuntimeError("Weixin login confirmed but no bot_token was returned")
                self._token = token
                base_url = str(data.get("baseurl") or "").strip()
                if base_url:
                    self._base_url = base_url.rstrip("/")
                self._configure_api()
                self._save_state()
                return True
            if status == "scaned_but_redirect":
                redirect_host = str(data.get("redirect_host") or "").strip()
                if redirect_host:
                    poll_base_url = _normalize_base_url(redirect_host)
            elif status == "expired":
                refresh_count += 1
                if refresh_count > MAX_QR_REFRESH_COUNT:
                    return False
                qrcode_id, scan_url = await self.api.get_qr_code()
                _print_qr_code(scan_url)
                poll_base_url = self._base_url
            await asyncio.sleep(1)
        return False

    async def _poll_once(self) -> None:
        remaining = self._session_pause_remaining_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)
            return

        self._configure_api(timeout_seconds=self._next_poll_timeout_seconds + 10)
        data = await self.api.get_updates(self._get_updates_buf)
        ret = _as_int(data.get("ret"))
        errcode = _as_int(data.get("errcode"))
        if ret != 0 or errcode != 0:
            if ret == ERRCODE_SESSION_EXPIRED or errcode == ERRCODE_SESSION_EXPIRED:
                self._session_pause_until = asyncio.get_running_loop().time() + SESSION_PAUSE_SECONDS
                return
            raise RuntimeError(
                f"Weixin getupdates failed: ret={ret} errcode={errcode} "
                f"errmsg={data.get('errmsg', '')}"
            )

        server_timeout_ms = data.get("longpolling_timeout_ms")
        if isinstance(server_timeout_ms, int) and server_timeout_ms > 0:
            self._next_poll_timeout_seconds = max(server_timeout_ms // 1000, 5)

        new_buf = str(data.get("get_updates_buf") or "")
        if new_buf:
            self._get_updates_buf = new_buf
            self._save_state()

        for message in data.get("msgs") or []:
            with suppress(Exception):
                await self.handle_message(message)

    def _session_pause_remaining_seconds(self) -> int:
        now = asyncio.get_running_loop().time()
        remaining = int(self._session_pause_until - now)
        if remaining <= 0:
            self._session_pause_until = 0.0
            return 0
        return remaining

    async def _open_api(self, *, timeout_seconds: float) -> None:
        self._configure_api(timeout_seconds=timeout_seconds)
        open_api = getattr(self.api, "open", None)
        if callable(open_api):
            await open_api()

    async def _close_api(self) -> None:
        close_api = getattr(self.api, "close", None)
        if callable(close_api):
            await close_api()

    def _configure_api(self, *, timeout_seconds: float | None = None) -> None:
        configure = getattr(self.api, "configure", None)
        if callable(configure):
            configure(
                base_url=self._base_url,
                route_tag=self.config.route_tag,
                token=self._token,
                timeout_seconds=timeout_seconds,
            )

    def _state_dir(self) -> Path:
        if self.config.state_dir:
            return Path(os.path.expandvars(self.config.state_dir)).expanduser().resolve()
        return xagent_home() / "channels" / "weixin"

    def _state_path(self) -> Path:
        return self._state_dir() / "account.json"

    def _load_state(self) -> bool:
        path = self._state_path()
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        self._token = str(data.get("token") or "")
        self._get_updates_buf = str(data.get("get_updates_buf") or "")
        self._base_url = str(data.get("base_url") or self.config.base_url).rstrip("/")
        context_tokens = data.get("context_tokens") or {}
        if isinstance(context_tokens, dict):
            self._context_tokens = {
                str(key): str(value)
                for key, value in context_tokens.items()
                if str(key).strip() and str(value).strip()
            }
        self._configure_api()
        return bool(self._token)

    def _save_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": self._token,
            "get_updates_buf": self._get_updates_buf,
            "context_tokens": self._context_tokens,
            "base_url": self._base_url,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _clear_state(self) -> None:
        self._token = ""
        self._get_updates_buf = ""
        self._context_tokens = {}
        self._base_url = self.config.base_url
        path = self._state_path()
        if path.exists():
            path.unlink()

    def _is_allowed(self, sender_id: str) -> bool:
        allowed = self.config.allow_from
        return "*" in allowed or sender_id in allowed


def _random_wechat_uin() -> str:
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode()).decode()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _message_id(message: dict[str, Any]) -> str:
    message_id = str(message.get("message_id") or message.get("seq") or "").strip()
    if message_id:
        return message_id
    return f"{message.get('from_user_id', '')}_{message.get('create_time_ms', '')}"


def _extract_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in message.get("item_list") or []:
        if _as_int(item.get("type")) != ITEM_TEXT:
            continue
        text = str((item.get("text_item") or {}).get("text") or "")
        if text:
            parts.append(text)
    return "\n".join(parts)


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _normalize_base_url(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return f"https://{value}".rstrip("/")


def _print_qr_code(url: str) -> None:
    try:
        import qrcode  # type: ignore[import-untyped]

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print(f"\nWeixin login URL: {url}\n")
