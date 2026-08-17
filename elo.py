"""Elo 排名纯函数。

用于 pairwise 对决(用户判断 A vs B 谁更好)后的排名更新。
与评分(AVG(score))互补:评分衡量绝对质量,Elo 衡量相对强弱。

约定:
- 起始分 1000, 每个模型独立
- K=32 (标准快速收敛值)
- outcome: 1.0 = 左边赢, 0.0 = 右边赢, 0.5 = 平局
"""

START_ELO = 1000.0
K_FACTOR = 32.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """A 对 B 的期望得分(0~1)。"""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def update_elo(rating_a: float, rating_b: float, outcome: float) -> tuple[float, float]:
    """按 outcome(1=赢 / 0.5=平 / 0=输)更新两个评分,返回 (new_a, new_b)。"""
    ea = expected_score(rating_a, rating_b)
    eb = 1.0 - ea
    new_a = rating_a + K_FACTOR * (outcome - ea)
    new_b = rating_b + K_FACTOR * ((1.0 - outcome) - eb)
    return round(new_a, 1), round(new_b, 1)