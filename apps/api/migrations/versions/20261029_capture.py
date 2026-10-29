"""URL job input and immutable capture metadata."""
from alembic import op
import sqlalchemy as sa

revision = "20261029_capture"
down_revision = "20261029_url"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("input_url", sa.String(2048), nullable=True))
    op.add_column("document_versions", sa.Column("capture_metadata", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("document_versions", "capture_metadata")
    op.drop_column("jobs", "input_url")
