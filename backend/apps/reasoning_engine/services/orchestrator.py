"""Bounded multi-agent orchestration for grant draft generation."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from django.conf import settings

from apps.reasoning_engine.adapters.meterless_adapter import (
    AGENT_SPECS,
    MeterlessAdapter,
    MeterlessAuthError,
    MeterlessNotConfigured,
    MeterlessNotFound,
    MeterlessRuntimeError,
    MeterlessTimeout,
)
from apps.reasoning_engine.adapters.nvidia_adapter import (
    NvidiaAdapter,
    NvidiaAuthError,
    NvidiaModelNotFound,
    NvidiaNotConfigured,
    NvidiaRuntimeError,
    NvidiaTimeout,
)
from apps.reasoning_engine.adapters.zetaris_adapter import (
    ZetarisAuthError,
    ZetarisAdapter,
    ZetarisNotConfigured,
    ZetarisNotFound,
    ZetarisQueryError,
    ZetarisTimeout,
)
from apps.reasoning_engine.adapters.hmem_adapter import HMemAdapter
from apps.reasoning_engine.agents.evaluator import EvaluatorAgent
from apps.reasoning_engine.agents.planner import PlannerAgent
from apps.reasoning_engine.agents.writer import WriterAgent
from apps.reasoning_engine.services.memory_service import MemoryService
from apps.reasoning_engine.services.tracer import AgentTracer, summarize

AGENT_CATALOG = list(AGENT_SPECS)


def is_sample_mode() -> bool:
    """SAMPLE_MODE is read only inside reasoning_engine (isolated from radar/prod)."""
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def max_revisions() -> int:
    return min(int(getattr(settings, "REASONING_ENGINE_MAX_REVISIONS", 3) or 3), 3)


def target_score() -> float:
    return float(os.getenv("REASONING_ENGINE_TARGET_SCORE", "8.0") or "8.0")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReasoningOrchestrator:
    """Zetaris context → Meterless Markovian loop, with NVIDIA direct fallback."""

    def run(
        self,
        *,
        grant_id: str,
        user_portfolio_text: str = "",
        output_language: str = "pt-BR",
        owner_key: str | None = None,
        resume_session: bool = False,
        confirmed_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4()
        tracer = AgentTracer(run_id)
        started = time.perf_counter()
        sample = is_sample_mode()
        mode = "sample" if sample else "live"
        owner_key = (owner_key or "").strip() or None
        confirmed_facts = dict(confirmed_facts or {})

        tracer.log(
            agent_name="Orchestrator",
            action="start",
            input_summary=(
                f"grant_id={grant_id} lang={output_language} mode={mode} "
                f"resume={resume_session} owner={summarize(owner_key or '-', 24)} "
                f"portfolio={summarize(user_portfolio_text, 120)}"
            ),
            output_summary="reasoning engine run",
            status="success",
            provider="sample" if sample else "meterless",
            extra={"is_sample_mode": sample, "has_owner_key": bool(owner_key)},
        )

        memory: MemoryService | None = None
        project: Any = None
        memory_context: dict[str, Any] = {}

        try:
            if owner_key:
                memory = MemoryService(tracer=tracer)
                project = memory.get_or_create_project(owner_key, grant_id)
                if user_portfolio_text.strip():
                    memory.ingest_portfolio_excerpt(project, user_portfolio_text)
                for key, value in confirmed_facts.items():
                    memory.upsert_fact(
                        project,
                        str(key),
                        value,
                        source="user_input",
                        confidence=1.0,
                        confirmed=True,
                    )
                if resume_session:
                    HMemAdapter(memory=memory, tracer=tracer).restore_project(
                        owner_key, grant_id
                    )
                memory_context = memory.get_resume_context(project)
                # If resuming without new portfolio, reuse stored excerpt.
                if resume_session and not user_portfolio_text.strip():
                    stored = (memory_context.get("confirmed_facts") or {}).get(
                        "portfolio.excerpt"
                    )
                    if not stored:
                        # portfolio.excerpt is typically unconfirmed — check unconfirmed too
                        unc = memory_context.get("unconfirmed_facts") or {}
                        raw = unc.get("portfolio.excerpt")
                        stored = raw.get("value") if isinstance(raw, dict) else raw
                    if stored:
                        user_portfolio_text = str(stored)

            grant_context = ZetarisAdapter().fetch_grant_context(grant_id, tracer=tracer)
            if memory and project:
                # Persist rubric titles as grant_rubric facts (non-PII).
                for idx, criterion in enumerate(
                    list(grant_context.get("evaluation_criteria") or [])[:8]
                ):
                    memory.upsert_fact(
                        project,
                        f"grant.criterion.{idx}",
                        str(criterion),
                        source="grant_rubric",
                        confidence=0.9,
                        confirmed=False,
                    )
                memory_context = memory.get_resume_context(project)

            seed_plan = PlannerAgent(tracer).build_plan(
                grant_context=grant_context,
                user_portfolio_text=user_portfolio_text,
                output_language=output_language,
                sample_mode=sample,
                provider="sample" if sample else "meterless",
                memory_context=memory_context,
            )
            if memory and project:
                memory.record_decision(
                    project,
                    agent="Planner",
                    action="plan",
                    input_summary=f"grant={grant_id}",
                    output_summary=summarize(str(seed_plan.get("angles")), 160),
                )

            options = {
                "max_revisions": max_revisions(),
                "target_score": target_score(),
                "output_language": output_language,
                "grant_id": grant_id,
                "portfolio_text": user_portfolio_text,
                "memory_context": memory_context,
            }

            try:
                loop_result = MeterlessAdapter().run_agent_loop(
                    seed_plan,
                    grant_context,
                    options,
                    tracer=tracer,
                )
            except (
                MeterlessRuntimeError,
                MeterlessTimeout,
                MeterlessNotFound,
                MeterlessNotConfigured,
            ) as meterless_exc:
                # SAMPLE_MODE never reaches network Meterless failures (local loop).
                if sample:
                    raise
                loop_result = self._maybe_nvidia_fallback(
                    meterless_exc=meterless_exc,
                    seed_plan=seed_plan,
                    grant_context=grant_context,
                    options=options,
                    tracer=tracer,
                )

            final = loop_result.get("final_output") or {}
            elapsed = round(time.perf_counter() - started, 3)
            status = str(loop_result.get("status") or "success")

            if memory and project:
                for step in loop_result.get("iterations") or []:
                    memory.record_decision(
                        project,
                        agent=str(step.get("agent") or "Agent"),
                        action=str(step.get("action") or "step"),
                        input_summary=str(step.get("critique") or ""),
                        output_summary=str(step.get("output_summary") or ""),
                        retry_count=int(step.get("revision") or 0),
                    )
                rev = memory.save_revision(
                    project,
                    content=str(final.get("draft") or ""),
                    score=float(final.get("score") or 0),
                    critique=str(final.get("critique") or ""),
                    evaluator_notes={
                        "status": status,
                        "revisions": int(final.get("revisions") or 0),
                    },
                )
                HMemAdapter(memory=memory, tracer=tracer).sync_project(project)
                memory_context = memory.get_resume_context(project)
                memory_context["saved_version"] = rev.get("version")

            tracer.log(
                agent_name="Orchestrator",
                action="stop",
                input_summary=f"status={status}",
                output_summary=(
                    f"score={final.get('score')} revisions={final.get('revisions')} "
                    f"mode={loop_result.get('mode', mode)}"
                ),
                status="success" if status == "success" else status,
                score=final.get("score"),
                provider=loop_result.get("provider")
                or ("sample" if sample else "meterless"),
            )

            payload = {
                "run_id": str(run_id),
                "status": status,
                "mode": loop_result.get("mode") or mode,
                "output": {
                    "draft": final.get("draft") or "",
                    "score": float(final.get("score") or 0),
                    "critique": final.get("critique") or "",
                    "revisions": int(final.get("revisions") or 0),
                    "language": final.get("language") or output_language,
                },
                "agents": loop_result.get("agents") or AGENT_CATALOG,
                "trace_id": str(run_id),
                "log_file": tracer.relative_log_path,
                "execution_time_seconds": elapsed,
                "iterations": loop_result.get("iterations") or [],
                "sample_mode": sample,
                "provider": loop_result.get("provider"),
            }
            if project is not None:
                payload["project_id"] = str(getattr(project, "id", ""))
                payload["current_version"] = int(
                    getattr(project, "current_version", 0) or 0
                )
                payload["memory"] = {
                    "backend": memory.backend if memory else None,
                    "confirmed_facts": memory_context.get("confirmed_facts") or {},
                    "pending_questions": memory_context.get("pending_questions") or [],
                    "facts_included": memory_context.get("facts_included") or [],
                    "facts_dropped_count": len(memory_context.get("facts_dropped") or []),
                }
            return payload

        except (
            ZetarisNotConfigured,
            ZetarisAuthError,
            ZetarisNotFound,
            ZetarisTimeout,
            ZetarisQueryError,
            MeterlessNotConfigured,
            MeterlessAuthError,
            MeterlessNotFound,
            MeterlessTimeout,
            MeterlessRuntimeError,
            NvidiaNotConfigured,
            NvidiaAuthError,
            NvidiaTimeout,
            NvidiaRuntimeError,
            NvidiaModelNotFound,
        ) as exc:
            return self._error_payload(
                run_id=run_id,
                tracer=tracer,
                started=started,
                sample=sample,
                exc=exc,
            )
        except Exception as exc:  # noqa: BLE001 — structured API error for judges
            tracer.log(
                agent_name="Orchestrator",
                action="error",
                input_summary=type(exc).__name__,
                output_summary=summarize(str(exc), 200),
                status="error",
                provider="sample" if sample else "meterless",
            )
            return self._error_payload(
                run_id=run_id,
                tracer=tracer,
                started=started,
                sample=sample,
                exc=exc,
            )

    def _maybe_nvidia_fallback(
        self,
        *,
        meterless_exc: BaseException,
        seed_plan: dict[str, Any],
        grant_context: dict[str, Any],
        options: dict[str, Any],
        tracer: AgentTracer,
    ) -> dict[str, Any]:
        tracer.log(
            agent_name="Orchestrator",
            action="meterless_fallback",
            input_summary=type(meterless_exc).__name__,
            output_summary=summarize(str(meterless_exc), 240),
            status="needs_revision",
            provider="meterless",
            extra={"reason": str(meterless_exc)},
        )
        nvidia = NvidiaAdapter()
        health = nvidia.health_check(tracer=tracer)
        if health.get("status") != "ok":
            tracer.log(
                agent_name="Orchestrator",
                action="error",
                input_summary="nvidia_fallback_unavailable",
                output_summary=summarize(str(health), 200),
                status="error",
                provider="nvidia_direct",
            )
            raise meterless_exc

        return self._run_with_nvidia_direct_fallback(
            seed_plan=seed_plan,
            grant_context=grant_context,
            options=options,
            tracer=tracer,
            nvidia=nvidia,
        )

    def _run_with_nvidia_direct_fallback(
        self,
        *,
        seed_plan: dict[str, Any],
        grant_context: dict[str, Any],
        options: dict[str, Any],
        tracer: AgentTracer,
        nvidia: NvidiaAdapter,
    ) -> dict[str, Any]:
        """Local Markovian loop using NVIDIA generate_text for Writer/Evaluator."""
        provider = "nvidia_direct"
        max_revs = int(options.get("max_revisions") or 3)
        target = float(options.get("target_score") or 8.0)
        portfolio = str(options.get("portfolio_text") or "")
        language = str(options.get("output_language") or "pt-BR")
        iterations: list[dict[str, Any]] = []

        tracer.log(
            agent_name="Orchestrator",
            action="start",
            input_summary=f"provider={provider} max_revisions={max_revs} target={target}",
            output_summary="NVIDIA direct fallback loop",
            status="success",
            provider=provider,
        )

        outline = seed_plan or PlannerAgent(tracer).build_plan(
            grant_context=grant_context,
            user_portfolio_text=portfolio,
            output_language=language,
            sample_mode=False,
            provider=provider,
        )
        iterations.append(
            {
                "agent": "Planner",
                "action": "plan",
                "timestamp": _utcnow_iso(),
                "output_summary": summarize(str(outline.get("sections")), 120),
            }
        )

        # Prefer NVIDIA draft; if generation fails oddly, fall back to local Writer heuristic.
        writer = WriterAgent(tracer)
        evaluator = EvaluatorAgent(tracer, pass_score=target)
        critique: str | None = None
        draft = ""
        score = 0.0
        revisions = 0
        status = "needs_review"

        for attempt in range(max_revs + 1):
            writer_prompt = self._writer_prompt(
                outline=outline,
                grant_context=grant_context,
                portfolio=portfolio,
                language=language,
                critique=critique,
                revision=attempt,
            )
            try:
                gen = nvidia.generate_text(
                    writer_prompt,
                    options={
                        "language": language,
                        "role": "writer",
                        "temperature": 0.4,
                        "max_tokens": 1400,
                        "revision": attempt,
                    },
                    tracer=tracer,
                )
                draft = str(gen.get("text") or "")
            except (NvidiaRuntimeError, NvidiaTimeout, NvidiaAuthError, NvidiaModelNotFound):
                draft = writer.write_draft(
                    plan=outline,
                    grant_context=grant_context,
                    user_portfolio_text=portfolio,
                    critique=critique,
                    revision=attempt,
                    sample_mode=True,
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

            eval_prompt = self._evaluator_prompt(
                draft=draft,
                outline=outline,
                grant_context=grant_context,
                target=target,
            )
            evaluation: dict[str, Any]
            try:
                gen_eval = nvidia.generate_text(
                    eval_prompt,
                    options={
                        "language": language,
                        "role": "evaluator",
                        "temperature": 0.1,
                        "max_tokens": 400,
                    },
                    tracer=tracer,
                )
                evaluation = self._parse_evaluator_json(str(gen_eval.get("text") or ""))
            except (NvidiaRuntimeError, NvidiaTimeout, NvidiaAuthError, NvidiaModelNotFound):
                evaluation = {}

            if not evaluation:
                evaluation = evaluator.evaluate(
                    draft=draft,
                    plan=outline,
                    grant_context=grant_context,
                    revision=attempt,
                    sample_mode=False,
                    provider=provider,
                    target_score=target,
                )
            else:
                tracer.log(
                    agent_name="Evaluator",
                    action="evaluate",
                    input_summary=summarize(draft, 160),
                    output_summary=summarize(
                        f"score={evaluation.get('score')} critique={evaluation.get('critique')}",
                        200,
                    ),
                    status=(
                        "needs_revision"
                        if evaluation.get("needs_revision")
                        else "success"
                    ),
                    score=float(evaluation.get("score") or 0),
                    retry_count=attempt,
                    provider=provider,
                )

            score = float(evaluation.get("score") or 0)
            critique = str(evaluation.get("critique") or "")
            needs_revision = bool(
                evaluation.get("needs_revision", score < target)
            )
            iterations.append(
                {
                    "agent": "Evaluator",
                    "action": "evaluate",
                    "score": score,
                    "critique": summarize(critique, 200),
                    "timestamp": _utcnow_iso(),
                }
            )

            if not needs_revision:
                revisions = attempt
                status = "success"
                break
            if attempt >= max_revs:
                revisions = max_revs
                status = "needs_review"
                break
            revisions = attempt + 1
            tracer.log(
                agent_name="Orchestrator",
                action="handoff",
                input_summary=f"Evaluator→Writer revision={revisions}",
                output_summary=summarize(critique, 200),
                target_agent="Writer",
                retry_count=revisions,
                status="needs_revision",
                score=score,
                provider=provider,
            )

        tracer.log(
            agent_name="Orchestrator",
            action="stop",
            input_summary=f"nvidia_direct status={status}",
            output_summary=f"score={score} revisions={revisions}",
            status="success" if status == "success" else status,
            score=score,
            provider=provider,
        )

        return {
            "run_id": tracer.run_id,
            "status": status,
            "mode": "live",
            "provider": provider,
            "iterations": iterations,
            "final_output": {
                "draft": draft,
                "score": score,
                "critique": critique or "",
                "revisions": revisions,
                "language": language,
            },
            "agents": AGENT_SPECS,
            "outline": outline,
        }

    @staticmethod
    def _writer_prompt(
        *,
        outline: dict[str, Any],
        grant_context: dict[str, Any],
        portfolio: str,
        language: str,
        critique: str | None,
        revision: int,
    ) -> str:
        return (
            f"Language: {language}\n"
            f"Grant: {grant_context.get('title')}\n"
            f"Description: {summarize(str(grant_context.get('description') or ''), 300)}\n"
            f"Requirements: {outline.get('must_cover_requirements') or grant_context.get('requirements')}\n"
            f"Rubric: {outline.get('evaluation_criteria') or grant_context.get('evaluation_criteria')}\n"
            f"Portfolio summary: {summarize(portfolio, 500)}\n"
            f"Critique: {critique or 'none'}\n"
            f"Revision: {revision}\n"
            "Write a grant application draft. Do not invent fake awards or fake deadlines. "
            "Cover accessibility and circulation when relevant."
        )

    @staticmethod
    def _evaluator_prompt(
        *,
        draft: str,
        outline: dict[str, Any],
        grant_context: dict[str, Any],
        target: float,
    ) -> str:
        return (
            "You are the Evaluator agent. Score the draft from 0 to 10.\n"
            f"Target score: {target}\n"
            f"Requirements: {outline.get('must_cover_requirements') or grant_context.get('requirements')}\n"
            f"Rubric: {outline.get('evaluation_criteria') or grant_context.get('evaluation_criteria')}\n"
            f"Draft:\n{summarize(draft, 1500)}\n\n"
            'Respond ONLY with JSON: '
            '{"score": <float>, "critique": "<string>", "needs_revision": <bool>}'
        )

    @staticmethod
    def _parse_evaluator_json(text: str) -> dict[str, Any]:
        if not text:
            return {}
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        raw = match.group(0) if match else text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        try:
            score = float(data.get("score"))
        except (TypeError, ValueError):
            return {}
        return {
            "score": score,
            "critique": str(data.get("critique") or ""),
            "needs_revision": bool(data.get("needs_revision", score < 8.0)),
            "accepted": not bool(data.get("needs_revision", score < 8.0)),
        }

    def _error_payload(
        self,
        *,
        run_id: uuid.UUID,
        tracer: AgentTracer,
        started: float,
        sample: bool,
        exc: BaseException,
    ) -> dict[str, Any]:
        err_type = type(exc).__name__
        message = str(exc)
        recoverable = err_type in {
            "MeterlessTimeout",
            "MeterlessRuntimeError",
            "ZetarisTimeout",
            "ZetarisQueryError",
            "NvidiaTimeout",
            "NvidiaRuntimeError",
        }
        tracer.log(
            agent_name="Orchestrator",
            action="error",
            input_summary=err_type,
            output_summary=summarize(message, 240),
            status="error",
            provider="sample" if sample else "meterless",
            extra={"recoverable": recoverable},
        )
        return {
            "run_id": str(run_id),
            "status": "error",
            "mode": "sample" if sample else "live",
            "output": {
                "draft": "",
                "score": 0,
                "critique": message,
                "revisions": 0,
                "language": "pt-BR",
            },
            "agents": AGENT_CATALOG,
            "trace_id": str(run_id),
            "log_file": tracer.relative_log_path,
            "execution_time_seconds": round(time.perf_counter() - started, 3),
            "iterations": [],
            "sample_mode": sample,
            "error": {
                "type": err_type,
                "message": message,
                "recoverable": recoverable,
            },
        }
