"""
应用配置。

设计原则：
    1. 所有可配置项集中在 Settings 类
    2. 从环境变量 / .env 文件加载（pydantic-settings）
    3. 业务代码中只通过 settings.xxx 访问，禁止直接读环境变量

完整配置项说明请参阅 docs/配置说明.md。
"""
import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_cors_origins(value: Any) -> list[str]:
    """兼容三种写法：
    1. JSON 数组：`["http://a","http://b"]`
    2. 逗号分隔：`http://a,http://b`
    3. 已经是 list[str]
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except json.JSONDecodeError:
                pass
        # 逗号分隔
        return [v.strip() for v in s.split(",") if v.strip()]
    return [str(value)]


class Settings(BaseSettings):
    """全局配置。"""

    # ---------- 应用基础 ----------
    APP_NAME: str = "Leadhub"
    APP_DESCRIPTION: str = "Leadhub 管理系统后端 API"
    APP_ENV: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = False   # 生产必须 false；dev 在 .env 中显式打开
    API_V1_PREFIX: str = "/api/v1"

    # ---------- 安全 ----------
    SECRET_KEY: str = Field(
        ...,
        min_length=32,
        description="JWT 签名密钥，至少 32 字符，请使用 openssl rand -hex 32 生成",
    )
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    # 短期 token 60 分钟；如需长期会话请实现 /auth/refresh
    JWT_EXPIRE_MINUTES: int = 60

    # ---------- 服务监听 ----------
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4  # K8s / gunicorn 生产 worker 数

    # ---------- 数据库 ----------
    # DB_TYPE: postgresql（默认，make start 复用本机/起 Docker）| sqlite（单文件零配置，适合纯本地体验）
    DB_TYPE: Literal["sqlite", "postgresql"] = "postgresql"
    SQLITE_PATH: str = "data/app.db"  # DB_TYPE=sqlite 时用；相对 backend/ 目录

    # PostgreSQL 配置（仅 DB_TYPE=postgresql 时生效）
    # 真实密码只放 .env（已被 .gitignore 排除）——绝不能烘焙进源码默认值，
    # 否则 --init-git 会把真实密码提交进 git 历史
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "youzi-leadhub"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "youzi-leadhub"

    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_PRE_PING: bool = True    # 防长连接被 PG/NAT 静默断开（SQLite 下无效但不报错）
    DB_POOL_RECYCLE: int = 3600      # 1 小时回收

    @property
    def DATABASE_URL(self) -> str:
        """运行时用——根据 DB_TYPE 返回对应 driver URL。"""
        if self.DB_TYPE == "sqlite":
            import os
            from pathlib import Path
            # 保证父目录存在
            db_path = Path(self.SQLITE_PATH)
            if not db_path.is_absolute():
                # 相对路径：相对 backend/ 解析
                db_path = Path(os.getcwd()) / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{db_path}"
        # postgresql：异步用 asyncpg
        from urllib.parse import quote
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{quote(self.POSTGRES_PASSWORD, safe='')}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def ALEMBIC_DATABASE_URL(self) -> str:
        """alembic 走同步路径。sqlite→sqlite，postgresql→psycopg2。"""
        if self.DB_TYPE == "sqlite":
            import os
            from pathlib import Path
            db_path = Path(self.SQLITE_PATH)
            if not db_path.is_absolute():
                db_path = Path(os.getcwd()) / db_path
            return f"sqlite:///{db_path}"
        from urllib.parse import quote
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{quote(self.POSTGRES_PASSWORD, safe='')}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ---------- CORS ----------
    # 生产必须显式列出可信域名。留空 = 拒绝所有跨域。
    CORS_ORIGINS: Annotated[list[str], BeforeValidator(_parse_cors_origins)] = [
        "http://localhost:3000",
    ]

    # ---------- 日志 ----------
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "text"

    # ---------- 初始化 ----------
    # 真实初始密码只放 .env（gitignored）——默认值保持中性，防密码进 git 历史
    INITIAL_ADMIN_USERNAME: str = "admin"
    INITIAL_ADMIN_PASSWORD: str = "admin"
    INITIAL_ADMIN_EMAIL: str = "admin@example.com"

    # ---------- 数据库自动维护 ----------
    # AUTO_INIT_DB: true=首次启动 create_all + stamp head；后续启动 alembic upgrade
    # AUTO_SEED_DATA: 种子数据。**生产必须 false**（避免 Demo@123 等公开凭据进入生产 DB）
    AUTO_INIT_DB: bool = True
    AUTO_SEED_DATA: bool = False

    # ---------- 线索采集 ----------
    GOOGLE_MAPS_API_KEY: str = ""   # google_maps 采集器必填，未配置任务直接 failed
    # Meta 广告资料库（Ad Library API）访问令牌，meta_ads 采集器必填。
    # 获取：https://www.facebook.com/ads/archive/api 创建应用申请 token（免费），
    # 需要的权限很窄（ads_archive 只读公开广告数据）。
    META_ADS_ACCESS_TOKEN: str = ""
    COLLECT_MAX_CONCURRENT: int = 2   # 同时运行的采集任务数（满则排队）
    COLLECT_TASK_TIMEOUT: int = 3600  # 单任务超时（秒）
    ENRICH_CONCURRENCY: int = 5     # 富化并发站点数
    SCHEDULER_ENABLED: bool = False  # 定时调度总开关（多 worker 只在一个进程开）
    # 评分权重覆盖（JSON，键名见 collectors/scoring.py）
    SCORING_WEIGHTS: dict[str, int] = {}
    # 高渗透目标地区（ISO2），逗号分隔或 JSON 数组
    TARGET_REGIONS: Annotated[list[str], BeforeValidator(_parse_cors_origins)] = [
        "MY", "SG", "ID", "TH", "PH", "VN", "AE", "SA", "QA", "KW",
        "BR", "MX", "CO", "AR", "CL",
    ]

    # ---------- 受信任主机（防 host header 注入）----------
    # 必须包含：localhost（dev）+ 你的真实域名（prod）+ testserver（TestClient/pytest）
    # 错误：只用 ["localhost","127.0.0.1"] → 任何带域名的 nginx 部署直接 400
    TRUSTED_HOSTS: Annotated[list[str], BeforeValidator(_parse_cors_origins)] = [
        "localhost", "127.0.0.1", "testserver",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """单例获取配置对象。"""
    return Settings()


settings = get_settings()
