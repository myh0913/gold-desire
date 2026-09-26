"""L1 连板捉妖策略（lianban_a）。

口径来源：emotion-cycle ``docs/strategy-matrix-final.md`` §二.1（L1）与
``scripts/t11_lianban_backtest.py``（主判定：场景 A 全过滤 + break 卖出）。

两阶段触发链：

1. ``POOL``（信号日盘后）：读取当日涨停池（``pool_type="limit_up"``）中
   ``continue_days ∈ {2, 3}`` 的主板非 ST 票，对每票做六道结构过滤
   （见 ``filters.py``），产出当日候选清单（不落库，仅供盘后核阅）。
2. ``OPENING``（次日集合竞价后）：重扫 T-1 候选；先做**环境否决**——
   T-1 池内全部 boards≥2 票（不限板型/ST）当日竞价跌幅 ≤ 跌停阈值
   （默认 -9.8%）的家数 > 上限（默认 2 家）→ 全策略当日否决；
   未否决时对候选计算开盘涨幅，≤ 场景 A 阈值（默认 -5%）→ 开盘价买入提醒。

卖出口径（break）：持有至首个未涨停日收盘卖出；20 个交易日未断板强制平仓。

门控声明：``bear_gate=True`` + 冰点/退潮禁入（``gates.py``）——连板接力属
顺风追涨路径，情绪冰封期禁止开仓。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
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
from app.strategies.plugins.lianban.gates import GATE_MATRIX
from app.strategies.plugins.lianban.scanner import (
    _Candidate,
    auction_env,
    opening_prices,
    prev_trading_day,
    scan_candidates,
)

KIND_ADVICE = "advice"


# ============================================================ 提醒卡片


@dataclass(frozen=True, slots=True)
class Advice:
    """连板捉妖买卖提醒（字段与 dragon 卡片对齐，前端零改动）。"""

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


def _to_float(value: Any) -> float | None:
    """宽松转 float（ORM 行可能给出 Decimal / str / None）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_main_board_code(code: str) -> bool:
    """主板代码前缀：沪市 60 / 深市 00。"""
    return code.startswith(("60", "00"))


def _is_st_name(name: str) -> bool:
    """名称前缀 ST 判定（stocks 主档缺失时的兜底）。"""
    return str(name).upper().startswith(("ST", "*ST"))


# ============================================================ 策略


