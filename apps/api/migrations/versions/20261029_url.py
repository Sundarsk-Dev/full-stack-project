"""Coordinate URL fetches across API/worker processes without changing saved reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261029_url"
down_revision = "20261028_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("fetch_hosts",
        sa.Column("host", sa.String(253), primary_key=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", DATETIME(fsp=6), nullable=True),
        sa.Column("last_finished_at", DATETIME(fsp=6), nullable=True),
        sa.Column("interval_seconds", sa.Float(), nullable=False, server_default="1"))


def downgrade():
    op.drop_table("fetch_hosts")
