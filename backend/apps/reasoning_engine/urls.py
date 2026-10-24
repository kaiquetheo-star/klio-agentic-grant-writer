from django.urls import path

from apps.reasoning_engine.views import (
    DraftAPIView,
    ProjectFactAPIView,
    ProjectResumeAPIView,
)

app_name = "reasoning_engine"

urlpatterns = [
    path("draft/", DraftAPIView.as_view(), name="draft"),
    path(
        "projects/<str:grant_id>/resume/",
        ProjectResumeAPIView.as_view(),
        name="project_resume",
    ),
    path(
        "projects/<uuid:project_id>/facts/",
        ProjectFactAPIView.as_view(),
        name="project_facts",
    ),
]
