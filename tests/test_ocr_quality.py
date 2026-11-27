import unittest

from app import nexon, party_lobby


class OcrCandidateTests(unittest.TestCase):
    def test_vote_prefers_repeated_ocr_result(self):
        picked, candidates = party_lobby.pick_line(
            ["퓨가중.", "휴가중.", "휴가중."]
        )
        self.assertEqual(picked, "휴가중.")
        self.assertEqual(candidates[0], "휴가중.")

    def test_known_name_breaks_variant_tie(self):
        picked, _ = party_lobby.pick_line(
            ["퓨가중.", "휴가중."], known=["휴가중."]
        )
        self.assertEqual(picked, "휴가중.")

    def test_single_close_candidate_is_verified_by_nexon_api(self):
        class FakeNexon:
            def __init__(self):
                self.calls = []

            def resolve_ouid(self, nickname):
                self.calls.append(nickname)
                if nickname == "휴가중.":
                    return "ouid-1"
                raise nexon.NexonError("not found")

            def user_basic(self, ouid):
                return {"user_name": "휴가중.", "clan_name": "리드"}

        client = FakeNexon()
        rows = party_lobby.validate_names(client, ["퓨가중."], ["휴가중."])
        self.assertEqual(client.calls, ["퓨가중.", "휴가중."])
        self.assertEqual(rows[0]["status"], "corrected")
        self.assertEqual(rows[0]["name"], "휴가중.")

    def test_ambiguous_candidate_is_not_auto_corrected(self):
        class FakeNexon:
            def __init__(self):
                self.calls = []

            def resolve_ouid(self, nickname):
                self.calls.append(nickname)
                raise nexon.NexonError("not found")

            def user_basic(self, ouid):
                raise AssertionError("user_basic should not be called")

        client = FakeNexon()
        rows = party_lobby.validate_names(
            client, ["퓨가중."], ["휴가중.", "퓨가중x", "휴가중x"]
        )
        self.assertEqual(rows[0]["status"], "needs_check")
        self.assertEqual(client.calls, ["퓨가중."])


if __name__ == "__main__":
    unittest.main()