@register_strategy
class LianbanStrategy(BaseStrategy):
    """L1 连板捉妖：2/3 板断板反包结构 + 竞价深低开买入，断板即卖。"""

    strategy_id = "lianban_a"
    label = "连板捉妖"
    version = "1.0.0"
    description = (
        "主板非 ST 个股 T-1 日涨停池 2/3 连板，且通过六道结构过滤"
        "（非第一波拒 / 三板组拒 / 量能持续 / 首板量比 ≥1.5 / 低位启动）；"
        "T 日竞价跌幅 ≤-5%（场景 A）时开盘价买入。环境否决：T-1 连板票"
        "（boards≥2）竞价跌停家数 >2 → 当日全策略停买。持有至首个未涨停日"
        "收盘卖出，20 个交易日未断板强制平仓。冰点/退潮期禁入。"
    )

    phases: ClassVar[frozenset[Phase]] = frozenset({Phase.POOL, Phase.OPENING})

    #: 连板接力属顺风追涨路径：市场转熊信号下同样受注册表空头门拦截。
    bear_gate: ClassVar[bool] = True

    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = (
        StrategyParamSpec(
            key="scene_a_low_open",
            label="场景A低开阈值",
            type="percent",
            default=-0.05,
            min=-1.0,
            max=0.0,
            step=0.005,
            description="开盘相对 T-1 收盘跌幅达到该值（默认 -5%）→ 开盘价买入。",
        ),
        StrategyParamSpec(
            key="min_first_board_volume_ratio",
            label="首板量比下限",
            type="float",
            default=1.5,
            min=1.0,
            max=5.0,
            step=0.1,
            description="首板成交量 / 前一日成交量下限（R4，放量启动确认）。",
        ),
        StrategyParamSpec(
            key="max_position_ratio",
            label="位置上限",
            type="float",
            default=1.15,
            min=1.0,
            max=2.0,
            step=0.01,
            description="首板前一日开盘价 / 位置窗口内最低价的上限（R5，低位启动）。",
        ),
        StrategyParamSpec(
            key="wave_lookback_days",
            label="前波回看交易日数",
            type="int",
            default=20,
            min=1,
            max=60,
            unit="日",
            description="当前波首板之前 N 个交易日内存在其他 ≥2 板波 → 拒（R1）。",
        ),
        StrategyParamSpec(
            key="limit_up_pct",
            label="涨停阈值",
            type="percent",
            default=0.098,
            min=0.01,
            max=0.2,
            step=0.001,
            description="涨跌幅达到该值视为涨停（主板近似口径，结构过滤的板判定基础）。",
        ),
        StrategyParamSpec(
            key="one_word_pct",
            label="一字板阈值",
            type="percent",
            default=0.0989,
            min=0.01,
            max=0.2,
            step=0.0001,
            description="开盘价与最低价涨幅均达到该值视为一字（对齐 quant limit_px×0.999）。",
        ),
        StrategyParamSpec(
            key="structure_window",
            label="结构窗口",
            type="int",
            default=60,
            min=10,
            max=250,
            unit="根",
            description="截至信号日（含）回看的交易日根数（六道过滤的计算窗口）。",
        ),
        StrategyParamSpec(
            key="min_window_bars",
            label="最少日线根数",
            type="int",
            default=10,
            min=2,
            max=120,
            unit="根",
            description="窗口内日线不足该值直接拒（数据质量护栏）。",
        ),
        StrategyParamSpec(
            key="position_window",
            label="位置过滤窗口",
            type="int",
            default=30,
            min=5,
            max=250,
            unit="根",
            description="末 N 根的最低价（含信号日）参与 R5 位置过滤。",
        ),
        StrategyParamSpec(
            key="auction_limit_down_pct",
            label="竞价跌停阈值",
            type="percent",
            default=-0.098,
            min=-1.0,
            max=0.0,
            step=0.001,
            description="开盘涨幅 ≤ 该值（默认 -9.8%）计为竞价跌停（环境否决计数）。",
        ),
        StrategyParamSpec(
            key="max_auction_limit_down",
            label="竞价跌停家数上限",
            type="int",
            default=2,
            min=0,
            max=10,
            unit="家",
            description="T-1 连板票（boards≥2）竞价跌停家数超过该值 → 当日全策略否决。",
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

    #: 冰点/退潮禁入、其余放行（追涨路径的周期门控）。
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = GATE_MATRIX

    # ------------------------------------------------ 阶段钩子

    async def build_pool(self, ctx: StrategyContext) -> dict[str, Any]:
        """信号日盘后：扫当日涨停池 2/3 板候选（不落库）。"""
        repos = ctx.repos
        if repos is None:
            return {
                "strategy_id": self.strategy_id,
                "trade_date": ctx.trade_date.isoformat(),
                "candidates": [],
            }
        params = await self.get_params(ctx)
        candidates = await self._scan_candidates(ctx, params, signal_day=ctx.trade_date)
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "candidates": [
                {
                    "code": item.code,
                    "name": item.name,
                    "boards": item.boards,
                    "pos": item.pos,
                    "vr": item.vr,
                    "prev_close": item.prev_close,
                }
                for item in candidates
            ],
        }

    async def confirm_opening(self, ctx: StrategyContext) -> dict[str, Any]:
        """次日开盘确认：环境否决 + 场景 A 深低开 → 买卖提醒并落库。"""
        repos = ctx.repos
        if repos is None:
            return self._empty_opening(ctx)
        signal_day = await prev_trading_day(ctx)
        if signal_day is None:
            return self._empty_opening(ctx)
        prices = await opening_prices(ctx)
        if not prices:
            return self._empty_opening(ctx)
        params = await self.get_params(ctx)
        candidates = await self._scan_candidates(ctx, params, signal_day=signal_day)
        by_code = {item.code: item for item in candidates}
        vetoed, veto_count = await auction_env(
            ctx,
            signal_day=signal_day,
            prices=prices,
            threshold=float(self.param(params, "auction_limit_down_pct")),
            limit=int(self.param(params, "max_auction_limit_down")),
        )
        if vetoed:
            return self._empty_opening(ctx, veto_count=veto_count)
        scene_a_low_open = float(self.param(params, "scene_a_low_open"))
        position = float(self.param(params, "position"))
        advices: list[Advice] = []
        for code in sorted(prices):
            item = by_code.get(code)
            if item is None:
                continue
            price = prices[code]
            if price <= 0 or item.prev_close <= 0:
                continue
            # 除权伪跳变护栏：单边深低开阈值拦不住 -50% 量级的除权伪跳变，越界整票拒收。
            if is_suspect_price_move(price, item.prev_close):
                continue
            open_gap = price / item.prev_close - 1
            if open_gap > scene_a_low_open:
                continue
            advices.append(
                self._advice(
                    item,
                    buy_price=price,
                    open_gap=open_gap,
                    trade_date=ctx.trade_date,
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
            "env_vetoed": False,
            "auction_limit_down_count": veto_count,
        }

    # ------------------------------------------------ 内部辅助

    def _empty_opening(
        self, ctx: StrategyContext, *, veto_count: int | None = None
    ) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "phase": "opening",
            "advices": [],
            "env_vetoed": veto_count is not None,
            "auction_limit_down_count": veto_count or 0,
        }

    async def _scan_candidates(
        self, ctx: StrategyContext, params: dict[str, Any], *, signal_day: date
    ) -> list[_Candidate]:
        """扫描 signal_day 涨停池候选（阈值解析后委托 ``scanner``）。"""
        return await scan_candidates(
            ctx,
            signal_day=signal_day,
            min_vr=float(self.param(params, "min_first_board_volume_ratio")),
            max_pos=float(self.param(params, "max_position_ratio")),
            wave_lookback=int(self.param(params, "wave_lookback_days")),
            limit_up_pct=float(self.param(params, "limit_up_pct")),
            one_word_pct=float(self.param(params, "one_word_pct")),
            structure_window=int(self.param(params, "structure_window")),
            min_window_bars=int(self.param(params, "min_window_bars")),
            position_window=int(self.param(params, "position_window")),
        )

    def _advice(
        self,
        item: _Candidate,
        *,
        buy_price: float,
        open_gap: float,
        trade_date: date,
        position: float,
    ) -> Advice:
        gates = (
            {
                "factor_id": "lianban_structure",
                "label": "连板结构",
                "passed": True,
                "detail": f"{item.signal_day.isoformat()} {item.boards} 板，六道结构过滤全部通过",
            },
            {
                "factor_id": "volume_structure",
                "label": "量能结构",
                "passed": True,
                "detail": f"首板量比 {item.vr:.2f}，波内量能持续不萎缩",
            },
            {
                "factor_id": "position_low",
                "label": "低位启动",
                "passed": True,
                "detail": f"启动位置 {item.pos:.2f}（首板前日开盘 / 位置窗口最低）",
            },
            {
                "factor_id": "scene_a",
                "label": "场景A深低开",
                "passed": True,
                "detail": f"竞价低开 {open_gap:.2%}（≤ 场景 A 阈值），开盘价买入",
            },
        )
        return Advice(
            code=item.code,
            name=item.name,
            trade_date=trade_date,
            path_id="A",
            path_label="连板捉妖",
            buy_day=trade_date,
            buy_price=buy_price,
            gates=gates,
            position=position,
            stop_loss_price=None,
            sell_timing=("断板即卖（首个未涨停日收盘卖出；20 个交易日未断板强制平仓）"),
            field_snapshot={
                "open_gap_pct": round(open_gap, 4),
                "boards": item.boards,
                "pos": item.pos,
                "vr": item.vr,
                "float_mv_yuan": item.float_mv_yuan,
                "signal_day": item.signal_day.isoformat(),
                "prev_close": item.prev_close,
                "note": "顺风追涨路径，冰点/退潮期禁入",
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
