"""eltdx（通达信行情，TDX TCP）数据源：分时分钟点 / 09:25 集合竞价撮合。

分时**唯一真实源**（旧项目 ``quant-system`` 同款：``registry.py`` 中分时无 HTTP
备源）。SDK 为可选依赖（``pip install eltdx`` / ``pyproject`` 的 ``eltdx`` extra），
本模块**延迟导入**：未安装时构造 provider 即抛 :class:`UpstreamError`（采集侧降级
到 fake / 跳过），业务测试零依赖。

能力 / 上游调用（对齐旧 ``eltdx_source.py`` 与 ``resolve.standard_minute_points``）
---------------------------------------------------------------------------

- ``minute_bars``：当日 ``client.minutes.today(code)``；
  历史 ``client.minutes.history(code, date)``。
  返回 240 点/日：09:31~11:30 → 0..119；13:01~15:00 → 120..239，
  ``time_label`` 零填充 ``"HH:MM"``，``volume`` 单位**手**（分钟增量）。
- ``opening_match``：当日 ``client.trades.opening_match_today(code)``；
  历史 ``client.trades.opening_match_history(code, date)``（约 2025-10 起可用）。
  09:25 正式撮合价即当日开盘价（旧项目龙回头盘中分类的数据源）。
- ``auction_series``：当日 ``client.auctions.series(code)``；
  历史 ``client.auctions.series(code, date)``（专用 transport）。
  9:15~9:25 竞价阶段逐点虚拟撮合，``time_label`` **带秒**（如 ``"09:20:03"``）；
  ``matched_volume`` 单位**手**，``matched_amount`` 为估算（价×手×100）。
  消费方（J1 竞价抢筹）取第一个 ``time_label`` 以 ``"09:20"`` 开头的点作 9:20 参考价。

本层**只取回原始 payload**（``{"points": [...]}`` / ``{"matches": [...]}`` 形态），
字段归一化交给 ``mappings/defs/eltdx.py`` 的声明式映射。
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from app.core.errors import UpstreamError
from app.datasources.base import BaseProvider, SourceKind
from app.datasources.registry import register_provider

__all__ = ["EltdxProvider", "to_eltdx_code"]

_SHANGHAI = ZoneInfo("Asia/Shanghai")

#: 交易日历池名（pool_snapshot 中「trading_calendar」缓存，见 scheduler）——
#: 供「今天是否为交易日」判定，避免非交易日触达 TCP。
def _today_sh() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%d")


def to_eltdx_code(thscode: str) -> str:
    """内部标准代码（``600519.SH``）→ eltdx 代码（``sh600519``）。

    仅支持沪深 A 股后缀；``.BJ``（北交所）等未知后缀显式报错（fail fast），
    避免被静默错映射为 ``sz`` 前缀。
    """
    if not (thscode.endswith(".SH") or thscode.endswith(".SZ")):
        raise ValueError(
            f"eltdx 数据源仅支持 .SH/.SZ 代码，收到 {thscode!r}（北交所等请改用其他源）"
        )
    digits = thscode.split(".")[0]
    prefix = "sh" if thscode.endswith(".SH") else "sz"
    return f"{prefix}{digits}"


@register_provider
class EltdxProvider(BaseProvider):
    """eltdx（通达信 TDX TCP）数据源：分时 / 集合竞价撮合 / 竞价时序。

    TCP 连接**惰性创建并跨 fetch 复用**（对齐旧 ``EltdxSource``）；SDK 缺失时
    fetch 抛 :class:`UpstreamError`（由 resolve 层降级到备用源）。
    """

    source_id: ClassVar[str] = "eltdx"
    label: ClassVar[str] = "eltdx（通达信行情）"
    kind: ClassVar[SourceKind] = SourceKind.TCP
    capabilities: ClassVar[tuple[str, ...]] = (
        "minute_bars",
        "opening_match",
        "auction_series",
    )
    rate_limit_per_min: ClassVar[int] = 600
    priority: ClassVar[int] = 10

    def __init__(self, *, timeout: float = 3.0) -> None:
        self._timeout = timeout
        self._client: Any | None = None

    # ------------------------------------------------------------ 内部

    def _get_client(self) -> Any:
        """惰性创建 TDX 客户端（连接复用）；SDK 未安装即抛错。"""
        if self._client is None:
            try:
                from eltdx import TdxClient  # 可选依赖，延迟导入
            except Exception as exc:  # pragma: no cover - SDK 缺失环境
                raise UpstreamError(
                    "eltdx SDK 未安装（pip install eltdx）",
                    detail={"source": self.source_id, "error": str(exc)},
                ) from exc
            self._client = TdxClient(timeout=self._timeout)
        return self._client

    def close(self) -> None:
        """释放 TCP 连接（进程退出 / 测试清理用）。"""
        if self._client is not None:
            with suppress(Exception):  # pragma: no cover - 关闭失败忽略
                self._client.close()
            self._client = None

    @staticmethod
    def _require(args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UpstreamError(
                f"eltdx 缺少必填取数参数 {key!r}",
                detail={"source": "eltdx", "param": key},
            )
        return value

    # ------------------------------------------------------------ fetch

    async def fetch(self, capability: str, **args: Any) -> dict[str, Any]:
        """取回该能力的**原始** payload（形态见模块 docstring）。

        eltdx 为同步 TCP SDK，这里在线程池中执行避免阻塞事件循环。
        """
        import asyncio

        thscode = self._require(args, "thscode")
        date = self._require(args, "date")
        return await asyncio.to_thread(self._fetch_sync, capability, thscode, date)

    def _minute_amounts(self, client: Any, code: str, date: str, today: str) -> dict[str, float]:
        """取**当日** 1m K 线的 ``time_label → amount(元)`` 映射。

        分时点对象（``client.minutes``）不带成交额，1m K 线（``client.bars.get``）
        每根自带 ``amount``。两路时间标签一一对应（09:31~15:00 各 240 根）。
        仅当日请求合并：SDK ``bars.get`` 只返回最新交易日 K 线、无法按历史日期
        取数，历史请求若强行合并会把最新日成交额张冠李戴——宁缺勿错（留空）。
        K 线拉取失败时返回空映射（amount 留空，不影响分时主数据）。
        """
        if date != today:
            return {}
        try:
            bars = client.bars.get(code, period="1m", count=240)
            items = list(getattr(bars, "bars", []) or [])
            result: dict[str, float] = {}
            for b in items:
                ts = getattr(b, "time", None)
                if ts is None:
                    continue
                result[ts.strftime("%H:%M")] = float(getattr(b, "amount", 0.0) or 0.0)
            return result
        except Exception:
            return {}

    def _fetch_sync(self, capability: str, thscode: str, date: str) -> dict[str, Any]:
        client = self._get_client()
        code = to_eltdx_code(thscode)
        today = _today_sh()
        try:
            if capability == "minute_bars":
                series = (
                    client.minutes.today(code)
                    if date == today
                    else client.minutes.history(code, date)
                )
                points = getattr(series, "points", None) or []
                # 分时点对象不带成交额；1m K 线（client.bars.get）每根自带 amount（元）。
                # 按 time_label 对齐把 amount 附加到分时点上（对齐 quant 的 eltdx_source
                # minute_bars 口径：b.amount 即该分钟成交额）。
                amounts = self._minute_amounts(client, code, date, today)
                return {
                    "points": [
                        {
                            "time_label": str(getattr(p, "time_label", "") or ""),
                            "price": float(getattr(p, "price", 0) or 0),
                            "volume": float(getattr(p, "volume", 0) or 0),
                            "amount": amounts.get(str(getattr(p, "time_label", "") or "")),
                        }
                        for p in points
                    ]
                }
            if capability == "opening_match":
                om = (
                    client.trades.opening_match_history(code, date)
                    if date != today
                    else client.trades.opening_match_today(code)
                )
                if om is None:
                    return {"matches": []}
                return {
                    "matches": [
                        {
                            "price": float(getattr(om, "price", 0) or 0),
                            "volume": float(getattr(om, "volume", 0) or 0),
                            "time_label": str(getattr(om, "time_label", "") or "09:25"),
                        }
                    ]
                }
            if capability == "auction_series":
                series = (
                    client.auctions.series(code, date)
                    if date != today
                    else client.auctions.series(code)
                )
                return {
                    "points": [
                        {
                            "time_label": str(getattr(p, "time_label", "") or ""),
                            "price": float(getattr(p, "price", 0) or 0),
                            "matched_volume": float(getattr(p, "matched_volume", 0) or 0),
                            # SDK 估算属性（价×手×100）；不可用时映射层留空。
                            "matched_amount": getattr(p, "matched_amount_estimated", None),
                        }
                        for p in (getattr(series, "points", None) or [])
                    ]
                }
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(
                f"eltdx {capability}({thscode},{date}) 取数失败: {exc}",
                detail={"source": self.source_id, "capability": capability},
            ) from exc
        raise UpstreamError(
            f"eltdx 不支持能力 {capability!r}",
            detail={"source": self.source_id, "capability": capability},
        )

    # 时间戳辅助（保留供日志/测试对时使用）
    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(UTC)
