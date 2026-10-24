"""Bounded project memory with provenance (local Django ORM + in-memory fallback).

Never stores email, phone, CPF, CNPJ-as-PII dumps, or API tokens.
Remote Meterless H-MEM sync is out of scope until an official contract exists —
see HMemAdapter.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apps.reasoning_engine.services.tracer import AgentTracer, summarize

# Keys / value patterns that must never be persisted.
_SENSITIVE_KEY_HINTS = (
    "cpf",
    "cnpj",
    "email",
    "e-mail",
    "telefone",
    "phone",
    "celular",
    "token",
    "api_key",
    "password",
    "senha",
    "secret",
    "authorization",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}[-\s]?\d{4}")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_TOKEN_RE = re.compile(r"\b(?:sk-|nvapi-|aact_|Bearer\s)[A-Za-z0-9._\-]{8,}\b")

# Writer may only assert these as facts when confirmed by the user.
PROTECTED_ASSERTION_HINTS = (
    "premio",
    "prêmio",
    "award",
    "parceria",
    "partnership",
    "oficio",
    "ofício",
    "anuencia",
    "anuência",
    "valor_aprovado",
    "orcamento_aprovado",
    "cnpj",
    "cpf",
    "endereco",
    "endereço",
    "address",
)

VALID_SOURCES = frozenset(
    {"user_input", "portfolio", "grant_rubric", "agent_inference"}
)

DEFAULT_MAX_CHARS = 1200  # ~token budget proxy for bounded recall


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_sample_mode() -> bool:
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def memory_backend() -> str:
    """django | memory — SAMPLE_MODE defaults to memory unless explicitly django."""
    explicit = os.getenv("REASONING_ENGINE_MEMORY_BACKEND", "").strip().lower()
    if explicit in {"django", "memory"}:
        return explicit
    if _is_sample_mode():
        return "memory"
    return "django"


def contains_sensitive_payload(key: str, value: str) -> bool:
    key_l = (key or "").lower()
    if any(h in key_l for h in _SENSITIVE_KEY_HINTS):
        return True
    text = value or ""
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        return True
    if _CPF_RE.search(text) or _CNPJ_RE.search(text):
        return True
    if _TOKEN_RE.search(text):
        return True
    return False


def is_protected_assertion_key(key: str) -> bool:
    key_l = (key or "").lower()
    return any(h in key_l for h in PROTECTED_ASSERTION_HINTS)


def _value_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "").strip()


@dataclass
class _MemFact:
    id: str
    key: str
    value: str
    source: str
    confidence: float
    confirmed_by_user: bool
    created_at: str
    updated_at: str


@dataclass
class _MemRevision:
    id: str
    version: int
    content: str
    score: float
    critique: str
    evaluator_notes: dict[str, Any]
    created_at: str


@dataclass
class _MemDecision:
    id: str
    agent_name: str
    action: str
    input_summary: str
    output_summary: str
    retry_count: int
    created_at: str


@dataclass
class _MemProject:
    id: str
    owner_key: str
    grant_id: str
    status: str = "drafting"
    current_version: int = 0
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: _utcnow().isoformat())
    facts: dict[str, _MemFact] = field(default_factory=dict)
    revisions: list[_MemRevision] = field(default_factory=list)
    decisions: list[_MemDecision] = field(default_factory=list)


# Process-local store for SAMPLE_MODE / memory backend (survives new service instances).
_IN_MEMORY_PROJECTS: dict[tuple[str, str], _MemProject] = {}


def clear_in_memory_store() -> None:
    """Test helper — wipe process-local memory backend."""
    _IN_MEMORY_PROJECTS.clear()


class MemoryService:
    """CRUD + bounded recall for grant-writing project memory."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        max_recall_chars: int | None = None,
        tracer: AgentTracer | None = None,
    ) -> None:
        self.backend = (backend or memory_backend()).strip().lower()
        if self.backend not in {"django", "memory"}:
            self.backend = "memory"
        self.max_recall_chars = int(
            max_recall_chars
            if max_recall_chars is not None
            else os.getenv("REASONING_ENGINE_MEMORY_MAX_CHARS", str(DEFAULT_MAX_CHARS))
        )
        self.tracer = tracer

    # ------------------------------------------------------------------ project
    def get_or_create_project(self, owner_key: str, grant_id: str) -> Any:
        owner_key = (owner_key or "").strip()
        grant_id = (grant_id or "").strip()
        if not owner_key or not grant_id:
            raise ValueError("owner_key and grant_id are required")
        if contains_sensitive_payload("owner_key", owner_key):
            raise ValueError(
                "owner_key looks like PII/secret — use an opaque hash, never email/CPF/phone"
            )

        if self.backend == "memory":
            key = (owner_key, grant_id)
            project = _IN_MEMORY_PROJECTS.get(key)
            created = False
            if project is None:
                project = _MemProject(
                    id=str(uuid.uuid4()),
                    owner_key=owner_key,
                    grant_id=grant_id,
                )
                _IN_MEMORY_PROJECTS[key] = project
                created = True
            self._trace(
                "memory_upsert",
                f"get_or_create owner={summarize(owner_key, 40)} grant={grant_id}",
                f"project_id={project.id} created={created} backend=memory",
            )
            return project

        from apps.reasoning_engine.models import GrantProject

        project, created = GrantProject.objects.get_or_create(
            owner_key=owner_key,
            grant_id=grant_id,
            defaults={"status": GrantProject.Status.DRAFTING},
        )
        self._trace(
            "memory_upsert",
            f"get_or_create owner={summarize(owner_key, 40)} grant={grant_id}",
            f"project_id={project.id} created={created} backend=django",
        )
        return project

    def get_project_by_id(self, project_id: str) -> Any | None:
        if self.backend == "memory":
            for project in _IN_MEMORY_PROJECTS.values():
                if project.id == str(project_id):
                    return project
            return None
        from apps.reasoning_engine.models import GrantProject

        return GrantProject.objects.filter(pk=project_id).first()

    def get_project(self, owner_key: str, grant_id: str) -> Any | None:
        if self.backend == "memory":
            return _IN_MEMORY_PROJECTS.get(((owner_key or "").strip(), (grant_id or "").strip()))
        from apps.reasoning_engine.models import GrantProject

        return GrantProject.objects.filter(
            owner_key=(owner_key or "").strip(),
            grant_id=(grant_id or "").strip(),
        ).first()

    # -------------------------------------------------------------------- facts
    def upsert_fact(
        self,
        project: Any,
        key: str,
        value: Any,
        source: str,
        confidence: float = 0.5,
        confirmed: bool = False,
    ) -> dict[str, Any] | None:
        key = (key or "").strip()
        text = _value_to_text(value)
        source = (source or "").strip()
        if not key or not text:
            return None
        if source not in VALID_SOURCES:
            raise ValueError(f"invalid fact source: {source}")
        if contains_sensitive_payload(key, text):
            self._trace(
                "memory_unconfirmed_blocked",
                f"key={key}",
                "rejected sensitive payload (PII/token)",
                status="error",
                extra={"reason": "sensitive_payload"},
            )
            return None

        confidence = max(0.0, min(float(confidence), 1.0))
        # Protected assertion keys cannot be treated as confirmed unless user_input.
        if confirmed and is_protected_assertion_key(key) and source != "user_input":
            confirmed = False
        if source == "user_input" and confirmed:
            confirmed = True
        elif source == "agent_inference":
            # Inferences stay unconfirmed until explicit user confirmation.
            if not confirmed:
                confirmed = False

        now = _utcnow().isoformat()
        if self.backend == "memory":
            existing = project.facts.get(key)
            # Never let a weaker inference clobber a user-confirmed fact.
            if (
                existing
                and existing.confirmed_by_user
                and not confirmed
                and source in {"agent_inference", "portfolio", "grant_rubric"}
            ):
                self._trace(
                    "memory_unconfirmed_blocked",
                    f"key={key} source={source}",
                    "refusing overwrite of confirmed user fact",
                    status="needs_revision",
                    extra={"kept_value_summary": summarize(existing.value, 60)},
                )
                return self._fact_dict(existing)
            fact = _MemFact(
                id=existing.id if existing else str(uuid.uuid4()),
                key=key,
                value=text,
                source=source,
                confidence=confidence,
                confirmed_by_user=confirmed,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            project.facts[key] = fact
            project.updated_at = now
            payload = self._fact_dict(fact)
        else:
            from apps.reasoning_engine.models import ProjectFact

            existing = ProjectFact.objects.filter(project=project, key=key).first()
            if (
                existing
                and existing.confirmed_by_user
                and not confirmed
                and source in {"agent_inference", "portfolio", "grant_rubric"}
            ):
                self._trace(
                    "memory_unconfirmed_blocked",
                    f"key={key} source={source}",
                    "refusing overwrite of confirmed user fact",
                    status="needs_revision",
                    extra={"kept_value_summary": summarize(existing.value, 60)},
                )
                return self._fact_dict(existing)
            fact, _ = ProjectFact.objects.update_or_create(
                project=project,
                key=key,
                defaults={
                    "value": text,
                    "source": source,
                    "confidence": confidence,
                    "confirmed_by_user": confirmed,
                },
            )
            payload = self._fact_dict(fact)

        self._trace(
            "memory_upsert",
            f"key={key} source={source} confirmed={confirmed}",
            f"confidence={confidence} value={summarize(text, 80)}",
            extra={"provenance": {"source": source, "confirmed": confirmed}},
        )
        return payload

    def recall_facts(self, project: Any, max_tokens: int | None = None) -> dict[str, Any]:
        """Return bounded fact packs prioritized for confirmed user facts."""
        max_chars = int(max_tokens if max_tokens is not None else self.max_recall_chars)
        facts = self._list_facts(project)

        def sort_key(item: dict[str, Any]) -> tuple:
            confirmed = 0 if item["confirmed_by_user"] else 1
            # Prefer user_input > portfolio > grant_rubric > agent_inference
            source_rank = {
                "user_input": 0,
                "portfolio": 1,
                "grant_rubric": 2,
                "agent_inference": 3,
            }.get(item["source"], 9)
            conf = -float(item.get("confidence") or 0)
            return (confirmed, source_rank, conf, item["key"])

        ordered = sorted(facts, key=sort_key)
        included: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        used = 0
        for item in ordered:
            cost = len(item["key"]) + len(item["value"]) + 24
            if included and used + cost > max_chars:
                dropped.append(
                    {
                        "key": item["key"],
                        "reason": "bounded_context_limit",
                        "confirmed": item["confirmed_by_user"],
                        "source": item["source"],
                    }
                )
                continue
            included.append(item)
            used += cost

        confirmed = {
            f["key"]: f
            for f in included
            if f["confirmed_by_user"]
        }
        unconfirmed = {
            f["key"]: {
                **f,
                "warning": "UNCONFIRMED — não afirmar como fato no draft",
            }
            for f in included
            if not f["confirmed_by_user"]
        }
        pending = [
            f"[PENDENTE: confirmar '{f['key']}' = {summarize(f['value'], 60)}]"
            for f in included
            if not f["confirmed_by_user"]
        ]

        result = {
            "confirmed_facts": confirmed,
            "unconfirmed_facts": unconfirmed,
            "pending_questions": pending,
            "facts_included": [f["key"] for f in included],
            "facts_dropped": dropped,
            "chars_used": used,
            "max_chars": max_chars,
        }
        self._trace(
            "memory_recall",
            f"project={self._project_id(project)} max_chars={max_chars}",
            (
                f"included={len(included)} dropped={len(dropped)} "
                f"confirmed={len(confirmed)} unconfirmed={len(unconfirmed)}"
            ),
            extra={
                "included_keys": result["facts_included"][:40],
                "dropped_keys": [d["key"] for d in dropped[:40]],
            },
        )
        return result

    # ---------------------------------------------------------------- revisions
    def save_revision(
        self,
        project: Any,
        content: str,
        score: float,
        critique: str = "",
        evaluator_notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        notes = evaluator_notes or {}
        if self.backend == "memory":
            version = int(project.current_version) + 1
            rev = _MemRevision(
                id=str(uuid.uuid4()),
                version=version,
                content=content or "",
                score=float(score or 0),
                critique=critique or "",
                evaluator_notes=notes,
                created_at=_utcnow().isoformat(),
            )
            project.revisions.append(rev)
            project.current_version = version
            project.updated_at = rev.created_at
            if float(score or 0) >= 8.0:
                project.status = "ready"
            elif float(score or 0) > 0:
                project.status = "needs_review"
            payload = {
                "id": rev.id,
                "version": version,
                "score": rev.score,
                "critique": summarize(rev.critique, 200),
            }
        else:
            from apps.reasoning_engine.models import DraftRevision, GrantProject

            version = int(project.current_version) + 1
            rev = DraftRevision.objects.create(
                project=project,
                version=version,
                content=content or "",
                score=float(score or 0),
                critique=critique or "",
                evaluator_notes=notes,
            )
            project.current_version = version
            if float(score or 0) >= 8.0:
                project.status = GrantProject.Status.READY
            elif float(score or 0) > 0:
                project.status = GrantProject.Status.NEEDS_REVIEW
            project.save(
                update_fields=["current_version", "status", "updated_at"]
            )
            payload = {
                "id": str(rev.id),
                "version": version,
                "score": rev.score,
                "critique": summarize(rev.critique, 200),
            }

        self._trace(
            "draft_version_saved",
            f"project={self._project_id(project)} version={payload['version']}",
            f"score={payload['score']} chars={len(content or '')}",
        )
        return payload

    def load_latest_revision(self, project: Any) -> dict[str, Any] | None:
        if self.backend == "memory":
            if not project.revisions:
                return None
            rev = max(project.revisions, key=lambda r: r.version)
            return {
                "id": rev.id,
                "version": rev.version,
                "content": rev.content,
                "score": rev.score,
                "critique": rev.critique,
                "evaluator_notes": deepcopy(rev.evaluator_notes),
                "created_at": rev.created_at,
            }
        from apps.reasoning_engine.models import DraftRevision

        rev = (
            DraftRevision.objects.filter(project=project)
            .order_by("-version")
            .first()
        )
        if rev is None:
            return None
        return {
            "id": str(rev.id),
            "version": rev.version,
            "content": rev.content,
            "score": rev.score,
            "critique": rev.critique,
            "evaluator_notes": rev.evaluator_notes or {},
            "created_at": rev.created_at.isoformat(),
        }

    # ---------------------------------------------------------------- decisions
    def record_decision(
        self,
        project: Any,
        agent: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        retry_count: int = 0,
    ) -> dict[str, Any]:
        if self.backend == "memory":
            decision = _MemDecision(
                id=str(uuid.uuid4()),
                agent_name=agent,
                action=action,
                input_summary=summarize(input_summary, 400),
                output_summary=summarize(output_summary, 400),
                retry_count=int(retry_count or 0),
                created_at=_utcnow().isoformat(),
            )
            project.decisions.append(decision)
            payload = {
                "id": decision.id,
                "agent_name": agent,
                "action": action,
            }
        else:
            from apps.reasoning_engine.models import AgentDecision

            decision = AgentDecision.objects.create(
                project=project,
                agent_name=agent,
                action=action,
                input_summary=summarize(input_summary, 400),
                output_summary=summarize(output_summary, 400),
                retry_count=int(retry_count or 0),
            )
            payload = {
                "id": str(decision.id),
                "agent_name": agent,
                "action": action,
            }
        return payload

    # ------------------------------------------------------------------ resume
    def get_resume_context(self, project: Any) -> dict[str, Any]:
        recall = self.recall_facts(project)
        latest = self.load_latest_revision(project)
        excerpt = ""
        if latest and latest.get("content"):
            excerpt = summarize(latest["content"], 400)
        return {
            "project_id": self._project_id(project),
            "status": getattr(project, "status", "drafting"),
            "current_version": int(getattr(project, "current_version", 0) or 0),
            "grant_id": getattr(project, "grant_id", ""),
            "owner_key": summarize(getattr(project, "owner_key", ""), 24),
            "confirmed_facts": {
                k: v.get("value") if isinstance(v, dict) else v
                for k, v in (recall.get("confirmed_facts") or {}).items()
            },
            "unconfirmed_facts": recall.get("unconfirmed_facts") or {},
            "pending_questions": recall.get("pending_questions") or [],
            "latest_draft_excerpt": excerpt,
            "facts_included": recall.get("facts_included") or [],
            "facts_dropped": recall.get("facts_dropped") or [],
            "memory_backend": self.backend,
        }

    def ingest_portfolio_excerpt(self, project: Any, portfolio_text: str) -> None:
        text = (portfolio_text or "").strip()
        if not text:
            return
        self.upsert_fact(
            project,
            "portfolio.excerpt",
            summarize(text, 800),
            source="portfolio",
            confidence=0.7,
            confirmed=False,
        )
        # Lightweight inference (always unconfirmed).
        years = re.search(
            r"(\d{1,2})\s*anos?",
            text,
            flags=re.IGNORECASE,
        )
        if years:
            self.upsert_fact(
                project,
                "coletivo.anos_atuacao",
                years.group(1),
                source="agent_inference",
                confidence=0.4,
                confirmed=False,
            )

    # ----------------------------------------------------------------- helpers
    def _list_facts(self, project: Any) -> list[dict[str, Any]]:
        if self.backend == "memory":
            return [self._fact_dict(f) for f in project.facts.values()]
        return [self._fact_dict(f) for f in project.facts.all()]

    @staticmethod
    def _fact_dict(fact: Any) -> dict[str, Any]:
        return {
            "id": str(getattr(fact, "id")),
            "key": fact.key,
            "value": fact.value,
            "source": fact.source,
            "confidence": float(fact.confidence),
            "confirmed_by_user": bool(fact.confirmed_by_user),
            "created_at": (
                fact.created_at.isoformat()
                if hasattr(fact.created_at, "isoformat")
                else str(fact.created_at)
            ),
            "updated_at": (
                fact.updated_at.isoformat()
                if hasattr(fact.updated_at, "isoformat")
                else str(fact.updated_at)
            ),
        }

    @staticmethod
    def _project_id(project: Any) -> str:
        return str(getattr(project, "id", ""))

    def _trace(
        self,
        action: str,
        input_summary: str,
        output_summary: str,
        *,
        status: str = "success",
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.tracer is None:
            return
        self.tracer.log(
            agent_name="MemoryService",
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            status=status,
            provider="local_memory" if self.backend == "memory" else "django_memory",
            extra=extra,
        )
