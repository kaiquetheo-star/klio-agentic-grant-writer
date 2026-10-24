"""Planner agent — structures the grant-writing plan from context + portfolio."""

from __future__ import annotations

from typing import Any

from apps.reasoning_engine.services.tracer import AgentTracer, summarize


class PlannerAgent:
    name = "Planner"
    role = "Analisa o edital e o portfólio; define seções, ângulos e critérios a cobrir."

    def __init__(self, tracer: AgentTracer) -> None:
        self.tracer = tracer

    def run(self, plan_input: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Markovian-friendly entry: build outline from seed plan + grant context."""
        memory_context = plan_input.get("memory_context") or {
            "confirmed_facts": context.get("confirmed_facts") or {},
            "unconfirmed_facts": context.get("unconfirmed_facts") or {},
            "pending_questions": context.get("pending_questions") or [],
            "project_id": context.get("memory_project_id"),
        }
        return self.build_plan(
            grant_context=context,
            user_portfolio_text=str(
                plan_input.get("portfolio_text")
                or plan_input.get("user_portfolio_text")
                or context.get("portfolio_text")
                or ""
            ),
            output_language=str(
                plan_input.get("output_language")
                or context.get("output_language")
                or "pt-BR"
            ),
            sample_mode=bool(
                plan_input.get("sample_mode", context.get("is_sample", False))
            ),
            provider=str(plan_input.get("provider") or "local_sample"),
            memory_context=memory_context,
        )

    def build_plan(
        self,
        *,
        grant_context: dict[str, Any],
        user_portfolio_text: str,
        output_language: str,
        sample_mode: bool,
        provider: str = "local_sample",
        memory_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = list(
            grant_context.get("requirements")
            or grant_context.get("eligibility_rules")
            or []
        )
        criteria = list(grant_context.get("evaluation_criteria") or [])
        if not criteria and grant_context.get("rubric"):
            criteria = [
                str(item.get("criterion") or "").strip()
                for item in grant_context["rubric"]
                if isinstance(item, dict) and item.get("criterion")
            ]
        sections = [
            "apresentacao_proponente",
            "objeto_e_justificativa",
            "metodologia_e_circulacao",
            "acessibilidade_e_impacto",
            "cronograma_e_orcamento_resumo",
        ]
        memory_context = memory_context or {}
        confirmed = memory_context.get("confirmed_facts") or {}
        pending = list(memory_context.get("pending_questions") or [])
        # Deterministic angles derived from portfolio tokens + confirmed fact keys.
        portfolio_tokens = [
            w.lower()
            for w in (user_portfolio_text or "").replace(",", " ").split()
            if len(w) >= 5
        ][:8]
        fact_tokens = [
            str(k).split(".")[-1].lower()
            for k in list(confirmed.keys())[:6]
            if str(k).strip()
        ]
        angles = (portfolio_tokens[:3] + fact_tokens[:2]) or [
            "trajetoria",
            "circulacao",
            "acessibilidade",
        ]

        plan: dict[str, Any] = {
            "agent": self.name,
            "grant_id": grant_context.get("grant_id"),
            "title": grant_context.get("title"),
            "description": grant_context.get("description"),
            "output_language": output_language,
            "sections": sections,
            "angles": angles,
            "must_cover_requirements": requirements,
            "evaluation_criteria": criteria,
            "rubric": list(grant_context.get("rubric") or []),
            "budget_limits": grant_context.get("budget_limits") or {},
            "deadlines": grant_context.get("deadlines") or {},
            "portfolio_summary": summarize(user_portfolio_text, 200),
            "confirmed_facts": confirmed,
            "pending_questions": pending,
            "memory_project_id": memory_context.get("project_id"),
            "is_sample": bool(sample_mode or grant_context.get("is_sample")),
            "instructions": (
                "Cobrir cada requisito do edital; amarrar evidências do portfólio; "
                "manter tom profissional e verificável; não inventar prêmios ou datas. "
                "Usar APENAS fatos com confirmed_by_user=True como verdade; "
                "fatos não confirmados viram [PENDENTE], nunca afirmação."
            ),
        }
        if sample_mode:
            plan["sample_banner"] = grant_context.get("sample_banner")

        self.tracer.log(
            agent_name=self.name,
            action="plan",
            input_summary=(
                f"grant={grant_context.get('grant_id')} lang={output_language} "
                f"sample={sample_mode} portfolio={summarize(user_portfolio_text, 120)} "
                f"confirmed_facts={len(confirmed)} pending={len(pending)}"
            ),
            output_summary=(
                f"sections={len(sections)} requirements={len(requirements)} "
                f"angles={angles}"
            ),
            target_agent="Writer",
            confidence=0.85 if requirements else 0.6,
            status="success",
            provider=provider,
        )
        return plan
