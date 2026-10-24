"""URLConf — only Reasoning Engine routes (+ health)."""

from __future__ import annotations

from django.http import JsonResponse
from django.urls import include, path
from django.conf import settings


def health(_request):
    return JsonResponse({"status": "ok", "app": "reasoning_engine", "sample_mode": True})


urlpatterns = [
    path("api/health/", health, name="health"),
]

if getattr(settings, "REASONING_ENGINE_ENABLED", True):
    urlpatterns += [
        path(
            "api/reasoning_engine/",
            include("apps.reasoning_engine.urls"),
        ),
    ]
