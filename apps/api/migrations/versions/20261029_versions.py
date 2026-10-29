"""Ordered report revisions and owned refresh/reanalysis jobs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261029_versions"
down_revision = "20261029_capture"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("operation", sa.String(16), nullable=False, server_default="new"))
    op.add_column("jobs", sa.Column("document_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_jobs_document", "jobs", "documents", ["document_id"], ["id"])
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"])
    op.execute("UPDATE jobs j JOIN document_versions v ON j.version_id = v.id SET j.document_id = v.document_id")
    op.add_column("jobs", sa.Column("checked_at", DATETIME(fsp=6), nullable=True))
    op.add_column("jobs", sa.Column("check_metadata", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("capture_changed", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("result_reused", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Keep an FK-supporting index before removing the initial schema's unique index.
    op.create_index("ix_jobs_result_id", "jobs", ["result_id"])
    op.drop_index("result_id", table_name="jobs")
    op.add_column("analyses", sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
    op.execute("""UPDATE analyses a JOIN (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY version_id ORDER BY created_at, id) AS revision
        FROM analyses) ordered ON a.id = ordered.id SET a.revision_number = ordered.revision""")
    op.create_unique_constraint("uq_analysis_revision", "analyses", ["version_id", "revision_number"])
    op.add_column("analyses", sa.Column("profile_revision", sa.String(64), nullable=True))


def downgrade():
    # Refuse downgrade when reused results exist; never discard jobs to force uniqueness.
    op.create_unique_constraint("result_id", "jobs", ["result_id"])
    op.drop_index("ix_jobs_result_id", table_name="jobs")
    op.drop_column("analyses", "profile_revision")
    op.drop_constraint("uq_analysis_revision", "analyses", type_="unique")
    op.drop_column("analyses", "revision_number")
    op.drop_constraint("fk_jobs_document", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_document_id", table_name="jobs")
    for name in ("result_reused", "capture_changed", "check_metadata", "checked_at", "document_id", "operation"):
        op.drop_column("jobs", name)
