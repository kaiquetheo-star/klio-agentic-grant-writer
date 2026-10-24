"""Offline audit tests for the Reasoning Engine (no credentials, no network).

Covers SAMPLE_MODE happy path, input variation, bounded revisions, and
structured errors when live credentials are missing.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

# Provider credential env vars that must never leak into offline tests.
_PROVIDER_ENV_KEYS = (
    "ZETARIS_API_KEY",
    "ZETARIS_BASE_URL",
    "METERLESS_RUNTIME_TOKEN",
    "METERLESS_BASE_URL",
    "NVIDIA_API_KEY",
)


def _clear_provider_env() -> dict[str, str | None]:
    """Remove provider credentials from os.environ; return previous values."""
    previous: dict[str, str | None] = {}
    for key in _PROVIDER_ENV_KEYS:
        previous[key] = os.environ.pop(key, None)
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class _NoNetworkHttpxClient:
    """Sentinel Client: any use fails the test (offline guarantee)."""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "httpx.Client must not be constructed in SAMPLE_MODE / offline tests"
        )


@override_settings(REASONING_ENGINE_ENABLED=True, REASONING_ENGINE_MAX_REVISIONS=3)
class SampleModeIsolatedTests(SimpleTestCase):
    """Full SAMPLE_MODE loop without network or Postgres."""

    def setUp(self) -> None:
        self._prev_provider = _clear_provider_env()
        self._prev_sample = os.environ.get("SAMPLE_MODE")
        self._prev_enabled = os.environ.get("REASONING_ENGINE_ENABLED")
        os.environ["SAMPLE_MODE"] = "true"
        os.environ["REASONING_ENGINE_ENABLED"] = "true"
        self._httpx_patcher = patch("httpx.Client", _NoNetworkHttpxClient)
        self._httpx_patcher.start()

    def tearDown(self) -> None:
        self._httpx_patcher.stop()
        _restore_env(self._prev_provider)
        if self._prev_sample is None:
            os.environ.pop("SAMPLE_MODE", None)
        else:
            os.environ["SAMPLE_MODE"] = self._prev_sample
        if self._prev_enabled is None:
            os.environ.pop("REASONING_ENGINE_ENABLED", None)
        else:
            os.environ["REASONING_ENGINE_ENABLED"] = self._prev_enabled

    def _run(
        self,
        *,
        grant_id: str = "sample-grant-cultura-sp",
        portfolio: str = (
            "Coletivo Aurora: teatro de rua, oficinas de acessibilidade, "
            "circulação em Campinas, Santos e Ribeirão Preto desde 2019."
        ),
        language: str = "pt-BR",
    ) -> dict:
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        return ReasoningOrchestrator().run(
            grant_id=grant_id,
            user_portfolio_text=portfolio,
            output_language=language,
        )

    def _repo_log_path(self, relative: str) -> Path:
        return Path(settings.BASE_DIR).resolve().parent / relative

    def test_sample_mode_full_loop_offline(self) -> None:
        result = self._run()

        self.assertIn(result["status"], ("success", "needs_review"))
        self.assertTrue(result.get("run_id"))
        draft = (result.get("output") or {}).get("draft") or ""
        self.assertTrue(draft.strip(), "output.draft must be non-empty")
        agents = result.get("agents") or []
        self.assertGreaterEqual(len(agents), 3)
        score = float((result.get("output") or {}).get("score") or -1)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)
        revisions = int((result.get("output") or {}).get("revisions") or 0)
        self.assertLessEqual(revisions, 3)
        self.assertEqual(result.get("mode"), "sample")

        log_rel = result.get("log_file") or ""
        self.assertTrue(log_rel)
        log_path = self._repo_log_path(log_rel)
        self.assertTrue(log_path.is_file(), f"missing log_file: {log_path}")
        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertGreaterEqual(len(lines), 10)

        actions: list[str] = []
        handoff_or_target = False
        for line in lines:
            event = json.loads(line)  # raises if not valid JSON
            self.assertIsInstance(event, dict)
            actions.append(str(event.get("action") or ""))
            if event.get("action") == "handoff" or event.get("target_agent"):
                handoff_or_target = True

        self.assertIn("evaluate", actions)
        self.assertTrue(handoff_or_target, "expected handoff action or target_agent")

    def test_no_hardcoded_outputs_portfolio_variation(self) -> None:
        portfolios = [
            (
                "Companhia Horizonte Teatro: espetáculo itinerante de palhaçaria "
                "social em escolas públicas do ABC paulista, com LIBRAS."
            ),
            (
                "Núcleo Dança Quilombo: residência de dança contemporânea afro "
                "diáspora, oficinas em quilombos urbanos de BH e Contagem."
            ),
            (
                "Coletivo Som Abierto: música experimental e instalação sonora "
                "em praças, parceria com SESCs do interior fluminense."
            ),
        ]
        drafts: list[str] = []
        for portfolio in portfolios:
            result = self._run(portfolio=portfolio)
            draft = (result.get("output") or {}).get("draft") or ""
            self.assertTrue(draft.strip())
            drafts.append(draft)

        self.assertEqual(len(set(drafts)), 3, "drafts must differ across portfolios")

    def test_no_hardcoded_outputs_language_variation(self) -> None:
        grant_id = "sample-grant-cultura-sp"
        portfolio = (
            "Produtora Lumiar: audiovisual documental sobre cultura popular, "
            "exibições itinerantes em três municípios do Vale do Paraíba."
        )
        drafts: dict[str, str] = {}
        for lang in ("en", "es", "pt-BR"):
            result = self._run(grant_id=grant_id, portfolio=portfolio, language=lang)
            draft = (result.get("output") or {}).get("draft") or ""
            self.assertTrue(draft.strip(), f"empty draft for language={lang}")
            drafts[lang] = draft

        self.assertNotEqual(drafts["en"], drafts["es"])
        self.assertNotEqual(drafts["en"], drafts["pt-BR"])
        self.assertNotEqual(drafts["es"], drafts["pt-BR"])

    def test_bounded_loop_stops_at_three_revisions(self) -> None:
        from apps.reasoning_engine.agents.evaluator import EvaluatorAgent
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        def _always_low(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            revision = int(kwargs.get("revision") or 0)
            result = {
                "score": 3.0,
                "critique": "[TEST] Forced low score for bounded-loop audit.",
                "accepted": False,
                "needs_revision": True,
                "coverage": 0.0,
                "missing_requirements": ["forced"],
                "is_sample": True,
                "confidence": 0.5,
            }
            self.tracer.log(
                agent_name=self.name,
                action="evaluate",
                input_summary="forced-low",
                output_summary="score=3.0 needs_revision=True",
                target_agent="Writer",
                retry_count=revision,
                status="needs_revision",
                score=3.0,
                provider="local_sample",
            )
            return result

        with patch.object(EvaluatorAgent, "evaluate", _always_low):
            result = ReasoningOrchestrator().run(
                grant_id="sample-grant-cultura-sp",
                user_portfolio_text=(
                    "Coletivo BoundTest: teatro e circulação cultural em SP."
                ),
                output_language="pt-BR",
            )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(int((result.get("output") or {}).get("revisions") or -1), 3)
        # Safety: revisions never exceed hard cap.
        self.assertLessEqual(int((result.get("output") or {}).get("revisions") or 0), 3)

    def test_live_mode_without_credentials_returns_structured_error(self) -> None:
        from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator

        os.environ["SAMPLE_MODE"] = "false"
        # Provider keys already cleared in setUp.
        result = ReasoningOrchestrator().run(
            grant_id="sample-grant-cultura-sp",
            user_portfolio_text="Portfolio sem credenciais live.",
            output_language="pt-BR",
        )

        self.assertEqual(result.get("status"), "error")
        self.assertIsInstance(result, dict)
        # Structured error — no raw traceback fields.
        self.assertNotIn("traceback", result)
        self.assertNotIn("exc_info", result)
        err = result.get("error")
        self.assertIsInstance(err, dict)
        self.assertIn("type", err)
        self.assertIn("message", err)
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Traceback (most recent call last)", dumped)


if __name__ == "__main__":
    unittest.main()
