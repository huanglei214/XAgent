from pathlib import Path

import typer

from xagent.agent.traces import (
    get_latest_trace,
    get_trace_summary,
    list_failed_traces,
    load_trace_events,
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
    body = (
        f"Trace ID: {summary.get('trace_id')}\n"
        f"Session ID: {summary.get('session_id')}\n"
        f"Mode: {summary.get('mode')}\n"
        f"Provider: {summary.get('provider')}\n"
        f"Model: {summary.get('model')}\n"
        f"Task kind: {summary.get('task_kind')}\n"
        f"Status: {summary.get('status')}\n"
        f"Started: {summary.get('started_at')}\n"
        f"Ended: {summary.get('ended_at')}\n"
        f"Trace file: {summary.get('trace_file')}\n"
        f"Error: {summary.get('error') or '-'}\n"
        f"Output preview: {summary.get('output_preview') or '-'}"
    )
    print_panel("Trace Summary", body)

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
