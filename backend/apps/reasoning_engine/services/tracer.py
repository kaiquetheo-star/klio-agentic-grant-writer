"""JSONL + stdout tracer for agent handoffs (no secrets, truncated summaries)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger("apps.reasoning_engine.tracer")

# Truncate portfolio / prompts so we never dump full PII-heavy text into traces.
_SUMMARY_MAX = 400

_SECRET_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "bearer",
    "runtime_token",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize(text: str | None, max_len: int = _SUMMARY_MAX) -> str:
    if not text:
        return ""
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        key_l = str(key).lower()
        if any(h in key_l for h in _SECRET_HINTS):
            safe[key] = "[REDACTED]"
            continue
        if isinstance(value, dict):
            safe[key] = _redact_mapping(value)
        elif isinstance(value, str) and len(value) > _SUMMARY_MAX:
            safe[key] = summarize(value)
        else:
            safe[key] = value
    return safe


class AgentTracer:
    """Writes one JSON object per line to logs/trace-<run_id>.jsonl and stdout."""

    def __init__(self, run_id: UUID | str, logs_dir: Path | None = None) -> None:
        self.run_id = str(run_id)
        if logs_dir is None:
            try:
                from django.conf import settings as dj_settings

                logs_dir = Path(dj_settings.BASE_DIR).resolve().parent / "logs"
            except Exception:  # noqa: BLE001
                logs_dir = Path(__file__).resolve().parents[4] / "logs"
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.logs_dir / f"trace-{self.run_id}.jsonl"
        self.relative_log_path = f"logs/trace-{self.run_id}.jsonl"

    def log(
        self,
        *,
        agent_name: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        target_agent: str | None = None,
        confidence: float | None = None,
        retry_count: int = 0,
        status: str = "ok",
        score: float | None = None,
        provider: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "timestamp": _utcnow_iso(),
            "run_id": self.run_id,
            "agent_name": agent_name,
            "action": action,
            "input_summary": summarize(input_summary),
            "output_summary": summarize(output_summary),
            "target_agent": target_agent,
            "confidence": confidence,
            "retry_count": retry_count,
            "status": status,
        }
        if score is not None:
            event["score"] = score
        if provider is not None:
            event["provider"] = provider
        if extra:
            event["extra"] = _redact_mapping(extra)

        line = json.dumps(event, ensure_ascii=False)
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        print(line, file=sys.stdout, flush=True)
        logger.info(
            "trace run=%s agent=%s action=%s status=%s provider=%s",
            self.run_id,
            agent_name,
            action,
            status,
            provider or "-",
        )
        return event
