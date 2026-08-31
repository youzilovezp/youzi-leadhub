"""
数据库初始化：表结构 + 种子数据。

行为（AUTO_INIT_DB / AUTO_SEED_DATA 两开关独立）：

| AUTO_INIT_DB | AUTO_SEED_DATA | 首次启动 (无 alembic_version 表) | 后续启动 |
|---|---|---|---|
| true   | true   | create_all + admin + demo 种子 | alembic upgrade + 种子补全 |
| true   | false  | create_all | alembic upgrade |
| false  | true   | 跳过（表不存在，跳过种子） | 跳过 |
| false  | false  | 跳过 | 跳过 |

首次启动 create_all 后 stamp 到预置基线 `0001_baseline`（versions/ 内置）；后续 add_module 的
`make db-migrate MSG="add xxx"` 正常生成增量迁移。

任何步骤失败只记录警告，不阻塞启动。

Alembic 用**子进程**调用：避免 alembic 的 logging/signal 副作用污染
uvicorn lifespan 的 asynccontextmanager（直接 in-process 调用会卡 yield）。
"""
import asyncio
from pathlib import Path

from loguru import logger
from sqlalchemy import inspect, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import async_session, engine
from app.models.base_class import Base  # noqa: F401  触发模型注册
from app.models.role import Role
from app.models.user import User


