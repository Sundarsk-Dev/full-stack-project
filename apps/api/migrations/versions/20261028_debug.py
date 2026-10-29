"""Add safe request/job correlation and last-failure metadata."""
from alembic import op
import sqlalchemy as sa

revision = "20261028_debug"
down_revision = "20261028_gate1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("request_id", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("error_code", sa.String(40), nullable=True))
    op.add_column("jobs", sa.Column("error_stage", sa.String(24), nullable=True))
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"])


def downgrade():
    op.drop_index("ix_jobs_request_id", table_name="jobs")
    op.drop_column("jobs", "error_stage")
    op.drop_column("jobs", "error_code")
    op.drop_column("jobs", "request_id")
