"""H-MEM adapter — local persistence interface; remote sync pending official contract.

Meterless H-MEM remote endpoints are **not invented** here. Until an official
API contract is confirmed:

- SAMPLE_MODE / local → MemoryService (in-memory or Django SQLite)
- Live remote sync → controlled error: "H-MEM remote contract pending"
"""

from __future__ import annotations

import os
from typing import Any

from apps.reasoning_engine.services.memory_service import MemoryService, memory_backend
from apps.reasoning_engine.services.tracer import AgentTracer, summarize


class HMemRemoteContractPending(RuntimeError):
    """Raised when live remote H-MEM sync is requested without a confirmed API."""


def _is_sample_mode() -> bool:
    return os.getenv("SAMPLE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


class HMemAdapter:
    """Interface for project memory sync/restore.

    Local persistence is implemented via MemoryService.
    Remote Meterless H-MEM sync is explicitly unimplemented pending vendor docs.
    """

    PROVIDER_LOCAL = "local_memory"
    PROVIDER_REMOTE_PENDING = "hmem_remote_pending"

    def __init__(
        self,
        *,
        memory: MemoryService | None = None,
        tracer: AgentTracer | None = None,
        allow_remote: bool | None = None,
    ) -> None:
        self.tracer = tracer
        self.memory = memory or MemoryService(tracer=tracer)
        # Opt-in only — default never pretends remote sync works.
        if allow_remote is None:
            allow_remote = os.getenv("METERLESS_HMEM_ENABLED", "false").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self.allow_remote = bool(allow_remote)

    def health_check(self) -> dict[str, Any]:
        backend = memory_backend()
        if _is_sample_mode() or not self.allow_remote:
            result = {
                "status": "ok",
                "provider": self.PROVIDER_LOCAL,
                "backend": backend,
                "remote_sync": "pending_official_api_contract",
                "message": (
                    "local persistence implemented; "
                    "H-MEM remote sync pending official API contract"
                ),
            }
            self._trace("hmem_health", "local", summarize(str(result), 200))
            return result
        # Remote enabled but still no confirmed contract → not healthy for sync.
        result = {
            "status": "degraded",
            "provider": self.PROVIDER_REMOTE_PENDING,
            "backend": backend,
            "remote_sync": "pending_official_api_contract",
            "message": "H-MEM remote contract pending",
        }
        self._trace("hmem_health", "remote_flag_on", summarize(str(result), 200), status="needs_revision")
        return result

    def sync_project(self, project: Any) -> dict[str, Any]:
        """Push local project snapshot to remote H-MEM (not available yet)."""
        local = self.memory.get_resume_context(project)
        if _is_sample_mode() or not self.allow_remote:
            result = {
                "status": "ok",
                "provider": self.PROVIDER_LOCAL,
                "synced": False,
                "local_only": True,
                "project_id": local.get("project_id"),
                "current_version": local.get("current_version"),
                "message": (
                    "local persistence implemented; "
                    "H-MEM remote sync pending official API contract"
                ),
            }
            self._trace(
                "hmem_sync",
                f"project={local.get('project_id')}",
                "local_only — remote contract pending",
            )
            return result

        self._trace(
            "hmem_sync",
            f"project={local.get('project_id')}",
            "H-MEM remote contract pending",
            status="error",
        )
        raise HMemRemoteContractPending(
            "H-MEM remote contract pending — no official Meterless H-MEM "
            "endpoint documented; refusing to invent a sync URL."
        )

    def restore_project(self, owner_key: str, grant_id: str) -> dict[str, Any]:
        """Restore project memory from local store (remote restore pending)."""
        project = self.memory.get_project(owner_key, grant_id)
        if project is None:
            result = {
                "status": "not_found",
                "provider": self.PROVIDER_LOCAL,
                "owner_key": summarize(owner_key, 24),
                "grant_id": grant_id,
                "message": "no local project memory for this owner_key + grant_id",
            }
            self._trace("hmem_restore", f"grant={grant_id}", "not_found", status="needs_revision")
            return result

        resume = self.memory.get_resume_context(project)
        if self.allow_remote and not _is_sample_mode():
            # Do not fabricate remote fetch — annotate pending only.
            resume["remote_restore"] = "pending_official_api_contract"
            resume["message"] = "H-MEM remote contract pending; returned local snapshot only"
        resume["status_code"] = "ok"
        resume["provider"] = self.PROVIDER_LOCAL
        self._trace(
            "hmem_restore",
            f"grant={grant_id} project={resume.get('project_id')}",
            f"version={resume.get('current_version')} facts={len(resume.get('confirmed_facts') or {})}",
        )
        return resume

    def _trace(
        self,
        action: str,
        input_summary: str,
        output_summary: str,
        *,
        status: str = "success",
    ) -> None:
        if self.tracer is None:
            return
        self.tracer.log(
            agent_name="HMemAdapter",
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            status=status,
            provider=self.PROVIDER_LOCAL,
        )
