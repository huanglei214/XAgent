from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from xagent.channel.models import ChannelEnvelope, ChannelIdentity


@dataclass
class FeishuInboundEvent:
    envelope: ChannelEnvelope | None
    should_ack: bool = False
    raw: dict[str, Any] | None = None


def parse_feishu_payload(raw_payload: str) -> FeishuInboundEvent:
    payload = json.loads(raw_payload)
    if payload.get("type") == "ping":
        return FeishuInboundEvent(envelope=None, should_ack=True, raw=payload)
    if payload.get("challenge"):
        return FeishuInboundEvent(envelope=None, should_ack=True, raw=payload)

    header = payload.get("header") or {}
    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}

    if header.get("event_type") and header.get("event_type") != "im.message.receive_v1":
        return FeishuInboundEvent(envelope=None, should_ack=False, raw=payload)

    if not message and payload.get("text"):
        identity = ChannelIdentity(
            channel="feishu",
            user_id=str(payload.get("user_id") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            chat_type=str(payload.get("chat_type") or "p2p"),
        )
        envelope = ChannelEnvelope(
            text=str(payload.get("text") or "").strip(),
            identity=identity,
            mentions=tuple(str(item) for item in payload.get("mentions") or ()),
            metadata={"raw": payload},
        )
        return FeishuInboundEvent(envelope=envelope, should_ack=False, raw=payload)

    content = message.get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"text": content}

    mentions = []
    for item in message.get("mentions") or content.get("mentions") or []:
        if isinstance(item, str):
            mentions.append(item)
            continue
        mention_id = (
            item.get("id", {}).get("open_id")
            or item.get("id", {}).get("user_id")
            or item.get("open_id")
            or item.get("user_id")
        )
        if mention_id:
            mentions.append(str(mention_id))

    sender_id = sender.get("sender_id") or {}
    identity = ChannelIdentity(
        channel="feishu",
        user_id=str(sender_id.get("open_id") or sender_id.get("user_id") or payload.get("user_id") or ""),
        chat_id=str(message.get("chat_id") or payload.get("chat_id") or ""),
        chat_type=str(message.get("chat_type") or payload.get("chat_type") or "p2p"),
    )
    envelope = ChannelEnvelope(
        text=str(content.get("text") or payload.get("text") or "").strip(),
        identity=identity,
        mentions=tuple(mentions),
        metadata={
            "event_type": header.get("event_type"),
            "event_id": header.get("event_id"),
            "message_id": message.get("message_id"),
            "raw": payload,
        },
    )
    return FeishuInboundEvent(envelope=envelope, should_ack=False, raw=payload)
