"""S1 首板低吸策略（firstboard_dip）。

口径来源：emotion-cycle ``docs/strategy-matrix-final.md`` §二.3（S1）与
``scripts/t13_web_strategies_backtest.py::s1_firstboard`` 回测口径。

两阶段触发链：

1. ``POOL``（盘后）：扫描当日主板非 ST 的"低位横盘首板"——
   T 日首板涨停、板前 10 个交易日无板、61 根窗口收盘位置分位不超过上限；
   产出当日候选清单（不落库，仅供盘后核阅）。
2. ``OPENING``（次日集合竞价后）：读取 ``opening_match`` 快照中的开盘价，
   对昨日候选计算低开幅度，落入 ``[dip_low, dip_high]``（默认 [-5%, -3%]）
   时产出买卖提醒（T+1 收盘无条件卖出，持 1 日）。

门控声明：全七周期态显式放行（``GateRule(True, 1.0)``）。低吸属逆势路径，
冰点/退潮期信号属正常产出；显式声明可避免注册表对未声明态的回退告警。

阈值全部走参数（``params_schema``），线上调整无需改代码；
信号纯计算部件（``_Bar`` / ``_firstboard_signal`` 等）拆分在同目录
``signals.py``——其中常量 ``_SUSPECT_PCT`` 是**数据质量护栏**
（涨跌幅绝对值超过阈值视为脏数据，如除权未回填），不是策略阈值。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, ClassVar

from app.engine.guards import is_suspect_price_move
from app.strategies import (
    BaseStrategy,
    CycleState,
    GateRule,
    Phase,
    StrategyParamSpec,
    register_strategy,
)
from app.strategies.context import StrategyContext
from app.strategies.plugins.firstboard_dip.signals import (
    _backfill_pre_close,
    _Bar,
    _Candidate,
    _firstboard_signal,
    _is_main_board_code,
    _to_float,
)

KIND_ADVICE = "advice"

#: 盘后快照池名：交易日历（与 dragon 插件共用同一种子格式）。
_CALENDAR_POOL_NAME = "trading_calendar"

#: 交易日历探测回看的自然日跨度（足够覆盖 61 个交易日窗口）。
_CALENDAR_LOOKBACK_DAYS = 45


# ============================================================ 提醒卡片


@dataclass(frozen=True, slots=True)
class Advice:
    """首板低吸买卖提醒（字段与 dragon 卡片对齐，前端零改动）。"""

    code: str
    name: str
    trade_date: date
    path_id: str
    path_label: str
    buy_day: date
    buy_price: float
    gates: tuple[dict[str, Any], ...]
    position: float
    stop_loss_price: float | None
    sell_timing: str
    field_snapshot: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """序列化为提醒卡片（键集合与 dragon 完全一致）。"""
        return {
            "path_id": self.path_id,
            "path_label": self.path_label,
            "code": self.code,
            "name": self.name,
            "buy_day": self.buy_day.isoformat(),
            "buy_price": self.buy_price,
            "gates": [dict(gate) for gate in self.gates],
            "bonus": [],
            "bonus_score": 0,
            "position": self.position,
            "stop_loss_price": self.stop_loss_price,
            "sell_timing": self.sell_timing,
            "field_snapshot": dict(self.field_snapshot),
        }


# ============================================================ 策略


@register_strategy
class FirstboardDipStrategy(BaseStrategy):
    """S1 首板低吸：低位横盘首板 + 次日深低开买入，T+1 收盘了结。"""

    strategy_id = "firstboard_dip"
    label = "首板低吸"
    version = "1.0.0"
    description = (
        "主板非 ST 个股 T 日首板涨停（板前 10 个交易日无板），且收盘位于"
        " 61 根窗口低位（默认不高于 55% 分位）；T+1 开盘深度低开"
        "（默认 [-5%, -3%]）时买入，T+1 收盘无条件卖出（持 1 日）。"
        "全周期放行：低吸属逆势路径，冰点/退潮期信号属正常产出。"
    )

    phases: ClassVar[frozenset[Phase]] = frozenset({Phase.POOL, Phase.OPENING})

    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = (
        StrategyParamSpec(
            key="dip_low",
            label="低开下界",
            type="percent",
            default=-0.05,
            min=-1.0,
            max=0.0,
            step=0.005,
            description="T+1 开盘相对首板收盘的最低跌幅（低于视为过深，放弃）。",
        ),
        StrategyParamSpec(
            key="dip_high",
            label="低开上界",
            type="percent",
            default=-0.03,
            min=-1.0,
            max=0.0,
            step=0.005,
            description="T+1 开盘相对首板收盘的最大跌幅（浅于此不买，保留确认度）。",
        ),
        StrategyParamSpec(
            key="pos_max",
            label="位置分位上限",
            type="percent",
            default=0.55,
            min=0.0,
            max=1.0,
            step=0.01,
            description="首板收盘在窗口中的位置分位上限（越低越低位）。",
        ),
        StrategyParamSpec(
            key="limit_up_pct",
            label="涨停阈值",
            type="percent",
            default=0.098,
            min=0.05,
            max=0.105,
            step=0.001,
            description="涨跌幅达到该值视为涨停（兼容 9.8% 的近似判定）。",
        ),
        StrategyParamSpec(
            key="no_board_days",
            label="板前无板交易日数",
            type="int",
            default=10,
            min=1,
            max=30,
            unit="日",
            description="首板之前连续无涨停的交易日数要求（横盘确认）。",
        ),
        StrategyParamSpec(
            key="pos_window",
            label="窗口交易日数",
            type="int",
            default=60,
            min=10,
            max=250,
            unit="日",
            description="位置分位回看的交易日窗口长度（板前 N 根 + 板日）。",
        ),
        StrategyParamSpec(
            key="position",
            label="单票仓位",
            type="percent",
            default=0.20,
            min=0.0,
            max=1.0,
            step=0.01,
            description="单票建议仓位（受总仓位上限等比压缩）。",
        ),
        StrategyParamSpec(
            key="max_total_position",
            label="总仓位上限",
            type="percent",
            default=0.80,
            min=0.0,
            max=1.0,
            step=0.05,
            description="全部提醒的仓位之和上限，超出按比例压缩。",
        ),
    )

    #: 全七态显式放行：低吸逆势路径，周期门控不拦截（避免回退告警）。
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        state: GateRule(True, 1.0) for state in CycleState
    }

    # ------------------------------------------------ 阶段钩子

    async def build_pool(self, ctx: StrategyContext) -> dict[str, Any]:
        """盘后扫描当日主板低位首板，产出候选清单（不落库）。"""
        repos = ctx.repos
        if repos is None:
            return {
                "strategy_id": self.strategy_id,
                "trade_date": ctx.trade_date.isoformat(),
                "candidates": [],
            }
        params = await self.get_params(ctx)
        candidates = await self._scan_firstboards(ctx, params, board_day=ctx.trade_date)
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "candidates": [
                {
                    "code": item.code,
                    "name": item.name,
                    "first_board_date": item.first_board_date.isoformat(),
                    "board_close": item.board_close,
                    "pos_60d": item.pos_60d,
                }
                for item in candidates
            ],
        }

    async def confirm_opening(self, ctx: StrategyContext) -> dict[str, Any]:
        """次日开盘确认：昨日首板候选 + 今日深低开 → 买卖提醒并落库。"""
        repos = ctx.repos
        if repos is None:
            return self._empty_opening(ctx)
        board_day = await self._prev_trading_day(ctx)
        if board_day is None:
            return self._empty_opening(ctx)
        prices = await self._opening_prices(ctx)
        if not prices:
            return self._empty_opening(ctx)
        params = await self.get_params(ctx)
        dip_low = float(self.param(params, "dip_low"))
        dip_high = float(self.param(params, "dip_high"))
        position = float(self.param(params, "position"))
        candidates = await self._scan_firstboards(ctx, params, board_day=board_day)
        by_code = {item.code: item for item in candidates}
        advices: list[Advice] = []
        for code in sorted(prices):
            item = by_code.get(code)
            if item is None:
                continue
            price = prices[code]
            if price <= 0:
                continue
            # 除权伪跳变护栏：开盘价 vs 昨收越界（复权口径未对齐 / 坏数据）整票拒收。
            if is_suspect_price_move(price, item.board_close):
                continue
            dip = price / item.board_close - 1
            if not dip_low <= dip <= dip_high:
                continue
            advices.append(
                self._advice(
                    item,
                    buy_price=price,
                    dip=dip,
                    trade_date=ctx.trade_date,
                    dip_low=dip_low,
                    dip_high=dip_high,
                    position=position,
                )
            )
        advices = self._cap_positions(advices, params)
        reports = self._to_reports(ctx, advices)
        if reports:
            await repos.advice_reports.upsert_many(reports)
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "phase": "opening",
            "advices": [advice.to_payload() for advice in advices],
        }

    # ------------------------------------------------ 内部辅助

    def _empty_opening(self, ctx: StrategyContext) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "phase": "opening",
            "advices": [],
        }

    async def _scan_firstboards(
        self, ctx: StrategyContext, params: dict[str, Any], *, board_day: date
    ) -> list[_Candidate]:
        """扫描 board_day 的主板低位首板候选（按代码排序保证输出稳定）。"""
        repos = ctx.repos
        if repos is None:
            return []
        limit_up_pct = float(self.param(params, "limit_up_pct"))
        no_board_days = int(self.param(params, "no_board_days"))
        pos_window = int(self.param(params, "pos_window"))
        pos_max = float(self.param(params, "pos_max"))
        # 61 个交易日约合 90 个自然日；1.8 倍冗余 + 节假日缓冲
        start = board_day - timedelta(days=int(pos_window * 1.8) + 15)
        candidates: list[_Candidate] = []
        for stock in await repos.stocks.list_all(board="主板"):
            if bool(getattr(stock, "is_st", False)):
                continue
            code = str(stock.code)
            if not _is_main_board_code(code):
                continue
            rows = await repos.daily_bars.get_range(code, start, board_day)
            if not rows or rows[-1].trade_date != board_day:
                continue
            bars = _backfill_pre_close(
                [
                    _Bar(
                        trade_date=row.trade_date,
                        open=_to_float(row.open) or 0.0,
                        high=_to_float(row.high) or 0.0,
                        low=_to_float(row.low) or 0.0,
                        close=_to_float(row.close) or 0.0,
                        pre_close=_to_float(row.pre_close),
                    )
                    for row in rows
                ]
            )
            signal = _firstboard_signal(
                bars,
                limit_up_pct=limit_up_pct,
                no_board_days=no_board_days,
                pos_window=pos_window,
                pos_max=pos_max,
            )
            if signal is None:
                continue
            candidates.append(
                _Candidate(
                    code=code,
                    name=str(stock.name),
                    first_board_date=signal["first_board_date"],
                    board_close=signal["board_close"],
                    pos_60d=signal["pos_60d"],
                )
            )
        candidates.sort(key=lambda item: item.code)
        return candidates

    async def _prev_trading_day(self, ctx: StrategyContext) -> date | None:
        """从交易日历快照解析 ctx.trade_date 的前一交易日（缺失不臆断）。"""
        repos = ctx.repos
        if repos is None:
            return None
        probe_dates = [
            ctx.trade_date,
            ctx.trade_date - timedelta(days=_CALENDAR_LOOKBACK_DAYS),
        ]
        for probe in probe_dates:
            snapshot = await repos.pool_snapshot.get(probe, _CALENDAR_POOL_NAME)
            payload = snapshot.payload if snapshot is not None else None
            if not isinstance(payload, dict):
                continue
            try:
                dates = [date.fromisoformat(str(item)) for item in payload.get("dates", [])]
            except ValueError:
                continue
            past = sorted(d for d in dates if d < ctx.trade_date)
            if past:
                return past[-1]
        return None

    async def _opening_prices(self, ctx: StrategyContext) -> dict[str, float]:
        """读取当日集合竞价撮合快照：{code: 开盘价}。"""
        repos = ctx.repos
        if repos is None:
            return {}
        snapshot = await repos.pool_snapshot.get(ctx.trade_date, "opening_match")
        payload = snapshot.payload if snapshot is not None else None
        if not isinstance(payload, dict):
            return {}
        return {
            str(code): float(item["price"])
            for code, item in payload.items()
            if isinstance(item, dict) and item.get("price")
        }

    def _advice(
        self,
        item: _Candidate,
        *,
        buy_price: float,
        dip: float,
        trade_date: date,
        dip_low: float,
        dip_high: float,
        position: float,
    ) -> Advice:
        gates = (
            {
                "factor_id": "first_board",
                "label": "首板确认",
                "passed": True,
                "detail": f"{item.first_board_date.isoformat()} 首板涨停，板前横盘无板",
            },
            {
                "factor_id": "pos_60d",
                "label": "窗口位置分位",
                "passed": True,
                "detail": f"首板收盘位于窗口 {item.pos_60d:.0%} 分位（低位）",
            },
            {
                "factor_id": "dip_bucket",
                "label": "深低开幅度",
                "passed": True,
                "detail": f"开盘低开 {dip:.2%}，落在 [{dip_low:.0%}, {dip_high:.0%}]",
            },
        )
        return Advice(
            code=item.code,
            name=item.name,
            trade_date=trade_date,
            path_id="dip",
            path_label="首板低吸",
            buy_day=trade_date,
            buy_price=buy_price,
            gates=gates,
            position=position,
            stop_loss_price=None,
            sell_timing="T+1 收盘卖出（持 1 日，无条件了结）",
            field_snapshot={
                "dip_pct": round(dip, 4),
                "first_board_date": item.first_board_date.isoformat(),
                "pos_60d": item.pos_60d,
                "note": "逆势低吸，冰点/退潮期信号属正常",
            },
        )

    def _to_reports(self, ctx: StrategyContext, advices: list[Advice]) -> list[dict[str, Any]]:
        ran_at = ctx.clock()
        return [
            {
                "trade_date": ctx.trade_date,
                "kind": KIND_ADVICE,
                "strategy_id": self.strategy_id,
                "strategy_version": ctx.param_version,
                "code": advice.code,
                "payload": advice.to_payload(),
                "ran_at": ran_at,
            }
            for advice in advices
        ]

    def _cap_positions(self, advices: list[Advice], params: dict[str, Any]) -> list[Advice]:
        """总仓位超上限时按比例压缩各单票仓位（dragon 同款口径）。"""
        cap = float(self.param(params, "max_total_position"))
        total = sum(advice.position for advice in advices)
        if total > cap and total > 0:
            scale = cap / total
            return [replace(advice, position=advice.position * scale) for advice in advices]
        return advices
