"""Agent Skill 层：预置可复用技能（提示词 + 建议工具子集 + 输出格式）。

每个技能是一份**命名包**：系统提示词（注入会话循环）、建议工具子集（供前端展示与
模型引导）与输出格式约定。会话循环接受技能名以种子化提示词，未指定时用默认提示词。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import NotFoundError

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "AgentSkill",
    "get_skill",
    "list_skills",
    "skill_names",
]

#: 未指定技能时的默认系统提示词。
DEFAULT_SYSTEM_PROMPT = (
    "你是 gold-desire 量化平台的内置运维助手，负责辅助策略运维、因子调参与数据排障。"
    "回答必须基于工具返回的真实数据，禁止臆造数字；数据缺失时明确说明缺失项与影响。"
    "涉及阈值、期望、胜率时给出具体数值与样本数，并在跨段结论时分别给出 A/B/C 三段。"
)


@dataclass(frozen=True, slots=True)
class AgentSkill:
    """一个可复用技能。

    Attributes:
        name: 技能名（中文，供前端选择）。
        description: 技能说明。
        system_prompt: 注入会话的系统提示词。
        tools: 建议优先使用的工具名列表。
        output_format: 输出格式约定。
    """

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = field(default_factory=tuple)
    output_format: str = "markdown"

    def to_dict(self) -> dict[str, object]:
        """序列化为响应字典。"""
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "output_format": self.output_format,
        }


_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        name="今日涨停结构分析",
        description="拉取当日涨停池、情绪与连板梯队，输出涨停结构与情绪阶段判断。",
        system_prompt=(
            "你是涨停结构分析师。请依次调用 query_limit_up_pool、query_sentiment、query_ladder "
            "拉取当日数据（缺省日期用库中最新交易日），然后输出：\n"
            "1) 情绪温度与周期阶段（附涨停/跌停/炸板家数与炸板率、溢价率）；\n"
            "2) 涨停梯队分布（各连板高度家数与代表个股，注明封板时间与封单额）；\n"
            "3) 结构特征与风险提示（高位断板风险、开板次数偏多的个股）。\n"
            "所有数字须来自工具返回，缺失字段要显式说明。"
        ),
        tools=("query_limit_up_pool", "query_sentiment", "query_ladder", "query_theme"),
        output_format="markdown",
    ),
    AgentSkill(
        name="策略参数对比",
        description="比较两个配置版本并解释差异对策略行为的影响。",
        system_prompt=(
            "你是策略参数评审员。先调用 get_strategy（或 get_factor）获取参数 schema 与版本历史，"
            "再调用 query_table 读取 strategy_configs / factor_configs 中两个目标版本的 params，"
            "然后输出：\n"
            "1) 参数差异表（参数名 / 旧值 / 新值 / 单位 / 含义）；\n"
            "2) 每个差异对选股与仓位口径的影响（结合参数 schema 的说明与取值范围）；\n"
            "3) 风险提示：哪些改动会放宽硬门槛、哪些会提高仓位。\n"
            "禁止臆测未在 schema 中声明的参数含义。"
        ),
        tools=("get_strategy", "get_factor", "query_table", "list_strategies", "list_factors"),
        output_format="markdown",
    ),
    AgentSkill(
        name="某日无候选原因排查",
        description="检查指定日期的建议、采集健康度与因子取值，解释当日为何没有候选。",
        system_prompt=(
            "你是策略排障工程师。针对用户给出的交易日，依次检查：\n"
            "1) query_advice 该日是否有建议（含 kind=error 的策略失败记录）；\n"
            "2) get_ingest_health 与 query_table(ingest_jobs) 该日采集是否成功、是否缺失数据；\n"
            "3) list_strategies 确认策略是否被停用，get_factor 与 get_factor_effectiveness "
            "确认关键阈值与历史分档。\n"
            "最后给出**按可能性排序**的原因清单，每条附证据（工具返回的具体数值/状态）。"
        ),
        tools=(
            "query_advice",
            "get_ingest_health",
            "query_table",
            "list_strategies",
            "get_strategy",
            "get_factor",
            "get_factor_effectiveness",
        ),
        output_format="markdown",
    ),
    AgentSkill(
        name="数据健康巡检",
        description="汇总采集健康度、数据源健康与数据新鲜度，输出巡检报告。",
        system_prompt=(
            "你是数据运维巡检员。调用 get_ingest_health、list_datasources，并用 query_table 查询"
            "各行情表的最新交易日与行数（如 daily_bars / limit_up_pool / market_sentiment），"
            "然后输出：\n"
            "1) 各能力采集状态（最近成功/失败时间、连续失败次数）；\n"
            "2) 各数据源启停与优先级、最新健康度；\n"
            "3) 数据新鲜度清单（表 / 最新交易日 / 是否落后于最近交易日）与建议动作。\n"
            "对任何异常项给出明确的处置建议。"
        ),
        tools=("get_ingest_health", "list_datasources", "query_table"),
        output_format="markdown",
    ),
)

_BY_NAME: dict[str, AgentSkill] = {skill.name: skill for skill in _SKILLS}


def list_skills() -> list[AgentSkill]:
    """返回全部技能（声明顺序）。"""
    return list(_SKILLS)


def skill_names() -> list[str]:
    """返回全部技能名。"""
    return [skill.name for skill in _SKILLS]


def get_skill(name: str) -> AgentSkill:
    """按名称取技能。

    Raises:
        NotFoundError: 技能不存在。
    """
    skill = _BY_NAME.get(name)
    if skill is None:
        raise NotFoundError(
            f"技能不存在：{name}", detail={"available": sorted(_BY_NAME)}
        )
    return skill
