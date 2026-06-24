"""add xform_files table

Revision ID: 0002_add_files_table
Revises: 0001_initial
Create Date: 2026-06-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_files_table"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "xform_files",
        sa.Column("id", sa.String(64), primary_key=True),  # file_id
        sa.Column(
            "form_id",
            sa.String(64),
            sa.ForeignKey("xform_forms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submission_id",
            sa.String(64),
            sa.ForeignKey("xform_submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("stored_name", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_xform_files_form_id", "xform_files", ["form_id"])
    op.create_index("ix_xform_files_submission_id", "xform_files", ["submission_id"])


def downgrade() -> None:
    op.drop_table("xform_files")