# ---------- Async 表操作 ----------
async def create_tables() -> None:
    """使用 SQLAlchemy metadata 自动建表。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ 数据库表已通过 metadata.create_all 创建")


async def _tables_missing_but_versioned() -> bool:
    """alembic_version 存在但 users 表不存在（空库被 upgrade 过 / 表被误删）。"""

    def _check(sync_conn) -> bool:
        inspector = inspect(sync_conn)
        return inspector.has_table("alembic_version") and not inspector.has_table("users")

    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_check)
    except Exception:
        return False


async def has_alembic_version_table() -> bool:
    """检查是否存在 alembic_version 表（用于判断是否首次启动）。"""

    def _check(sync_conn) -> bool:
        inspector = inspect(sync_conn)
        return inspector.has_table("alembic_version")

    async with engine.connect() as conn:
        return await conn.run_sync(_check)


# ---------- Alembic：完全同步路径 ----------
def _find_alembic_ini() -> Path:
    """定位 alembic.ini（admin 模式 backend/，server 模式 根）。"""
    candidates = [Path.cwd() / "alembic.ini", Path.cwd() / "backend" / "alembic.ini"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _run_alembic_subprocess(*args: str) -> tuple[int, str]:
    """用子进程调用 alembic——彻底隔离 alembic 与 uvicorn 事件循环。

    为什么不用 asyncio.to_thread：alembic 的 command.X 在内部会修改 logging 配置、
    注册 signal handler、甚至 sys.exit()，副作用会污染 uvicorn lifespan 的
    asynccontextmanager，导致 yield 之后 uvicorn 永远不打印
    "Application startup complete"（持续返回 502）。

    子进程方案：完全隔离，最干净。代价：多一次进程启动，但 init_db 只在
    启动时跑一次，性能可接受。
    """
    import subprocess
    import sys

    ini_path = _find_alembic_ini()
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(ini_path),
        *args,
    ]
    # 通过环境变量传入 URL，避免 alembic.ini 与 config.py 不同步
    env_overrides = {
        "DATABASE_URL": settings.ALEMBIC_DATABASE_URL,
    }
    import os

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **env_overrides},
        check=False,
    )
    return proc.returncode, (proc.stderr or "") + (proc.stdout or "")


def _diagnose_alembic_error(output: str) -> str:
    """把 alembic 错误翻译成中文，给非技术用户看。

    识别常见场景：
      1. 数据库连接失败（密码错/服务未起）
      2. 数据库不存在
      3. 版本冲突（手动改过库）
      4. 其他
    """
    text = output.lower()
    if "already exists" in text:
        return (
            "❌ 迁移报「对象已存在」——alembic 版本与实际表结构错位。\n"
            "   解决：备份后重置迁移状态——\n"
            "     PG：make stop && docker compose down --volumes && make start\n"
            "     SQLite：删 backend/data/app.db 后重启后端\n"
            "   （⚠️ 清库操作；backups/ 有备份先 make restore 恢复）"
        )
    if "near \"alter\"" in text or "alter" in text and "syntax error" in text:
        return (
            "❌ SQLite 不支持直接 ALTER 改列类型。\n"
            "   解决（开发库）：make backup 后删 backend/data/app.db，改完 model 重启重建；\n"
            "   生产 PG 库不受此限制。"
        )
    if "password authentication failed" in text:
        return (
            "❌ 数据库密码认证失败。按顺序试：\n"
            "   【首选】改 .env 的 POSTGRES_PORT 为空闲端口（如 15432）→ make start\n"
            "          （Docker 起本项目独立 PG，全自动建用户/库，最省事）\n"
            "   或：在本机 PG 里创建 .env 对应的用户/库（需会用 psql）：\n"
            "        create user \"<POSTGRES_USER>\" with password '<POSTGRES_PASSWORD>';\n"
            "        create database \"<POSTGRES_DB>\" owner \"<POSTGRES_USER>\";\n"
            "   或：项目重新生成后旧 Docker 卷密码不匹配 →\n"
            "        make stop && docker compose down --volumes && make start\n"
            "        （⚠️ 清空该卷数据；backups/ 有备份可恢复）"
        )
    if (
        "could not connect" in text
        or "connection refused" in text
        or "operationalerror" in text
    ):
        return (
            "❌ 数据库连接失败。请检查：\n"
            "   1. 中间件是否启动？`make start`（会优先复用本机已运行的 PG/Redis）\n"
            "   2. .env 中 POSTGRES_PASSWORD / POSTGRES_USER / POSTGRES_HOST 是否与实际数据库一致\n"
            "   3. POSTGRES_PORT 是否被占用"
        )
    if "database" in text and "does not exist" in text:
        return (
            "❌ 数据库不存在。请在 PostgreSQL 里创建 .env 中 POSTGRES_DB 指定的库，\n"
            "   或 `make start` 用 Docker 起一套（会自动建库）"
        )
    if "version_num" in text or "conflicts with" in text:
        return (
            "❌ alembic 版本冲突——可能是手动改过数据库。\n"
            "   解决：备份数据后删除数据库重建（SQLite：删 backend/data/app.db；PG：删库后 make start）"
        )
    return f"❌ Alembic 失败：{output.strip()[:300]}"


async def _alembic_cmd(*args: str, success_msg: str) -> None:
    """子进程方式跑 alembic，失败时给非技术用户友好的中文诊断。"""
    code, output = await asyncio.to_thread(_run_alembic_subprocess, *args)
    if code == 0:
        logger.info(success_msg)
    else:
        logger.warning(_diagnose_alembic_error(output))


# ---------- 种子数据 ----------
DEMO_USERS = [
    # (username, nickname, email, role_code, password_plain)
    ("manager", "张经理", "manager@example.com", "sales_manager", "Demo@123"),
    ("alice", "李爱丽丝", "alice@example.com", "sales", "Demo@123"),
    ("bob", "王伯伯", "bob@example.com", "sales", "Demo@123"),
]


async def seed_business_data() -> None:
    """业务种子（2026-08-31，导出自 dev 库）：其他电脑首次启动导入。

    只初始化一次：leads 表非空直接跳过——已有任何线索的库（跑过采集/
    导入过种子）绝不触碰；导入走 upsert_lead（与采集器同路径），dedupe_key/
    评分/ICP 状态在新机器上自动重算。cron 采集任务按 collector 判存在。
    """
    from sqlalchemy import func as sa_func

    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead
    from app.db.seed_data import SEED_LEAD_COUNT, SEED_PAYLOAD
    from app.models.collect_task import CollectTask
    from app.models.lead import Lead

    async with async_session() as session:
        existing = (
            await session.execute(select(sa_func.count()).select_from(Lead))
        ).scalar_one()
        if existing > 0:
            logger.info(f"⏭️  业务种子跳过：leads 已有 {existing} 条（只初始化一次）")
            return

        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        created = merged = 0
        for row in SEED_PAYLOAD["leads"]:
            sources = row.get("sources") or ["seed_import"]
            payload = {k: v for k, v in row.items() if k != "sources"}
            draft = LeadDraft(source=sources[0], **payload)
            lead, is_new = await upsert_lead(session, draft)
            # 多来源行（历史上 jobui 与种子合并过）直接追加来源记录——
            # 不再走第二次 upsert（空 draft 会造出重复行）
            recs = list(lead.sources or [])
            for extra in sources[1:]:
                if not any(r.get("source") == extra for r in recs):
                    recs.append({"source": extra, "first_seen": now_iso, "last_seen": now_iso})
            if len(recs) != len(lead.sources or []):
                lead.sources = recs
            created += int(is_new)
            merged += int(not is_new)
        await session.commit()
        logger.info(f"🌱 业务种子导入：{created} 新建 / {merged} 合并（共 {SEED_LEAD_COUNT} 条）")

        # cron 采集任务：同 collector 已有任务则不重建
        for t in SEED_PAYLOAD.get("tasks", []):
            stmt = select(CollectTask).where(CollectTask.collector == t["collector"])
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                continue
            session.add(
                CollectTask(
                    name=t["name"],
                    collector=t["collector"],
                    params=t.get("params") or {},
                    cron_expr=t.get("cron_expr"),
                )
            )
        await session.commit()
        logger.info("🌱 采集任务种子完成（按 collector 判存在）")


async def create_initial_data() -> None:
    """插入种子数据：PRD 五角色（含权限码）+ 1 管理员 + 3 demo 用户。

    生产环境（APP_ENV=prod）**只创建 admin + 5 个角色**，不创建 demo 用户。
    这样可以避免 Demo@123 这种公开凭据进入生产 DB。

    - admin / 默认 admin（--admin-pass 可指定）
    - manager / alice / bob 仅 dev 环境创建（dev 默认密码 Demo@123）
    - 角色权限码与迁移 d2b1e98f091f 共用 ROLE_SEEDS 口径：新库走
      create_all+stamp（迁移种子不执行），必须在这里把 RBAC 种齐
    """
    from app.models.role import ROLE_SEEDS

    async with async_session() as session:
        # ---------- 角色 ----------
        # 不指定 id，让 sequence 自增——避免与已有数据 PK 冲突
        code_to_id: dict[str, int] = {}
        for code, name, perms in ROLE_SEEDS:
            stmt = select(Role).where(Role.code == code)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                existing = Role(name=name, code=code, permissions=perms, remark="系统内置")
                session.add(existing)
                await session.flush()  # flush 让新角色拿到 id
            elif not existing.permissions:
                # 旧库角色无权限码（ROLE_SEEDS 出现之前建的）→ 补齐
                existing.permissions = perms
            code_to_id[code] = existing.id
        await session.commit()

        # ---------- 管理员 ----------
        stmt = select(User).where(User.username == settings.INITIAL_ADMIN_USERNAME)
        result = await session.execute(stmt)
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = User(
                username=settings.INITIAL_ADMIN_USERNAME,
                email=settings.INITIAL_ADMIN_EMAIL,
                password_hash=hash_password(settings.INITIAL_ADMIN_PASSWORD),
                is_active=True,
                is_superuser=True,
                role_id=code_to_id["admin"],  # 用 code 查到的 id，避免硬编码
                nickname="柚子",
            )
            session.add(admin)
            await session.commit()
            logger.info(
                f"✅ 默认管理员已创建：{settings.INITIAL_ADMIN_USERNAME}"
            )
            if settings.APP_ENV != "prod":
                logger.warning(
                    f"🔑 默认管理员密码：{settings.INITIAL_ADMIN_PASSWORD}"
                    "（生产环境请立即修改！）"
                )

        # ---------- Demo 用户（生产或 AUTO_SEED_DATA=false 时跳过）----------
        # 注意：admin 创建在上面（不受 AUTO_SEED_DATA 影响）——否则按文档把
        # AUTO_SEED_DATA=false 部署的生产库会永远没有管理员，且 reset_admin
        # 对不存在用户直接报错，无法自救。
        auto_seed = getattr(settings, "AUTO_SEED_DATA", True)
        if settings.APP_ENV != "prod" and auto_seed:
            for username, nickname, email, role_code, plain_pwd in DEMO_USERS:
                stmt = select(User).where(User.username == username)
                result = await session.execute(stmt)
                if result.scalar_one_or_none() is not None:
                    continue
                # 用 code 查 role_id，而不是硬编码 2/3
                role_id = code_to_id.get(role_code, code_to_id["admin"])
                user = User(
                    username=username,
                    nickname=nickname,
                    email=email,
                    password_hash=hash_password(plain_pwd),
                    is_active=True,
                    is_superuser=False,
                    role_id=role_id,
                )
                session.add(user)
            await session.commit()
            logger.info(
                "✅ demo 用户已创建：manager / alice / bob（仅 dev 环境；生产跳过）"
            )
        else:
            logger.info("⏭️  跳过 demo 用户创建（生产环境或 AUTO_SEED_DATA=false）")


# ---------- 主入口 ----------
_diag_printed = False


def _diag_once(output: str) -> str:
    """认证失败在启动流程会触发 3 次（连接检查/create_all/种子）——只在第一次打印全量诊断。"""
    global _diag_printed
    if _diag_printed and "认证失败" in output:
        return "（同上：数据库认证失败，见上方诊断）"
    _diag_printed = True
    return output


async def init_db() -> None:
    """启动时调用：根据数据库状态自动决定如何维护。

    注意：不在这里 dispose()——会让连接池被无意义重建（pool_pre_ping/rec
    ycle 配置失效）。lifespan 结束后引擎自然释放。
    """
    auto_init = getattr(settings, "AUTO_INIT_DB", True)
    auto_seed = getattr(settings, "AUTO_SEED_DATA", True)

    if not auto_init and not auto_seed:
        logger.info("⏭️  数据库自动维护已全部关闭，跳过")
        return

    # 防御性检查：AUTO_SEED_DATA=true 但 AUTO_INIT_DB=false 时，roles/users 表可能不存在。
    if auto_seed and not auto_init:
        logger.warning(
            "⚠️ AUTO_SEED_DATA=true 但 AUTO_INIT_DB=false，表结构可能不存在，"
            "跳过种子写入。建议同时开启 AUTO_INIT_DB=true。"
        )
        return

    # 注意：AUTO_SEED_DATA=false 只跳过 demo 用户；admin + 角色必须始终创建——
    # 否则生产库（文档要求 AUTO_SEED_DATA=false）会永远没有管理员。

    if auto_init:
        try:
            is_first_run = not await has_alembic_version_table()
        except Exception as exc:
            logger.warning(_diag_once(_diagnose_alembic_error(f"{type(exc).__name__}: {exc}")))
            logger.warning("⚠️ 因上述原因检查 alembic 状态失败，降级为首次启动流程")
            is_first_run = True

        if is_first_run:
            # 首次启动：create_all + 自动 stamp head。
            # stamp 后基线版本号写入 alembic_version，之后 add_module 的
            # `make db-migrate MSG="add xxx"` 能正常生成增量迁移（不会空）。
            # 注意：基线本身的 DDL 不进迁移链——这是刻意取舍（create_all 直建），
            # 换库重建时同样走 create_all，无需回放基线迁移。
            logger.info("📦 首次启动：create_all + stamp head")
            try:
                await create_tables()
                # stamp 到当前 head（versions/ 预置了 0001_baseline，head 永不为空）。
                # 不能写死 "0001_baseline"：克隆已含增量迁移的项目（models 已注册）
                # 时，create_all 建出的是 head 态表结构，stamp 到 baseline 会让后续
                # upgrade 重放增量迁移 → "table already exists" 死循环。
                await _alembic_cmd("stamp", "head", success_msg="✅ 已标记迁移基线（stamp head）")
            except Exception as exc:
                # 走中文诊断（认证失败/连不上等原因在这里也能精确翻译）
                logger.error(_diag_once(_diagnose_alembic_error(f"{type(exc).__name__}: {exc}")))
        elif await _tables_missing_but_versioned():
            # 陷阱场景：alembic_version 存在但业务表没了（如空库直接 upgrade 过 /
            # 手动 drop 过表）。upgrade 分支只会空转，API 全 500 且无提示。
            logger.warning("⚠️ 检测到 alembic_version 存在但 users 表缺失——降级为 create_all 重建表结构（原数据已丢失，如有备份可 make restore 恢复）")
            try:
                await create_tables()
                # 重建后必须重新 stamp：否则版本行指向的迁移与实际表结构错位
                await _alembic_cmd("stamp", "head", success_msg="✅ 重建后已重新标记迁移版本")
            except Exception as exc:
                logger.error(_diag_once(_diagnose_alembic_error(f"{type(exc).__name__}: {exc}")))
        else:
            logger.info("🔄 检测到 alembic_version，执行 upgrade head")
            await _alembic_cmd("upgrade", "head", success_msg="✅ Alembic 迁移完成")

    # 角色与 admin 的创建不受 AUTO_SEED_DATA 影响（demo 用户才受影响）
    if auto_init:
        try:
            await create_initial_data()
        except Exception as exc:
            logger.warning(_diag_once(_diagnose_alembic_error(f"初始数据写入失败 {type(exc).__name__}: {exc}")))

        # 业务种子（2026-08-31）：中国企业出海线索 + 采集任务，仅空库导入一次
        try:
            await seed_business_data()
        except Exception as exc:
            logger.warning(f"⚠️ 业务种子导入失败（不影响启动）：{type(exc).__name__}: {exc}")
