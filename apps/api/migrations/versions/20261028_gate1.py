"""Initial MySQL schema, frozen independently of live ORM models."""
from alembic import op

revision = "20261028_gate1"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(32) NOT NULL, 
	password_hash VARCHAR(512) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	document_type VARCHAR(16) NOT NULL, 
	source_kind VARCHAR(16) NOT NULL, 
	tombstoned BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_documents_owner_id ON documents (owner_id)""",
    """CREATE TABLE sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_sessions_expires_at ON sessions (expires_at)""",
    """CREATE INDEX ix_sessions_user_id ON sessions (user_id)""",
    """CREATE TABLE document_versions (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	version_number INTEGER NOT NULL, 
	parent_id VARCHAR(36), 
	text LONGTEXT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	extractor_version VARCHAR(64) NOT NULL, 
	snapshot_path VARCHAR(512), 
	captured_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_document_version UNIQUE (document_id, version_number), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(parent_id) REFERENCES document_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE analyses (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	requested_mode VARCHAR(24) NOT NULL, 
	effective_mode VARCHAR(24) NOT NULL, 
	engine JSON NOT NULL, 
	supported_groups JSON NOT NULL, 
	coverage FLOAT NOT NULL, 
	best FLOAT NOT NULL, 
	worst FLOAT NOT NULL, 
	grade VARCHAR(1), 
	scope_consistent BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_analyses_owner_created ON analyses (owner_id, created_at)""",
    """CREATE INDEX ix_analyses_version_id ON analyses (version_id)""",
    """CREATE TABLE category_scores (
	analysis_id VARCHAR(36) NOT NULL, 
	category INTEGER NOT NULL, 
	max_severity INTEGER NOT NULL, 
	unknown BOOL NOT NULL, 
	applicable BOOL NOT NULL, 
	accepted_count INTEGER NOT NULL, 
	PRIMARY KEY (analysis_id, category), 
	FOREIGN KEY(analysis_id) REFERENCES analyses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE clauses (
	id VARCHAR(36) NOT NULL, 
	analysis_id VARCHAR(36) NOT NULL, 
	local_id INTEGER NOT NULL, 
	start INTEGER NOT NULL, 
	end INTEGER NOT NULL, 
	substantive BOOL NOT NULL, 
	unrated_reason VARCHAR(240), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analysis_clause UNIQUE (analysis_id, local_id), 
	FOREIGN KEY(analysis_id) REFERENCES analyses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	input_text LONGTEXT NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	document_type VARCHAR(16) NOT NULL, 
	requested_mode VARCHAR(24) NOT NULL, 
	consent BOOL NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	stage VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	lease_token VARCHAR(36), 
	lease_expires_at DATETIME(6), 
	version_id VARCHAR(36), 
	result_id VARCHAR(36), 
	error VARCHAR(240), 
	created_at DATETIME(6) NOT NULL, 
	finished_at DATETIME(6), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_owner_idempotency UNIQUE (owner_id, idempotency_key), 
	FOREIGN KEY(owner_id) REFERENCES users (id), 
	FOREIGN KEY(version_id) REFERENCES document_versions (id), 
	UNIQUE (result_id), 
	FOREIGN KEY(result_id) REFERENCES analyses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_jobs_state_created ON jobs (state, created_at)""",
    """CREATE INDEX ix_jobs_state_lease ON jobs (state, lease_expires_at)""",
    """CREATE TABLE clause_findings (
	id VARCHAR(36) NOT NULL, 
	clause_id VARCHAR(36) NOT NULL, 
	category INTEGER NOT NULL, 
	rating VARCHAR(16) NOT NULL, 
	severity INTEGER NOT NULL, 
	quote LONGTEXT NOT NULL, 
	start INTEGER NOT NULL, 
	end INTEGER NOT NULL, 
	explanation TEXT NOT NULL, 
	source VARCHAR(24) NOT NULL, 
	rule_id VARCHAR(80) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(clause_id) REFERENCES clauses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE INDEX ix_clause_findings_clause_id ON clause_findings (clause_id)""",
)

def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)

def downgrade():
    op.drop_table('clause_findings')
    op.drop_table('jobs')
    op.drop_table('clauses')
    op.drop_table('category_scores')
    op.drop_table('analyses')
    op.drop_table('document_versions')
    op.drop_table('sessions')
    op.drop_table('documents')
    op.drop_table('users')
