"""Frozen rubric examples and read-only profile views over stored evidence."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, func, select

from app.analysis import analyze
from app.models import AICall, Analysis, Job
from app.reports import report_data
from app.scoring import APPLICABLE, PROFILE_REVISION, saved_profiles, score_categories, weights_for
from app.worker import claim, process_claim
from tests.test_ai_ledger import AILedgerFixture
from tests.test_ai_review import FakeTransport
from tests.test_worker import WorkerFixture


class ScoringTests(unittest.TestCase):
    def categories(self, document_type="terms"):
        return [{"id": n, "max_severity": 0, "unknown": False, "applicable": n in APPLICABLE[document_type]}
                for n in range(1, 9)]

    def test_privacy_weights_apply_to_fixed_scope_including_unknown_groups(self):
        categories = self.categories("privacy")
        categories[0].update(max_severity=5)
        categories[3].update(unknown=True)
        balanced = score_categories(categories, [], "privacy", 1, True)
        privacy = score_categories(categories, [], "privacy", 1, True, profile="privacy_first")
        self.assertEqual((balanced["best"], balanced["worst"]), (80, 60))
        # Privacy scope totals 85; sale has weight 25, unknown content has weight 15.
        self.assertEqual((privacy["best"], privacy["worst"]), (70.59, 52.94))
        self.assertIsNone(privacy["grade"])

    def test_scope_weights_match_all_four_frozen_policy_types(self):
        self.assertEqual(weights_for("terms", "privacy_first"), {1: 25, 2: 20, 3: 20, 4: 15, 5: 5, 6: 5, 7: 5, 8: 5})
        self.assertEqual(weights_for("privacy", "privacy_first"), {1: 25, 2: 20, 3: 20, 4: 15, 6: 5})
        self.assertEqual(weights_for("cookies", "privacy_first"), {1: 25, 2: 20, 3: 20, 6: 5})
        self.assertEqual(weights_for("refund", "privacy_first"), {5: 5, 6: 5})

    def test_unknowns_never_become_safe_through_a_profile_change(self):
        categories = self.categories()
        for category in categories:
            category["unknown"] = True
        for document_type in APPLICABLE:
            for profile in ("balanced", "privacy_first"):
                score = score_categories(categories, [], document_type, 0, True, profile=profile)
                self.assertEqual((score["best"], score["worst"], score["grade"]), (100, 0, None))

    def test_caps_and_grade_thresholds_apply_to_both_profiles(self):
        for profile in ("balanced", "privacy_first"):
            for ids, cap, grade in [(["sell-data"], 74, "C"), (["perpetual-license", "mandatory-arbitration"], 59, "D")]:
                score = score_categories(self.categories(), ids, "terms", 1, True, profile=profile)
                self.assertEqual((score["best"], score["worst"], score["grade"]), (cap, cap, grade))
            self.assertIsNone(score_categories(self.categories(), [], "terms", .799, True, profile=profile)["grade"])
            self.assertIsNone(score_categories(self.categories(), [], "terms", 1, False, profile=profile)["grade"])
            self.assertEqual(score_categories(self.categories(), [], "terms", .8, True, profile=profile)["grade"], "A")

    def test_invalid_profile_and_incomplete_categories_fail_instead_of_dropping_unknowns(self):
        for document_type, profile in [("other", "balanced"), ("terms", "custom")]:
            with self.assertRaises(ValueError): weights_for(document_type, profile)
        with self.assertRaises(ValueError): score_categories(self.categories()[:-1], [], "terms", 1, True)

    def test_views_preserve_saved_balanced_score_and_input_without_reanalysis(self):
        view = analyze("We sell your personal data to advertisers.", "terms")
        view["score"]["best"] = 73.25  # A historical stored score must not be silently replaced.
        before = deepcopy(view)
        with patch("app.analysis.analyze", side_effect=AssertionError("no reanalysis")):
            profiles = saved_profiles(view, "terms")
        self.assertEqual(profiles["revision"], PROFILE_REVISION)
        self.assertEqual(profiles["balanced"]["score"]["best"], 73.25)
        self.assertEqual(profiles["privacy_first"]["score"]["best"], 74)
        self.assertAlmostEqual(sum(profiles["privacy_first"]["weights"].values()), 1)
        self.assertEqual(view, before)
        profiles["balanced"]["score"]["best"] = 0
        self.assertEqual(view, before)

    def test_unsupported_saved_rubric_or_scope_stays_balanced_only(self):
        original = analyze("We do not sell your personal data.", "privacy")
        for change in ("rubric", "scope", "categories"):
            view = deepcopy(original)
            if change == "rubric": view["engine"]["rubric"] = "historical-unknown"
            elif change == "scope": view["categories"][7]["applicable"] = True
            else: view["categories"].pop()
            profiles = saved_profiles(view, "privacy")
            self.assertNotIn("privacy_first", profiles)
            self.assertIsNone(profiles["revision"])
            self.assertEqual(profiles["balanced"]["score"], original["score"])


class StoredProfileTests(WorkerFixture):
    def test_read_adds_both_profiles_without_writes_jobs_or_provider_calls(self):
        report_id = process_claim(self.factory, claim(self.factory))
        with self.factory() as db:
            stored = deepcopy(db.get(Analysis, report_id).rules_baseline)
            counts = [db.scalar(select(func.count()).select_from(model)) for model in (Analysis, Job, AICall)]
        statements = []
        def statement(_connection, _cursor, sql, _parameters, _context, _many): statements.append(sql)
        event.listen(self.engine, "before_cursor_execute", statement)
        try:
            with patch("app.analysis.analyze", side_effect=AssertionError("no reanalysis")), \
                    patch("app.groq_transport.GroqTransport.send", side_effect=AssertionError("no provider calls")), self.factory() as db:
                report = report_data(db, self.alice, report_id)
                self.assertFalse(db.dirty or db.new or db.deleted)
                self.assertEqual(report["score_profiles"]["balanced"]["score"], report["score"])
                self.assertLess(report["score_profiles"]["privacy_first"]["score"]["best"], report["score"]["best"])
                self.assertEqual(report["score_profiles"], report["rules_baseline"]["score_profiles"])
                self.assertEqual(db.get(Analysis, report_id).rules_baseline, stored)
                self.assertEqual([db.scalar(select(func.count()).select_from(model)) for model in (Analysis, Job, AICall)], counts)
                self.assertEqual(json.loads(json.dumps(report))["id"], report_id)
                with self.assertRaises(HTTPException) as error: report_data(db, self.bob, report_id)
                self.assertEqual(error.exception.status_code, 404)
        finally:
            event.remove(self.engine, "before_cursor_execute", statement)
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in statements))

    def test_legacy_report_reopens_with_its_stored_score(self):
        report_id = process_claim(self.factory, claim(self.factory))
        with self.factory.begin() as db:
            analysis = db.get(Analysis, report_id)
            analysis.engine = {**analysis.engine, "rubric": "unrecognized-old-rubric"}
            analysis.rules_baseline = None
            saved = analysis.best, analysis.worst
        with self.factory() as db:
            report = report_data(db, self.alice, report_id)
            self.assertEqual((report["score"]["best"], report["score"]["worst"]), saved)
            self.assertNotIn("privacy_first", report["score_profiles"])
            self.assertIsNone(report["rules_baseline"])


class HybridProfileTests(AILedgerFixture):
    def test_hybrid_and_baseline_profiles_use_their_own_saved_findings(self):
        transport = FakeTransport()
        report_id = process_claim(self.factory, self.claimed, policy=self.policy, transport=transport)
        sent = len(transport.calls)
        with self.factory() as db:
            report = report_data(db, self.alice, report_id)
            hybrid = report["score_profiles"]["privacy_first"]["score"]
            baseline = report["rules_baseline"]["score_profiles"]["privacy_first"]["score"]
            self.assertLess(hybrid["best"], baseline["best"])
            self.assertEqual(hybrid["coverage"], report["score"]["coverage"])
            self.assertEqual(baseline["coverage"], 0)
            self.assertEqual(len(transport.calls), sent)
            self.assertEqual(len(report["ai_provenance"]), 2)


if __name__ == "__main__": unittest.main()
