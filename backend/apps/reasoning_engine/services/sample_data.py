"""Synthetic grant context for SAMPLE_MODE only.

Isolated from the production radar / Edital models. Never persist these rows
into the main database. Always labeled as sample for judges.
"""

from __future__ import annotations

from typing import Any

# Explicit banner so responses never look like production grant data.
SAMPLE_BANNER = (
    "[SAMPLE_MODE] Dados sintéticos para julgamento do Open Agent Hackathon 2026. "
    "Não são editais reais da Klio / radar de produção."
)

_SAMPLE_GRANTS: dict[str, dict[str, Any]] = {
    "sample-grant-cultura-sp": {
        "grant_id": "sample-grant-cultura-sp",
        "title": "[SAMPLE] Edital de Circulação Cultural — Região Sudeste",
        "organizer": "[SAMPLE] Fundação Cultural Demonstrativa",
        "scope": "SP",
        "deadline": "2026-12-15",
        "target_categories": ["teatro", "dança", "música"],
        "funding_ceiling_brl": 80000,
        "requirements": [
            "Projeto com histórico de apresentações nos últimos 24 meses",
            "Plano de circulação com mínimo de 3 municípios",
            "Contrapartida de acessibilidade (audiodescrição ou LIBRAS)",
            "Orçamento detalhado e cronograma de 6 meses",
        ],
        "evaluation_criteria": [
            "Relevância artística e coerência com o portfólio",
            "Viabilidade técnica e financeira",
            "Impacto territorial e acessibilidade",
            "Clareza e objetividade do texto",
        ],
        "url": "https://example.invalid/sample-grant-cultura-sp",
        "is_sample": True,
        "sample_banner": SAMPLE_BANNER,
    },
    "sample-grant-audiovisual-br": {
        "grant_id": "sample-grant-audiovisual-br",
        "title": "[SAMPLE] Apoio à Produção Audiovisual Independente",
        "organizer": "[SAMPLE] Instituto Demo de Audiovisual",
        "scope": "Nacional",
        "deadline": "2026-11-30",
        "target_categories": ["audiovisual"],
        "funding_ceiling_brl": 120000,
        "requirements": [
            "Roteiro ou treatment de até 10 páginas",
            "Equipe mínima com direção e produção",
            "Estratégia de distribuição / exibição",
        ],
        "evaluation_criteria": [
            "Originalidade narrativa",
            "Capacidade de execução",
            "Potencial de circulação",
        ],
        "url": "https://example.invalid/sample-grant-audiovisual-br",
        "is_sample": True,
        "sample_banner": SAMPLE_BANNER,
    },
}


def list_sample_grant_ids() -> list[str]:
    return sorted(_SAMPLE_GRANTS.keys())


def get_sample_grant_context(grant_id: str) -> dict[str, Any]:
    """Return synthetic grant context. Falls back to a generic sample if id unknown."""
    if grant_id in _SAMPLE_GRANTS:
        ctx = dict(_SAMPLE_GRANTS[grant_id])
    else:
        ctx = {
            "grant_id": grant_id or "sample-unknown",
            "title": f"[SAMPLE] Edital genérico ({grant_id or 'sem-id'})",
            "organizer": "[SAMPLE] Organização Demonstrativa",
            "scope": "Nacional",
            "deadline": "2026-12-31",
            "target_categories": ["cultura"],
            "funding_ceiling_brl": 50000,
            "requirements": [
                "Descrição do projeto artístico",
                "Currículo / portfólio da organização",
                "Orçamento simplificado",
            ],
            "evaluation_criteria": [
                "Adequação ao objeto do edital",
                "Qualidade da escrita",
                "Viabilidade",
            ],
            "url": "https://example.invalid/sample-generic",
            "is_sample": True,
            "sample_banner": SAMPLE_BANNER,
            "note": "grant_id não mapeado — usando template sintético genérico",
        }
    ctx["source"] = "reasoning_engine.sample_data"
    ctx["is_sample"] = True
    return ctx


def sample_portfolio_excerpt(user_portfolio_text: str, max_chars: int = 800) -> str:
    text = (user_portfolio_text or "").strip()
    if not text:
        return (
            "[SAMPLE] Coletivo artístico multidisciplinar com trajetória em circulação "
            "cultural, oficinas e apresentações em espaços públicos."
        )
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"
