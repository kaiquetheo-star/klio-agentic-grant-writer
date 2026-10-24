# Generated manually for Reasoning Engine local memory (hackathon).

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GrantProject",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "owner_key",
                    models.CharField(
                        db_index=True,
                        help_text="Opaque owner id (hash). Never email/CPF/phone.",
                        max_length=128,
                    ),
                ),
                ("grant_id", models.CharField(db_index=True, max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("drafting", "Drafting"),
                            ("paused", "Paused"),
                            ("needs_review", "Needs review"),
                            ("ready", "Ready"),
                        ],
                        default="drafting",
                        max_length=32,
                    ),
                ),
                ("current_version", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["owner_key", "grant_id"],
                        name="reasoning_e_owner_k_7c9a1a_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProjectFact",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("key", models.CharField(db_index=True, max_length=200)),
                ("value", models.TextField()),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("user_input", "User input"),
                            ("portfolio", "Portfolio"),
                            ("grant_rubric", "Grant rubric"),
                            ("agent_inference", "Agent inference"),
                        ],
                        max_length=32,
                    ),
                ),
                ("confidence", models.FloatField(default=0.5)),
                ("confirmed_by_user", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="facts",
                        to="reasoning_engine.grantproject",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DraftRevision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("version", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("score", models.FloatField(default=0.0)),
                ("critique", models.TextField(blank=True, default="")),
                ("evaluator_notes", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="reasoning_engine.grantproject",
                    ),
                ),
            ],
            options={
                "ordering": ["-version"],
            },
        ),
        migrations.CreateModel(
            name="AgentDecision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("agent_name", models.CharField(max_length=64)),
                ("action", models.CharField(max_length=64)),
                ("input_summary", models.TextField(blank=True, default="")),
                ("output_summary", models.TextField(blank=True, default="")),
                ("retry_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="decisions",
                        to="reasoning_engine.grantproject",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="grantproject",
            constraint=models.UniqueConstraint(
                fields=("owner_key", "grant_id"),
                name="re_grantproject_owner_grant_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="projectfact",
            constraint=models.UniqueConstraint(
                fields=("project", "key"),
                name="re_projectfact_project_key_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="draftrevision",
            constraint=models.UniqueConstraint(
                fields=("project", "version"),
                name="re_draftrevision_project_version_uniq",
            ),
        ),
    ]
