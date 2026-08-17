"""Elo 纯函数测试 + 数据库对决/Elo 持久化测试"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database
import elo


class TestEloMath:
    def test_expected_equal(self):
        assert elo.expected_score(1000, 1000) == pytest.approx(0.5)

    def test_expected_symmetric(self):
        ea = elo.expected_score(1200, 800)
        eb = elo.expected_score(800, 1200)
        assert ea + eb == pytest.approx(1.0)
        assert ea > 0.9

    def test_win_moves_16_points(self):
        a, b = elo.update_elo(1000, 1000, 1.0)
        assert a == pytest.approx(1016.0)
        assert b == pytest.approx(984.0)

    def test_tie_moves_both(self):
        a, b = elo.update_elo(1000, 1000, 0.5)
        assert a == pytest.approx(1000.0)
        assert b == pytest.approx(1000.0)

    def test_strong_beat_weak_almost_no_change(self):
        a, b = elo.update_elo(2400, 1000, 1.0)
        assert a == pytest.approx(2400.0, abs=0.1)
        assert b == pytest.approx(1000.0, abs=0.1)

    def test_upset_moves_a_lot(self):
        a, b = elo.update_elo(1000, 2400, 1.0)
        assert b - a > 20


class TestDuelDB:
    @pytest.fixture(autouse=True)
    def isolate_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
        database.init_db()

    def _seed(self):
        qid = database.create_question("测试问题", "通用")
        a1 = database.create_answer(qid, "model_a", "答案A", 100, 10)
        a2 = database.create_answer(qid, "model_b", "答案B", 200, 20)
        return qid, a1, a2

    def test_duel_updates_elo(self):
        qid, a1, a2 = self._seed()
        res = database.record_duel(qid, "model_a", "model_b", 1.0)
        assert res["new_winner_elo"] > res["new_loser_elo"]
        assert database.get_elo("model_a")[0] == pytest.approx(1016.0)
        assert database.get_elo("model_b")[0] == pytest.approx(984.0)

    def test_elo_games_counter(self):
        qid, a1, a2 = self._seed()
        database.record_duel(qid, "model_a", "model_b", 1.0)
        database.record_duel(qid, "model_b", "model_a", 1.0)
        _, games_a = database.get_elo("model_a")
        _, games_b = database.get_elo("model_b")
        assert games_a == 2 and games_b == 2

    def test_duel_tie(self):
        qid, a1, a2 = self._seed()
        res = database.record_duel(qid, "model_a", "model_b", 0.5)
        assert res["outcome"] == 0.5

    def test_duel_invalid_outcome(self):
        qid, a1, a2 = self._seed()
        with pytest.raises(ValueError):
            database.record_duel(qid, "model_a", "model_b", 0.7)

    def test_elo_leaderboard_ordered(self):
        qid, a1, a2 = self._seed()
        database.record_duel(qid, "model_a", "model_b", 1.0)
        board = database.get_elo_leaderboard()
        assert board[0]["model"] == "model_a"
        assert board[0]["elo"] > board[1]["elo"]
        assert board[0]["duel_count"] == 1

    def test_new_model_starts_at_1000(self):
        rating, games = database.get_elo("nobody")
        assert rating == elo.START_ELO
        assert games == 0

    def test_leaderboard_without_duels_falls_back_to_score(self):
        qid = database.create_question("Q", "通用")
        database.create_answer(qid, "m1", "x", 10, 10)
        database.create_answer(qid, "m2", "y", 10, 10)
        board = database.get_elo_leaderboard()
        assert len(board) == 2
        for item in board:
            assert item["elo"] == elo.START_ELO