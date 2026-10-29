"""Run bounded checks; HTTP and MySQL checks require explicit flags."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd, timeout=60, env=None):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("NOT VERIFIED: check timed out; inspect runtime restrictions before retrying.", flush=True)
        return False
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Six basic offline checks; no Groq, HTTP or MySQL suites")
    parser.add_argument("--http", action="store_true", help="Also run FastAPI TestClient checks (needs a working asyncio runtime)")
    parser.add_argument("--mysql", action="store_true", help="Also run the real isolated MySQL acceptance test")
    args = parser.parse_args()
    if args.quick:
        if args.http or args.mysql: parser.error("Use --quick separately from runtime acceptance flags.")
        checks = [run([sys.executable, "scripts/check_context.py"], ROOT),
                  run([sys.executable, "-m", "compileall", "-q", "app"], ROOT / "apps/api"),
                  run([sys.executable, "-m", "unittest",
                       "tests.test_analysis.AnalysisTests.test_all_eight_groups_and_combined_critical_cap",
                       "tests.test_analysis.AnalysisTests.test_non_bmp_offsets_reproduce_exact_quote",
                       "tests.test_scoring.StoredProfileTests.test_read_adds_both_profiles_without_writes_jobs_or_provider_calls",
                       "tests.test_worker.WorkerTests.test_publish_reopen_and_owner_isolation",
                       "tests.test_comparisons.ComparisonTests.test_saved_comparison_reopens_with_exact_unicode_evidence_without_calls",
                       "tests.test_deletion.DeletionTests.test_deletion_removes_all_versions_and_comparisons_but_preserves_other_document",
                       "-q"], ROOT / "apps/api")]
        print("Quick baseline only: runtime, evaluation and live Groq acceptance remain separate.")
        raise SystemExit(0 if all(checks) else 1)
    checks = [run([sys.executable, "scripts/check_context.py"], ROOT),
              run([sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-v"], ROOT),
              run([sys.executable, "-m", "compileall", "-q", "app", "migrations", "tests"], ROOT / "apps/api"),
              run([sys.executable, "-m", "unittest", "tests.test_analysis", "tests.test_scoring", "tests.test_worker", "tests.test_diagnostics", "tests.test_ai_contract", "tests.test_ai_ledger", "tests.test_ai_review", "tests.test_review_persistence", "-v"], ROOT / "apps/api"),
              # Process-lifetime checks get their own bound, independent of database fixture setup.
              run([sys.executable, "-m", "unittest", "tests.test_groq", "-v"], ROOT / "apps/api"),
              run([sys.executable, "-m", "unittest", "tests.test_url_fetch", "tests.test_url_runtime", "tests.test_url_jobs", "tests.test_versions", "-v"], ROOT / "apps/api"),
              run([sys.executable, "-m", "unittest", "tests.test_comparisons", "tests.test_deletion", "tests.test_lifecycle_harness", "-v"], ROOT / "apps/api")]
    if args.http:
        checks.append(run([sys.executable, "-m", "unittest", "tests.test_storage", "-v"], ROOT / "apps/api", timeout=45))
    else:
        print("HTTP checks not run; use --http on a host with a working asyncio runtime.")
    if args.mysql:
        environment = dict(os.environ, TERMSLENS_RUN_MYSQL_TESTS="1")
        checks.append(run([sys.executable, "-m", "unittest", "tests.test_mysql", "-v"], ROOT / "apps/api", timeout=60, env=environment))
        checks.append(run([sys.executable, "-m", "unittest", "tests.test_ai_mysql", "-v"], ROOT / "apps/api", timeout=60, env=environment))
        checks.append(run([sys.executable, "-m", "unittest", "tests.test_lifecycle_mysql", "-v"], ROOT / "apps/api", timeout=60, env=environment))
    else:
        print("MySQL acceptance not run; use --mysql after test database setup/migrations.")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__": main()
