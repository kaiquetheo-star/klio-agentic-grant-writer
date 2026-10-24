"""Zetaris adapter — Lightning SQL over REST (read-only grant context).

SAMPLE_MODE never calls the network; live mode requires ZETARIS_BASE_URL +
ZETARIS_API_KEY (and ZETARIS_ORG_ID for the enterprise SQL-editor endpoint).
Nothing is written to the Klio database.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

import httpx

from apps.reasoning_engine.services.sample_data import get_sample_grant_context
from apps.reasoning_engine.services.tracer import AgentTracer, summarize

# Enterprise Lightning SQL editor (kbase / pipeline examples).
_DEFAULT_QUERY_PATH = "/api/v1.0/sql-editor/sqls/run-query"
_SAFE_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ZetarisNotConfigured(RuntimeError):
    """Raised when Zetaris credentials are missing (non-sample path)."""


class ZetarisAuthError(RuntimeError):
    """401/403 from Zetaris."""


class ZetarisNotFound(RuntimeError):
    """Grant row missing or HTTP 404."""


class ZetarisTimeout(RuntimeError):
    """Query exceeded timeout."""


class ZetarisQueryError(RuntimeError):
    """Non-retryable or exhausted query failure."""


def _is_sample_mode() -> bool:
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _escape_sql_literal(value: str) -> str:
    return (value or "").replace("'", "''")


def _parse_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text[0] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


class ZetarisAdapter:
    """Fetch structured grant context from Zetaris via Lightning SQL over REST."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        org_id: str | None = None,
        query_path: str | None = None,
        grants_table: str | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("ZETARIS_API_KEY", "")).strip()
        self.base_url = (
            base_url if base_url is not None else os.getenv("ZETARIS_BASE_URL", "")
        ).strip().rstrip("/")
        # Zetaris-specific timeout (default 30s per task brief).
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("ZETARIS_TIMEOUT_SECONDS", "30")
        )
        self.org_id = (org_id if org_id is not None else os.getenv("ZETARIS_ORG_ID", "")).strip()
        self.query_path = (
            query_path
            if query_path is not None
            else os.getenv("ZETARIS_QUERY_PATH", _DEFAULT_QUERY_PATH)
        ).strip()
        if not self.query_path.startswith("/"):
            self.query_path = "/" + self.query_path
        table = (
            grants_table
            if grants_table is not None
            else os.getenv("ZETARIS_GRANTS_TABLE", "grants")
        ).strip()
        if not _SAFE_TABLE_RE.match(table):
            raise ZetarisNotConfigured(
                f"ZETARIS_GRANTS_TABLE inválida: {table!r}. Use schema.table alfanumérico."
            )
        self.grants_table = table
        # max 2 attempts (initial + 1 retry on 5xx).
        self.max_attempts = int(
            max_attempts
            if max_attempts is not None
            else os.getenv("ZETARIS_MAX_ATTEMPTS", "2")
        )
        self.max_attempts = max(1, min(self.max_attempts, 3))
        # query_mode: run_query | open_sql | generic
        self.query_mode = os.getenv("ZETARIS_QUERY_MODE", "run_query").strip().lower()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def fetch_grant_context(
        self,
        grant_id: str,
        tracer: AgentTracer | None = None,
    ) -> dict[str, Any]:
        """Return grant rubric/context for the Planner.

        SAMPLE_MODE=true → synthetic data (no network).
        Otherwise → Lightning SQL over REST; never persists to Klio DB.
        """
        grant_id = (grant_id or "").strip()
        if not grant_id:
            raise ZetarisQueryError("grant_id is required")

        if _is_sample_mode():
            ctx = get_sample_grant_context(grant_id)
            normalized = self._normalize_context(ctx, source_hint="sample_data")
            if tracer:
                tracer.log(
                    agent_name="ZetarisAdapter",
                    action="zetaris_query",
                    input_summary=f"SAMPLE_MODE grant_id={grant_id}",
                    output_summary=f"grant_id={normalized.get('grant_id')} title={summarize(str(normalized.get('title')), 80)}",
                    target_agent="Planner",
                    status="success",
                    extra={"is_sample": True, "execution_time_ms": 0},
                )
            return normalized

        if not self.is_configured():
            raise ZetarisNotConfigured(
                "Zetaris credentials not configured. "
                "Set ZETARIS_BASE_URL and ZETARIS_API_KEY, or use SAMPLE_MODE=true."
            )

        sql = self._build_grant_sql(grant_id)
        started = time.perf_counter()
        try:
            rows = self._execute_sql(sql, tracer=tracer)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not rows:
                if tracer:
                    tracer.log(
                        agent_name="ZetarisAdapter",
                        action="zetaris_query",
                        input_summary=summarize(sql, 200),
                        output_summary=f"Grant not found in Zetaris: {grant_id}",
                        status="error",
                        extra={"execution_time_ms": elapsed_ms, "http_status": 200},
                    )
                raise ZetarisNotFound(f"Grant not found in Zetaris: {grant_id}")

            raw = rows[0]
            context = self._normalize_context(raw, source_hint="zetaris")
            context["grant_id"] = context.get("grant_id") or grant_id
            if tracer:
                tracer.log(
                    agent_name="ZetarisAdapter",
                    action="zetaris_query",
                    input_summary=summarize(sql, 200),
                    output_summary=(
                        f"grant_id={context.get('grant_id')} "
                        f"title={summarize(str(context.get('title')), 80)}"
                    ),
                    target_agent="Planner",
                    status="success",
                    extra={"execution_time_ms": elapsed_ms, "row_count": len(rows)},
                )
            return context
        except (ZetarisAuthError, ZetarisNotFound, ZetarisTimeout, ZetarisQueryError, ZetarisNotConfigured):
            raise
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if tracer:
                tracer.log(
                    agent_name="ZetarisAdapter",
                    action="zetaris_query",
                    input_summary=summarize(sql, 200),
                    output_summary=summarize(f"{type(exc).__name__}: {exc}", 200),
                    status="error",
                    extra={"execution_time_ms": elapsed_ms},
                )
            raise ZetarisQueryError(f"Zetaris query failed: {exc}") from exc

    def _build_grant_sql(self, grant_id: str) -> str:
        safe_id = _escape_sql_literal(grant_id)
        # Columns match the hackathon grant-context contract; aliases keep mapping stable
        # if the warehouse uses slightly different names via views.
        return (
            "SELECT "
            "grant_id, "
            "title, "
            "description, "
            "rubric_criteria, "
            "budget_limits, "
            "eligibility_rules, "
            "deadlines, "
            "source "
            f"FROM {self.grants_table} "
            f"WHERE grant_id = '{safe_id}' "
            "LIMIT 1"
        )

    def _endpoint_url(self) -> str:
        return f"{self.base_url}{self.query_path}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        }
        if self.org_id:
            headers["X-Org-ID"] = self.org_id
        return headers

    def _payload(self, sql: str) -> dict[str, Any]:
        if self.query_mode == "open_sql":
            return {"select": sql, "pageLimit": 1}
        if self.query_mode == "generic":
            return {"query": sql, "format": "json"}
        # Default: enterprise SQL editor (Lightning SQL over REST).
        return {"sql": sql}

    def _execute_sql(self, sql: str, tracer: AgentTracer | None = None) -> list[dict[str, Any]]:
        url = self._endpoint_url()
        payload = self._payload(sql)
        headers = self._headers()
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            attempt_started = time.perf_counter()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                elapsed_ms = int((time.perf_counter() - attempt_started) * 1000)
                if tracer:
                    tracer.log(
                        agent_name="ZetarisAdapter",
                        action="zetaris_query",
                        input_summary=summarize(sql, 200),
                        output_summary="Zetaris query timeout",
                        status="error",
                        retry_count=attempt - 1,
                        extra={"execution_time_ms": elapsed_ms, "attempt": attempt},
                    )
                raise ZetarisTimeout(
                    f"Zetaris query timeout after {self.timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise ZetarisQueryError(f"Zetaris transport error: {exc}") from exc
                time.sleep(0.4 * attempt)
                continue

            elapsed_ms = int((time.perf_counter() - attempt_started) * 1000)
            status = response.status_code

            if status in (401, 403):
                if tracer:
                    tracer.log(
                        agent_name="ZetarisAdapter",
                        action="zetaris_query",
                        input_summary=summarize(sql, 200),
                        output_summary="Zetaris authentication failed",
                        status="error",
                        retry_count=attempt - 1,
                        extra={"execution_time_ms": elapsed_ms, "http_status": status},
                    )
                raise ZetarisAuthError("Zetaris authentication failed")

            if status == 404:
                if tracer:
                    tracer.log(
                        agent_name="ZetarisAdapter",
                        action="zetaris_query",
                        input_summary=summarize(sql, 200),
                        output_summary="Grant not found in Zetaris",
                        status="error",
                        retry_count=attempt - 1,
                        extra={"execution_time_ms": elapsed_ms, "http_status": status},
                    )
                raise ZetarisNotFound("Grant not found in Zetaris")

            if status >= 500:
                last_error = ZetarisQueryError(f"Zetaris server error ({status})")
                if tracer:
                    tracer.log(
                        agent_name="ZetarisAdapter",
                        action="zetaris_query",
                        input_summary=summarize(sql, 200),
                        output_summary=f"Zetaris server error ({status})",
                        status="error",
                        retry_count=attempt - 1,
                        extra={
                            "execution_time_ms": elapsed_ms,
                            "http_status": status,
                            "attempt": attempt,
                            "will_retry": attempt < self.max_attempts,
                        },
                    )
                if attempt >= self.max_attempts:
                    raise last_error
                time.sleep(0.5 * attempt)
                continue

            if status != 200:
                body_preview = summarize(response.text, 180)
                raise ZetarisQueryError(
                    f"Zetaris unexpected status {status}: {body_preview}"
                )

            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                raise ZetarisQueryError("Zetaris returned non-JSON body") from exc

            return self._rows_from_response(body)

        raise ZetarisQueryError(f"Zetaris query failed after retries: {last_error}")

    def _rows_from_response(self, body: Any) -> list[dict[str, Any]]:
        """Normalize Zetaris response shapes into a list of row dicts."""
        if body is None:
            return []

        # Enterprise SQL editor: {"headers": [...], "data": [[...], ...]}
        if isinstance(body, dict) and "headers" in body and "data" in body:
            headers = [str(h) for h in (body.get("headers") or [])]
            data = body.get("data") or []
            rows: list[dict[str, Any]] = []
            for item in data:
                if isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, (list, tuple)):
                    rows.append({headers[i]: item[i] for i in range(min(len(headers), len(item)))})
            return rows

        # Generic / OpenSql-style: {"rows": [...]} or {"results": [...]} or {"data": [...]}
        if isinstance(body, dict):
            for key in ("rows", "results", "records", "data", "items"):
                candidate = body.get(key)
                if isinstance(candidate, list):
                    if not candidate:
                        return []
                    if isinstance(candidate[0], dict):
                        return list(candidate)
                    break
            # Single object payload
            if any(k in body for k in ("grant_id", "title")):
                return [body]

        if isinstance(body, list):
            if not body:
                return []
            if isinstance(body[0], dict):
                return list(body)

        raise ZetarisQueryError(
            "Unrecognized Zetaris response shape; expected headers/data or row objects"
        )

    def _normalize_context(self, raw: dict[str, Any], *, source_hint: str) -> dict[str, Any]:
        """Map warehouse / sample fields → Planner schema (+ legacy compat keys)."""
        rubric = self._normalize_rubric(
            raw.get("rubric")
            or raw.get("rubric_criteria")
            or raw.get("evaluation_criteria")
        )
        eligibility = self._normalize_string_list(
            raw.get("eligibility_rules") or raw.get("requirements")
        )
        budget = self._normalize_budget(
            raw.get("budget_limits")
            or raw.get("funding_ceiling_brl")
            or raw.get("budget")
        )
        deadlines = self._normalize_deadlines(
            raw.get("deadlines") or raw.get("deadline") or raw.get("submission_deadline")
        )

        title = str(raw.get("title") or "").strip()
        description = str(raw.get("description") or raw.get("summary") or "").strip()
        source = str(raw.get("source") or raw.get("url") or source_hint).strip()

        # Legacy keys kept so Planner/Writer/Evaluator keep working unchanged.
        evaluation_criteria = [
            str(item.get("criterion") or "").strip()
            for item in rubric
            if str(item.get("criterion") or "").strip()
        ]
        if not evaluation_criteria and isinstance(raw.get("evaluation_criteria"), list):
            evaluation_criteria = [str(x) for x in raw["evaluation_criteria"]]

        context: dict[str, Any] = {
            "grant_id": str(raw.get("grant_id") or "").strip(),
            "title": title,
            "description": description,
            "rubric": rubric,
            "budget_limits": budget,
            "eligibility_rules": eligibility,
            "deadlines": deadlines,
            "source": source,
            # Compat with existing agents / SAMPLE_MODE shape
            "requirements": eligibility or list(raw.get("requirements") or []),
            "evaluation_criteria": evaluation_criteria,
            "deadline": deadlines.get("submission"),
            "funding_ceiling_brl": budget.get("total"),
            "url": raw.get("url") or source,
            "is_sample": bool(raw.get("is_sample") or source_hint == "sample_data"),
            "source_system": source_hint,
        }
        if raw.get("sample_banner"):
            context["sample_banner"] = raw["sample_banner"]
        if raw.get("organizer"):
            context["organizer"] = raw["organizer"]
        if raw.get("scope"):
            context["scope"] = raw["scope"]
        if raw.get("target_categories"):
            context["target_categories"] = raw["target_categories"]
        return context

    def _normalize_rubric(self, value: Any) -> list[dict[str, Any]]:
        parsed = _parse_jsonish(value)
        if parsed is None:
            return []
        if isinstance(parsed, str):
            # Semicolon / newline separated criterion names
            parts = [p.strip() for p in re.split(r"[;\n|]", parsed) if p.strip()]
            return [
                {"criterion": p, "weight": 1.0, "description": p}
                for p in parts
            ]
        if isinstance(parsed, list):
            out: list[dict[str, Any]] = []
            for item in parsed:
                if isinstance(item, str):
                    out.append({"criterion": item, "weight": 1.0, "description": item})
                elif isinstance(item, dict):
                    criterion = str(
                        item.get("criterion")
                        or item.get("name")
                        or item.get("title")
                        or ""
                    ).strip()
                    if not criterion:
                        continue
                    try:
                        weight = float(item.get("weight", 1.0))
                    except (TypeError, ValueError):
                        weight = 1.0
                    out.append(
                        {
                            "criterion": criterion,
                            "weight": weight,
                            "description": str(
                                item.get("description") or criterion
                            ).strip(),
                        }
                    )
            return out
        return []

    def _normalize_string_list(self, value: Any) -> list[str]:
        parsed = _parse_jsonish(value)
        if parsed is None:
            return []
        if isinstance(parsed, str):
            return [p.strip() for p in re.split(r"[;\n|]", parsed) if p.strip()]
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        return []

    def _normalize_budget(self, value: Any) -> dict[str, Any]:
        parsed = _parse_jsonish(value)
        if parsed is None:
            return {"total": None, "per_category": {}}
        if isinstance(parsed, (int, float)):
            return {"total": float(parsed), "per_category": {}}
        if isinstance(parsed, str):
            try:
                return {"total": float(parsed.replace(",", ".")), "per_category": {}}
            except ValueError:
                return {"total": None, "per_category": {}}
        if isinstance(parsed, dict):
            total = parsed.get("total", parsed.get("max", parsed.get("ceiling")))
            try:
                total_f = float(total) if total is not None else None
            except (TypeError, ValueError):
                total_f = None
            per_cat = parsed.get("per_category") or parsed.get("categories") or {}
            if not isinstance(per_cat, dict):
                per_cat = {}
            return {"total": total_f, "per_category": per_cat}
        return {"total": None, "per_category": {}}

    def _normalize_deadlines(self, value: Any) -> dict[str, Any]:
        parsed = _parse_jsonish(value)
        if parsed is None:
            return {"submission": None, "announcement": None}
        if isinstance(parsed, str):
            return {"submission": parsed, "announcement": None}
        if isinstance(parsed, dict):
            return {
                "submission": parsed.get("submission")
                or parsed.get("deadline")
                or parsed.get("due"),
                "announcement": parsed.get("announcement")
                or parsed.get("result")
                or parsed.get("results"),
            }
        return {"submission": None, "announcement": None}
