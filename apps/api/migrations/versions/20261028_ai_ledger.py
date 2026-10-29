"""Durable AI attempts and conservative quota windows; frozen DDL."""

from alembic import op

revision = "20261028_ai_ledger"
down_revision = "20261028_debug"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE ai_budget_windows (
	id VARCHAR(64) NOT NULL, 
	resource VARCHAR(24) NOT NULL, 
	period_seconds INTEGER NOT NULL, 
	capacity INTEGER NOT NULL, 
	used INTEGER NOT NULL, 
	generation VARCHAR(36) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE ai_calls (
	id VARCHAR(36) NOT NULL, 
	job_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	attempt_number INTEGER NOT NULL, 
	retry_of VARCHAR(36), 
	identity VARCHAR(64) NOT NULL, 
	batch_hash VARCHAR(64) NOT NULL, 
	snapshot VARCHAR(64) NOT NULL, 
	provider VARCHAR(24) NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	prompt_revision VARCHAR(64) NOT NULL, 
	clause_ids JSON NOT NULL, 
	request_payload JSON NOT NULL, 
	input_reserved INTEGER NOT NULL, 
	output_reserved INTEGER NOT NULL, 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	budget_charges JSON NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	lease_token VARCHAR(36) NOT NULL, 
	dispatch_before DATETIME(6) NOT NULL, 
	retry_after DATETIME(6), 
	response_text LONGTEXT, 
	error_code VARCHAR(40), 
	created_at DATETIME(6) NOT NULL, 
	finished_at DATETIME(6), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ai_job_attempt UNIQUE (job_id, attempt_number), 
	FOREIGN KEY(job_id) REFERENCES jobs (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id), 
	FOREIGN KEY(retry_of) REFERENCES ai_calls (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_ai_calls_job_id ON ai_calls (job_id)""",
)

def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)

def downgrade():
    op.drop_table("ai_calls")
    op.drop_table("ai_budget_windows")
