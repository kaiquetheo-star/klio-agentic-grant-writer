"""HTTP API for the hackathon reasoning engine."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reasoning_engine.serializers import DraftRequestSerializer, FactUpsertSerializer
from apps.reasoning_engine.services.memory_service import MemoryService
from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator
from apps.reasoning_engine.services.tracer import summarize


class DraftAPIView(APIView):
    """POST /api/reasoning_engine/draft/ — programmatic entry for judges."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = DraftRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = ReasoningOrchestrator().run(
            grant_id=data["grant_id"],
            user_portfolio_text=data.get("user_portfolio_text") or "",
            output_language=data["output_language"],
            owner_key=(data.get("owner_key") or "").strip() or None,
            resume_session=bool(data.get("resume_session")),
            confirmed_facts=dict(data.get("confirmed_facts") or {}),
        )

        http_status = status.HTTP_200_OK
        if result.get("status") == "error":
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        elif result.get("status") == "needs_review":
            http_status = status.HTTP_200_OK

        return Response(result, status=http_status)


class ProjectResumeAPIView(APIView):
    """GET /api/reasoning_engine/projects/<grant_id>/resume/?owner_key=..."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, grant_id: str, *args, **kwargs):
        owner_key = (request.query_params.get("owner_key") or "").strip()
        if not owner_key:
            return Response(
                {
                    "status": "error",
                    "error": {
                        "type": "ValidationError",
                        "message": "owner_key query param is required (opaque hash).",
                        "recoverable": False,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        memory = MemoryService()
        project = memory.get_project(owner_key, grant_id)
        if project is None:
            return Response(
                {
                    "status": "not_found",
                    "grant_id": grant_id,
                    "message": "No project memory for this owner_key + grant_id",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        resume = memory.get_resume_context(project)
        return Response(
            {
                "project_id": resume.get("project_id"),
                "status": resume.get("status"),
                "current_version": resume.get("current_version"),
                "confirmed_facts": resume.get("confirmed_facts") or {},
                "pending_questions": resume.get("pending_questions") or [],
                "latest_draft_excerpt": resume.get("latest_draft_excerpt") or "",
                "memory_backend": resume.get("memory_backend"),
            },
            status=status.HTTP_200_OK,
        )


class ProjectFactAPIView(APIView):
    """POST /api/reasoning_engine/projects/<project_id>/facts/"""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request, project_id: str, *args, **kwargs):
        serializer = FactUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        memory = MemoryService()
        project = memory.get_project_by_id(project_id)
        if project is None:
            return Response(
                {
                    "status": "not_found",
                    "message": f"project_id not found: {summarize(project_id, 64)}",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        fact = memory.upsert_fact(
            project,
            key=data["key"],
            value=data["value"],
            source="user_input",
            confidence=1.0,
            confirmed=bool(data.get("confirmed", True)),
        )
        if fact is None:
            return Response(
                {
                    "status": "error",
                    "error": {
                        "type": "SensitivePayloadRejected",
                        "message": "Fact rejected (empty or sensitive PII/token payload).",
                        "recoverable": False,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "status": "ok",
                "project_id": str(getattr(project, "id", project_id)),
                "fact": {
                    "key": fact["key"],
                    "value": summarize(fact["value"], 200),
                    "source": fact["source"],
                    "confirmed_by_user": fact["confirmed_by_user"],
                    "confidence": fact["confidence"],
                },
            },
            status=status.HTTP_200_OK,
        )
