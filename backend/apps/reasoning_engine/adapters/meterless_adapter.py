"""Meterless adapter — Markovian orchestration via Nightshift Runtime.

SAMPLE_MODE runs a bounded local Planner→Writer⇄Evaluator loop (no network).
Live mode POSTs to the Nightshift Markovian engine when credentials exist.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from apps.reasoning_engine.agents.evaluator import EvaluatorAgent
from apps.reasoning_engine.agents.planner import PlannerAgent
from apps.reasoning_engine.agents.writer import WriterAgent
from apps.reasoning_engine.services.tracer import AgentTracer, summarize

_DEFAULT_MARKOVIAN_PATH = "/api/v1/engines/markovian/run"
_DEFAULT_TARGET_SCORE = 8.0
_DEFAULT_MAX_REVISIONS = 3

AGENT_SPECS = [
    {
        "name": PlannerAgent.name,
        "role": PlannerAgent.role,
    },
    {
        "name": WriterAgent.name,
        "role": WriterAgent.role,
    },
    {
        "name": EvaluatorAgent.name,
        "role": EvaluatorAgent.role,
    },
]


class MeterlessNotConfigured(RuntimeError):
    """Missing METERLESS_BASE_URL / METERLESS_RUNTIME_TOKEN."""


class MeterlessAuthError(RuntimeError):
    """401/403 from Meterless."""


class MeterlessNotFound(RuntimeError):
    """404 from Meterless."""


class MeterlessTimeout(RuntimeError):
    """HTTP timeout talking to Meterless."""


class MeterlessRuntimeError(RuntimeError):
    """5xx / exhausted retries / malformed response."""


def _is_sample_mode() -> bool:
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeterlessAdapter:
    """Run a bounded multi-agent Markovian loop (local sample or Nightshift)."""

    def __init__(
        self,
        runtime_token: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        markovian_path: str | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.runtime_token = (
            runtime_token
            if runtime_token is not None
            else os.getenv("METERLESS_RUNTIME_TOKEN", "")
        ).strip()
        self.base_url = (
            base_url if base_url is not None else os.getenv("METERLESS_BASE_URL", "")
        ).strip().rstrip("/")
        # Normalize OpenAI-compatible base (.../v1) → host root for markovian path.
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3].rstrip("/")
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("METERLESS_TIMEOUT_SECONDS", "60")
        )
        path = (
            markovian_path
            if markovian_path is not None
            else os.getenv("METERLESS_MARKOVIAN_PATH", _DEFAULT_MARKOVIAN_PATH)
        ).strip()
        if not path.startswith("/"):
            path = "/" + path
        self.markovian_path = path
        self.max_attempts = max(
            1,
            min(
                int(
                    max_attempts
                    if max_attempts is not None
                    else os.getenv("METERLESS_MAX_ATTEMPTS", "2")
                ),
                3,
            ),
        )
        self.app_id = os.getenv("METERLESS_APP_ID", "klio-reasoning-engine").strip()

    def is_configured(self) -> bool:
        return bool(self.base_url and self.runtime_token)

    def run_agent_loop(
        self,
        plan: dict[str, Any],
        context: dict[str, Any],
        options: dict[str, Any] | None = None,
        *,
        tracer: AgentTracer | None = None,
    ) -> dict[str, Any]:
        """Execute Planner→Writer⇄Evaluator Markovian loop.

        options:
          max_revisions, target_score, output_language, grant_id, portfolio_text
        """
        options = dict(options or {})
        options.setdefault("max_revisions", _DEFAULT_MAX_REVISIONS)
        options.setdefault("target_score", _DEFAULT_TARGET_SCORE)
        options["max_revisions"] = max(0, min(int(options["max_revisions"]), 3))
        options["target_score"] = float(options["target_score"])

        if _is_sample_mode():
            if tracer is None:
                raise MeterlessRuntimeError("tracer is required for SAMPLE_MODE loop")
            return self._run_local_sample_loop(plan, context, options, tracer=tracer)

        if not self.is_configured():
            raise MeterlessNotConfigured(
                "Meterless credentials not configured. "
                "Set METERLESS_BASE_URL and METERLESS_RUNTIME_TOKEN, or use SAMPLE_MODE=true."
            )
        return self._call_meterless_nightshift(plan, context, options, tracer=tracer)

    # ------------------------------------------------------------------ sample
    def _run_local_sample_loop(
        self,
        plan: dict[str, Any],
        context: dict[str, Any],
        options: dict[str, Any],
        *,
        tracer: AgentTracer,
    ) -> dict[str, Any]:
        max_revisions = int(options["max_revisions"])
        target_score = float(options["target_score"])
        portfolio_text = str(options.get("portfolio_text") or "")
        output_language = str(options.get("output_language") or plan.get("output_language") or "pt-BR")
        provider = "local_sample"
        iterations: list[dict[str, Any]] = []

        tracer.log(
            agent_name="Orchestrator",
            action="start",
            input_summary=(
                f"provider={provider} max_revisions={max_revisions} "
                f"target_score={target_score} lang={output_language}"
            ),
            output_summary="local Markovian sample loop",
            status="success",
            provider=provider,
        )

        planner = PlannerAgent(tracer)
        writer = WriterAgent(tracer)
        evaluator = EvaluatorAgent(tracer, pass_score=target_score)

        tracer.log(
            agent_name="Orchestrator",
            action="delegate",
            input_summary="handoff to Planner",
            output_summary="build outline",
            target_agent="Planner",
            status="success",
            provider=provider,
        )

        memory_context = options.get("memory_context") or {}
        # Merge memory into grant context so Writer/Evaluator Markovian entries see it.
        if memory_context:
            context = {
                **context,
                "confirmed_facts": memory_context.get("confirmed_facts") or {},
                "unconfirmed_facts": memory_context.get("unconfirmed_facts") or {},
                "pending_questions": memory_context.get("pending_questions") or [],
            }
        outline = planner.run(
            {
                "portfolio_text": portfolio_text,
                "output_language": output_language,
                "sample_mode": True,
                "provider": provider,
                "memory_context": memory_context,
                **(plan or {}),
            },
            context,
        )
        if memory_context:
            outline["confirmed_facts"] = memory_context.get("confirmed_facts") or outline.get(
                "confirmed_facts"
            ) or {}
            outline["unconfirmed_facts"] = memory_context.get("unconfirmed_facts") or {}
            outline["pending_questions"] = memory_context.get("pending_questions") or []
        iterations.append(
            {
                "agent": "Planner",
                "action": "plan",
                "timestamp": _utcnow_iso(),
                "output_summary": summarize(str(outline.get("sections")), 120),
            }
        )

        tracer.log(
            agent_name="Orchestrator",
            action="handoff",
            input_summary="Planner→Writer",
            output_summary=summarize(str(outline.get("angles")), 160),
            target_agent="Writer",
            status="success",
            provider=provider,
        )

        critique: str | None = None
        draft = ""
        score = 0.0
        revisions = 0
        status = "needs_review"

        # attempt 0 = initial draft; up to max_revisions revisions afterward.
        for attempt in range(max_revisions + 1):
            draft = writer.run(
                outline,
                context,
                critique=critique,
                revision=attempt,
                portfolio_text=portfolio_text,
                provider=provider,
            )
            iterations.append(
                {
                    "agent": "Writer",
                    "action": "draft" if attempt == 0 else "revise",
                    "revision": attempt,
                    "timestamp": _utcnow_iso(),
                    "output_summary": summarize(draft, 160),
                }
            )

            tracer.log(
                agent_name="Orchestrator",
                action="handoff",
                input_summary=f"Writer→Evaluator revision={attempt}",
                output_summary=summarize(draft, 160),
                target_agent="Evaluator",
                retry_count=attempt,
                status="success",
                provider=provider,
            )

            evaluation = evaluator.run(
                draft,
                outline.get("rubric") or context.get("rubric"),
                plan=outline,
                grant_context=context,
                revision=attempt,
                sample_mode=True,
                provider=provider,
                target_score=target_score,
            )
            score = float(evaluation["score"])
            critique = str(evaluation["critique"])
            iterations.append(
                {
                    "agent": "Evaluator",
                    "action": "evaluate",
                    "score": score,
                    "critique": summarize(critique, 200),
                    "timestamp": _utcnow_iso(),
                }
            )

            if not evaluation.get("needs_revision", True):
                revisions = attempt
                status = "success"
                tracer.log(
                    agent_name="Orchestrator",
                    action="stop",
                    input_summary=f"accepted score={score} revisions={revisions}",
                    output_summary="target reached",
                    status="success",
                    score=score,
                    retry_count=revisions,
                    provider=provider,
                )
                break

            if attempt >= max_revisions:
                revisions = max_revisions
                status = "needs_review"
                tracer.log(
                    agent_name="Orchestrator",
                    action="stop",
                    input_summary=f"max_revisions={max_revisions} score={score}",
                    output_summary=summarize(critique, 200),
                    status="needs_revision",
                    score=score,
                    retry_count=revisions,
                    provider=provider,
                )
                break

            revisions = attempt + 1
            tracer.log(
                agent_name="Orchestrator",
                action="handoff",
                input_summary=f"Evaluator→Writer critique revision={revisions}",
                output_summary=summarize(critique, 200),
                target_agent="Writer",
                retry_count=revisions,
                status="needs_revision",
                score=score,
                provider=provider,
            )

        return {
            "run_id": tracer.run_id,
            "status": status,
            "mode": "sample",
            "provider": provider,
            "iterations": iterations,
            "final_output": {
                "draft": draft,
                "score": score,
                "critique": critique or "",
                "revisions": revisions,
                "language": output_language,
            },
            "agents": AGENT_SPECS,
            "outline": outline,
        }

    # ------------------------------------------------------------------ live
    def _call_meterless_nightshift(
        self,
        plan: dict[str, Any],
        context: dict[str, Any],
        options: dict[str, Any],
        *,
        tracer: AgentTracer | None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{self.markovian_path}"
        model = os.getenv("NEMOTRON_MODEL", "").strip() or "meterless/deep"
        portfolio_text = str(options.get("portfolio_text") or "")
        safe_context = {
            k: v
            for k, v in context.items()
            if k not in {"portfolio_text", "user_portfolio_text"}
        }
        payload = {
            "engine": "markovian",
            "plan": plan,
            "context": {
                **safe_context,
                "portfolio_summary": summarize(portfolio_text, 400),
            },
            "agents": AGENT_SPECS,
            "config": {
                "max_revisions": int(options["max_revisions"]),
                "target_score": float(options["target_score"]),
                "timeout_per_step_seconds": int(
                    os.getenv("METERLESS_STEP_TIMEOUT_SECONDS", "90")
                ),
                "model": model,
                "output_language": options.get("output_language") or plan.get("output_language"),
                "grant_id": options.get("grant_id") or context.get("grant_id"),
            },
        }
        headers = {
            "Authorization": f"Bearer {self.runtime_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
            "meterless-app": self.app_id,
            "meterless-scope": "user",
        }

        if tracer:
            tracer.log(
                agent_name="MeterlessAdapter",
                action="delegate",
                input_summary=summarize(
                    f"POST {url} engine=markovian grant={payload['config'].get('grant_id')}",
                    400,
                ),
                output_summary="calling Nightshift Markovian",
                status="success",
                provider="meterless",
                extra={"endpoint": url, "max_revisions": payload["config"]["max_revisions"]},
            )

        body = self._post_with_retries(url, headers, payload, tracer=tracer)
        mapped = self._map_nightshift_response(body, options=options)

        if tracer:
            target = float(options["target_score"])
            for step in mapped.get("iterations") or []:
                score_val = step.get("score")
                if step.get("action") == "evaluate" and score_val is not None:
                    step_status = (
                        "success" if float(score_val) >= target else "needs_revision"
                    )
                else:
                    step_status = "success"
                tracer.log(
                    agent_name=str(step.get("agent") or "MeterlessAdapter"),
                    action=str(step.get("action") or "handoff"),
                    input_summary=summarize(
                        str(step.get("critique") or step.get("input_summary") or ""),
                        200,
                    ),
                    output_summary=summarize(
                        str(step.get("output_summary") or step.get("output") or ""),
                        400,
                    ),
                    target_agent=step.get("target_agent"),
                    retry_count=int(step.get("revision") or step.get("retry_count") or 0),
                    status=step_status,
                    score=float(score_val) if score_val is not None else None,
                    provider="meterless",
                )
            tracer.log(
                agent_name="MeterlessAdapter",
                action="stop",
                input_summary=f"status={mapped.get('status')}",
                output_summary=summarize(
                    f"score={mapped['final_output'].get('score')} "
                    f"revisions={mapped['final_output'].get('revisions')}",
                    200,
                ),
                status="success" if mapped.get("status") == "success" else mapped.get("status", "error"),
                score=mapped["final_output"].get("score"),
                provider="meterless",
            )
        return mapped

    def _post_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        tracer: AgentTracer | None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if tracer:
                    tracer.log(
                        agent_name="MeterlessAdapter",
                        action="error",
                        input_summary=summarize(url, 200),
                        output_summary="Meterless query timeout",
                        status="error",
                        retry_count=attempt - 1,
                        provider="meterless",
                        extra={"execution_time_ms": elapsed_ms},
                    )
                raise MeterlessTimeout(
                    f"Meterless timeout after {self.timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise MeterlessRuntimeError(f"Meterless transport error: {exc}") from exc
                time.sleep(0.4 * attempt)
                continue

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            status = response.status_code

            if status in (401, 403):
                if tracer:
                    tracer.log(
                        agent_name="MeterlessAdapter",
                        action="error",
                        input_summary=summarize(url, 200),
                        output_summary="Meterless authentication failed",
                        status="error",
                        provider="meterless",
                        extra={"http_status": status, "execution_time_ms": elapsed_ms},
                    )
                raise MeterlessAuthError("Meterless authentication failed")

            if status == 404:
                if tracer:
                    tracer.log(
                        agent_name="MeterlessAdapter",
                        action="error",
                        input_summary=summarize(url, 200),
                        output_summary="Meterless Markovian endpoint not found",
                        status="error",
                        provider="meterless",
                        extra={"http_status": status, "execution_time_ms": elapsed_ms},
                    )
                raise MeterlessNotFound("Meterless Markovian endpoint not found")

            if status >= 500:
                if tracer:
                    tracer.log(
                        agent_name="MeterlessAdapter",
                        action="error",
                        input_summary=summarize(url, 200),
                        output_summary=f"Meterless server error ({status})",
                        status="error",
                        retry_count=attempt - 1,
                        provider="meterless",
                        extra={
                            "http_status": status,
                            "execution_time_ms": elapsed_ms,
                            "will_retry": attempt < self.max_attempts,
                        },
                    )
                last_error = MeterlessRuntimeError(f"Meterless server error ({status})")
                if attempt >= self.max_attempts:
                    raise last_error
                time.sleep(0.5 * attempt)
                continue

            if status != 200:
                raise MeterlessRuntimeError(
                    f"Meterless unexpected status {status}: {summarize(response.text, 180)}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise MeterlessRuntimeError("Meterless returned non-JSON body") from exc

        raise MeterlessRuntimeError(f"Meterless failed after retries: {last_error}")

    def _map_nightshift_response(
        self, body: dict[str, Any], *, options: dict[str, Any]
    ) -> dict[str, Any]:
        final = body.get("final_output") or body.get("output") or {}
        draft = str(final.get("draft") or body.get("draft") or "")
        score = float(final.get("score") or body.get("score") or 0)
        revisions = int(final.get("revisions") or body.get("revisions") or 0)
        critique = str(final.get("critique") or body.get("critique") or "")
        status = str(body.get("status") or "success").lower()
        if status not in {"success", "needs_review", "error"}:
            status = "success" if score >= float(options["target_score"]) else "needs_review"

        iterations_in = body.get("iterations") or body.get("traces") or []
        iterations: list[dict[str, Any]] = []
        for item in iterations_in:
            if not isinstance(item, dict):
                continue
            iterations.append(
                {
                    "agent": item.get("agent") or item.get("name"),
                    "action": item.get("action") or item.get("type"),
                    "score": item.get("score"),
                    "critique": item.get("critique"),
                    "revision": item.get("revision") or item.get("retry_count"),
                    "output_summary": summarize(
                        str(item.get("output") or item.get("output_summary") or ""),
                        200,
                    ),
                    "timestamp": item.get("timestamp") or _utcnow_iso(),
                }
            )

        return {
            "run_id": str(body.get("run_id") or uuid.uuid4()),
            "status": status,
            "mode": "live",
            "provider": "meterless",
            "iterations": iterations,
            "final_output": {
                "draft": draft,
                "score": score,
                "critique": critique,
                "revisions": min(max(revisions, 0), 3),
                "language": options.get("output_language") or "pt-BR",
            },
            "agents": body.get("agents") or AGENT_SPECS,
            "raw_traces": body.get("traces") or [],
        }
