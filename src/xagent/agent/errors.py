"""Agent 层公用异常定义。

``WorkspaceEscapeError`` 语义上是"工作区路径越界"，仅在 agent/paths 与
agent/tools 内使用，与"消息总线"概念无关（openspec 0001-simplify-bus）。
"""

from __future__ import annotations


class WorkspaceEscapeError(ValueError):
    """工具访问路径越出当前工作区根目录时抛出的异常。"""
