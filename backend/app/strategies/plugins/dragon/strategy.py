"""龙回头策略插件（第一个真实策略，领域核心）。

两路并行（readme §3）：

- **S2 高位跳水型**：``D+1`` 开盘买入；硬门槛 = 首阴形态=尾盘跳水 AND 首阴振幅≥8%
  AND 次日开盘≤-3%；固定 20% 仓、**不加码**。
- **S4 缩量反转型**：``D+2`` 开盘买入；硬门槛 = 次日量/首阴量<0.6 AND 首阴振幅≥8%；
  按 6 条加分项加码（20% → 上限 30%）。

卖出规则两路统一：**不设止盈 + 3% 止损 + 持 1 个可卖日**（readme §6），止损**仅在可卖日
生效**（readme §14.2）。组合风控：同票同日按优先级（``S2 > S4``）去重、同日总仓 ≤ 80%
等比压缩（readme §7）。

**阈值来源**：所有硬门槛/加分项阈值均取自**因子参数**（``first_yin_shape`` /
``first_yin_amplitude`` / ``next_day_open_pct`` / ``next_day_vol_vs_first_yin`` 等），
本策略不硬编码阈值常量。唯一例外见 :mod:`app.strategies.plugins.dragon.bonus`
（``d_low_pct`` 无对应因子，改由策略参数 ``s2_bonus_low_floor`` 提供，且仅用于展示）。

**结构化建议**（readme §8）：每条建议含 路次 / 命中硬门槛明细 / 加分项清单 / 建议仓位 /
止损价 / 卖出时点 / **依据字段快照**，保证建议可解释。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, ClassVar

from app.engine.dragon_samples import DragonSample
from app.engine.portfolio import (
    BonusHit,
    BonusSpec,
    GateHit,
    GateSpec,
    PathSpec,
    evaluate_path,
)
from app.strategies.plugins.dragon.bonus import s2_bonus, s4_bonus
from app.strategies.plugins.dragon.gates import GATE_MATRIX, s2_gates, s4_gates
from app.strategies.plugins.dragon.paths import build_paths
from app.strategies.protocol import (
    BaseStrategy,
    Phase,
    StrategyParamSpec,
)

__all__ = ["KIND_ADVICE", "Advice", "DragonStrategy"]

#: 建议报告类型（落 ``advice_reports.kind``）。
KIND_ADVICE = "advice"

#: 建池/判定窗口（自然日）——覆盖连板波（向前）与 D+1/D+2/D+3（向后）。
_POOL_LOOKBACK_DAYS = 30
_POOL_LOOKAHEAD_DAYS = 5


@dataclass(frozen=True, slots=True)
class Advice:
    """结构化买入建议。

    含 readme §8 要求：路次 / 硬门槛明细 / 加分项清单 / 建议仓位 / 止损价 / 卖出时点 / 字段快照。
    """

    code: str
    name: str
    trade_date: date
    path_id: str
    path_label: str
    buy_day: date
    buy_price: float
    gates: tuple[GateHit, ...]
    bonus: tuple[BonusHit, ...]
    bonus_score: int
    position: float
    stop_loss_price: float
    sell_timing: str
    field_snapshot: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """转为 ``advice_reports.payload`` 形态（前端卡片 / Agent 可直接读取）。"""
        return {
            "path_id": self.path_id,
            "path_label": self.path_label,
            "code": self.code,
            "name": self.name,
            "buy_day": self.buy_day.isoformat(),
            "buy_price": self.buy_price,
            "gates": [
                {
                    "factor_id": hit.factor_id,
                    "label": hit.label,
                    "passed": hit.passed,
                    "detail": hit.detail,
                }
                for hit in self.gates
            ],
            "bonus": [
                {"factor_id": hit.factor_id, "label": hit.label, "satisfied": hit.satisfied}
                for hit in self.bonus
            ],
            "bonus_score": self.bonus_score,
            "position": self.position,
            "stop_loss_price": self.stop_loss_price,
            "sell_timing": self.sell_timing,
            "field_snapshot": dict(self.field_snapshot),
        }


class DragonStrategy(BaseStrategy):
    """龙回头策略（两路：S2 高位跳水型 / S4 缩量反转型）。"""

    strategy_id = "dragon"
    label = "龙回头"
    version = "1.0.0"
    description = "龙回头两路：S2 = D+1 开盘（高位跳水型）、S4 = D+2 开盘（缩量反转型）。"
    phases: ClassVar[frozenset[Phase]] = frozenset({Phase.POOL, Phase.OPENING, Phase.INTRADAY})
    params_schema: ClassVar[tuple[StrategyParamSpec, ...]] = (
        StrategyParamSpec(
            key="base_position",
            label="单路基础仓位",
            type="percent",
            default=0.20,
            min=0.0,
            max=1.0,
            step=0.01,
            unit="小数",
            description="S2 / S4 的单路基础仓位；默认 0.20（readme §7.1）。",
        ),
        StrategyParamSpec(
            key="s4_max_position_factor",
            label="S4 单路上限倍数",
            type="float",
            default=1.5,
            min=1.0,
            max=3.0,
            step=0.1,
            unit="倍",
            description="S4 加码后的单路上限倍数；默认 1.5（readme §7.1 / §14.5）。",
        ),
        StrategyParamSpec(
            key="bonus_step",
            label="加分项加码步长",
            type="percent",
            default=0.25,
            min=0.0,
            max=1.0,
            step=0.05,
            unit="小数",
            description="每满足 1 条加分项的仓位增幅；默认 0.25（readme §7.1）。",
        ),
        StrategyParamSpec(
            key="max_total_position",
            label="同日总仓上限",
            type="percent",
            default=0.80,
            min=0.0,
            max=1.0,
            step=0.05,
            unit="小数",
            description="同一日历日总仓位上限，超限等比压缩；默认 0.80（readme §7.2）。",
        ),
        StrategyParamSpec(
            key="stop_loss",
            label="止损档",
            type="percent",
            default=-0.03,
            min=-1.0,
            max=0.0,
            step=0.01,
            unit="小数",
            description="分钟级止损档；默认 -0.03（readme §6 / §14.1）。",
        ),
        StrategyParamSpec(
            key="hold_sellable_days",
            label="持有可卖日数",
            type="int",
            default=1,
            min=1,
            max=3,
            step=1,
            unit="日",
            description="持有几个可卖日；默认 1（readme §6）。",
        ),
        StrategyParamSpec(
            key="s2_bonus_low_floor",
            label="S2 加分：首阴最大跌幅下界",
            type="percent",
            default=-0.02,
            min=-1.0,
            max=0.0,
            step=0.01,
            unit="小数",
            description=(
                "S2 加分项「首阴最低 >-2%（浅）」阈值；因子集中无 d_low_pct 对应因子，"
                "故由策略参数提供，仅用于展示（readme §4.2）。"
            ),
        ),
    )
    #: 门控矩阵由策略自身声明（核心零改动）。
    gate_matrix: ClassVar[Mapping[Any, Any]] = GATE_MATRIX

    # ------------------------------------------------------------ 路径 / 参数

    def build_paths(
        self, params: Mapping[str, Any], *, scale_by_bonus: bool = True
    ) -> list[PathSpec]:
        """按已解析策略参数构造两路路径。"""
        return build_paths(
            base_position=float(self.param(params, "base_position")),
            s4_max_position_factor=float(self.param(params, "s4_max_position_factor")),
            scale_by_bonus=scale_by_bonus,
        )

    @staticmethod
    def factor_param_ids() -> tuple[str, ...]:
        """返回两路门槛/加分项引用的**因子**标识（去重；不含策略命名空间 ``dragon``）。"""
        specs: list[GateSpec | BonusSpec] = [
            *s2_gates(),
            *s4_gates(),
            *s2_bonus(),
            *s4_bonus(),
        ]
        ids: list[str] = []
        for spec in specs:
            if spec.factor_id != DragonStrategy.strategy_id and spec.factor_id not in ids:
                ids.append(spec.factor_id)
        return tuple(ids)

    def build_factor_params(
        self,
        params: Mapping[str, Any],
        resolved: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """构造 ``{owner_id: 参数}``：因子参数 + 策略自身命名空间（供无因子的加分项取阈值）。"""
        merged: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in (resolved or {}).items()
        }
        merged[self.strategy_id] = dict(params)
        return merged

    # ------------------------------------------------------------ 建议（同步核心）

    def sell_timing(self, path: PathSpec, params: Mapping[str, Any]) -> str:
        """生成人类可读的卖出时点（如「T1（D+2）收盘了结，持 1 个可卖日」）。"""
        hold = int(self.param(params, "hold_sellable_days"))
        first = path.sellable_day_fields[0] if path.sellable_day_fields else "?"
        return f"{first} 收盘了结（持 {hold} 个可卖日；止损仅在可卖日生效）"

    def advise(
        self,
        sample: DragonSample,
        *,
        factor_params: Mapping[str, Mapping[str, Any]],
        params: Mapping[str, Any],
        require_sell: bool = True,
    ) -> Advice | None:
        """对单条样本给出最高优先级的结构化建议；未命中任何一路返回 ``None``。

        ``require_sell=False`` 为**开盘判定**（Phase.OPENING）路径：可卖日尚未
        发生，卖出撮合无从模拟，跳过 ``sell is not None`` 校验（止损价仍可给出）。
        """
        ctx = sample.factor_context()
        stop_loss = float(self.param(params, "stop_loss"))
        for path in sorted(self.build_paths(params), key=lambda item: item.priority):
            evaluation = evaluate_path(path, sample, ctx, factor_params)
            if not evaluation.matched:
                continue
            if require_sell and evaluation.sell is None:
                continue
            buy_price = path.buy_price(sample)
            buy_day = evaluation.buy_day or sample.D
            if buy_price is None:
                continue
            return Advice(
                code=sample.code,
                name=sample.name,
                trade_date=sample.D,
                path_id=path.path_id,
                path_label=path.label,
                buy_day=buy_day,
                buy_price=buy_price,
                gates=evaluation.gates,
                bonus=evaluation.bonus,
                bonus_score=evaluation.bonus_score,
                position=evaluation.position,
                stop_loss_price=buy_price * (1 + stop_loss),
                sell_timing=self.sell_timing(path, params),
                field_snapshot=sample.field_snapshot(),
            )
        return None

    # ------------------------------------------------------------ 生命周期钩子

    async def confirm_opening(self, ctx: Any) -> dict[str, Any]:
        """开盘判定（09:25 撮合价就绪后，竞价窗口内执行一次）。

        数据链：``opening_match`` 采集任务把当日撮合价落 ``pool_snapshot`` →
        本钩子经 :func:`build_opening_samples` 合成开盘注入样本 → S2（D=上一
        交易日）/ S4（D=上上交易日）按硬门槛判定 → 命中即落 ``advice_reports``
        （``buy_day = 今日``），实现「次日开盘买点」的**盘中实时提示**
        （readme §8 时间轴的 09:25 行）。
        """
        if getattr(ctx, "repos", None) is None:
            return {"strategy_id": self.strategy_id, "trade_date": ctx.trade_date.isoformat(), "advices": []}
        from app.engine.dragon_samples import build_opening_samples

        row = await ctx.repos.pool_snapshot.get(ctx.trade_date, "opening_match")
        opening: dict[str, float] = {}
        if row is not None:
            payload = row.payload or {}
            opening = {
                str(code): float(item["price"])
                for code, item in payload.items()
                if isinstance(item, dict) and item.get("price")
            }
        if not opening:
            return {
                "strategy_id": self.strategy_id,
                "trade_date": ctx.trade_date.isoformat(),
                "phase": "opening",
                "advices": [],
            }

        samples = await build_opening_samples(ctx.repos, ctx.trade_date, opening)
        params = await self.get_params(ctx)
        resolved = await self._resolve_factor_params(ctx)
        factor_params = self.build_factor_params(params, resolved)

        advices = [
            advice
            for advice in (
                self.advise(
                    sample,
                    factor_params=factor_params,
                    params=params,
                    require_sell=False,
                )
                for sample in samples
            )
            if advice is not None
        ]
        payloads = self._to_reports(ctx, advices)
        if payloads:
            await ctx.repos.advice_reports.upsert_many(payloads)
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "phase": "opening",
            "advices": [advice.to_payload() for advice in advices],
        }

    async def build_pool(self, ctx: Any) -> dict[str, Any]:
        """盘后建池：识别候选龙回头结构（≥2 连板波 + 紧邻首阴）。

        依赖 ``ctx.repos``（由框架注入）；无仓储（轻量上下文）时返回空候选池。
        有仓储时把候选**落库**（``dragon_pool``，同日重跑整体替换）——这是
        「量化选股」页「盘后建池（次日参考）」区块的数据来源。
        """
        samples = await self._window_samples(ctx)
        if getattr(ctx, "repos", None) is not None:
            ran_at = ctx.clock()
            await ctx.repos.dragon_pool.replace_pool(
                ctx.trade_date,
                self.strategy_id,
                [
                    {
                        "code": sample.code,
                        "name": sample.name,
                        "d_date": sample.D,
                        "boards": sample.boards,
                        "d_amp_pct": sample.d_amp_pct,
                        "shape_label": sample.shape_label,
                        "ran_at": ran_at,
                    }
                    for sample in samples
                ],
            )
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "candidates": [
                {
                    "code": sample.code,
                    "name": sample.name,
                    "D": sample.D.isoformat(),
                    "boards": sample.boards,
                    "d_amp_pct": sample.d_amp_pct,
                    "shape_label": sample.shape_label,
                }
                for sample in samples
            ],
        }

    async def confirm_intraday(self, ctx: Any) -> dict[str, Any]:
        """盘中确认：按两路门槛判定并产出结构化建议（落 ``advice_reports``）。"""
        samples = await self._window_samples(ctx)
        params = await self.get_params(ctx)
        resolved = await self._resolve_factor_params(ctx)
        factor_params = self.build_factor_params(params, resolved)

        advices = [
            advice
            for advice in (
                self.advise(sample, factor_params=factor_params, params=params)
                for sample in samples
            )
            if advice is not None
        ]
        payloads = self._to_reports(ctx, advices)
        if ctx.repos is not None and payloads:
            await ctx.repos.advice_reports.upsert_many(payloads)
        return {
            "strategy_id": self.strategy_id,
            "trade_date": ctx.trade_date.isoformat(),
            "advices": [advice.to_payload() for advice in advices],
        }

    # ------------------------------------------------------------ 内部

    async def _window_samples(self, ctx: Any) -> list[DragonSample]:
        """取判定窗口内的候选样本（无仓储时返回空）。"""
        if getattr(ctx, "repos", None) is None:
            return []
        from app.engine.dragon_samples import build_samples

        return await build_samples(
            ctx.repos,
            ctx.trade_date - timedelta(days=_POOL_LOOKBACK_DAYS),
            ctx.trade_date,
            lookback_days=_POOL_LOOKBACK_DAYS,
            lookahead_days=_POOL_LOOKAHEAD_DAYS,
        )

    async def _resolve_factor_params(self, ctx: Any) -> dict[str, dict[str, Any]]:
        """从因子注册表解析两路所需因子参数（阈值唯一来源）。"""
        from app.engine.backtest import resolve_factor_params

        return await resolve_factor_params(self.factor_param_ids(), getattr(ctx, "repos", None))

    def _to_reports(self, ctx: Any, advices: Sequence[Advice]) -> list[dict[str, Any]]:
        """把建议转为 ``advice_reports`` 行（幂等键含运行时间）。"""
        ran_at = ctx.clock()
        return [
            {
                "trade_date": advice.trade_date,
                "kind": KIND_ADVICE,
                "strategy_id": self.strategy_id,
                "strategy_version": None,
                "payload": advice.to_payload(),
                "ran_at": ran_at,
            }
            for advice in advices
        ]
