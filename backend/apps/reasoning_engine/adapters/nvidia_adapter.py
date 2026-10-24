"""NVIDIA NIM / Nemotron adapter — health, validation and direct generation fallback.

Primary path for grant writing is Meterless Nightshift (which routes to Nemotron).
This adapter validates NVIDIA access and provides a direct OpenAI-compatible
fallback when Meterless fails but NIM is healthy.

SAMPLE_MODE never opens network sockets.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from apps.reasoning_engine.services.tracer import AgentTracer, summarize

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-4-340b-instruct"

# Static catalog for SAMPLE_MODE / offline judges (not an exhaustive NVIDIA catalog).
KNOWN_SAMPLE_MODELS = [
    {
        "id": "nvidia/nemotron-4-340b-instruct",
        "owned_by": "nvidia",
        "reasoning": True,
    },
    {
        "id": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "owned_by": "nvidia",
        "reasoning": True,
    },
    {
        "id": "nvidia/llama-3.3-nemotron-super-49b-v1",
        "owned_by": "nvidia",
        "reasoning": True,
    },
    {
        "id": "nvidia/nemotron-3-super-120b-a12b",
        "owned_by": "nvidia",
        "reasoning": True,
    },
    {
        "id": "nvidia/nemotron-3-nano-30b-a3b",
        "owned_by": "nvidia",
        "reasoning": True,
    },
    {
        "id": "nvidia/llama-3.1-70b-instruct",
        "owned_by": "nvidia",
        "reasoning": False,
    },
]


class NvidiaNotConfigured(RuntimeError):
    """NVIDIA_API_KEY missing (non-sample path)."""


class NvidiaAuthError(RuntimeError):
    """401/403 from NVIDIA NIM."""


class NvidiaTimeout(RuntimeError):
    """HTTP timeout talking to NVIDIA."""


class NvidiaRuntimeError(RuntimeError):
    """5xx / exhausted retries / malformed response."""


class NvidiaModelNotFound(RuntimeError):
    """Requested model id is not available."""


def _is_sample_mode() -> bool:
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


class NvidiaAdapter:
    """NVIDIA NIM client for health checks, model validation and direct generation."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("NVIDIA_API_KEY", "")
        ).strip()
        env_model = os.getenv("NEMOTRON_MODEL", "").strip()
        self.default_model = (
            model if model is not None else (env_model or _DEFAULT_MODEL)
        ).strip()
        # Back-compat attribute used by older call sites.
        self.model = self.default_model
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("NVIDIA_BASE_URL", _DEFAULT_BASE_URL)
        ).strip().rstrip("/")
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("NVIDIA_TIMEOUT_SECONDS", "30")
        )
        self.max_attempts = max(
            1,
            min(
                int(
                    max_attempts
                    if max_attempts is not None
                    else os.getenv("NVIDIA_MAX_ATTEMPTS", "2")
                ),
                3,
            ),
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def health_check(self, tracer: AgentTracer | None = None) -> dict[str, Any]:
        """Verify NVIDIA NIM connectivity. SAMPLE_MODE → ok without network."""
        if _is_sample_mode():
            result = {"status": "ok", "latency_ms": 0, "mode": "sample", "base_url": self.base_url}
            self._trace(
                tracer,
                action="nvidia_health_check",
                input_summary="SAMPLE_MODE",
                output_summary="ok",
                status="success",
            )
            return result

        if not self.is_configured():
            result = {
                "status": "error",
                "reason": "not_configured",
                "message": "NVIDIA_API_KEY not set",
            }
            self._trace(
                tracer,
                action="nvidia_health_check",
                input_summary="missing API key",
                output_summary="not_configured",
                status="error",
            )
            return result

        started = time.perf_counter()
        try:
            self._make_request("GET", "/models", tracer=tracer, action="nvidia_health_check")
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = {"status": "ok", "latency_ms": latency_ms, "mode": "live", "base_url": self.base_url}
            self._trace(
                tracer,
                action="nvidia_health_check",
                input_summary="GET /models",
                output_summary=f"ok latency_ms={latency_ms}",
                status="success",
                extra={"latency_ms": latency_ms},
            )
            return result
        except (NvidiaAuthError, NvidiaTimeout, NvidiaRuntimeError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = {
                "status": "error",
                "reason": type(exc).__name__,
                "message": str(exc),
                "latency_ms": latency_ms,
            }
            self._trace(
                tracer,
                action="nvidia_health_check",
                input_summary="GET /models",
                output_summary=summarize(str(exc), 200),
                status="error",
                extra={"latency_ms": latency_ms},
            )
            return result

    def validate_model(
        self, model_name: str | None = None, tracer: AgentTracer | None = None
    ) -> dict[str, Any]:
        """Validate that a model id exists on NVIDIA NIM (or sample catalog)."""
        model_name = (model_name or self.default_model or "").strip()
        if not model_name:
            raise NvidiaModelNotFound("model_name is required")

        if _is_sample_mode():
            known = {m["id"] for m in KNOWN_SAMPLE_MODELS}
            valid = model_name in known or "nemotron" in model_name.lower()
            result = {
                "valid": valid,
                "model": model_name,
                "mode": "sample",
                "reason": None if valid else "not_in_sample_catalog",
            }
            self._trace(
                tracer,
                action="nvidia_validate_model",
                input_summary=model_name,
                output_summary=f"valid={valid}",
                status="success" if valid else "error",
            )
            return result

        if not self.is_configured():
            raise NvidiaNotConfigured(
                "NVIDIA credentials not configured. Set NVIDIA_API_KEY, or use SAMPLE_MODE=true."
            )

        # Prefer listing models (GET /models/{id} is not always available on NIM).
        try:
            models = self.list_models(tracer=tracer)
            ids = {str(m.get("id") or m.get("name") or "") for m in models}
            valid = model_name in ids
            if not valid:
                # Some catalogs omit ids intermittently — accept nemotron-like names if list empty.
                if not ids and "nemotron" in model_name.lower():
                    valid = True
            result = {
                "valid": valid,
                "model": model_name,
                "mode": "live",
                "reason": None if valid else "model_not_found",
            }
            if not valid:
                self._trace(
                    tracer,
                    action="nvidia_validate_model",
                    input_summary=model_name,
                    output_summary="valid=false",
                    status="error",
                )
                return result
            self._trace(
                tracer,
                action="nvidia_validate_model",
                input_summary=model_name,
                output_summary="valid=true",
                status="success",
            )
            return result
        except NvidiaModelNotFound:
            raise
        except (NvidiaAuthError, NvidiaTimeout, NvidiaRuntimeError) as exc:
            raise NvidiaRuntimeError(f"Unable to validate model: {exc}") from exc

    def list_models(self, tracer: AgentTracer | None = None) -> list[dict[str, Any]]:
        """List NVIDIA models, preferring Nemotron / reasoning ids."""
        if _is_sample_mode():
            self._trace(
                tracer,
                action="nvidia_list_models",
                input_summary="SAMPLE_MODE",
                output_summary=f"count={len(KNOWN_SAMPLE_MODELS)}",
                status="success",
            )
            return list(KNOWN_SAMPLE_MODELS)

        if not self.is_configured():
            raise NvidiaNotConfigured(
                "NVIDIA credentials not configured. Set NVIDIA_API_KEY, or use SAMPLE_MODE=true."
            )

        body = self._make_request("GET", "/models", tracer=tracer, action="nvidia_list_models")
        raw = body.get("data") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            raw = body if isinstance(body, list) else []

        models: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("name") or "")
            if not mid:
                continue
            mid_l = mid.lower()
            if any(k in mid_l for k in ("nemotron", "nvidia/", "reasoning")):
                models.append(
                    {
                        "id": mid,
                        "owned_by": item.get("owned_by") or "nvidia",
                        "reasoning": "nemotron" in mid_l or "reasoning" in mid_l,
                    }
                )
        if not models:
            # Fall back to full list if filter is too strict.
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    models.append(
                        {
                            "id": str(item["id"]),
                            "owned_by": item.get("owned_by") or "unknown",
                            "reasoning": False,
                        }
                    )

        self._trace(
            tracer,
            action="nvidia_list_models",
            input_summary="GET /models",
            output_summary=f"count={len(models)}",
            status="success",
            extra={"count": len(models)},
        )
        return models

    def generate_text(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        tracer: AgentTracer | None = None,
    ) -> dict[str, Any]:
        """Generate text via NVIDIA chat completions (or SAMPLE heuristic).

        Returns {"text": str, "usage": dict, "model": str}.
        """
        options = dict(options or {})
        model = str(options.get("model") or self.default_model).strip()
        temperature = float(options.get("temperature", 0.7))
        max_tokens = int(options.get("max_tokens", 1024))
        language = str(options.get("language") or options.get("output_language") or "pt-BR")

        if _is_sample_mode():
            text = self._sample_generate(prompt, language=language, options=options)
            result = {
                "text": text,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "model": model or "sample/nemotron-heuristic",
                "mode": "sample",
            }
            self._trace(
                tracer,
                action="nvidia_generate_text",
                input_summary=summarize(prompt, 200),
                output_summary=summarize(text, 200),
                status="success",
                extra={"mode": "sample", "model": result["model"]},
            )
            return result

        if not self.is_configured():
            raise NvidiaNotConfigured(
                "NVIDIA credentials not configured. Set NVIDIA_API_KEY, or use SAMPLE_MODE=true."
            )
        if not model:
            raise NvidiaModelNotFound(
                "NEMOTRON_MODEL not set. Configure NEMOTRON_MODEL or pass options.model."
            )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = self._make_request(
            "POST",
            "/chat/completions",
            json=payload,
            tracer=tracer,
            action="nvidia_generate_text",
        )
        text = self._extract_text(body)
        usage = body.get("usage") if isinstance(body, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        result = {
            "text": text,
            "usage": usage,
            "model": body.get("model") if isinstance(body, dict) else model,
            "mode": "live",
        }
        self._trace(
            tracer,
            action="nvidia_generate_text",
            input_summary=summarize(prompt, 200),
            output_summary=summarize(text, 200),
            status="success",
            extra={
                "model": result["model"],
                # Avoid keys containing "token" (tracer redacts *token*).
                "usage_prompt": usage.get("prompt_tokens"),
                "usage_completion": usage.get("completion_tokens"),
                "usage_total": usage.get("total_tokens"),
            },
        )
        return result

    # ------------------------------------------------------------------ HTTP
    def _make_request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        tracer: AgentTracer | None = None,
        action: str = "nvidia_request",
    ) -> Any:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.request(method, url, headers=headers, json=json)
            except httpx.TimeoutException as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._trace(
                    tracer,
                    action=action,
                    input_summary=f"{method} {endpoint}",
                    output_summary="NVIDIA timeout",
                    status="error",
                    retry_count=attempt - 1,
                    extra={"execution_time_ms": elapsed_ms},
                )
                raise NvidiaTimeout(f"NVIDIA timeout after {self.timeout_seconds}s") from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise NvidiaRuntimeError(f"NVIDIA transport error: {exc}") from exc
                time.sleep(0.4 * attempt)
                continue

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            status = response.status_code

            if status in (401, 403):
                self._trace(
                    tracer,
                    action=action,
                    input_summary=f"{method} {endpoint}",
                    output_summary="NVIDIA authentication failed",
                    status="error",
                    extra={"http_status": status, "execution_time_ms": elapsed_ms},
                )
                raise NvidiaAuthError("NVIDIA authentication failed")

            if status == 404:
                self._trace(
                    tracer,
                    action=action,
                    input_summary=f"{method} {endpoint}",
                    output_summary="NVIDIA model or endpoint not found",
                    status="error",
                    extra={"http_status": status, "execution_time_ms": elapsed_ms},
                )
                raise NvidiaModelNotFound("NVIDIA model or endpoint not found")

            if status >= 500:
                self._trace(
                    tracer,
                    action=action,
                    input_summary=f"{method} {endpoint}",
                    output_summary=f"NVIDIA server error ({status})",
                    status="error",
                    retry_count=attempt - 1,
                    extra={
                        "http_status": status,
                        "execution_time_ms": elapsed_ms,
                        "will_retry": attempt < self.max_attempts,
                    },
                )
                last_error = NvidiaRuntimeError(f"NVIDIA server error ({status})")
                if attempt >= self.max_attempts:
                    raise last_error
                time.sleep(0.5 * attempt)
                continue

            if status != 200:
                raise NvidiaRuntimeError(
                    f"NVIDIA unexpected status {status}: {summarize(response.text, 180)}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise NvidiaRuntimeError("NVIDIA returned non-JSON body") from exc

        raise NvidiaRuntimeError(f"NVIDIA failed after retries: {last_error}")

    @staticmethod
    def _extract_text(body: Any) -> str:
        if not isinstance(body, dict):
            return str(body or "")
        choices = body.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"])
            if choices[0].get("text"):
                return str(choices[0]["text"])
        return str(body.get("text") or "")

    def _sample_generate(
        self, prompt: str, *, language: str, options: dict[str, Any]
    ) -> str:
        """Deterministic heuristic text for SAMPLE_MODE (varies with prompt)."""
        role = str(options.get("role") or "writer")
        snippet = summarize(prompt, 220)
        tokens = [w for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", prompt)[:8]]
        joined = ", ".join(tokens) if tokens else "projeto cultural"

        if role == "evaluator":
            return (
                '{"score": 8.2, "critique": "[SAMPLE][NVIDIA] Cobertura adequada dos '
                + f"critérios ({joined})."
                + '", "needs_revision": false}'
            )
        if language == "en":
            return (
                "[SAMPLE_MODE][NVIDIA heuristic]\n"
                f"Draft based on prompt signals ({joined}).\n"
                f"Context: {snippet}\n"
                "Includes accessibility, circulation across 3 municipalities, "
                "budget and 6-month schedule.\n"
            )
        if language == "es":
            return (
                "[SAMPLE_MODE][NVIDIA heuristic]\n"
                f"Borrador basado en señales del prompt ({joined}).\n"
                f"Contexto: {snippet}\n"
                "Incluye accesibilidad, circulación en 3 municipios, "
                "presupuesto y cronograma de 6 meses.\n"
            )
        return (
            "[SAMPLE_MODE][NVIDIA heuristic]\n"
            f"Rascunho baseado em sinais do prompt ({joined}).\n"
            f"Contexto: {snippet}\n"
            "Inclui acessibilidade (audiodescrição/LIBRAS), circulação em no mínimo "
            "3 municípios, orçamento e cronograma de 6 meses.\n"
        )

    def _trace(
        self,
        tracer: AgentTracer | None,
        *,
        action: str,
        input_summary: str,
        output_summary: str,
        status: str,
        retry_count: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if tracer is None:
            return
        tracer.log(
            agent_name="NvidiaAdapter",
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            status=status,
            retry_count=retry_count,
            provider="sample" if _is_sample_mode() else "nvidia_direct",
            extra=extra,
        )
