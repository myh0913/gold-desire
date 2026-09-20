"""应用配置。

所有可配置项集中于此，通过环境变量或 ``.env`` 注入（见 ``.env.example``）。
密钥类字段只从环境读取，SHALL NOT 写入版本库。

用法::

    from app.core.config import get_settings

    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CacheBackendName = Literal["memory", "redis"]
AppEnv = Literal["dev", "test", "staging", "prod"]


class Settings(BaseSettings):
    """gold-desire 全量运行配置。

    字段按域分组：应用 / 服务 / 数据库 / 缓存 / 认证 / Agent / 上游数据源 / CORS / 限流。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- 应用
    app_env: AppEnv = Field(default="dev", description="运行环境：dev/test/staging/prod")
    app_name: str = Field(default="gold-desire", description="应用名，用于日志与 OpenAPI 标题")
    app_debug: bool = Field(default=False, description="调试模式：开启 SQL echo 与详细报错")
    app_version: str = Field(default="0.1.0", description="应用版本号")

    # ---------------------------------------------------------------- 服务
    server_host: str = Field(default="0.0.0.0", description="监听地址")
    server_port: int = Field(default=8000, description="监听端口")
    server_workers: int = Field(default=1, description="uvicorn worker 数量")
    timezone: str = Field(default="Asia/Shanghai", description="业务统一时区")

    # ------------------------------------------------------------ 数据库
    database_url: str = Field(
        default="sqlite+aiosqlite:///./gold_desire.db",
        description="SQLAlchemy async DSN；生产为 postgresql+asyncpg://...",
    )
    db_pool_size: int = Field(default=20, description="连接池大小（仅 PG 生效）")
    db_max_overflow: int = Field(default=10, description="连接池溢出上限（仅 PG 生效）")
    db_pool_timeout: int = Field(default=30, description="获取连接超时秒数")
    db_echo: bool = Field(default=False, description="是否打印 SQL")

    # ------------------------------------------------------------ 分页
    page_size_default: int = Field(default=20, description="列表接口默认页大小")
    page_size_max: int = Field(default=200, description="列表接口最大页大小（禁止无界返回）")
    upsert_chunk_size: int = Field(default=500, description="批量 upsert 每批行数（避免参数上限）")

    # -------------------------------------------------------------- 缓存
    cache_backend: CacheBackendName = Field(
        default="memory", description="缓存后端：memory（默认，无需 Redis）或 redis"
    )
    redis_url: str | None = Field(default=None, description="Redis DSN；cache_backend=redis 时必填")
    cache_default_ttl: int = Field(default=60, description="默认 TTL（秒）")
    cache_max_entries: int = Field(default=10_000, description="进程内 LRU 最大条目数")

    # -------------------------------------------------------------- 认证
    jwt_secret: str = Field(default="change-me-in-production", description="JWT 签名密钥")
    jwt_algorithm: str = Field(default="HS256", description="JWT 算法")
    access_token_minutes: int = Field(default=30, description="access token 有效期（分钟）")
    refresh_token_days: int = Field(default=14, description="refresh token 有效期（天）")
    password_hash_rounds: int = Field(default=3, description="Argon2 时间成本参数")

    # -------------------------------------------------------------- Agent
    agent_model_base_url: str = Field(
        default="https://api.openai.com/v1", description="OpenAI 兼容接口 base_url"
    )
    agent_model_api_key: str | None = Field(default=None, description="模型 API Key")
    agent_model_name: str = Field(default="gpt-4o-mini", description="模型名")
    agent_timeout_seconds: float = Field(default=60.0, description="单次模型调用超时（秒）")
    agent_max_turns: int = Field(default=12, description="单会话最大轮数")
    agent_max_tool_calls: int = Field(default=30, description="单会话最大工具调用次数")

    # ---------------------------------------------------------- 上游数据源
    hithink_api_key: str | None = Field(default=None, description="同花顺（hithink）密钥占位")
    hithink_base_url: str = Field(
        default="https://api.hithink.example", description="同花顺接口地址"
    )
    xuangutong_api_key: str | None = Field(default=None, description="选股通密钥占位")
    xuangutong_base_url: str = Field(
        default="https://api.xuangutong.example", description="选股通接口地址"
    )
    eastmoney_api_key: str | None = Field(default=None, description="东方财富密钥占位")
    eastmoney_base_url: str = Field(
        default="https://api.eastmoney.example", description="东方财富接口地址"
    )
    allow_fake_fallback: bool = Field(
        default=True,
        description="真实源全失败时是否允许降级到 fake 源（生产强制关闭）",
    )

    # --------------------------------------------------------------- CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:5273", "http://127.0.0.1:5273"],
        description="允许的跨域来源，逗号分隔",
    )
    cors_allow_credentials: bool = Field(default=True, description="是否允许携带凭证")

    # -------------------------------------------------------------- 限流
    rate_limit_enabled: bool = Field(default=True, description="是否启用限流")
    rate_limit_requests: int = Field(default=600, description="全局令牌桶容量/窗口请求数")
    rate_limit_window_seconds: int = Field(default=60, description="限流窗口秒数")
    login_rate_limit_requests: int = Field(default=10, description="登录接口独立限流额度")
    login_max_failures: int = Field(default=5, description="连续失败锁定阈值")
    login_lockout_minutes: int = Field(default=5, description="锁定时长（分钟）")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """允许 ``CORS_ORIGINS=a,b`` 形式的逗号分隔字符串。"""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_sqlite(self) -> bool:
        """当前 DSN 是否为 SQLite（测试与本地默认）。"""
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        """是否生产环境。"""
        return self.app_env == "prod"

    @property
    def fake_fallback_enabled(self) -> bool:
        """fake 源是否可作为兜底候选（生产环境恒为 ``False``）。

        fake 源返回固定假数据，仅供本地开发与测试兜底；生产环境若用它兜底，
        等于把假行情写进库并被后续策略/页面当作真实数据消费，故 ``app_env=prod``
        时无论 ``allow_fake_fallback`` 配成什么，一律强制关闭。
        """
        return self.allow_fake_fallback and not self.is_production


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程内缓存的配置单例。"""
    return Settings()
