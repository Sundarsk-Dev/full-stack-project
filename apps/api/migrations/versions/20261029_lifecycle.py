"""Saved comparisons and recoverable document deletion receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261029_lifecycle"
down_revision = "20261029_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("comparisons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("left_id", sa.String(36), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("right_id", sa.String(36), sa.ForeignKey("analyses.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("profile", sa.String(24), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False), sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    for field in ("owner_id", "left_id", "right_id"):
        op.create_index("ix_comparisons_" + field, "comparisons", [field])
    op.create_table("document_deletions",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False), sa.Column("cursor_job_id", sa.String(36)),
        sa.Column("retry_at", DATETIME(fsp=6), nullable=False), sa.Column("error_code", sa.String(40)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False), sa.Column("completed_at", DATETIME(fsp=6)),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    for field in ("owner_id", "state"):
        op.create_index("ix_document_deletions_" + field, "document_deletions", [field])


def downgrade():
    op.drop_table("comparisons")
    op.drop_table("document_deletions")
