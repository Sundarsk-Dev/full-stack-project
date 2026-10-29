from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    test_mode: bool = False
    allowed_origins: frozenset[str] = frozenset({"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"})
    secure_cookies: bool = False
    session_hours: int = 12
    lease_seconds: int = 120
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = ""
    groq_enabled: bool = False
    groq_readiness_file: str = "var/groq-readiness.json"

    @classmethod
    def from_env(cls, *, test=False):
        load_dotenv(ROOT / ".env", override=False)
        origins = os.getenv("TERMSLENS_ALLOWED_ORIGINS")
        return cls(
            test_mode=test,
            database_url=os.getenv("TERMSLENS_TEST_DATABASE_URL" if test else "TERMSLENS_DATABASE_URL", ""),
            allowed_origins=frozenset(s.strip() for s in origins.split(",") if s.strip()) if origins else cls.allowed_origins,
            secure_cookies=os.getenv("TERMSLENS_SECURE_COOKIES", "false").lower() == "true",
            # Test schemas must never spend production provider capacity.
            groq_api_key="" if test else os.getenv("GROQ_API_KEY", ""),
            groq_model="" if test else os.getenv("TERMSLENS_GROQ_MODEL", ""),
            groq_enabled=not test and os.getenv("TERMSLENS_GROQ_ENABLED", "false").lower() == "true",
            groq_readiness_file="" if test else os.getenv("TERMSLENS_GROQ_READINESS_FILE", "var/groq-readiness.json"),
        )
