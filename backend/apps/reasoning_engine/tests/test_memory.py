"""Persistent memory tests — SAMPLE_MODE / local backend (no production Postgres)."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.reasoning_engine.services.memory_service import (
    MemoryService,
    clear_in_memory_store,
)


_PROVIDER_ENV_KEYS = (
    "ZETARIS_API_KEY",
    "ZETARIS_BASE_URL",
    "METERLESS_RUNTIME_TOKEN",
    "METERLESS_BASE_URL",
    "NVIDIA_API_KEY",
)


class _NoNetworkHttpxClient:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("httpx.Client must not be used in memory SAMPLE tests")


@override_settings(REASONING_ENGINE_ENABLED=True, REASONING_ENGINE_MAX_REVISIONS=3)
class MemoryPersistenceTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_in_memory_store()
        self._prev = {k: os.environ.pop(k, None) for k in _PROVIDER_ENV_KEYS}
        self._prev.update(
            {
                "SAMPLE_MODE": os.environ.get("SAMPLE_MODE"),
                "REASONING_ENGINE_ENABLED": os.environ.get("REASONING_ENGINE_ENABLED"),
                "REASONING_ENGINE_MEMORY_BACKEND": os.environ.get(
                    "REASONING_ENGINE_MEMORY_BACKEND"
                ),
                "REASONING_ENGINE_MEMORY_MAX_CHARS": os.environ.get(
                    "REASONING_ENGINE_MEMORY_MAX_CHARS"
                ),
            }
        )
        os.environ["SAMPLE_MODE"] = "true"
        os.environ["REASONING_ENGINE_ENABLED"] = "true"
        os.environ["REASONING_ENGINE_MEMORY_BACKEND"] = "memory"
        self._httpx = patch("httpx.Client", _NoNetworkHttpxClient)
        self._httpx.start()

    def tearDown(self) -> None:
        self._httpx.stop()
        clear_in_memory_store()
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_memory_persists_across_sessions(self) -> None:
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        owner = "opaque-owner-abc123"
        grant = "sample-grant-cultura-sp"

        first = ReasoningOrchestrator().run(
            grant_id=grant,
            user_portfolio_text=(
                "Coletivo Aurora com circulação cultural e acessibilidade LIBRAS."
            ),
            output_language="pt-BR",
            owner_key=owner,
            confirmed_facts={"coletivo.anos_atuacao": "5"},
        )
        self.assertIn(first["status"], ("success", "needs_review"))
        self.assertTrue(first.get("project_id"))
        self.assertEqual(
            str((first.get("memory") or {}).get("confirmed_facts", {}).get("coletivo.anos_atuacao")),
            "5",
        )

        # New orchestrator instance, no portfolio — resume only.
        second = ReasoningOrchestrator().run(
            grant_id=grant,
            user_portfolio_text="",
            output_language="pt-BR",
            owner_key=owner,
            resume_session=True,
        )
        self.assertIn(second["status"], ("success", "needs_review"))
        confirmed = (second.get("memory") or {}).get("confirmed_facts") or {}
        self.assertEqual(str(confirmed.get("coletivo.anos_atuacao")), "5")
        # Draft should still be produced from recalled context.
        self.assertTrue((second.get("output") or {}).get("draft"))

    def test_unconfirmed_fact_not_asserted(self) -> None:
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        owner = "opaque-owner-unconfirmed"
        grant = "sample-grant-cultura-sp"
        memory = MemoryService(backend="memory")
        project = memory.get_or_create_project(owner, grant)
        memory.upsert_fact(
            project,
            "premio.nacional_inventado",
            "Prêmio Nacional Inventado XYZ-999",
            source="agent_inference",
            confidence=0.3,
            confirmed=False,
        )

        result = ReasoningOrchestrator().run(
            grant_id=grant,
            user_portfolio_text="Coletivo de teatro com circulação em três municípios.",
            output_language="pt-BR",
            owner_key=owner,
            resume_session=True,
        )
        draft = (result.get("output") or {}).get("draft") or ""
        self.assertIn("[PENDENTE", draft)
        # Must not appear as a confirmed-fact assertion line.
        for line in draft.splitlines():
            if "Prêmio Nacional Inventado XYZ-999" in line:
                self.assertIn("[PENDENTE", line.upper().replace("pendente", "PENDENTE") or line)
                self.assertTrue(
                    "[PENDENTE" in line or "pendente" in line.lower(),
                    f"unconfirmed award asserted outside pending marker: {line}",
                )
        # Stronger: confirmed-facts section must not list the award value.
        if "## Fatos confirmados" in draft:
            confirmed_section = draft.split("## Fatos confirmados", 1)[1].split("## ", 1)[0]
            self.assertNotIn("Prêmio Nacional Inventado XYZ-999", confirmed_section)

    def test_contradiction_detected(self) -> None:
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        owner = "opaque-owner-contradiction"
        grant = "sample-grant-cultura-sp"
        memory = MemoryService(backend="memory")
        project = memory.get_or_create_project(owner, grant)
        memory.upsert_fact(
            project,
            "coletivo.anos_atuacao",
            "5",
            source="user_input",
            confidence=1.0,
            confirmed=True,
        )

        result = ReasoningOrchestrator().run(
            grant_id=grant,
            user_portfolio_text=(
                "Nosso coletivo atua há 10 anos em circulação cultural no interior."
            ),
            output_language="pt-BR",
            owner_key=owner,
            resume_session=True,
        )
        critique = ((result.get("output") or {}).get("critique") or "").lower()
        self.assertIn("contradi", critique)
        # Loop should surface the conflict (needs_review or critique flag).
        self.assertTrue(
            result.get("status") in ("needs_review", "success")
            or "contradi" in critique
        )

    def test_bounded_context(self) -> None:
        memory = MemoryService(backend="memory", max_recall_chars=400)
        project = memory.get_or_create_project("opaque-bound", "sample-grant-cultura-sp")
        # Seed many facts; confirmed ones must win priority.
        for i in range(200):
            memory.upsert_fact(
                project,
                f"noise.fact_{i:03d}",
                f"valor_ruido_{i}_" + ("x" * 40),
                source="agent_inference",
                confidence=0.2,
                confirmed=False,
            )
        memory.upsert_fact(
            project,
            "coletivo.anos_atuacao",
            "5",
            source="user_input",
            confidence=1.0,
            confirmed=True,
        )
        memory.upsert_fact(
            project,
            "projeto.linguagem",
            "teatro",
            source="user_input",
            confidence=1.0,
            confirmed=True,
        )

        recall = memory.recall_facts(project, max_tokens=400)
        self.assertLessEqual(int(recall["chars_used"]), 400)
        self.assertIn("coletivo.anos_atuacao", recall["confirmed_facts"])
        self.assertIn("projeto.linguagem", recall["confirmed_facts"])
        self.assertGreater(len(recall["facts_dropped"]), 0)
        # Confirmed keys must be included before noise fills the budget.
        self.assertIn("coletivo.anos_atuacao", recall["facts_included"])

    def test_sample_mode_no_postgres(self) -> None:
        """SAMPLE_MODE + memory backend runs without external DB / network."""
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        # Ensure we are not pointing at a postgres URL in this process.
        os.environ.pop("DATABASE_URL", None)
        os.environ["REASONING_ENGINE_MEMORY_BACKEND"] = "memory"

        result = ReasoningOrchestrator().run(
            grant_id="sample-grant-cultura-sp",
            user_portfolio_text=(
                "Coletivo offline: teatro, acessibilidade e circulação em 3 municípios."
            ),
            output_language="pt-BR",
            owner_key="opaque-sample-no-pg",
        )
        self.assertEqual(result.get("mode"), "sample")
        self.assertIn(result.get("status"), ("success", "needs_review"))
        self.assertTrue((result.get("output") or {}).get("draft"))
        self.assertEqual((result.get("memory") or {}).get("backend"), "memory")

    def test_sensitive_payload_rejected(self) -> None:
        memory = MemoryService(backend="memory")
        project = memory.get_or_create_project("opaque-safe", "sample-grant-cultura-sp")
        rejected = memory.upsert_fact(
            project,
            "contato.email",
            "pessoa@example.com",
            source="user_input",
            confidence=1.0,
            confirmed=True,
        )
        self.assertIsNone(rejected)
        recall = memory.recall_facts(project)
        self.assertNotIn("contato.email", recall["facts_included"])
