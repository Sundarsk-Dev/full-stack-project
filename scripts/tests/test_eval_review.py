from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import prepare_eval_review as review


class EvaluationReviewTests(unittest.TestCase):
    def setUp(self):
        scratch = review.ROOT / "var/tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "eval").mkdir()
        self.catalog = (review.ROOT / "eval/source-candidates.json").read_bytes()
        (self.root / "eval/source-candidates.json").write_bytes(self.catalog)

    def capture(self, text="Heading\r\nPolicy \U0001f4dc text.\r\nExcept when required by law.", family_index=0):
        family = json.loads(self.catalog)["policy_families"][family_index]
        document = family["documents"][0]
        relative = f"var/eval/sources/{document['id']}/{'1' * 32}"
        folder = self.root / relative
        folder.mkdir(parents=True)
        raw = b"synthetic fixture, not collected evidence"
        metadata = {**document, "role": "policy", "family": family["id"], "split": family["split"],
                    "retrieved_at": "2026-10-29T12:00:00+00:00", "extractor": "research-html-text-v1",
                    "raw_sha256": sha256(raw).hexdigest(), "text_sha256": sha256(text.encode()).hexdigest(),
                    "rights_review": "pending", "content_review": "pending"}
        (folder / "raw.bin").write_bytes(raw)
        (folder / "text.txt").write_bytes(text.encode())
        (folder / "capture.json").write_text(json.dumps(metadata), encoding="utf-8")
        return relative

    def test_draft_has_exact_unicode_offsets_context_and_no_machine_labels(self):
        relative = self.capture()
        original = {name: (self.root / relative / name).read_bytes()
                    for name in ["raw.bin", "text.txt", "capture.json"]}
        worksheet = review.prepare([relative], root=self.root)
        clause = worksheet["records"][1]
        self.assertEqual((clause["start"], clause["end"], clause["quote"]), (9, 23, "Policy \U0001f4dc text."))
        self.assertEqual(clause["context"]["text"], original["text.txt"].decode())
        self.assertTrue(all(record["human_review"] is None and record["selected"] is False
                            for record in worksheet["records"]))
        self.assertTrue(all(value is None for value in worksheet["sources"][relative]["review"].values()))
        report = review.check(worksheet, root=self.root)
        self.assertEqual((report["candidates"], report["selected_candidates"]), (3, 0))
        self.assertFalse(report["human_labels_verified"])
        self.assertFalse(report["corpus_ready"])
        self.assertEqual(worksheet, review.prepare([relative], root=self.root))
        for name, data in original.items():
            self.assertEqual((self.root / relative / name).read_bytes(), data)

    def test_raw_or_text_tampering_is_rejected(self):
        relative = self.capture()
        worksheet = review.prepare([relative], root=self.root)
        for name in ["raw.bin", "text.txt"]:
            path = self.root / relative / name
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(name=name), self.assertRaisesRegex(review.ReviewError, "capture_hash_mismatch"):
                review.check(worksheet, root=self.root)
            path.write_bytes(original)

    def test_provenance_offsets_and_quote_edits_fail_integrity_checks(self):
        relative = self.capture()
        original = review.prepare([relative], root=self.root)
        for field, value in [("quote", "invented quote"), ("start", -1), ("end", True),
                             ("id", "invented-id"), ("capture", "unknown-source"), ("selected", "true")]:
            changed = deepcopy(original)
            changed["records"][1][field] = value
            with self.subTest(field=field), self.assertRaises(review.ReviewError):
                review.check(changed, root=self.root)
        changed = deepcopy(original)
        changed["records"][1]["context"]["text"] = "Invented context"
        with self.assertRaisesRegex(review.ReviewError, "excerpt_mismatch"):
            review.check(changed, root=self.root)
        changed = deepcopy(original)
        changed["sources"][relative]["metadata"]["split"] = "heldout"
        with self.assertRaisesRegex(review.ReviewError, "worksheet_provenance_changed"):
            review.check(changed, root=self.root)

    def test_selected_normalized_duplicates_across_splits_are_reported(self):
        development = self.capture("Same POLICY text.", 0)
        heldout = self.capture("Ｓａｍｅ  policy TEXT.", 5)
        worksheet = review.prepare([development, heldout], root=self.root)
        self.assertEqual(review.check(worksheet, root=self.root)["cross_split_exact_duplicates"], [])
        for record in worksheet["records"]:
            record["selected"] = True
        report = review.check(worksheet, root=self.root)
        self.assertEqual(report["cross_split_exact_duplicates"], [sorted(record["id"] for record in worksheet["records"])])
        self.assertEqual((report["selected_families"], report["selected_heldout_candidates"]), (2, 1))
        self.assertFalse(report["corpus_ready"])

    def test_populated_review_fields_do_not_certify_human_labels(self):
        relative = self.capture()
        worksheet = review.prepare([relative], root=self.root)
        worksheet["records"][0].update(selected=True, human_review={"reviewer": "fixture", "rating": "neutral"})
        worksheet["sources"][relative]["review"]["rights"] = "fixture approval"
        report = review.check(worksheet, root=self.root)
        self.assertFalse(report["human_labels_verified"])
        self.assertFalse(report["corpus_ready"])

    def test_capture_and_worksheet_paths_cannot_reach_secrets_or_source_outputs(self):
        for relative in [".env", "../.env", "var/eval/sources/../.env", "C:/outside", "var\\eval\\sources\\secret"]:
            with self.subTest(relative=relative), self.assertRaises(review.ReviewError):
                review.prepare([relative], root=self.root)
        for relative in [".env", "var/eval/reviews/../sources/output.json", "var/eval/reviews/output.txt"]:
            with self.subTest(relative=relative), self.assertRaises(review.ReviewError):
                review.worksheet_path(relative, self.root)

    def test_missing_incomplete_and_nonpolicy_captures_are_rejected(self):
        relative = self.capture()
        path = self.root / relative / "capture.json"
        metadata = json.loads(path.read_text())
        metadata["role"] = "provider_reference"
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(review.ReviewError, "policy_capture_required"):
            review.prepare([relative], root=self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            review.prepare([relative], root=self.root)

    def test_new_worksheet_cannot_overwrite_review_edits(self):
        relative = self.capture()
        worksheet = review.prepare([relative], root=self.root)
        destination = review.worksheet_path("var/eval/reviews/draft.json", self.root)
        review.save_new(worksheet, destination)
        original = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            review.save_new({"changed": True}, destination)
        self.assertEqual(destination.read_bytes(), original)

    def test_duplicate_ids_changed_catalog_and_size_limits_are_rejected(self):
        relative = self.capture()
        worksheet = review.prepare([relative], root=self.root)
        worksheet["records"].append(deepcopy(worksheet["records"][0]))
        with self.assertRaisesRegex(review.ReviewError, "invalid_record_id"):
            review.check(worksheet, root=self.root)
        with self.assertRaisesRegex(review.ReviewError, "duplicate_capture"):
            review.prepare([relative, relative], root=self.root)
        with patch.object(review, "MAX_RECORDS", 1), self.assertRaisesRegex(review.ReviewError, "too_many_candidates"):
            review.prepare([relative], root=self.root)
        with patch.object(review, "MAX_SOURCE_BYTES", 1), self.assertRaisesRegex(review.ReviewError, "file_too_large"):
            review.prepare([relative], root=self.root)
        worksheet["catalog_revision"] = "different-split"
        with self.assertRaisesRegex(review.ReviewError, "worksheet_revision_mismatch"):
            review.check(worksheet, root=self.root)

    def test_cli_reports_safe_errors_without_input_text(self):
        output = StringIO()
        with patch("sys.argv", ["prepare_eval_review.py", "check", "var/eval/reviews/draft.json"]), \
                patch.object(review, "read_bytes", side_effect=ValueError("private fixture text")), \
                redirect_stdout(output), self.assertRaises(SystemExit) as error:
            review.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "failed", "reason": "invalid_or_unreadable_review_input"})


if __name__ == "__main__": unittest.main()
