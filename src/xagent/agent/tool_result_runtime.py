from __future__ import annotations

import json
from typing import Any

from xagent.foundation.tools import ToolResult


def get_tool_result_policy(tool_name: str) -> dict[str, Any]:
    if tool_name in {"list_files", "glob_search", "grep_search", "file_info", "mkdir", "move_path"}:
        return {"prefer_summary_only": True, "include_data": False, "max_length": 1000, "ui_summary_only": True}
    if tool_name == "read_file":
        return {"prefer_summary_only": False, "include_data": True, "max_length": 12000, "ui_summary_only": False}
    if tool_name in {"apply_patch", "write_file", "str_replace", "bash"}:
        return {"prefer_summary_only": False, "include_data": True, "max_length": 4000, "ui_summary_only": False}
    return {"prefer_summary_only": False, "include_data": True, "max_length": 4000, "ui_summary_only": False}


def format_tool_result_for_message(tool_name: str, result: ToolResult) -> str:
    policy = get_tool_result_policy(tool_name)
    normalized = normalize_tool_result(result)

    if tool_name == "read_file" and normalized["ok"] and normalized.get("content"):
        return normalized["content"]

    if not normalized["ok"]:
        payload = {
            "ok": False,
            "summary": normalized["summary"],
            "error": normalized["error"],
            **({"code": normalized["code"]} if normalized.get("code") else {}),
            **({"details": normalized["details"]} if normalized.get("details") is not None else {}),
        }
        return _stringify_with_limit(payload, policy["max_length"])

    if policy["prefer_summary_only"] or not policy["include_data"]:
        return json.dumps({"ok": True, "summary": normalized["summary"]}, ensure_ascii=False)

    payload = {
        "ok": True,
        "summary": normalized["summary"],
        **({"data": normalized["data"]} if normalized.get("data") is not None else {}),
    }
    return _stringify_with_limit(payload, policy["max_length"])


def summarize_tool_result_for_ui(tool_name: str, content: str, is_error: bool) -> str:
    try:
        payload = json.loads(content)
    except Exception:
        return content
    if not isinstance(payload, dict):
        return content
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return content
    if is_error and isinstance(payload.get("error"), str) and payload.get("error") != summary:
        return f"{summary}\n{payload['error']}"
    return summary


def normalize_tool_result(result: ToolResult) -> dict[str, Any]:
    if result.is_error:
        error = result.error or result.content or "Tool execution failed."
        summary = result.summary or error
        return {
            "ok": False,
            "summary": summary,
            "error": error,
            "code": result.code,
            "details": result.details,
            "content": result.content,
        }

    summary = result.summary or result.content or "Tool completed successfully."
    data = result.data
    if data is None and result.content and result.summary and result.content != result.summary:
        data = {"content": result.content}
    return {
        "ok": True,
        "summary": summary,
        "data": data,
        "content": result.content,
    }


def _stringify_with_limit(payload: dict[str, Any], max_length: int) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_length:
        return text
    fallback = {"ok": payload.get("ok", True), "summary": str(payload.get("summary", ""))[: max_length - 64]}
    if payload.get("ok") is False:
        fallback["error"] = str(payload.get("error", ""))[: max_length - 64]
        if payload.get("code"):
            fallback["code"] = payload["code"]
    return json.dumps(fallback, ensure_ascii=False)
