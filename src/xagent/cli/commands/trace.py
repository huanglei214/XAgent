from pathlib import Path

import typer

from xagent.agent.traces import (
    get_latest_trace,
    get_trace_summary,
    list_failed_traces,
    list_traces,
    load_trace_events,
    summarize_sessions,
    summarize_trace,
)
from xagent.cli.tui.render import console, print_error, print_info, print_panel

trace_app = typer.Typer(help="Inspect local XAgent traces.")


@trace_app.command("latest")
def trace_latest(events: int = typer.Option(12, min=1, max=200, help="Number of recent events to show.")) -> None:
    summary = get_latest_trace(Path.cwd())
    if summary is None:
        print_info("No traces found in this project.")
        return
    _render_trace(summary, events_limit=events)


@trace_app.command("list")
def trace_list(
    limit: int = typer.Option(20, min=1, max=200, help="Maximum traces to list."),
    session_id: str = typer.Option("", help="Filter by session id."),
    status: str = typer.Option("", help="Filter by status."),
    reason: str = typer.Option("", help="Filter by termination reason."),
    tool: str = typer.Option("", help="Filter by tool name."),
) -> None:
    traces = list_traces(
        Path.cwd(),
        limit=limit,
        session_id=session_id or None,
        status=status or None,
        termination_reason=reason or None,
        tool_name=tool or None,
    )
    if not traces:
        print_info("No traces matched the given filters.")
        return

    lines = []
    for item in traces:
        lines.append(
            f"{item.get('trace_id')} | {item.get('status')} | {item.get('termination_reason') or '-'} | "
            f"{item.get('task_kind')} | {item.get('model')} | {item.get('started_at')}"
        )
    print_panel("Traces", "\n".join(lines))


@trace_app.command("show")
def trace_show(
    trace_id: str = typer.Argument(..., help="Trace id to inspect."),
    events: int = typer.Option(50, min=1, max=500, help="Number of recent events to show."),
) -> None:
    summary = get_trace_summary(Path.cwd(), trace_id)
    if summary is None:
        print_error(f"Trace not found: {trace_id}")
        raise typer.Exit(code=1)
    _render_trace(summary, events_limit=events)


@trace_app.command("sessions")
def trace_sessions(limit: int = typer.Option(20, min=1, max=200, help="Maximum sessions to summarize.")) -> None:
    sessions = summarize_sessions(Path.cwd(), limit=limit)
    if not sessions:
        print_info("No trace sessions found in this project.")
        return

    lines = []
    for session in sessions:
        tool_bits = ", ".join(sorted(session.get("tools", {}).keys())[:3]) or "-"
        lines.append(
            f"{session.get('session_id')} | traces={session.get('trace_count')} | "
            f"latest={session.get('latest_status')}/{session.get('latest_reason') or '-'} | "
            f"restored={session.get('restored_turns')} | tools={tool_bits}"
        )
    print_panel("Trace Sessions", "\n".join(lines))


@trace_app.command("failed")
def trace_failed(limit: int = typer.Option(10, min=1, max=200, help="Maximum failed traces to list.")) -> None:
    failed = list_failed_traces(Path.cwd(), limit=limit)
    if not failed:
        print_info("No failed traces found in this project.")
        return

    lines = []
    for item in failed:
        lines.append(
            f"{item.get('trace_id')} | {item.get('task_kind')} | {item.get('model')} | "
            f"{item.get('started_at')} | {item.get('error') or '-'}"
        )
    print_panel("Failed Traces", "\n".join(lines))


def _render_trace(summary, events_limit: int) -> None:
    derived = summarize_trace(summary)
    body = (
        f"Trace ID: {summary.get('trace_id')}\n"
        f"Session ID: {summary.get('session_id')}\n"
        f"Mode: {summary.get('mode')}\n"
        f"Provider: {summary.get('provider')}\n"
        f"Model: {summary.get('model')}\n"
        f"Task kind: {summary.get('task_kind')}\n"
        f"Status: {summary.get('status')}\n"
        f"Termination: {summary.get('termination_reason') or '-'}\n"
        f"Started: {summary.get('started_at')}\n"
        f"Ended: {summary.get('ended_at')}\n"
        f"Trace file: {summary.get('trace_file')}\n"
        f"Error: {summary.get('error') or '-'}\n"
        f"Output preview: {summary.get('output_preview') or '-'}"
    )
    print_panel("Trace Summary", body)
    print_panel("Trace Stats", _render_trace_stats(derived))

    events = load_trace_events(summary.get("trace_file", ""))[-events_limit:]
    if not events:
        print_info("No events found for this trace.")
        return

    lines = []
    for event in events:
        timestamp = str(event.get("timestamp", ""))
        event_type = str(event.get("event_type", ""))
        tags = event.get("tags", {}) or {}
        tag_bits = []
        if "tool_name" in tags:
            tag_bits.append(f"tool={tags['tool_name']}")
        if "status" in tags:
            tag_bits.append(f"status={tags['status']}")
        if "failure_stage" in tags:
            tag_bits.append(f"stage={tags['failure_stage']}")
        if "termination_reason" in tags:
            tag_bits.append(f"reason={tags['termination_reason']}")
        if "step" in tags:
            tag_bits.append(f"step={tags['step']}")
        if "access_kind" in tags:
            tag_bits.append(f"access={tags['access_kind']}")
        payload = event.get("payload", {})
        preview = _payload_preview(payload)
        suffix = f" [{' '.join(tag_bits)}]" if tag_bits else ""
        lines.append(f"{timestamp} {event_type}{suffix}\n  {preview}")

    print_panel("Trace Events", "\n".join(lines))


def _payload_preview(payload) -> str:
    text = str(payload)
    if len(text) > 300:
        return text[:297] + "..."
    return text


def _render_trace_stats(derived: dict) -> str:
    lines = [
        f"Events: {derived.get('event_count', 0)}",
        f"Steps: {derived.get('step_count', 0)}",
        f"Restored context: {'yes' if derived.get('restored_context') else 'no'}",
    ]

    tool_stats = derived.get("tool_stats", {})
    if tool_stats:
        lines.append("Tools:")
        for tool_name, stats in sorted(tool_stats.items()):
            lines.append(
                f"  {tool_name}: started={stats.get('started', 0)} "
                f"success={stats.get('success', 0)} error={stats.get('error', 0)}"
            )

    approval_decisions = derived.get("approval_decisions", {})
    if approval_decisions:
        lines.append(
            "Approvals: "
            + ", ".join(f"{decision}={count}" for decision, count in sorted(approval_decisions.items()))
        )

    external_path_decisions = derived.get("external_path_decisions", {})
    if external_path_decisions:
        lines.append(
            "External path approvals: "
            + ", ".join(f"{decision}={count}" for decision, count in sorted(external_path_decisions.items()))
        )

    return "\n".join(lines)
