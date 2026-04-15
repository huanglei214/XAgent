from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from xagent.agent.runtime import SessionRuntimeManager
from xagent.cli.runtime import build_runtime_agent, build_session_runtime, render_final_message
from xagent.cli.tui.render import print_error, print_info
from xagent.foundation.messages import Message

schedule_app = typer.Typer(help="Run scheduled jobs.")


def _parse_run_at(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return timestamp.timestamp()


async def run_scheduled_once(
    *,
    text: str,
    delay_seconds: float = 0.0,
    session_id: Optional[str] = None,
    requested_skill_name: Optional[str] = None,
) -> None:
    cwd = str(Path.cwd())
    manager = SessionRuntimeManager(
        cwd=cwd,
        agent_factory=lambda: build_runtime_agent(cwd),
        runtime_factory=build_session_runtime,
    )
    try:
        if session_id is None:
            session_id = manager.create_session()
        job = manager.schedule_message(
            session_id,
            text,
            delay_seconds=delay_seconds,
            requested_skill_name=requested_skill_name,
            source="cli.schedule",
        )
        if delay_seconds > 0:
            print_info(f"Scheduled job {job['job_id']} for session {job['session_id']} in {delay_seconds:.2f}s")
        response = manager.wait_for_job(job["job_id"])
        render_final_message(Message.model_validate(response["message"]))
        print_info(f"Session: {response['session_id']}")
    finally:
        manager.close()


def _build_manager(cwd: str) -> SessionRuntimeManager:
    return SessionRuntimeManager(
        cwd=cwd,
        agent_factory=lambda: build_runtime_agent(cwd),
        runtime_factory=build_session_runtime,
    )


async def run_scheduler_service(*, poll_interval_seconds: float = 1.0) -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        manager.start_persistent_scheduler(poll_interval_seconds=poll_interval_seconds)
        print_info(f"Persistent scheduler running (poll interval {poll_interval_seconds:.2f}s)")
        while True:
            await asyncio.sleep(3600)
    finally:
        manager.close()


@schedule_app.command("once")
def schedule_once_command(
    text: str = typer.Argument(..., help="Prompt text to schedule."),
    delay_seconds: float = typer.Option(0.0, help="Delay before dispatching the job."),
    session_id: Optional[str] = typer.Option(None, help="Existing session id to target. Defaults to a new session."),
    requested_skill_name: Optional[str] = typer.Option(None, "--skill", help="Optional requested skill name."),
) -> None:
    try:
        asyncio.run(
            run_scheduled_once(
                text=text,
                delay_seconds=delay_seconds,
                session_id=session_id,
                requested_skill_name=requested_skill_name,
            )
        )
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


@schedule_app.command("add")
def schedule_add_command(
    text: str = typer.Argument(..., help="Prompt text to schedule."),
    cron: Optional[str] = typer.Option(None, "--cron", help="Cron expression like '*/5 * * * *'."),
    delay_seconds: Optional[float] = typer.Option(None, "--delay-seconds", help="One-shot delay in seconds."),
    at: Optional[str] = typer.Option(None, "--at", help="Absolute execution time in ISO 8601 format."),
    session_id: Optional[str] = typer.Option(None, help="Existing session id to target. Defaults to a new session."),
    requested_skill_name: Optional[str] = typer.Option(None, "--skill", help="Optional requested skill name."),
    retry_enabled: bool = typer.Option(False, "--retry", help="Retry failed runs."),
    retry_delay_seconds: float = typer.Option(60.0, "--retry-delay-seconds", help="Retry delay in seconds."),
    retry_backoff_multiplier: float = typer.Option(1.0, "--retry-backoff-multiplier", help="Retry backoff multiplier."),
    max_retries: int = typer.Option(0, "--max-retries", help="Maximum retries after a failure."),
) -> None:
    one_shot_count = int(delay_seconds is not None) + int(at is not None)
    if bool(cron) == bool(one_shot_count):
        raise typer.BadParameter("Specify exactly one of --cron, --delay-seconds, or --at.")
    if one_shot_count > 1:
        raise typer.BadParameter("Choose only one of --delay-seconds or --at.")

    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        target_session_id = session_id or manager.create_session()
        if cron:
            job = manager.add_cron_job(
                target_session_id,
                text,
                cron_expression=cron,
                requested_skill_name=requested_skill_name,
                retry_enabled=retry_enabled,
                retry_delay_seconds=retry_delay_seconds,
                retry_backoff_multiplier=retry_backoff_multiplier,
                max_retries=max_retries,
                source="cli.schedule",
            )
        else:
            job = manager.add_once_job(
                target_session_id,
                text,
                delay_seconds=delay_seconds or 0.0,
                run_at=_parse_run_at(at),
                requested_skill_name=requested_skill_name,
                retry_enabled=retry_enabled,
                retry_delay_seconds=retry_delay_seconds,
                retry_backoff_multiplier=retry_backoff_multiplier,
                max_retries=max_retries,
                source="cli.schedule",
            )
        print_info(f"Created job {job['job_id']} for session {job['session_id']}")
    finally:
        manager.close()


@schedule_app.command("list")
def schedule_list_command() -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        jobs = manager.list_jobs()
        if not jobs:
            print_info("No scheduled jobs.")
            return
        for job in jobs:
            if job["schedule_type"] == "cron":
                schedule_desc = f"cron={job['cron_expression']}"
            else:
                run_at = datetime.fromtimestamp(job["next_run_at"]).astimezone().isoformat()
                schedule_desc = f"run_at={run_at}"
            status_bits = ["enabled" if job["enabled"] else "disabled", schedule_desc]
            if job.get("retry_enabled"):
                status_bits.append(
                    f"retry={job.get('retry_count', 0)}/{job.get('max_retries', 0)} "
                    f"delay={job.get('retry_delay_seconds', 0):.0f}s "
                    f"x{job.get('retry_backoff_multiplier', 1.0):.2f}"
                )
            if job.get("last_error"):
                status_bits.append(f"last_error={job['last_error']}")
            print_info(f"{job['job_id']}  session={job['session_id']}  {'  '.join(status_bits)}  text={job['text']}")
    finally:
        manager.close()


@schedule_app.command("remove")
def schedule_remove_command(job_id: str = typer.Argument(..., help="Job id to remove.")) -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        removed = manager.remove_job(job_id)
        if not removed:
            raise typer.BadParameter(f"Job '{job_id}' was not found.")
        print_info(f"Removed job {job_id}")
    finally:
        manager.close()


@schedule_app.command("pause")
def schedule_pause_command(job_id: str = typer.Argument(..., help="Job id to pause.")) -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        job = manager.pause_job(job_id)
        print_info(f"Paused job {job['job_id']}")
    except KeyError:
        raise typer.BadParameter(f"Job '{job_id}' was not found.")
    finally:
        manager.close()


@schedule_app.command("resume")
def schedule_resume_command(job_id: str = typer.Argument(..., help="Job id to resume.")) -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        job = manager.resume_job(job_id)
        print_info(f"Resumed job {job['job_id']}")
    except KeyError:
        raise typer.BadParameter(f"Job '{job_id}' was not found.")
    finally:
        manager.close()


@schedule_app.command("update")
def schedule_update_command(
    job_id: str = typer.Argument(..., help="Job id to update."),
    text: Optional[str] = typer.Option(None, help="Updated prompt text."),
    cron: Optional[str] = typer.Option(None, "--cron", help="Replace the schedule with a cron expression."),
    delay_seconds: Optional[float] = typer.Option(None, "--delay-seconds", help="Replace the schedule with a one-shot delay."),
    at: Optional[str] = typer.Option(None, "--at", help="Replace the schedule with an absolute ISO 8601 time."),
    requested_skill_name: Optional[str] = typer.Option(None, "--skill", help="Updated requested skill."),
    retry_enabled: Optional[bool] = typer.Option(None, "--retry/--no-retry", help="Enable or disable retries."),
    retry_delay_seconds: Optional[float] = typer.Option(None, "--retry-delay-seconds", help="Retry delay in seconds."),
    retry_backoff_multiplier: Optional[float] = typer.Option(None, "--retry-backoff-multiplier", help="Retry backoff multiplier."),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Maximum retries after failure."),
    enable: Optional[bool] = typer.Option(None, "--enable/--disable", help="Enable or disable the job."),
) -> None:
    one_shot_count = int(delay_seconds is not None) + int(at is not None)
    if cron and one_shot_count:
        raise typer.BadParameter("Choose either --cron, --delay-seconds, or --at, not multiple.")
    if one_shot_count > 1:
        raise typer.BadParameter("Choose only one of --delay-seconds or --at.")
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        job = manager.update_job(
            job_id,
            text=text,
            cron_expression=cron,
            delay_seconds=delay_seconds,
            run_at=_parse_run_at(at),
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            enabled=enable,
        )
        print_info(f"Updated job {job['job_id']}")
    except KeyError:
        raise typer.BadParameter(f"Job '{job_id}' was not found.")
    finally:
        manager.close()


@schedule_app.command("history")
def schedule_history_command(
    job_id: Optional[str] = typer.Option(None, help="Optional job id to filter history."),
    limit: int = typer.Option(20, help="Maximum number of history entries to show."),
) -> None:
    cwd = str(Path.cwd())
    manager = _build_manager(cwd)
    try:
        entries = manager.list_job_history(job_id=job_id, limit=limit)
        if not entries:
            print_info("No scheduler history.")
            return
        for entry in entries:
            when = datetime.fromtimestamp(entry["recorded_at"]).astimezone().isoformat()
            parts = [when, entry["job_id"], entry["status"], entry["text"]]
            if entry.get("attempt"):
                parts.append(f"attempt={entry['attempt']}")
            if entry.get("error_text"):
                parts.append(f"error={entry['error_text']}")
            print_info("  ".join(parts))
    finally:
        manager.close()


@schedule_app.command("serve")
def schedule_serve_command(
    poll_interval_seconds: float = typer.Option(1.0, help="Scheduler poll interval in seconds."),
) -> None:
    try:
        asyncio.run(run_scheduler_service(poll_interval_seconds=poll_interval_seconds))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None
