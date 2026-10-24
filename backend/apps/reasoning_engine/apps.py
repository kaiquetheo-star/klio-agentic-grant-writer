from django.apps import AppConfig


class ReasoningEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reasoning_engine"
    verbose_name = "Reasoning Engine (Hackathon 2026)"

    def ready(self) -> None:
        # No signals / no side effects on production radar or other apps.
        return None
