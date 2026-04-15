from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from xagent.channel.feishu.config import FeishuConfig

logger = logging.getLogger(__name__)


class FeishuApiError(RuntimeError):
    pass


def _load_lark_oapi() -> Any:
    try:
        return importlib.import_module("lark_oapi")
    except ImportError:
        project_root = Path(__file__).resolve().parents[4]
        for candidate in sorted((project_root / ".venv" / "lib").glob("python*/site-packages")):
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
        return importlib.import_module("lark_oapi")


class FeishuApiClient:
    def __init__(self, config: FeishuConfig, sdk_module: Any | None = None) -> None:
        self.config = config
        self._sdk = sdk_module or _load_lark_oapi()
        self._client = (
            self._sdk.Client.builder()
            .app_id(config.app_id)
            .app_secret(config.app_secret)
            .domain(config.api_base_url)
            .build()
        )
        model_module = importlib.import_module(
            "lark_oapi.api.im.v1.model.create_message_request",
        )
        body_module = importlib.import_module(
            "lark_oapi.api.im.v1.model.create_message_request_body",
        )
        update_request_module = importlib.import_module(
            "lark_oapi.api.im.v1.model.update_message_request",
        )
        update_body_module = importlib.import_module(
            "lark_oapi.api.im.v1.model.update_message_request_body",
        )
        self._request_builder = model_module.CreateMessageRequest
        self._body_builder = body_module.CreateMessageRequestBody
        self._update_request_builder = update_request_module.UpdateMessageRequest
        self._update_body_builder = update_body_module.UpdateMessageRequestBody

    def send_text_message(self, chat_id: str, text: str) -> str | None:
        logger.info("[FeishuApi] send_text_message: chat_id=%s, text_len=%d", chat_id, len(text))
        body = (
            self._body_builder.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            self._request_builder.builder().receive_id_type("chat_id").request_body(body).build()
        )
        response = self._client.im.v1.message.create(request)
        if getattr(response, "code", None) != 0:
            error_msg = getattr(response, "msg", "Feishu send message request failed.")
            logger.error(
                "[FeishuApi] send_text_message failed: code=%s, msg=%s",
                getattr(response, "code", None),
                error_msg,
            )
            raise FeishuApiError(error_msg)
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None)
        logger.info("[FeishuApi] send_text_message success: message_id=%s", message_id)
        return message_id

    def update_text_message(self, message_id: str, text: str) -> None:
        logger.info(
            "[FeishuApi] update_text_message: message_id=%s, text_len=%d", message_id, len(text)
        )
        body = (
            self._update_body_builder.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            self._update_request_builder.builder().message_id(message_id).request_body(body).build()
        )
        response = self._client.im.v1.message.update(request)
        if getattr(response, "code", None) != 0:
            error_msg = getattr(response, "msg", "Feishu update message request failed.")
            logger.error(
                "[FeishuApi] update_text_message failed: code=%s, msg=%s",
                getattr(response, "code", None),
                error_msg,
            )
            raise FeishuApiError(error_msg)
        logger.info("[FeishuApi] update_text_message success: message_id=%s", message_id)


class FeishuLongConnectionClient:
    def __init__(
        self,
        config: FeishuConfig,
        message_handler: Callable[[Any], None],
        *,
        sdk_module: Any | None = None,
    ) -> None:
        self.config = config
        self._sdk = sdk_module or _load_lark_oapi()
        dispatcher = (
            self._sdk.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(message_handler)
            .build()
        )
        self._client = self._sdk.ws.Client(
            config.app_id,
            config.app_secret,
            event_handler=dispatcher,
            domain=config.api_base_url,
            auto_reconnect=True,
        )

    def start(self) -> None:
        self._client.start()

    def close(self) -> None:
        # The official ws.Client manages the connection lifecycle internally.
        # The CLI process exits on startup failure or user interrupt.
        return None
