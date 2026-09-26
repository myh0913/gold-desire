"""J1 竞价抢筹策略（auction_grab）。

口径来源：emotion-cycle ``docs/strategy-matrix-final.md`` §二.2（J1）与
``scripts/verify_auction_pilot.py`` 三腿布尔式（分时期 7/7 全正，246 日样本
净含成本 +1.52%，胜率 54%，n=320）。

单阶段触发（T 日 9:25~9:30，``confirm_opening``）：对竞价快照宇宙中的
主板非 ST 个股做**三腿判定**——

1. 腿2 ``p920 < 昨收``：竞价前段（9:20 参考价）低于昨收（低吸位）；
2. 腿4 ``p925 / p920 - 1 >= min_rise``：竞价尾段快速拉升（默认 +2%）；
3. 腿5 ``p925 / 昨收 - 1 < max_chg``：开盘价未过热（默认 +5% 上限，
   排除一字板与高开抢筹透支）。

腿3（p925 > p920）被腿4 蕴含，不单列。通过者按**竞价额**
（p925 × 竞价量手 × 100，元）降序取 ``min(limit, n)`` / 日
（T-0012 R3：额前 10 加严无增益，不预筛）。

数据依赖（pool_snapshot 两个池）：

- ``opening_match``：{code: {price, volume_lots, ...}}，p925 为开盘撮合价；
- ``auction_series``：{code: {p920, time_label}}，9:20 参考价
  （writer 已按"第一个 time_label 以 09:20 开头"的口径提炼，缺失的票不参与）。

昨收取自日线表（T-1 收盘）。买卖：买 = T 日开盘（= p925 撮合价）；
主卖 = T+1 开盘（持 1 日）。回测中 T 收盘口径（+2.10%/0.60）更优，
仅作持仓者参考上限写入卡片，不改变买卖口径。

门控声明：T-1 定版态拦 {冰点, 退潮}（抢筹顺势路径，弱市不做）；
分歧放行——样本期分歧偏负已在 §五记疑，弱市样本积累后复验再收紧；
bear_gate=False（样本期未见长熊交互）。
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

KIND_ADVICE = "advice"

#: 盘后快照池名：交易日历（与 firstboard_dip / dragon 共用同一种子格式）。
_CALENDAR_POOL_NAME = "trading_calendar"

#: 交易日历探测回看的自然日跨度。
_CALENDAR_LOOKBACK_DAYS = 45

#: 竞价撮合快照池名（p925 + 竞价量，与 firstboard_dip 共用同一来源）。
_OPENING_MATCH_POOL = "opening_match"

#: 竞价时序池名（writer 提炼后的 {code: {p920, time_label}}）。
_AUCTION_SERIES_POOL = "auction_series"


# ============================================================ 纯计算部件


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


def _auction_signal(
    *,
    p920: float,
    p925: float,
    pre_close: float,
    min_rise: float,
    max_chg: float,
) -> dict[str, Any] | None:
    """纯函数三腿判定（布尔式对齐 ``verify_auction_pilot.py``）。

    全腿通过返回量值快照（供 gates 展示）；任一腿不通过返回 ``None``。
    腿3（p925 > p920）被腿4 蕴含，不单列。
    """
    if not (p920 > 0 and p925 > 0 and pre_close > 0):
        return None
    if not p920 < pre_close:  # 腿2：9:20 参考价低于昨收
        return None
    rise_vs_p920 = p925 / p920 - 1
    if not rise_vs_p920 >= min_rise:  # 腿4：尾段拉升
        return None
    chg_vs_pc = p925 / pre_close - 1
    if not chg_vs_pc < max_chg:  # 腿5：开盘不过热
        return None
    return {
        "rise_vs_p920": rise_vs_p920,
        "chg_vs_pc": chg_vs_pc,
    }


# ============================================================ 提醒卡片


@dataclass(frozen=True, slots=True)
class Advice:
    """竞价抢筹买卖提醒（字段与 dragon 卡片对齐，前端零改动）。"""

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
        """序列化为提醒卡片（键集合与 dragon 完全一致）。

        ``sell_price_ref="open"``：复盘按 T+1 **开盘价**了结（本策略卖出时点）；
        其余策略不输出该键，复盘缺省按 T+1 收盘（向后兼容历史行）。
        """
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
            "sell_price_ref": "open",
            "field_snapshot": dict(self.field_snapshot),
        }


# ============================================================ 策略


@register_strategy
class AuctionGrabStrategy(BaseStrategy):
    """J1 竞价抢筹：低吸位竞价抢筹三腿 + 竞价额排序，T+1 开盘了结。"""

    strategy_id = "auction_grab"
    label = "竞价抢筹"
    version = "1.0.0"
    description = (
        "主板非 ST 个股 T 日竞价三腿全过：9:20 参考价低于昨收（低吸位）、"
        "9:25 相对 9:20 拉升不低于 +2%、9:25 相对昨收涨幅低于 +5%（不过热）；"
        "通过者按竞价额降序取每日前三，T 日开盘买入、T+1 开盘卖出（持 1 日）。"
        "门控拦冰点/退潮：抢筹顺势路径，弱市不做；分歧期放行（样本记疑，复验中）。"
    )

    #: 仅开盘确认阶段（9:25~9:30 竞价定版后一次性判定）。
    phases: ClassVar[frozenset[Phase]] = frozenset({Phase.OPENING})

    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = (
        StrategyParamSpec(
            key="min_rise",
            label="尾段拉升下限",
            type="percent",
            default=0.02,
            min=0.0,
            max=0.10,
            step=0.005,
            description="p925 相对 p920 的最低涨幅（腿4：竞价尾段快速拉升确认）。",
        ),
        StrategyParamSpec(
            key="max_chg",
            label="开盘涨幅上限",
            type="percent",
            default=0.05,
            min=0.0,
            max=0.20,
            step=0.005,
            description="p925 相对昨收的最大涨幅（腿5：排除一字板与高开透支）。",
        ),
        StrategyParamSpec(
            key="limit",
            label="每日提醒上限",
            type="int",
            default=3,
            min=1,
            max=10,
            unit="只",
            description="每日最多提醒只数（按竞价额降序取 min(limit, n)）。",
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

    #: T-1 定版态拦 {冰点, 退潮}；其余五态（含分歧）显式放行——避免注册表
    #: 对未声明态的回退告警与 0.5 降系数；分歧放行属样本记疑项，复验后再调。
    gate_matrix: ClassVar[Mapping[CycleState, GateRule]] = {
        CycleState.ICE: GateRule(False, 0.0),
        CycleState.RETREAT: GateRule(False, 0.0),
        CycleState.TURN: GateRule(True, 1.0),
        CycleState.REPAIR: GateRule(True, 1.0),
        CycleState.ACCEL: GateRule(True, 1.0),
        CycleState.DIVERGE: GateRule(True, 1.0),
        CycleState.UNKNOWN: GateRule(True, 1.0),
    }

    # ------------------------------------------------ 阶段钩子

    async def confirm_opening(self, ctx: StrategyContext) -> dict[str, Any]:
        """竞价定版确认：三腿判定 + 竞价额排序 → 买卖提醒并落库。"""
        repos = ctx.repos
        if repos is None:
            return self._empty_opening(ctx)
        opening = await self._opening_match(ctx)
        refs = await self._auction_refs(ctx)
        if not opening or not refs:
            return self._empty_opening(ctx)
        pre_day = await self._prev_trading_day(ctx)
        if pre_day is None:
            return self._empty_opening(ctx)
        params = await self.get_params(ctx)
        min_rise = float(self.param(params, "min_rise"))
        max_chg = float(self.param(params, "max_chg"))
        limit = int(self.param(params, "limit"))
        position = float(self.param(params, "position"))

        hits: list[dict[str, Any]] = []
        for code in sorted(set(opening) & set(refs)):
            stock = await repos.stocks.get(code)
            if stock is None or bool(getattr(stock, "is_st", False)):
                continue
            if not _is_main_board_code(code):
                continue
            p925 = opening[code]["price"]
            volume_lots = opening[code]["volume_lots"]
            pre_close = await self._pre_close(repos, code, pre_day)
            if pre_close is None:
                continue
            # 除权伪跳变护栏：三腿均为单边上界，拦不住 -50% 量级的除权伪跳变；
            # 竞价价 vs 昨收越界（复权口径未对齐 / 坏数据）整票拒收。
            p920 = refs[code]
            if is_suspect_price_move(p925, pre_close) or is_suspect_price_move(p920, pre_close):
                continue
            legs = _auction_signal(
                p920=refs[code],
                p925=p925,
                pre_close=pre_close,
                min_rise=min_rise,
                max_chg=max_chg,
            )
            if legs is None:
                continue
            hits.append(
                {
                    "code": code,
                    "name": str(stock.name),
                    "p920": refs[code],
                    "p925": p925,
                    "pre_close": pre_close,
                    "amount": p925 * volume_lots * 100.0,  # 手 → 股 → 元
                    "legs": legs,
                }
            )
        # 竞价额降序取 min(limit, n)；同额按代码排序保证输出稳定。
        hits.sort(key=lambda item: (-item["amount"], item["code"]))
        picked = hits[:limit]
        for rank, item in enumerate(picked, start=1):
            item["rank"] = rank
        advices = [
            self._advice(item, trade_date=ctx.trade_date, position=position)
            for item in picked
        ]
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

    async def _opening_match(self, ctx: StrategyContext) -> dict[str, dict[str, float]]:
        """读取竞价撮合快照：{code: {price, volume_lots}}（p925 + 竞价量）。"""
        repos = ctx.repos
        if repos is None:
            return {}
        snapshot = await repos.pool_snapshot.get(ctx.trade_date, _OPENING_MATCH_POOL)
        payload = snapshot.payload if snapshot is not None else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for code, item in payload.items():
            if not isinstance(item, dict):
                continue
            price = _to_float(item.get("price"))
            volume = _to_float(item.get("volume_lots"))
            if price and volume:
                out[str(code)] = {"price": price, "volume_lots": volume}
        return out

    async def _auction_refs(self, ctx: StrategyContext) -> dict[str, float]:
        """读取竞价时序池（writer 已提炼）：{code: p920}。"""
        repos = ctx.repos
        if repos is None:
            return {}
        snapshot = await repos.pool_snapshot.get(ctx.trade_date, _AUCTION_SERIES_POOL)
        payload = snapshot.payload if snapshot is not None else None
        if not isinstance(payload, dict):
            return {}
        out: dict[str, float] = {}
        for code, item in payload.items():
            if not isinstance(item, dict):
                continue
            p920 = _to_float(item.get("p920"))
            if p920:
                out[str(code)] = p920
        return out

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

    async def _pre_close(self, repos: Any, code: str, pre_day: date) -> float | None:
        """取 T-1 收盘价作为昨收（该日无日线则拒绝判定）。"""
        rows = await repos.daily_bars.get_range(code, pre_day, pre_day)
        if not rows or rows[-1].trade_date != pre_day:
            return None
        return _to_float(rows[-1].close)

    def _advice(
        self,
        item: dict[str, Any],
        *,
        trade_date: date,
        position: float,
    ) -> Advice:
        legs = item["legs"]
        gates = (
            {
                "factor_id": "p920_lt_pc",
                "label": "低吸位（p920<昨收）",
                "passed": True,
                "detail": f"9:20 价 {item['p920']:.2f} 低于昨收 {item['pre_close']:.2f}",
            },
            {
                "factor_id": "rise_vs_p920",
                "label": "尾段拉升",
                "passed": True,
                "detail": f"9:25 相对 9:20 拉升 {legs['rise_vs_p920']:.2%}",
            },
            {
                "factor_id": "chg_vs_pc",
                "label": "开盘不过热",
                "passed": True,
                "detail": f"9:25 相对昨收 {legs['chg_vs_pc']:+.2%}（< 上限）",
            },
            {
                "factor_id": "amt_rank",
                "label": "竞价额排序",
                "passed": True,
                "detail": f"竞价额 {item['amount'] / 1e8:.2f} 亿元，前 {item['rank']}",
            },
        )
        return Advice(
            code=str(item["code"]),
            name=str(item["name"]),
            trade_date=trade_date,
            path_id="auction",
            path_label="竞价抢筹",
            buy_day=trade_date,
            buy_price=float(item["p925"]),
            gates=gates,
            position=position,
            stop_loss_price=None,
            sell_timing="T+1 开盘卖出（持 1 日，主口径）",
            field_snapshot={
                "p920": float(item["p920"]),
                "p925": float(item["p925"]),
                "pre_close": float(item["pre_close"]),
                "rise_vs_p920": round(float(legs["rise_vs_p920"]), 4),
                "chg_vs_pc": round(float(legs["chg_vs_pc"]), 4),
                "matched_amount_yuan": round(float(item["amount"]), 2),
                "note": "持仓者参考：T 收盘卖出口径回测更优（+2.10%/胜率 0.60）",
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
