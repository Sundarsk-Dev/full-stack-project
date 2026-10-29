"""Local, expiring readiness attestations. Reading configuration never calls Groq."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .ai_contract import ESTIMATOR_REVISION
from .ai_ledger import LedgerBlocked, QuotaLimit, QuotaPolicy, validate_policy
from .config import ROOT
from .db import utcnow


class LimitRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    resource: str
    period_seconds: int
    capacity: int


class ReadinessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    account_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str
    verified_at: datetime
    valid_until: datetime
    free_access_verified: bool
    exclusive_account_usage_verified: bool
    limits_complete_verified: bool
    estimator_verified: bool
    estimator_revision: str
    verification_reference: str = Field(min_length=1, max_length=1024)
    limits: list[LimitRecord] = Field(min_length=1, max_length=16)


@dataclass(frozen=True)
class GroqReadiness:
    reason: str
    policy: QuotaPolicy | None = None

    def public_status(self):
        return {"available": self.policy is not None, "provider": "groq", "reason": self.reason}


def groq_readiness(settings):
    if not settings.groq_enabled: return GroqReadiness("disabled")
    key = settings.groq_api_key
    if not key or len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key):
        return GroqReadiness("key_missing_or_invalid")
    if not settings.groq_model: return GroqReadiness("model_missing")
    if not settings.groq_readiness_file: return GroqReadiness("readiness_missing_or_invalid")
    try:
        path = Path(settings.groq_readiness_file)
        if not path.is_absolute(): path = ROOT / path
        with path.open("rb") as stream: raw = stream.read(16385)
        if len(raw) > 16384: return GroqReadiness("readiness_missing_or_invalid")
        record = ReadinessRecord.model_validate_json(raw)
        if record.model != settings.groq_model: return GroqReadiness("model_mismatch")
        if not all((record.free_access_verified, record.exclusive_account_usage_verified,
                    record.limits_complete_verified, record.estimator_verified)):
            return GroqReadiness("verification_incomplete")
        if record.estimator_revision != ESTIMATOR_REVISION or not record.verification_reference.strip():
            return GroqReadiness("verification_incomplete")
        if record.verified_at.tzinfo is None or record.valid_until.tzinfo is None:
            return GroqReadiness("verification_expired_or_invalid")
        verified = record.verified_at.astimezone(timezone.utc).replace(tzinfo=None)
        expires = record.valid_until.astimezone(timezone.utc).replace(tzinfo=None)
        now = utcnow()
        if not verified <= now < expires <= verified + timedelta(hours=24):
            return GroqReadiness("verification_expired_or_invalid")
        policy = QuotaPolicy(record.account_id, record.model, expires,
                             tuple(QuotaLimit(**limit.model_dump()) for limit in record.limits))
        validate_policy(policy, now)
        return GroqReadiness("configured", policy)
    except (OSError, ValueError, ValidationError, LedgerBlocked):
        # Validation errors can echo private file contents; expose only fixed codes.
        return GroqReadiness("readiness_missing_or_invalid")
