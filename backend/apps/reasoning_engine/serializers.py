"""DRF serializers for the reasoning engine API."""

from __future__ import annotations

from rest_framework import serializers

ALLOWED_LANGUAGES = ("pt-BR", "en", "es")


class DraftRequestSerializer(serializers.Serializer):
    grant_id = serializers.CharField(max_length=128, allow_blank=False, trim_whitespace=True)
    user_portfolio_text = serializers.CharField(
        allow_blank=True,
        required=False,
        default="",
        max_length=50_000,
        trim_whitespace=False,
    )
    output_language = serializers.ChoiceField(choices=ALLOWED_LANGUAGES, default="pt-BR")
    owner_key = serializers.CharField(
        max_length=128,
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
        help_text="Opaque owner id (hash). Never email/CPF/phone.",
    )
    resume_session = serializers.BooleanField(required=False, default=False)
    confirmed_facts = serializers.DictField(
        child=serializers.CharField(allow_blank=False, max_length=4000),
        required=False,
        default=dict,
    )


class FactUpsertSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True)
    value = serializers.CharField(max_length=8000, allow_blank=False)
    confirmed = serializers.BooleanField(default=True)
    owner_key = serializers.CharField(
        max_length=128,
        required=False,
        allow_blank=True,
        default="",
        help_text="Required when using in-memory backend lookups by project_id alone is enough.",
    )


class AgentInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    role = serializers.CharField()


class DraftOutputSerializer(serializers.Serializer):
    draft = serializers.CharField(allow_blank=True)
    score = serializers.FloatField(min_value=0, max_value=10)
    critique = serializers.CharField(allow_blank=True)
    revisions = serializers.IntegerField(min_value=0, max_value=3)


class DraftResponseSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=("success", "error", "needs_review"))
    output = DraftOutputSerializer()
    agents = AgentInfoSerializer(many=True)
    trace_id = serializers.CharField()
    log_file = serializers.CharField()
    execution_time_seconds = serializers.FloatField(min_value=0)
    sample_mode = serializers.BooleanField(required=False)
    error = serializers.CharField(required=False, allow_blank=True)
