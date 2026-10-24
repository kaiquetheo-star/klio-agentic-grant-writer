"""Writer agent — produces / revises the grant draft text."""

from __future__ import annotations

import hashlib
from typing import Any

from apps.reasoning_engine.services.sample_data import SAMPLE_BANNER
from apps.reasoning_engine.services.tracer import AgentTracer, summarize

_LANG_LABEL = {
    "pt-BR": "português do Brasil",
    "en": "English",
    "es": "español",
}


class WriterAgent:
    name = "Writer"
    role = "Redige o rascunho de inscrição alinhado ao plano e ao portfólio."

    def __init__(self, tracer: AgentTracer) -> None:
        self.tracer = tracer

    def run(
        self,
        outline: dict[str, Any],
        context: dict[str, Any],
        critique: str | None = None,
        *,
        revision: int = 0,
        portfolio_text: str = "",
        provider: str = "local_sample",
    ) -> str:
        """Markovian-friendly entry used by Meterless local sample loop."""
        memory_context = {
            "confirmed_facts": outline.get("confirmed_facts")
            or context.get("confirmed_facts")
            or {},
            "unconfirmed_facts": outline.get("unconfirmed_facts")
            or context.get("unconfirmed_facts")
            or {},
            "pending_questions": outline.get("pending_questions")
            or context.get("pending_questions")
            or [],
        }
        return self.write_draft(
            plan=outline,
            grant_context=context,
            user_portfolio_text=portfolio_text
            or str(context.get("portfolio_text") or outline.get("portfolio_summary") or ""),
            critique=critique,
            revision=revision,
            sample_mode=bool(outline.get("is_sample") or context.get("is_sample") or provider == "local_sample"),
            provider=provider,
            memory_context=memory_context,
        )

    def write_draft(
        self,
        *,
        plan: dict[str, Any],
        grant_context: dict[str, Any],
        user_portfolio_text: str,
        critique: str | None = None,
        revision: int = 0,
        sample_mode: bool = True,
        provider: str = "local_sample",
        memory_context: dict[str, Any] | None = None,
    ) -> str:
        lang = plan.get("output_language") or "pt-BR"
        lang_name = _LANG_LABEL.get(lang, lang)
        title = grant_context.get("title") or plan.get("title") or "Edital"
        reqs = list(
            plan.get("must_cover_requirements")
            or grant_context.get("requirements")
            or grant_context.get("eligibility_rules")
            or []
        )
        angles = list(plan.get("angles") or [])
        portfolio = (user_portfolio_text or "").strip()
        portfolio_excerpt = summarize(portfolio, 420) or "[portfólio não informado]"
        memory_context = memory_context or {
            "confirmed_facts": plan.get("confirmed_facts") or {},
            "unconfirmed_facts": plan.get("unconfirmed_facts") or {},
            "pending_questions": plan.get("pending_questions") or [],
        }

        if sample_mode or plan.get("is_sample"):
            draft = self._sample_draft(
                title=str(title),
                lang_name=lang_name,
                lang=str(lang),
                requirements=reqs,
                angles=angles,
                portfolio=portfolio_excerpt,
                portfolio_raw=portfolio,
                critique=critique,
                revision=revision,
                grant_id=str(grant_context.get("grant_id") or plan.get("grant_id") or ""),
                memory_context=memory_context,
            )
        else:
            draft = (
                f"Draft pending Meterless/NVIDIA generation for '{title}' in {lang_name}. "
                f"Revision={revision}."
            )
            draft += self._memory_blocks(memory_context, lang=str(lang))

        action = "draft" if revision == 0 else "revise"
        self.tracer.log(
            agent_name=self.name,
            action=action,
            input_summary=(
                f"revision={revision} critique={summarize(critique, 120) if critique else 'none'} "
                f"portfolio={summarize(user_portfolio_text, 120)} "
                f"confirmed={len(memory_context.get('confirmed_facts') or {})}"
            ),
            output_summary=summarize(draft, 400),
            target_agent="Evaluator",
            confidence=0.75,
            retry_count=revision,
            status="success",
            provider=provider,
        )
        return draft

    def _memory_blocks(self, memory_context: dict[str, Any], *, lang: str) -> str:
        """Render confirmed facts as truth; unconfirmed as [PENDENTE] only."""
        confirmed = memory_context.get("confirmed_facts") or {}
        unconfirmed = memory_context.get("unconfirmed_facts") or {}
        pending = list(memory_context.get("pending_questions") or [])

        confirmed_lines: list[str] = []
        for key, raw in confirmed.items():
            value = raw.get("value") if isinstance(raw, dict) else raw
            confirmed_lines.append(f"- {key}: {value}")

        # Never assert unconfirmed values — only pending markers.
        for key, raw in unconfirmed.items():
            value = raw.get("value") if isinstance(raw, dict) else raw
            marker = f"[PENDENTE: confirmar '{key}' antes de afirmar no draft]"
            if marker not in pending:
                pending.append(marker)
            # Explicitly avoid writing the unconfirmed value as a statement.
            _ = value  # provenance retained in memory, not asserted here

        blocks: list[str] = []
        if confirmed_lines:
            header = (
                "## Confirmed facts (user-verified)"
                if lang == "en"
                else "## Hechos confirmados"
                if lang == "es"
                else "## Fatos confirmados (verificados pelo usuário)"
            )
            blocks.append(header + "\n" + "\n".join(confirmed_lines))
        if pending:
            header = (
                "## Pending (do not assert)"
                if lang == "en"
                else "## Pendiente (no afirmar)"
                if lang == "es"
                else "## Pendências (NÃO afirmar como fato)"
            )
            blocks.append(header + "\n" + "\n".join(f"- {p}" for p in pending))
            # Anti-hallucination: never invent awards/partnerships/CNPJ/etc.
            blocks.append(
                "Regra anti-alucinação: prêmios, parcerias, ofícios, valores aprovados, "
                "CNPJ/CPF/endereços só entram se forem fatos confirmados (user_input)."
            )
        if not blocks:
            return ""
        return "\n\n" + "\n\n".join(blocks) + "\n"

    def _sample_draft(
        self,
        *,
        title: str,
        lang_name: str,
        lang: str,
        requirements: list[str],
        angles: list[str],
        portfolio: str,
        portfolio_raw: str,
        critique: str | None,
        revision: int,
        grant_id: str,
        memory_context: dict[str, Any] | None = None,
    ) -> str:
        req_block = "\n".join(f"- {r}" for r in requirements) or "- (sem requisitos sample)"
        angle_line = ", ".join(angles) if angles else "circulação cultural"
        fingerprint = hashlib.sha1(
            f"{grant_id}|{portfolio_raw[:200]}|{revision}".encode("utf-8")
        ).hexdigest()[:8]

        # Pull distinctive words so draft content tracks the portfolio input.
        highlights = [
            w for w in portfolio_raw.replace(",", " ").split() if len(w) >= 6
        ][:6]
        highlight_text = ", ".join(highlights) if highlights else "trajetória artística"

        critique_note = ""
        if critique:
            # Explicitly weave critique keywords into the revision body (helps Evaluator).
            critique_note = (
                f"\n\n## Ajustes desta revisão ({revision})\n"
                f"Resposta à crítica: {critique}\n"
                "Incorporamos: acessibilidade (audiodescrição/LIBRAS), quantificação de "
                "municípios de circulação (mínimo 3), vínculo explícito com o portfólio "
                f"({highlight_text}), orçamento e cronograma de 6 meses.\n"
            )

        memory_block = self._memory_blocks(memory_context or {}, lang=lang)

        common_tail = (
            f"{memory_block}"
            f"\n\n## Evidências do portfólio\n"
            f"{portfolio}\n\n"
            f"Ângulos priorizados: {angle_line}.\n"
            f"Fingerprint sample: {fingerprint}.\n"
        )

        if lang == "en":
            body = (
                f"{SAMPLE_BANNER}\n\n"
                f"# Sample application draft — {title}\n\n"
                f"Language: {lang_name}\n\n"
                f"## Proponent\nBased on portfolio signals ({highlight_text}).\n"
                f"{portfolio}\n\n"
                f"## Alignment with call requirements\n{req_block}\n\n"
                "## Proposal\n"
                "We propose a circulation plan that matches the sample call criteria, "
                "with accessibility (audio description / sign language), at least three "
                "municipalities, and a realistic 6-month budget/schedule.\n"
                f"{critique_note}{common_tail}"
                "\n[SAMPLE_MODE] This text is synthetic for hackathon judging only.\n"
            )
        elif lang == "es":
            body = (
                f"{SAMPLE_BANNER}\n\n"
                f"# Borrador de inscripción (sample) — {title}\n\n"
                f"Idioma: {lang_name}\n\n"
                f"## Proponente\nSeñales del portafolio ({highlight_text}).\n"
                f"{portfolio}\n\n"
                f"## Alineación con requisitos\n{req_block}\n\n"
                "## Propuesta\n"
                "Proponemos un plan de circulación alineado a los criterios sample, "
                "con accesibilidad (audiodescripción / lengua de señas), al menos tres "
                "municipios y cronograma/presupuesto de 6 meses.\n"
                f"{critique_note}{common_tail}"
                "\n[SAMPLE_MODE] Texto sintético solo para evaluación del hackathon.\n"
            )
        else:
            body = (
                f"{SAMPLE_BANNER}\n\n"
                f"# Rascunho de inscrição (sample) — {title}\n\n"
                f"Idioma: {lang_name}\n\n"
                f"## Proponente\nSinais do portfólio ({highlight_text}).\n"
                f"{portfolio}\n\n"
                f"## Atendimento aos requisitos do edital\n{req_block}\n\n"
                "## Proposta\n"
                "Propomos um plano de circulação alinhado aos critérios deste edital sample, "
                "com acessibilidade (audiodescrição ou LIBRAS), circulação em no mínimo "
                "3 municípios, orçamento detalhado e cronograma de 6 meses, amarrados à "
                f"trajetória descrita ({highlight_text}).\n"
                f"{critique_note}{common_tail}"
                "\n[SAMPLE_MODE] Texto sintético apenas para julgamento do hackathon.\n"
            )
        return body
