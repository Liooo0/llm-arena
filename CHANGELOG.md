# Changelog

## [Unreleased]

### Added

- **Elo 排名体系**: 新增 `elo.py` 纯函数引擎(K=32, 起始 1000)+ `duels`/`elo_ratings` 表
  - `POST /api/duel`: 记录 pairwise 对决并实时更新双方 Elo
  - 排行榜新增 Elo 列(有对决记录才启用,未开启前按平均分排)
- **双盲评测**: 对战结果默认匿名展示(模型 A/B/C,卡片乱序),避免品牌偏见;
  支持单卡片"揭晓模型"与全局"揭晓全部";选定两张卡片 → 判定胜负 → 自动记录对决
- 测试: `tests/test_elo.py`(Elo 数学 + 对决持久化 + 排行榜排序),13 个用例
- GitHub Actions CI(pytest + ruff)

### Changed

- `get_leaderboard()` 替换为 `get_elo_leaderboard()`(Elo 优先,评分统计保留)
- 排行榜表头新增 Elo 列