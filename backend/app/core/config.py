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
    # 单进程 = API + 采集任务执行器 + 调度器同进程（DB 队列是单进程设计，
    # WORKERS>1 会禁用任务执行——见 main.py lifespan）。吞吐不足时垂直扩容
    # （加 CPU/连接池），或拆独立 worker 进程部署后再调大。
    WORKERS: int = 1

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
    # AUTO_SEED_BUSINESS: 业务种子（中国企业出海线索 93 条 + 3 个采集任务，
    # 导出自 dev 库 2026-08-31）——仅当 leads 表为空时导入一次；想部署完全
    # 干净的库（自行采集）设 false。测试环境由 conftest 强制关闭
    AUTO_SEED_BUSINESS: bool = True

    # ---------- 线索采集 ----------
    # Meta 广告资料库（Ad Library API）访问令牌，meta_ads 采集器必填。
    # 获取：https://www.facebook.com/ads/archive/api 创建应用申请 token（免费），
    # 需要的权限很窄（ads_archive 只读公开广告数据）。
    META_ADS_ACCESS_TOKEN: str = ""
    # web_search 采集器（§6.2 P1 搜索数据源）。默认引擎 duckduckgo 零 key 零费用
    # （DDG 不可达时自动降级 bing_cn）；bing_cn = 必应中国版直连（国内网络免代理）；
    # 可选：searxng（自托管开源元搜索，SEARXNG_URL 指向实例的 JSON API）、
    # google_cse / bing（付费加速通道，需凭据——纯免费部署保持不配即可）
    SEARCH_ENGINE: Literal["duckduckgo", "bing_cn", "searxng", "google_cse", "bing"] = "duckduckgo"
    SEARXNG_URL: str = ""      # 如 http://localhost:8888（SearxNG 实例，format=json 已开）
    GOOGLE_CSE_KEY: str = ""   # Google Custom Search JSON API key（免费 100 次/天，超出付费）
    GOOGLE_CSE_CX: str = ""    # Google CSE 的搜索引擎 ID
    BING_SEARCH_KEY: str = ""  # Bing Web Search（Azure）key
    COLLECT_MAX_CONCURRENT: int = 2   # 同时运行的采集任务数（满则排队）
    COLLECT_TASK_TIMEOUT: int = 3600  # 单任务超时（秒）
    # 采集流水线自动接力（2026-08-31 交互改造）：发现类采集器（web_search/
    # job_posting/meta_ads）任务完成 → 自动排入一个隐式 website_enrich 全库扫描
    # （官网发现 + 信号复核）。依赖关系由系统承担，用户不需要知道「先采集后富化」的顺序。
    AUTO_CHAIN_ENRICH: bool = True
    # 自动接力去重：已有全库富化（params 为空）排队中就不再堆——排队中的那次
    # 扫描必然覆盖新增线索；不设时间窗口（刚跑完的富化扫不到本次新增，会漏）
    ENRICH_CONCURRENCY: int = 5     # 富化并发站点数
    SCHEDULER_ENABLED: bool = False  # 定时调度总开关（WORKERS=1 的进程才会启动）
    # 六维评分权重覆盖（JSON，键 overseas/whatsapp/saas/scale/marketing/contact，按和归一化）
    SCORING_DIM_WEIGHTS: dict[str, int] = {}
    # LLM（OpenAI 兼容协议：智谱 GLM / DeepSeek / OpenAI 均可）。未配置时 AI 能力降级为规则模板
    LLM_BASE_URL: str = ""  # 如 https://open.bigmodel.cn/api/paas/v4
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "glm-4-flash"
    LLM_TIMEOUT: int = 30  # 单次调用超时（秒）
    # C 级线索富化兜底周期（小时）= 分级增量重爬的最低档（PRD §九：S 1 天 /
    # A 3 天 / B 7 天由代码写死，C 档走此配置）。需求口径 C = 30 天 → 720
    ENRICH_INTERVAL_HOURS: int = 720
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
