"""Verify this demo's saved evidence/model/tokenizer, then enable Groq. No inference."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

import httpx

from app.ai_contract import ESTIMATOR_REVISION, estimate_input_tokens, prepare_review
from app.analysis import analyze
from app.config import Settings
from app.groq_config import ReadinessRecord, groq_readiness

EVIDENCE = ROOT / "var/groq-demo-evidence.json"


class SetupError(Exception):
    """Fixed messages only; never wrap provider, parser or tokenizer exceptions."""


def verify_estimator():
    try:
        from openai_harmony import (Conversation, DeveloperContent, HarmonyEncodingName,
                                   Message, ReasoningEffort, Role, SystemContent, load_harmony_encoding)
    except ImportError:
        raise SetupError("Install the one-time verifier: .venv/Scripts/python.exe -m pip install openai-harmony") from None
    encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)

    def render(system, user):
        meta = SystemContent.new().with_reasoning_effort(ReasoningEffort.MEDIUM)
        meta = meta.with_conversation_start_date(datetime.now(timezone.utc).date().isoformat())
        conversation = Conversation.from_messages([
            Message.from_role_and_content(Role.SYSTEM, meta),
            Message.from_role_and_content(Role.DEVELOPER, DeveloperContent.new().with_instructions(system)),
            Message.from_role_and_content(Role.USER, user),
        ])
        return len(encoding.render_conversation_for_completion(conversation, Role.ASSISTANT))

    # Byte-level BPE cannot produce more text tokens than UTF-8 bytes. Measure the
    # chosen model's no-tool framing, then check both that upper bound and tokens.
    framing = render("", "")
    if not 0 < framing <= 256:
        raise SetupError("Model framing exceeds the estimator allowance; AI remains disabled.")
    sample = (ROOT / "eval/fixtures/wordpress-terms-demo.md").read_text(encoding="utf-8")
    batches, _ = prepare_review(analyze(sample, "terms"), model="openai/gpt-oss-20b", consent=True)
    if len(batches) != 1:
        raise SetupError("The demo must prepare exactly one AI batch.")
    payload = batches[0].payload
    measured = render(*(message["content"] for message in payload["messages"]))
    text_bytes = sum(len(message["content"].encode("utf-8")) for message in payload["messages"])
    reserved = estimate_input_tokens(payload)
    if not 0 < measured <= text_bytes + framing <= reserved:
        raise SetupError("Token verification failed; AI remains disabled.")
    return {"encoding": "HARMONY_GPT_OSS", "framing_tokens": framing,
            "demo_input_tokens": measured, "demo_input_reserved": reserved,
            "sample_sha256": sha256(sample.encode("utf-8")).hexdigest(),
            "scope": "Official no-tool model framing; live Groq usage still requires reconciliation."}


def verify_model(settings):
    deadline = monotonic() + 15
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        with client.stream("GET", "https://api.groq.com/openai/v1/models",
                           headers={"Authorization": "Bearer " + settings.groq_api_key}) as response:
            if response.status_code != 200:
                raise SetupError(f"Groq model lookup returned HTTP {response.status_code}; no inference was sent.")
            raw = bytearray()
            for chunk in response.iter_bytes():
                if monotonic() >= deadline or len(raw) + len(chunk) > 1048576:
                    raise SetupError("Groq model lookup exceeded its bound; no inference was sent.")
                raw.extend(chunk)
    models = json.loads(raw).get("data", [])
    if not any(model.get("id") == settings.groq_model and model.get("active", True) is True for model in models):
        raise SetupError("The saved model is unavailable for the saved key; no inference was sent.")


def configure():
    settings = replace(Settings.from_env(), groq_enabled=True)
    status = groq_readiness(settings)
    if status.policy:
        print("Readiness already configured. Restart API/worker with Groq enabled; no inference sent.")
        return
    if status.reason in {"key_missing_or_invalid", "model_missing"}:
        raise SetupError("Keep the Groq key/model in private .env before setup.")
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    confirmed = datetime.fromisoformat(evidence["account_confirmed_at"])
    if not (evidence["account_and_existing_usage_verified"] is True and confirmed <= now < confirmed + timedelta(hours=24)):
        raise SetupError("Account confirmation is missing/expired; verify free access and account usage before setup.")
    if settings.groq_model != evidence["model"] or settings.groq_model != "openai/gpt-oss-20b":
        raise SetupError("This evidence covers only the saved openai/gpt-oss-20b model.")
    path = Path(settings.groq_readiness_file)
    if not path.is_absolute():
        path = ROOT / path
    if path.resolve() != (ROOT / "var/groq-readiness.json").resolve():
        raise SetupError("Use var/groq-readiness.json for this local demo setup.")
    if path.exists():
        raise SetupError("An existing readiness record needs review; setup will not replace or renew it.")
    env_path = ROOT / ".env"
    original = env_path.read_bytes()
    estimate = verify_estimator()
    verify_model(settings)
    record = ReadinessRecord(schema_version=1,
        account_id=sha256(evidence["account_reference"].encode("utf-8")).hexdigest(),
        model=settings.groq_model, verified_at=now,
        valid_until=min(now + timedelta(hours=4), confirmed + timedelta(hours=24)),
        free_access_verified=True, exclusive_account_usage_verified=True, limits_complete_verified=True,
        estimator_verified=True, estimator_revision=ESTIMATOR_REVISION,
        verification_reference="var/groq-demo-evidence.json", limits=[
            {"name": name, "resource": resource, "period_seconds": period, "capacity": evidence["limits"][source]}
            for name, resource, period, source in [
                ("model_requests_minute", "requests", 60, "requests_per_minute"),
                ("model_requests_day", "requests", 86400, "requests_per_day"),
                ("model_tokens_minute", "total_tokens", 60, "tokens_per_minute"),
                ("model_tokens_day", "total_tokens", 86400, "tokens_per_day")]])
    if env_path.read_bytes() != original:
        raise SetupError(".env changed during verification; nothing was enabled. Run setup again.")
    evidence.update(estimator_verified=True, estimator_check=estimate,
                    authenticated_model_lookup="passed", readiness_created_at=now.isoformat())
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    # Exclusive creation preserves an existing record; never renew/reset quotas silently.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(record.model_dump_json(indent=2) + "\n")
    if groq_readiness(settings).policy is None:
        raise SetupError("Readiness validation failed; the enable setting was not changed.")
    with env_path.open("ab") as stream:
        stream.write((b"" if original.endswith(b"\n") else b"\n") + b"TERMSLENS_GROQ_ENABLED=true\n")
    print("PASS: Groq configured for four hours or until account confirmation expires. No inference sent.")
    print("Restart API and one normal worker, refresh the browser, then submit the demo once with Hybrid consent.")
    print("Use this account only with TermsLens during the demo; its saved ledger must cover all usage.")


def main():
    try:
        configure()
    except SetupError as error:
        print("NOT READY: " + str(error))
        raise SystemExit(1) from None
    except (Exception, KeyboardInterrupt):
        print("NOT READY: setup incomplete. Check verifier installation/network and saved evidence; no inference sent.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
