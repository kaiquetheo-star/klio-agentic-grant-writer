"""Persistent project memory for the Reasoning Engine (local Django models).

Isolated from Klio radar / accounts / billing. owner_key must be opaque
(never email, CPF, or phone). Sensitive third-party PII is rejected at write time
by MemoryService — not stored here.
"""

from __future__ import annotations

import uuid

from django.db import models


class GrantProject(models.Model):
    """Long-lived grant-writing session for one opaque owner + grant_id."""

    class Status(models.TextChoices):
        DRAFTING = "drafting", "Drafting"
        PAUSED = "paused", "Paused"
        NEEDS_REVIEW = "needs_review", "Needs review"
        READY = "ready", "Ready"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_key = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Opaque owner id (hash). Never email/CPF/phone.",
    )
    grant_id = models.CharField(max_length=128, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFTING,
    )
    current_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "reasoning_engine"
        constraints = [
            models.UniqueConstraint(
                fields=("owner_key", "grant_id"),
                name="re_grantproject_owner_grant_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("owner_key", "grant_id")),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"GrantProject({self.grant_id}, v{self.current_version})"


class ProjectFact(models.Model):
    """A single remembered fact with provenance."""

    class Source(models.TextChoices):
        USER_INPUT = "user_input", "User input"
        PORTFOLIO = "portfolio", "Portfolio"
        GRANT_RUBRIC = "grant_rubric", "Grant rubric"
        AGENT_INFERENCE = "agent_inference", "Agent inference"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        GrantProject,
        on_delete=models.CASCADE,
        related_name="facts",
    )
    key = models.CharField(max_length=200, db_index=True)
    value = models.TextField()
    source = models.CharField(max_length=32, choices=Source.choices)
    confidence = models.FloatField(default=0.5)
    confirmed_by_user = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "reasoning_engine"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "key"),
                name="re_projectfact_project_key_uniq",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"ProjectFact({self.key}, confirmed={self.confirmed_by_user})"


class DraftRevision(models.Model):
    """Versioned draft snapshot after an orchestrator run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        GrantProject,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    version = models.PositiveIntegerField()
    content = models.TextField()
    score = models.FloatField(default=0.0)
    critique = models.TextField(blank=True, default="")
    evaluator_notes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "reasoning_engine"
        constraints = [
            models.UniqueConstraint(
                fields=("project", "version"),
                name="re_draftrevision_project_version_uniq",
            ),
        ]
        ordering = ["-version"]

    def __str__(self) -> str:  # pragma: no cover
        return f"DraftRevision(v{self.version}, score={self.score})"


class AgentDecision(models.Model):
    """Auditable agent handoff / decision (summaries only — no secrets)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        GrantProject,
        on_delete=models.CASCADE,
        related_name="decisions",
    )
    agent_name = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    input_summary = models.TextField(blank=True, default="")
    output_summary = models.TextField(blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "reasoning_engine"
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"AgentDecision({self.agent_name}:{self.action})"
