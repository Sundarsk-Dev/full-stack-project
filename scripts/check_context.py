"""Check frozen source inputs and the project's authored-document limits."""
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINES = {
    "plan-proposal.md": "5400bc0097636187ed106b3e3fcf6cc913d5e88334393402ebb466461e14040e",
    "TermsLens_Project_Plan.md": "85cfcb0673255b084b13d620d3c02c8f1c4eeae87194e46d113afcef1d36f7e2",
    "Assignment_10_Report.pdf": "6046e9fa81368b30e602b8ebf593ee604056f287934057342cf7c5655590c8cd",
}


def main():
    for name, digest in BASELINES.items():
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, f"Frozen input changed: {name}"
        print(f"PASS unchanged: {name}")
    for name in ["plan-proposal.md", "implementation-workflow.md", "architecture-debugging.md", "url-fetching.md", "versions.md", "comparisons-deletion.md", "verification-checklist.md", "transfercontext.md", "README.md", "RUNNING.md", "DEMO.md", "eval/fixtures/wordpress-terms-demo.md", "eval/fixtures/consumer-demo.md", "eval/README.md", "reports/README.md", "working.md"]:
        file = ROOT / name
        if name == "working.md" and not file.exists(): continue
        assert file.is_file(), f"Missing maintained reference: {name}"
        content = file.read_bytes()
        lines = len(content.decode("utf-8").splitlines())
        max_lines, max_bytes = (60, 6144) if name == "transfercontext.md" else (200, 16384)
        assert lines <= max_lines and len(content) <= max_bytes, f"Context cap exceeded: {name}"
        print(f"PASS limits: {name} ({lines} lines, {len(content)} bytes)")


if __name__ == "__main__": main()
