from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select

from app.comparisons import MAX_SIMILARITY_PAIRS, align, comparison_data, comparison_history, create_comparison
from app.main import Submission
from app.models import Analysis, Comparison, Document
from app.worker import claim, process_claim
from tests.test_versions import VersionFixture
from tests.test_worker import TEXT


class ComparisonTests(VersionFixture):
    def second(self, *, text=TEXT + "\nWe do not sell your personal data.", separate=False):
        if separate:
            with self.factory() as db:
                self.submit(Submission(text=text, document_type="privacy"), self.request(), "second-policy", self.owner, db)
        else: self.enqueue_refresh(text)
        return process_claim(self.factory, claim(self.factory))

    def test_saved_comparison_reopens_with_exact_unicode_evidence_without_calls(self):
        right = self.second()
        with self.factory() as db, patch("app.url_runtime.URLFetcher.fetch", side_effect=AssertionError("no fetch")), \
             patch("app.groq_transport.GroqTransport.send", side_effect=AssertionError("no AI")):
            result = create_comparison(db, self.alice, self.result_id, right, "versions", "privacy_first")
            value = comparison_data(db, self.alice, result["comparison_id"])
            self.assertTrue(value["summary"]["source_text_changed"])
            self.assertGreater(value["summary"]["text_changes"], 0)
            self.assertEqual(value["profile"], "privacy_first")
            for row in value["passages"]:
                for side, text in [("left", TEXT), ("right", TEXT + "\nWe do not sell your personal data.")]:
                    if row[side]: self.assertEqual(text[row[side]["start"]:row[side]["end"]], row[side]["text"])
            self.assertEqual(comparison_history(db, self.alice, right, 20, 0)["total"], 1)
        self.engine.dispose()
        with self.factory() as db:
            self.assertEqual(comparison_data(db, self.alice, result["comparison_id"]), value)
            changed = comparison_data(db, self.alice, result["comparison_id"], 1, 0, True)
            self.assertEqual(len(changed["passages"]), 1)
            self.assertTrue(changed["passages"][0]["text_change"] != "unchanged" or changed["passages"][0]["interpretation_changed"])

    def test_cross_policy_ownership_and_tombstones(self):
        right = self.second(separate=True)
        with self.factory() as db:
            result = create_comparison(db, self.alice, self.result_id, right, "policies", "balanced")
            with self.assertRaises(HTTPException) as error: comparison_data(db, self.bob, result["comparison_id"])
            self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(HTTPException): create_comparison(db, self.bob, self.result_id, right, "policies", "balanced")
        with self.factory.begin() as db: db.get(Document, self.document_id).tombstoned = True
        with self.factory() as db:
            with self.assertRaises(HTTPException): comparison_data(db, self.alice, result["comparison_id"])
            self.assertEqual(comparison_history(db, self.alice, right, 20, 0)["total"], 0)

    def test_engine_mode_profile_model_and_type_mismatch_require_reanalysis(self):
        right_id = self.second(separate=True)
        changes = [dict(engine={**self.report["engine"], "rules": "other"}), dict(profile_revision=None),
                   dict(effective_mode="hybrid", ai_provenance=[{"provider": "groq", "model": "other", "prompt_revision": "v1"}])]
        for changeset in changes:
            with self.factory.begin() as db:
                a = db.get(Analysis, right_id)
                old = {k: deepcopy(getattr(a, k)) for k in changeset}
                for key, value in changeset.items(): setattr(a, key, value)
            with self.factory() as db:
                with self.assertRaises(HTTPException) as error: create_comparison(db, self.alice, self.result_id, right_id, "policies", "balanced")
                self.assertEqual(error.exception.status_code, 409)
            with self.factory.begin() as db:
                for key, value in old.items(): setattr(db.get(Analysis, right_id), key, value)
        with self.factory.begin() as db: db.get(Document, self.document_id).document_type = "terms"
        with self.factory() as db:
            with self.assertRaises(HTTPException): create_comparison(db, self.alice, self.result_id, right_id, "policies", "balanced")
            self.assertEqual(db.scalar(select(func.count()).select_from(Comparison)), 0)

    def test_comparison_kind_and_same_report_are_rejected(self):
        right = self.second()
        with self.factory() as db:
            for left_id, right_id, kind in [(self.result_id, right, "policies"), (right, right, "versions")]:
                with self.assertRaises(HTTPException) as error: create_comparison(db, self.alice, left_id, right_id, kind, "balanced")
                self.assertEqual(error.exception.status_code, 422)

    def test_exact_duplicates_align_once_and_explanation_changes_are_not_policy_changes(self):
        clauses = [deepcopy(self.report["clauses"][1]) for _ in range(2)]
        for i, clause in enumerate(clauses): clause["id"] = i + 1
        right = deepcopy(clauses)
        right[0]["findings"][0]["explanation"] = "A differently worded explanation."
        rows, _ = align(clauses, right)
        self.assertEqual(len({r["right"] for r in rows}), 2)
        self.assertFalse(any(r["interpretation_changed"] for r in rows))
        right[0]["findings"][0]["severity"] = 2
        rows, _ = align(clauses, right)
        self.assertTrue(rows[0]["interpretation_changed"])
        self.assertTrue(all(r["text_change"] == "unchanged" for r in rows))

    def test_similarity_is_bounded_and_unmatched_passages_are_retained(self):
        clause = lambda i, prefix: {"id": i, "text": f"{prefix} number {i}", "substantive": True, "unrated_reason": "unsupported_wording", "findings": []}
        left = [clause(i, "Old statement") for i in range(100)]
        right = [clause(i, "New contract") for i in range(100)]
        rows, limits = align(left, right)
        self.assertTrue(limits["similarity_limited"])
        self.assertEqual(limits["similarity_pairs"], MAX_SIMILARITY_PAIRS)
        self.assertEqual({r["left"] for r in rows if r["left"] is not None}, set(range(100)))
        self.assertEqual({r["right"] for r in rows if r["right"] is not None}, set(range(100)))
