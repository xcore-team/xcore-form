"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── xform_forms ──────────────────────────────────────────────────────────
    op.create_table(
        "xform_forms",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("fields", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("steps", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("settings", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("theme", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("tags", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_xform_forms_slug", "xform_forms", ["slug"], unique=True)
    op.create_index("ix_xform_forms_owner_id", "xform_forms", ["owner_id"])
    op.create_index("ix_xform_forms_status", "xform_forms", ["status"])

    # ── xform_submissions ────────────────────────────────────────────────────
    op.create_table(
        "xform_submissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "form_id",
            sa.String(64),
            sa.ForeignKey("xform_forms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("meta", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_xform_submissions_form_id", "xform_submissions", ["form_id"])
    op.create_index("ix_xform_submissions_status", "xform_submissions", ["status"])
    op.create_index("ix_xform_submissions_created_at", "xform_submissions", ["created_at"])

    # ── xform_views ──────────────────────────────────────────────────────────
    op.create_table(
        "xform_views",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "form_id",
            sa.String(64),
            sa.ForeignKey("xform_forms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_xform_views_form_id", "xform_views", ["form_id"])

    # ── xform_pipeline_logs ──────────────────────────────────────────────────
    op.create_table(
        "xform_pipeline_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            sa.String(64),
            sa.ForeignKey("xform_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("form_id", sa.String(64), nullable=False),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_xform_pipeline_logs_submission_id",
        "xform_pipeline_logs",
        ["submission_id"],
    )
    op.create_index(
        "ix_xform_pipeline_logs_form_id",
        "xform_pipeline_logs",
        ["form_id"],
    )


def downgrade() -> None:
    op.drop_table("xform_pipeline_logs")
    op.drop_table("xform_views")
    op.drop_table("xform_submissions")
    op.drop_table("xform_forms")
