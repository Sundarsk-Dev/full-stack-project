"""Persist consent, review outcomes and finding provenance without rewriting reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "20261028_review"
down_revision = "20261028_ai_ledger"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("jobs", "analyses"):
        op.add_column(table, sa.Column("consent_provider", sa.String(24), nullable=True))
        op.add_column(table, sa.Column("consent_at", DATETIME(fsp=6), nullable=True))
    for column in (
        sa.Column("review_status", sa.String(24), nullable=True),
        sa.Column("degradation_reason", sa.String(40), nullable=True),
        sa.Column("unresolved_count", sa.Integer(), nullable=True),
        sa.Column("accepted_ai_findings", sa.Integer(), nullable=True),
        sa.Column("rules_baseline", sa.JSON(), nullable=True),
        sa.Column("ai_provenance", sa.JSON(), nullable=True),
    ):
        op.add_column("analyses", column)
    op.add_column("clause_findings", sa.Column("ai_call_id", sa.String(36), nullable=True))
    op.create_index("ix_clause_findings_ai_call_id", "clause_findings", ["ai_call_id"])
    op.create_foreign_key("fk_finding_ai_call", "clause_findings", "ai_calls", ["ai_call_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_finding_ai_call", "clause_findings", type_="foreignkey")
    op.drop_index("ix_clause_findings_ai_call_id", table_name="clause_findings")
    op.drop_column("clause_findings", "ai_call_id")
    for name in ("review_status", "degradation_reason", "unresolved_count", "accepted_ai_findings", "rules_baseline", "ai_provenance"):
        op.drop_column("analyses", name)
    for table in ("analyses", "jobs"):
        op.drop_column(table, "consent_at")
        op.drop_column(table, "consent_provider")
