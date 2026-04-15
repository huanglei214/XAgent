from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SemanticMemory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        payload = self._load()
        values = payload.get(namespace, {})
        return dict(values) if isinstance(values, dict) else {}

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.get_namespace(namespace).get(key, default)

    def set(self, namespace: str, key: str, value: Any) -> None:
        payload = self._load()
        values = payload.setdefault(namespace, {})
        if not isinstance(values, dict):
            values = {}
            payload[namespace] = values
        values[key] = value
        self._save(payload)

    def replace_namespace(self, namespace: str, values: dict[str, Any]) -> None:
        payload = self._load()
        payload[namespace] = dict(values)
        self._save(payload)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
