import unittest
from pathlib import Path

from app.analysis import analyze, compute_scores


class AnalysisTests(unittest.TestCase):
    def test_three_groups_and_unknown_scope(self):
        result = analyze("We sell your personal data to advertisers.\nWe retain your personal data indefinitely.\nWe use advertising cookies without your consent.", "privacy")
        self.assertEqual([f["category"] for c in result["clauses"] for f in c["findings"]], [1, 2, 3])
        self.assertEqual(result["score"]["coverage"], 1)
        self.assertLessEqual(result["score"]["best"], 74)
        self.assertTrue(result["categories"][3]["unknown"])
        self.assertIsNone(result["score"]["grade"])

    def test_non_bmp_offsets_reproduce_exact_quote(self):
        text = "An unfamiliar \U0001f4dc heading.\n\n  We sell your personal data to advertisers.  "
        result = analyze(text, "privacy")
        for clause in result["clauses"]:
            self.assertEqual(text[clause["start"]:clause["end"]], clause["text"])
            for finding in clause["findings"]:
                self.assertEqual(text[finding["start"]:finding["end"]], finding["quote"])
        self.assertEqual(result["score"]["coverage"], .5)

    def test_actor_negation_and_qualifier_abstain(self):
        for text in ["Advertisers sell your personal data to us.", "We never sell your personal data to advertisers.", "We may sell your personal data to advertisers.", "We sell your personal data to advertisers if you agree."]:
            with self.subTest(text=text):
                self.assertFalse(analyze(text, "privacy")["clauses"][0]["findings"])

    def test_explicit_no_sale_is_favorable(self):
        result = analyze("We do not sell your personal data.", "privacy")
        self.assertEqual(result["clauses"][0]["findings"][0]["severity"], 0)
        self.assertIsNone(result["score"]["grade"])

    def test_exceptions_and_definitions_preserved_for_review(self):
        for suffix in [" However, exceptions apply.", " Unless you consent.", " Personal data means billing details only."]:
            result = analyze("We do not sell your personal data." + suffix, "privacy")
            self.assertEqual(result["score"]["coverage"], 0)
            self.assertEqual(result["clauses"][0]["unrated_reason"], "context_requires_review")

    def test_conflicting_claims_abstain(self):
        result = analyze("We do not sell your personal data. We sell your personal data to advertisers.", "privacy")
        self.assertEqual(result["score"]["coverage"], 0)
        self.assertEqual({c["unrated_reason"] for c in result["clauses"]}, {"conflicting_statements"})

    def test_false_example_is_not_accepted_as_policy(self):
        result = analyze("The following statements are false.\nWe sell your personal data to advertisers.", "privacy")
        self.assertEqual(result["score"]["coverage"], 0)

    def test_unknown_is_not_safety(self):
        result = analyze("Your information supports our commercial partnerships.", "privacy")
        self.assertEqual((result["score"]["worst"], result["score"]["best"]), (0, 100))
        self.assertIsNone(result["score"]["grade"])

    def test_repetition_does_not_dilute_risk(self):
        text = "We sell your personal data to advertisers. We use advertising cookies only with your consent."
        first = analyze(text, "privacy")
        repeated = analyze(text + " We use advertising cookies only with your consent." * 10, "privacy")
        self.assertEqual(first["score"]["best"], repeated["score"]["best"])
        self.assertEqual(repeated["categories"][2]["accepted_count"], 1)

    def test_out_of_scope_concern_withholds_grade(self):
        result = analyze("We sell your personal data to advertisers.", "refund")
        self.assertFalse(result["score"]["scope_consistent"])
        self.assertIsNone(result["score"]["grade"])

    def test_empty_or_excessive_input_rejected(self):
        for text in ["  ", "a" * 200001, "word " * 25001]:
            with self.assertRaises(ValueError): analyze(text, "privacy")

    def test_all_eight_groups_and_combined_critical_cap(self):
        fixture = Path(__file__).resolve().parents[3] / "eval" / "fixtures" / "eight-groups.txt"
        result = analyze(fixture.read_text(encoding="utf-8"), "terms")
        self.assertEqual(result["supported_groups"], list(range(1, 9)))
        self.assertEqual({f["category"] for c in result["clauses"] for f in c["findings"]}, set(range(1, 9)))
        self.assertEqual(result["score"], {"best": 59, "worst": 59, "grade": "D", "coverage": 1, "scope_consistent": True})

    def test_new_groups_preserve_actor_and_qualifier_uncertainty(self):
        for statement in ["Advertisers grant us a perpetual license to your content.", "You may resolve disputes through binding arbitration.",
                          "We may terminate your account without notice unless you appeal."]:
            with self.subTest(statement=statement):
                self.assertEqual(analyze(statement, "terms")["score"]["coverage"], 0)

    def test_license_duration_conflict_abstains(self):
        result = analyze("You grant us a perpetual license to your content. The license to your content ends when you delete it.", "terms")
        self.assertEqual(result["score"]["coverage"], 0)


if __name__ == "__main__":
    unittest.main()
