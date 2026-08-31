# 商机意向分重设计（Intent Score Redesign）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把评分从六维加权切换为 PRD §五 MVP 加分制（意向分 v3）成为唯一主分，ICP 门增加买家判定（媒体/社区/软件页不再进池），job_posting 降级为信号巡检器，新增三问生成器（为什么需要你/应该卖什么/应该找谁）并挂进今日商机场槛，最后清洗存量污染并全库重评。

**Architecture:** 业务层重做、平台底座（任务系统/去重合并/线索池/跟进/RBAC）不动。评分唯一写入口仍是 `apply_score`（评分/ICP/export_type 同点派生）。三问生成器是新纯函数模块 `collectors/intent.py`，无 IO，API 层组装。

**Tech Stack:** FastAPI + SQLAlchemy async（PG dev / SQLite test）+ Vue3 + Naive UI + pytest / vitest。

**Spec:** `docs/superpowers/specs/2026-08-31-intent-score-redesign.md`（本计划从 spec 出发，执行者两个都读）

## Global Constraints

- **纯免费**：不引入任何付费 API、不采购数据、不新增后端依赖
- **零数据库迁移**：`icp_status` 新增值 `non_buyer` 是字符串值不是列变更；`score_signals` 语义切换是数据级。本次不写 alembic 迁移
- **历史迁移约束**：`backend/alembic/versions/f8a4c31e9d02_wa_business_score_breakdown.py:33` import 了 `bonus_breakdown`——该函数名**必须保留**（v3 实现的兼容包装），删除会让新机器跑迁移直接崩
- **测试共享库**：测试夹具的域名/用户名必须用本任务唯一前缀（如 `v3score` / `v3gate` / `patrol` / `intent3q`），不能与其他测试文件撞（SQLite/PG 共享库无隔离）
- **sessionmaker autoflush=False**：upsert 后必须显式 `flush`
- **后端测试**：`cd backend && uv run pytest tests/ -q`；**前端验证**：`cd frontend && pnpm type-check && pnpm test`
- **提交信息**：中文、`feat:`/`refactor:`/`test:`/`docs:` 前缀，与仓库现状一致

---

### Task 1: 评分 v3——加分制成为唯一主分

六维加权（`score_lead_inputs` 旧实现）与加分制（`bonus_breakdown`）并存的双口径终结：加分制就是主分。本任务只动 `scoring.py` + `config.py` + 测试；六维死代码与其消费方的清理在 Task 2。

**Files:**
- Modify: `backend/app/collectors/scoring.py`（全文件重写）
- Modify: `backend/app/core/config.py`（删 `SCORING_DIM_WEIGHTS` 配置项——**挪到 Task 2**，本任务先不动，避免 Task 1 期间 `effective_dim_weights` 引用悬空）
- Create: `backend/tests/test_scoring_v3.py`
- Delete: `backend/tests/test_scoring_v2.py`
- Modify: `backend/tests/test_bonus_import.py`（断言对齐 v3）

**Interfaces:**
- Produces: `score_lead_inputs(**行属性) -> tuple[int, list[dict], str]`（总分, 命中信号 items `[{key,label,points}]`, 分级）；`INTENT_SIGNALS: list[tuple[str,str,int]]`；`bonus_breakdown(**kwargs) -> dict`（兼容包装，返回 `{"total", "items"}`，历史迁移 f8a4c31e9d02 依赖）；`grade_of` 不变；`apply_score(lead, *, contacts_count=0, has_tier1=False, has_tier2=False)` 签名不变（contacts 参数保留但**不参与打分**——三问的事，不是分的事）
- Produces: `lead.score_signals` 从此写入 `{命中信号键: 分值}`（v3 口径；六维格式废弃）

- [ ] **Step 1: 写失败测试（锚点案例 = spec §3.3）**

创建 `backend/tests/test_scoring_v3.py`：

```python
"""意向分 V3（加分制主分）：PRD §五 MVP 口径的锚点校准用例。

锚点来自 spec §3.3——设计定稿时的判别力验证，改分值表必须同步改这里。
"""

from app.collectors.scoring import INTENT_SIGNALS, bonus_breakdown, grade_of, score_lead_inputs


def _items(total_items):
    return {it["key"]: it["points"] for it in total_items}


def test_grade_boundaries():
    assert grade_of(100) == "S"
    assert grade_of(80) == "S"
    assert grade_of(79) == "A"
    assert grade_of(60) == "A"
    assert grade_of(59) == "B"
    assert grade_of(40) == "B"
    assert grade_of(39) == "C"
    assert grade_of(0) == "C"


def test_anchor_ctwa_ecommerce_is_S():
    """PRD 范例（CTWA 跨境电商）：CTWA 40 + 官网WA 25 + 海外客服招聘 20 = 85 → S。"""
    score, items, grade = score_lead_inputs(
        fb_whatsapp=True,
        sources=[{"source": "meta_ads"}],
        whatsapp_hit=True,
        job_signals={"overseas_cs": {"label": "海外/英文客服", "points": 20}},
    )
    assert (score, grade) == (85, "S")
    assert _items(items) == {"ctwa_ad": 40, "site_whatsapp": 25, "overseas_cs_job": 20}


def test_anchor_site_private_dtc_is_A():
    """官网私域型 DTC：官网WA 25 + 出海 15 + 独立站 10 + 3国 10 + 社媒 5 = 65 → A。"""
    score, items, grade = score_lead_inputs(
        whatsapp_hit=True,
        website="https://dtc.example.com",
        overseas_signals={"currencies": ["USD"], "markets": ["US", "GB", "AE"]},
        target_countries=["US", "GB", "AE"],
        social={"facebook": "f", "instagram": "i"},
    )
    assert (score, grade) == (65, "A")


def test_anchor_wa_ops_midsize_is_B():
    """WA 运营在招中型：wa_ops 30 + crm 10 + 出海 15 = 55 → B。"""
    score, _, grade = score_lead_inputs(
        job_signals={
            "wa_ops": {"label": "WhatsApp 运营/客服", "points": 30},
            "crm_ops": {"label": "CRM/Customer Success 运营", "points": 12},
        },
        whatsapp_job=True,
        overseas_signals={"export_words": ["export"]},
    )
    assert (score, grade) == (55, "B")


def test_anchor_overseas_factory_no_wa_is_C():
    """有出海无 WA 的工厂：出海 15 + 独立站 10 = 25 → C（培育池）。"""
    score, _, grade = score_lead_inputs(
        website="https://factory.example.com",
        overseas_signals={"shipping": ["worldwide"]},
    )
    assert (score, grade) == (25, "C")


def test_anchor_bsp_migration_is_A():
    """竞品迁移型：wa_bsp 30 + 官网WA 25 + 出海 15 = 70 → A。"""
    score, items, grade = score_lead_inputs(
        saas_signals={"wa_bsp": 1},
        whatsapp_hit=True,
        overseas_signals={"languages": ["EN"]},
    )
    assert (score, grade) == (70, "A")
    assert _items(items)["wa_bsp_competitor"] == 30


def test_ctwa_and_meta_ads_mutually_exclusive():
    """CTWA（+40）成立时替代「在投广告」（+15），合计 40 而非 55。"""
    _, items, _ = score_lead_inputs(
        fb_whatsapp=True, sources=[{"source": "meta_ads"}]
    )
    assert _items(items) == {"ctwa_ad": 40}

    # FB 主页挂 wa.me 但没有任何在投证据 → 两者都不成立（CTWA 是组合信号）
    _, items, _ = score_lead_inputs(fb_whatsapp=True)
    assert "ctwa_ad" not in _items(items) and "meta_ads_running" not in _items(items)

    # 在投广告但主页无 wa.me → 只有 +15
    _, items, _ = score_lead_inputs(sources=[{"source": "meta_ads"}])
    assert _items(items) == {"meta_ads_running": 15}


def test_fb_whatsapp_without_ads_alone_scores_zero():
    """fb_whatsapp 单独存在（无在投证据）不给分——CTWA 是「私域+投放」组合信号。"""
    score, items, _ = score_lead_inputs(fb_whatsapp=True)
    assert score == 0 and items == []


def test_whatsapp_job_and_wa_ops_same_fact_counted_once():
    """whatsapp_job 列与 job_signals.wa_ops 是同一事实，只计一次 +30。"""
    score, items, _ = score_lead_inputs(
        whatsapp_job=True, job_signals={"wa_ops": {"label": "x", "points": 30}}
    )
    assert score == 30
    assert [it["key"] for it in items] == ["wa_ops_job"]


def test_saas_and_scale_signals_do_not_score():
    """SaaS 类目/规模类信号不进主分（它们回答「卖什么」，不是意向本身）；
    唯一例外 wa_bsp（竞品栈=迁移意向）。"""
    score, _, _ = score_lead_inputs(
        saas_signals={"crm": 1, "helpdesk": 1, "chatbot": 1},
        job_urls=["j1", "j2", "j3", "j4"],
        email="x@a.com",
        contacts_count=3,
        has_tier1=True,
    )
    assert score == 0


def test_total_caps_at_100():
    score, _, _ = score_lead_inputs(
        fb_whatsapp=True,
        sources=[{"source": "meta_ads"}],
        whatsapp_hit=True,
        whatsapp_job=True,
        saas_signals={"wa_bsp": 1},
        job_signals={
            "wa_ops": {"label": "x", "points": 30},
            "overseas_cs": {"label": "y", "points": 20},
            "crm_ops": {"label": "z", "points": 12},
        },
        wa_business=True,
        whatsapp_numbers=["1", "2"],
        website="https://a.com",
        overseas_signals={"markets": ["US", "GB", "AE"]},
        target_countries=["US", "GB", "AE"],
        social={"fb": "1", "ig": "2"},
    )
    assert score == 100


def test_intent_signal_table_values():
    """分值表 = spec §3.2 定稿值。改表必须过 spec，不许悄悄改。"""
    table = {k: p for k, _l, p in INTENT_SIGNALS}
    assert table == {
        "ctwa_ad": 40,
        "wa_ops_job": 30,
        "wa_bsp_competitor": 30,
        "site_whatsapp": 25,
        "overseas_cs_job": 20,
        "wa_business": 15,
        "meta_ads_running": 15,
        "overseas_biz": 15,
        "overseas_site": 10,
        "crm_job": 10,
        "three_markets": 10,
        "multi_numbers": 10,
        "social_active": 5,
    }


def test_bonus_breakdown_compat_wrapper():
    """历史迁移 f8a4c31e9d02 依赖的兼容接口：返回 v3 明细的同构 dict。"""
    bd = bonus_breakdown(
        fb_whatsapp=True, sources=[{"source": "meta_ads"}], whatsapp_hit=True
    )
    assert bd["total"] == 65
    assert {it["key"] for it in bd["items"]} == {"ctwa_ad", "site_whatsapp"}
    assert bonus_breakdown() == {"total": 0, "items": []}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_scoring_v3.py -q`
Expected: FAIL（`ImportError: cannot import name 'INTENT_SIGNALS'`）

- [ ] **Step 3: 重写 scoring.py**

`backend/app/collectors/scoring.py` 全文件替换为：

```python
"""商机意向分 V3：加分制（PRD §五 MVP 口径，2026-08-31 重设计定稿）。

Lead Score = 命中信号分值直接相加，封顶 100
分级：80-100=S（立即跟进） 60-79=A（高潜力） 40-59=B（培育池） 0-39=C

设计说明（spec §3）：
- 分数含义是「这家公司现在有多需要你」（WhatsApp 商机意向），不是企业质量分
- 每 1 分可溯源到一条证据（score_breakdown items，销售直读"这分怎么来的"）
- 六维加权（75% 权重押在无数据通道、旗舰锚点仅 63 分、全库最高 36 分全员 C）
  已废弃——那是 PRD 明确留给「成交数据回传后」的成熟阶段形态
- SaaS 类目信号（crm/helpdesk/chatbot…）与规模信号（岗位数/社媒广度/联系人）
  不进主分：它们回答「应该卖什么」（三问之二），唯一例外 wa_bsp（竞品栈=迁移意向）
- score_signals JSON 存 {命中信号键: 分值}（v3 口径；旧六维格式废弃）
"""

from __future__ import annotations

from typing import Any

# ---------- 信号分值表（spec §3.2 定稿；改值必须过 spec 评审） ----------

INTENT_SIGNALS: list[tuple[str, str, int]] = [
    ("ctwa_ad", "CTWA 私域获客（FB 主页挂 WhatsApp + 在投广告）", 40),
    ("wa_ops_job", "在招 WhatsApp 运营岗", 30),
    ("wa_bsp_competitor", "已用其他 WhatsApp SaaS（替换商机）", 30),
    ("site_whatsapp", "官网 WhatsApp 入口", 25),
    ("overseas_cs_job", "在招海外/英文客服岗", 20),
    ("wa_business", "WhatsApp Business 业务号", 15),
    ("meta_ads_running", "在投 Meta 海外广告", 15),
    ("overseas_biz", "出海业务证据", 15),
    ("overseas_site", "海外独立站", 10),
    ("crm_job", "在招 CRM/客服系统运营岗", 10),
    ("three_markets", "覆盖 ≥3 国市场", 10),
    ("multi_numbers", "多 WhatsApp 分线（≥2 号码）", 10),
    ("social_active", "海外社媒活跃（≥2 平台）", 5),
]

INTENT_LABELS_ZH: dict[str, str] = {k: label for k, label, _ in INTENT_SIGNALS}


def grade_of(score: int) -> str:
    """总分 → S/A/B/C 分级（阈值 PRD 口径不变）。"""
    if score >= 80:
        return "S"
    if score >= 60:
        return "A"
    if score >= 40:
        return "B"
    return "C"


def score_lead_inputs(
    *,
    fb_whatsapp: bool = False,
    website: str | None = None,
    whatsapp_hit: bool = False,
    whatsapp_job: bool = False,
    whatsapp_numbers: list[str] | None = None,
    wa_business: bool = False,
    saas_signals: dict[str, Any] | None = None,
    social: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    target_countries: list[str] | None = None,
    overseas_signals: dict[str, list[str]] | None = None,
    job_signals: dict[str, dict[str, Any]] | None = None,
    ad_count: int = 0,
    # 以下参数保留签名兼容（apply_score 调用方在传），v3 不参与打分
    is_cn: bool = False,  # noqa: ARG001
    country: str | None = None,  # noqa: ARG001
    whatsapp_url: str | None = None,  # noqa: ARG001
    scenes: list[str] | None = None,  # noqa: ARG001
    job_urls: list[str] | None = None,  # noqa: ARG001
    email: str | None = None,  # noqa: ARG001
    phone_raw: str | None = None,  # noqa: ARG001
    phone_e164: str | None = None,  # noqa: ARG001
    contacts_count: int = 0,  # noqa: ARG001
    has_tier1: bool = False,  # noqa: ARG001
    has_tier2: bool = False,  # noqa: ARG001
) -> tuple[int, list[dict], str]:
    """意向分 v3 纯函数：返回 (总分, 命中信号 items, 分级)。无 IO。

    互斥规则：CTWA（+40）= fb_whatsapp ∧ 在投证据（meta_ads 来源或 ad_count>0），
    成立时替代「在投广告」（+15）——CTWA 已隐含在投事实。
    overseas_biz = overseas_signals 非空；overseas_site = website ∧ 出海证据（可叠加）。
    """
    job_keys = set(job_signals or {})
    saas_keys = set(saas_signals or {})
    source_names = {r.get("source") for r in (sources or []) if r.get("source")}
    ad_running = "meta_ads" in source_names or ad_count > 0
    ctwa = fb_whatsapp and ad_running
    ov = overseas_signals or {}
    markets = {c.upper() for c in (target_countries or []) if c}
    markets |= {m.upper() for m in ov.get("markets", []) if m}

    matched: dict[str, bool] = {
        "ctwa_ad": ctwa,
        "wa_ops_job": "wa_ops" in job_keys or whatsapp_job,
        "wa_bsp_competitor": "wa_bsp" in saas_keys,
        "site_whatsapp": whatsapp_hit,
        "overseas_cs_job": "overseas_cs" in job_keys,
        "wa_business": wa_business,
        "meta_ads_running": ad_running and not ctwa,
        "overseas_biz": bool(ov),
        "overseas_site": bool(website) and bool(ov),
        "crm_job": "crm_ops" in job_keys,
        "three_markets": len(markets) >= 3,
        "multi_numbers": len(whatsapp_numbers or []) >= 2,
        "social_active": len(social or {}) >= 2,
    }
    items = [
        {"key": key, "label": label, "points": points}
        for key, label, points in INTENT_SIGNALS
        if matched.get(key)
    ]
    total = min(100, sum(it["points"] for it in items))
    return total, items, grade_of(total)


def bonus_breakdown(**kwargs: Any) -> dict[str, Any]:
    """兼容包装（历史迁移 f8a4c31e9d02 import 此名）：返回 v3 明细。

    语义与旧版一致——{"total": 总分, "items": [{key,label,points}]}，
    区别只是它现在就是主分口径（不再是六维之外的"参考层"）。
    """
    total, items, _grade = score_lead_inputs(**kwargs)
    return {"total": total, "items": items}


def apply_score(
    lead: Any,
    *,
    contacts_count: int = 0,  # noqa: ARG001 — 联系人不进意向分，进三问（spec §3.2）
    has_tier1: bool = False,  # noqa: ARG001
    has_tier2: bool = False,  # noqa: ARG001
) -> tuple[int, int, str]:
    """对 ORM Lead 行评分并写回 score/score_signals/score_breakdown/grade。

    返回 (旧分, 新分, 新分级)，供事件 diff 使用。
    评分 / ICP 门 / export_type 同点派生的架构不变。
    """
    old_score = lead.score
    total, items, grade = score_lead_inputs(
        fb_whatsapp=lead.fb_whatsapp,
        website=lead.website,
        whatsapp_hit=lead.whatsapp_hit,
        whatsapp_job=lead.whatsapp_job,
        whatsapp_numbers=lead.whatsapp_numbers,
        wa_business=getattr(lead, "wa_business", False),
        saas_signals=lead.saas_signals,
        social=lead.social,
        sources=lead.sources,
        target_countries=getattr(lead, "target_countries", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        job_signals=getattr(lead, "job_signals", None),
        ad_count=getattr(lead, "ad_count", 0) or 0,
    )
    lead.score = total
    # v3 口径：命中信号键 → 分值（旧六维格式废弃；历史值随重评被覆盖）
    lead.score_signals = {it["key"]: it["points"] for it in items}
    lead.score_breakdown = {"total": total, "items": items}
    lead.grade = grade
    # ICP 门：资格与评分同点重算（upsert/富化/联系人变更都走到这里）
    from app.collectors.icp import compute_icp_status_of

    lead.icp_status = compute_icp_status_of(lead)
    # 出海业务类型：同为行属性派生，同点重算
    from app.collectors.overseas import derive_export_type

    lead.export_type = derive_export_type(
        industry=getattr(lead, "industry", None),
        overseas_signals=getattr(lead, "overseas_signals", None),
        target_countries=getattr(lead, "target_countries", None),
        job_signals=getattr(lead, "job_signals", None),
        sources=lead.sources,
    )
    return old_score, total, grade
```

注意：旧文件里的 `DIM_WEIGHTS` / `DIM_LABELS_ZH` / `SAAS_SIGNAL_POINTS` / `effective_dim_weights` / `BONUS_SIGNALS` / 旧 `score_lead_inputs` 六维实现全部删除。但 `DIM_LABELS_ZH` 被 `crud/lead_events.py:16` 和 `services/llm.py:22` import、`effective_dim_weights` 被 `endpoints/collect.py:25` import——**本任务暂保留这两个符号**（加 `# TODO(Task2): 六维消费方清理后删除` 注释），否则 import 崩、全站起不来。保留的桩：

```python
# ---------- 六维遗留（Task 2 清理消费方后删除，勿在新代码引用） ----------

DIM_WEIGHTS: dict[str, int] = {
    "overseas": 25, "whatsapp": 30, "saas": 20,
    "scale": 10, "marketing": 10, "contact": 5,
}
DIM_LABELS_ZH: dict[str, str] = {
    "overseas": "出海指数", "whatsapp": "WhatsApp 指数", "saas": "SaaS 需求",
    "scale": "企业规模", "marketing": "营销活跃", "contact": "联系人质量",
}


def effective_dim_weights() -> dict[str, int]:
    return dict(DIM_WEIGHTS)
```

- [ ] **Step 4: 跑新测试确认通过**

Run: `cd backend && uv run pytest tests/test_scoring_v3.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 删除旧锚点测试，修全量受影响断言**

1. `git rm backend/tests/test_scoring_v2.py`
2. `backend/tests/test_bonus_import.py`：`bonus_breakdown` 现在走 v3——`site_whatsapp` 30→25、`meta_ads` 键改 `meta_ads_running`、`ctwa_ad` 需要 fb_whatsapp∧meta_ads 同时成立、`overseas_biz` 不再认 `is_cn`、`social_active` 门槛 3→2。按新口径重写断言（保持用例骨架：PRD 信号命中 / 空输入 / 封顶）
3. 跑全量：`cd backend && uv run pytest tests/ -q`。已知会受影响的文件及其修法：
   - `test_daily_batch.py`：`_STRONG_DRAFT` 在 v3 下 = ctwa40+site25+multi10+wa_biz15+3国10+出海15+独立站10+社媒5 = 130→100/S。断言 `score >= 60`、`grade in ("S","A")` 仍过，**预期无需改**；若挂在别处按实际值修
   - `test_closedloop_fixes.py`：e2e 断言「64 分 A 级」——v3 下同一份 meta_ads 强证据是 100/S，把断言改为新值并在注释里说明锚点换轨
   - `test_collect_core.py` / `test_signal_system.py` / `test_meta_ads.py`：可能有 score/grade 具体值断言，按 v3 重算值修（规则：ctwa=fb_whatsapp∧meta_ads 来源、只认 overseas_signals 非空为出海证据）
   - 其他文件 `test_icp_gate` / `test_quality_review` / `test_cn_evidence` / `test_negative_evidence` / `test_website_discovery` / `test_enrich_*.py` 不依赖具体分值，跑了确认即可

- [ ] **Step 6: 全量绿后提交**

```bash
cd backend && uv run pytest tests/ -q
git add -A backend/app/collectors/scoring.py backend/tests/
git commit -m "feat: 意向分v3——加分制成为唯一主分（PRD §五 MVP 口径），六维加权废弃"
```

---

### Task 2: 六维消费方清理

Task 1 留下的六维遗留符号（`DIM_WEIGHTS`/`DIM_LABELS_ZH`/`effective_dim_weights`/`describe_dimensions`）与全部消费方退场：推荐引擎不再吃 `dim_saas`、导出列换意向明细、详情响应删六维字段、LLM 模板改读意向信号。

**Files:**
- Modify: `backend/app/collectors/scoring.py`（删六维遗留块）
- Modify: `backend/app/crud/lead_events.py`（删 `describe_dimensions`，`DIM_LABELS_ZH` import）
- Modify: `backend/app/collectors/recommend.py`（`dim_saas` 参数删除，内部算 SaaS 强度）
- Modify: `backend/app/api/v1/endpoints/collect.py`（3 处 `recommend_products` 调用、export `dim_*` 列、detail `dimensions`/`dimension_weights`）
- Modify: `backend/app/services/llm.py`（`DIM_LABELS_ZH` → 意向信号标签）
- Modify: `backend/app/schemas/collect.py`（`LeadDetailOut.dimensions/dimension_weights` 删除；`EXPORT_FIELDS` dim_* 六列 → `intent_detail`；`LeadOut.score_signals` 注释改 v3）
- Modify: `backend/app/core/config.py`（删 `SCORING_DIM_WEIGHTS`）
- Modify: `frontend/src/views/collect/lead/index.vue`（导出字段勾选列表同步 dim_* → intent_detail，前端硬编码同名列）
- Test: `backend/tests/test_recommend.py`、`backend/tests/test_collect_core.py`（导出列断言）

**Interfaces:**
- Consumes: Task 1 的 `INTENT_LABELS_ZH`、`score_breakdown` v3 结构 `{"total": int, "items": [{key,label,points}]}`
- Produces: `recommend_products(...)` 无 `dim_saas` 参数；导出新字段键 `intent_detail`（表头「意向分明细」，值如 `CTWA 私域获客（FB 主页挂 WhatsApp + 在投广告）+40；官网 WhatsApp 入口+25`）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_recommend.py` 增补（并删除该文件里所有传 `dim_saas=` 的用法）：

```python
def test_recommend_products_no_dim_saas_param():
    """v3 后 SaaS 强度内部计算：单类 crm 不触发 SaaS 方案，两类触发（原 dim_saas>=40 口径）。"""
    one = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"crm": 1},
    )
    assert all(r["key"] != "overseas_saas" for r in one)
    two = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"crm": 1, "helpdesk": 1},
    )
    assert any(r["key"] == "overseas_saas" for r in two)
    # wa_bsp 一类即 30 分强度（>=40 不满足）→ 不触发；wa_bsp+crm 满足
    bsp_only = recommend_products(
        whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
        scenes=[], saas_signals={"wa_bsp": 1},
    )
    assert all(r["key"] != "overseas_saas" for r in bsp_only)
```

导出测试（放 `test_collect_core.py` 已有导出用例旁；`fields` 参数的传法——单串逗号分隔还是重复 query 参数——**先看该文件既有导出用例怎么传就怎么传**）：

```python
async def test_export_intent_detail_field(client, admin_credentials):
    """v3 导出：dim_* 六列已删，intent_detail 输出命中信号明细文本。"""
    r = await client.post("/api/v1/auth/login", json=admin_credentials)
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    r = await client.get(
        "/api/v1/collect/leads/export",
        headers=h,
        params={"fields": "name,score,intent_detail", "limit": 10},
    )
    assert r.status_code == 200
    assert "意向分明细" in r.content.decode("utf-8-sig")
```

（若 `test_collect_core.py` 里已有 dim_overseas 等导出断言，同步删除/替换。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_recommend.py tests/test_collect_core.py -q`
Expected: FAIL（`dim_saas` 用法 / `intent_detail` 未识别 422）

- [ ] **Step 3: 实施**

1. `recommend.py`：
   - 顶部加 `SAAS_CATEGORY_POINTS: dict[str, int] = {"crm": 22, "helpdesk": 22, "chatbot": 18, "ai_service": 18, "marketing_automation": 12, "omnichannel": 8, "wa_bsp": 30}`（从 scoring 迁来，语义=SaaS 买入强度，只服务推荐阈值）
   - `detect_need_types` / `recommend_products` / `sales_suggestion` 签名里删 `dim_saas`
   - `recommend_products` 内部：`saas_strength = sum(SAAS_CATEGORY_POINTS.get(k, 0) for k in saas)`；规则四条件 `saas_strength >= 40`；规则五条件 `len(saas) >= 2 or saas_strength >= 40`
2. `endpoints/collect.py`：
   - 删 `from app.collectors.scoring import effective_dim_weights`（line 25）
   - `_fill_lead_list_fields`（~line 130）：`recommend_products(...)` 调用删 `dim_saas=...` 行
   - export 分支（~line 379）：删 `key.startswith("dim_")` 取六维的分支，新增：
     ```python
     elif key == "intent_detail":
         value = "；".join(
             f"{it.get('label', it.get('key'))}+{it.get('points')}"
             for it in (lead.score_breakdown or {}).get("items", [])
         )
     ```
   - detail（~line 685-710）：删 `dims = describe_dimensions(...)`、`out.dimensions = dims`、`out.dimension_weights = effective_dim_weights()`；`recommend_products(...)` 删 `dim_saas` 实参
3. `crud/lead_events.py`：删 `describe_dimensions` 函数与 `DIM_LABELS_ZH` import
4. `services/llm.py`：`DIM_LABELS_ZH` import 换 `INTENT_LABELS_ZH`；line 74 附近的维度拼接改为意向信号拼接：
   ```python
   "，".join(
       f"{INTENT_LABELS_ZH.get(k, k)} {v}"
       for k, v in (lead.score_signals or {}).items()
   ) or "暂未检测到意向信号"
   ```
   （保持该函数上下文语义——读代码确认拼接处变量名后照此改）
5. `schemas/collect.py`：`LeadDetailOut` 删 `dimensions`/`dimension_weights` 两行；`LeadOut.score_signals` 注释改 `# v3 意向分 {命中信号键: 分值}`；`EXPORT_FIELDS` 里六行 `("dim_*", ...)` 删除，在 `("score", "Lead Score")` 后插 `("intent_detail", "意向分明细"),`
6. `config.py`：删 `SCORING_DIM_WEIGHTS` 配置项（grep 确认 `.env`/`.env.example` 没有残留引用，有就一并删）
7. `scoring.py`：删六维遗留块（`DIM_WEIGHTS`/`DIM_LABELS_ZH`/`effective_dim_weights` + TODO 注释）
8. `frontend/src/views/collect/lead/index.vue`：导出字段勾选列表里六维项（出海指数/WhatsApp指数/SaaS需求/企业规模/营销活跃/联系人质量）删除，加 `{ key: 'intent_detail', label: '意向分明细' }`（grep `dim_` 或 `六维` 定位硬编码列表）

- [ ] **Step 4: 全量回归**

Run: `cd backend && uv run pytest tests/ -q && cd ../frontend && pnpm type-check && pnpm test`
Expected: 后端全绿；前端 type-check 过（detail.vue 还在读 `dimensions`/`dimension_weights`，是可选链访问 `?? {}` 不会类型崩——若 type-check 报错，最小修补：`frontend/src/api/collect.ts` 的 `LeadDetail` 类型把这两个字段删掉、detail.vue 里引用处随删，完整 UI 改造在 Task 9）

- [ ] **Step 5: 提交**

```bash
git add -A backend/app backend/tests frontend/src
git commit -m "refactor: 六维加权消费方清理——推荐/导出/详情/LLM 全部切到意向分v3口径"
```

---

### Task 3: ICP 买家门（第五态 non_buyer）

ICP 门从「中国企业门」升级为「目标买家门」：黑名单命中的媒体/社区/软件页/平台门户 → `non_buyer`，与 `foreign` 一样默认不进销售视野。

**Files:**
- Modify: `backend/app/collectors/icp.py`
- Modify: `backend/app/crud/lead.py`（`_lead_conditions` 默认排除）
- Modify: `backend/app/api/v1/endpoints/collect.py`（`icp` Query pattern、daily-batch alerts 切片）
- Test: `backend/tests/test_icp_gate.py`

**Interfaces:**
- Produces: `is_non_buyer(*, name: str | None = None, domain: str | None = None) -> bool`；`ICP_STATUS_VALUES` 五元组 `("qualified", "cn_domestic", "foreign", "non_buyer", "unknown")`；`compute_icp_status`/`compute_icp_status_of` 新增 `name`/`domain` 入参（`compute_icp_status_of` 从 lead 行自动取）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_icp_gate.py` 追加（沿用该文件既有夹具风格）：

```python
from app.collectors.icp import compute_icp_status, is_non_buyer


def test_non_buyer_blacklist_domains():
    """实测漏网的行业媒体/社区/平台门户域（2026-08-31 dev 库查实清单）。"""
    for domain in (
        "ikjzd.com",        # 跨境知道（资讯）
        "wearesellers.com", # 知无不言（社区）
        "cifnews.com",      # 雨果跨境（媒体/平台）
        "kuajingyan.com",   # 跨境眼
        "kjtong.com",       # 跨境通
        "mckinsey.com.cn",  # 咨询报告页
        "www.ikjzd.com",    # 子域同样命中
    ):
        assert is_non_buyer(domain=domain), domain


def test_non_buyer_name_patterns():
    """名称词表：媒体/社区/报告/下载形态不是买家。"""
    for name in (
        "跨境知道-看跨境电商平台资讯、查报告、找资源",
        "知无不言跨境电商社区",
        "中国跨境电商市场研究白皮书",
        "Download WhatsApp (free) for Windows",
    ):
        assert is_non_buyer(name=name), name
    # 正常目标企业不得误杀
    for name in ("安克创新科技股份有限公司", "深圳市某跨境电子商务有限公司", "SHEIN"):
        assert not is_non_buyer(name=name), name


def test_icp_status_non_buyer_precedes_qualified():
    """黑名单优先于 CN/出海证据：媒体站哪怕 CN+出海全占也不进销售池。"""
    status = compute_icp_status(
        name="知无不言跨境电商社区",
        domain="wearesellers.com",
        is_cn=True,
        country="CN",
        phone_e164="+8613800138000",
        overseas_signals={"languages": ["EN"]},
        enriched_at="2026-08-31T00:00:00+00:00",
    )
    assert status == "non_buyer"


def test_normal_buyer_unaffected():
    status = compute_icp_status(
        name="安克创新科技股份有限公司",
        domain="anker.com",
        is_cn=True,
        country="CN",
        overseas_signals={"languages": ["EN"]},
    )
    assert status == "qualified"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_icp_gate.py -q`
Expected: FAIL（`ImportError: cannot import name 'is_non_buyer'`）

- [ ] **Step 3: 实施**

1. `icp.py`：
   - `ICP_STATUS_VALUES = ("qualified", "cn_domestic", "foreign", "non_buyer", "unknown")`
   - `ICP_STATUS_LABELS_ZH` 加 `"non_buyer": "非目标买家"`
   - 模块 docstring 追加买家门一段
   - 新增（放在 `OVERSEAS_JOB_KEYS` 附近）：

```python
# ---------- 买家门（spec §四）：不是所有中国企业都是潜在买家 ----------

# 实测漏网域名（2026-08-31 dev 库查实：霸榜的就是这些"行业媒体/社区/平台门户"）
NON_BUYER_DOMAINS: tuple[str, ...] = (
    "ikjzd.com",         # 跨境知道（资讯站）
    "wearesellers.com",  # 知无不言（卖家社区）
    "cifnews.com",       # 雨果跨境（行业媒体/平台）
    "kuajingyan.com",    # 跨境眼
    "kjtong.com",        # 跨境通（门户导航）
    "mckinsey.com.cn",   # 咨询报告页
    "gizmodo.com",       # 海外科技媒体（软件下载页宿主）
    "whatsappbusiness.com",  # WhatsApp 官方产品页
)

# 名称词表（域边界锚定不适用于中文，用子串；宁可窄不可误杀正常企业）
import re

_NON_BUYER_NAME_RE = re.compile(
    r"资讯|社区|论坛|白皮书|市场研究|行业报告|研究报告|下载|百科|导航|工具箱|协会|学会",
    re.IGNORECASE,
)


def is_non_buyer(*, name: str | None = None, domain: str | None = None) -> bool:
    """非目标买家判定：行业媒体/社区/报告页/软件页/门户导航。

    域名（含子域）与名称词表任一命中即 True。判不了（两样都没有）不算
    non_buyer——白名单五行业是归类标签不是硬门（industry 常缺失，硬门杀召回）。
    """
    d = (domain or "").lower().strip()
    if d:
        for b in NON_BUYER_DOMAINS:
            if d == b or d.endswith("." + b):
                return True
    if name and _NON_BUYER_NAME_RE.search(name):
        return True
    return False
```

（`import re` 挪到文件顶部与现有 import 合并）

   - `compute_icp_status` 签名加 `name: str | None = None, domain: str | None = None`；函数体最前插入：

```python
    if is_non_buyer(name=name, domain=domain):
        return "non_buyer"
```

   - `compute_icp_status_of` 调用处加 `name=getattr(lead, "name", None), domain=getattr(lead, "domain", None)`
   - 模块 docstring 的四态说明更新为五态

2. `crud/lead.py` `_lead_conditions`：`icp is None` 分支改 `conds.append(Lead.icp_status.notin_(("foreign", "non_buyer")))`，docstring 同步
3. `endpoints/collect.py`：
   - `list_leads` 的 `icp` Query pattern 改 `^(qualified|cn_domestic|foreign|non_buyer|unknown|all)$`
   - daily-batch alerts 切片（~line 610）`Lead.icp_status != "foreign"` 改 `Lead.icp_status.notin_(("foreign", "non_buyer"))`
   - grep `icp_status != "foreign"` 与 `pattern="^(qualified` 全仓扫一遍，同类处一并改（含 `endpoints/quality.py`、`endpoints/sales.py` 若有）
4. `schemas/collect.py` grep `qualified|cn_domestic` 确认无硬编码四态校验残留

- [ ] **Step 3.5: 五行业归类标签（spec §四白名单映射）**

spec 要求「PRD §二五类目标行业映射到标签、可筛选」。零迁移约束下不新增列，做成**读取时派生**：`industry_labels.py` 加映射函数，`LeadOut` 加派生字段（列表注入，同 `recommended_products` 的既有模式）。

`backend/app/collectors/industry_labels.py` 末尾追加：

```python
# ---------- PRD §二 五类目标行业（spec §四白名单映射；归类标签不是 ICP 硬门） ----------

INDUSTRY_GROUPS: dict[str, tuple[str, ...]] = {
    # 组键 → （industry token / 公司名子串，大小写不敏感）
    # 注意：不用裸「跨境」——跨境物流/货代会被抢先误归电商；组间按插入顺序首匹配
    "cross_border_ecom": ("跨境电商", "电商", "e-commerce", "ecommerce", "retail", "shopping",
                          "独立站", "品牌出海"),
    "game_app": ("游戏", "game", "gaming", "移动应用", "出海app"),
    "manufacturing": ("制造", "工厂", "factory", "工业", "器械", "设备", "汽配", "新能源"),
    "overseas_service": ("货代", "物流", "freight", "logistics", "营销", "广告", "advertising",
                         "客服外包", "外包", "consulting", "咨询"),
    "overseas_saas": ("saas", "软件", "software", "科技", "technology", "互联网"),
}

INDUSTRY_GROUP_LABELS_ZH: dict[str, str] = {
    "cross_border_ecom": "跨境电商/品牌DTC",
    "game_app": "出海游戏/App",
    "manufacturing": "制造业出海",
    "overseas_service": "出海服务",
    "overseas_saas": "出海SaaS",
}


def industry_group_of(industry: str | None, name: str | None = None) -> str:
    """行业 token / 公司名 → 五类目标行业组键（命中多个取先匹配的；不命中返回 ""）。

    只是归类展示标签（销售按行业看名单），不是 ICP 硬门——industry 常缺失，
    硬门会杀召回；买家排除只走 is_non_buyer 黑名单。
    """
    text = f"{industry or ''} {name or ''}".lower()
    if not text.strip():
        return ""
    for group, tokens in INDUSTRY_GROUPS.items():
        if any(t.lower() in text for t in tokens):
            return group
    return ""
```

`endpoints/collect.py` `_fill_lead_list_fields` 循环里追加一行（import 放顶部）：

```python
        o.industry_group = industry_group_of(i.industry, i.name)
```

`schemas/collect.py` `LeadOut` 加字段：`industry_group: str = ""  # 五类目标行业组键（读取时派生，industry_labels.INDUSTRY_GROUP_LABELS_ZH 做展示名）`。detail 端点同样注入。

`test_icp_gate.py` 追加：

```python
def test_industry_group_mapping():
    from app.collectors.industry_labels import industry_group_of

    assert industry_group_of("电商", "深圳市安克创新科技股份有限公司") == "cross_border_ecom"
    assert industry_group_of(None, "某游戏网络科技有限公司") == "game_app"
    assert industry_group_of("广告公司", None) == "overseas_service"
    assert industry_group_of(None, "某餐饮管理有限公司") == ""
```

（前端 `INDUSTRY_GROUP_LABELS_ZH` 的展示名映射在 Task 9 接；筛选仍走原 `industry` 字段，不新增查询参数。）

- [ ] **Step 4: 跑测试 + 全量**

Run: `cd backend && uv run pytest tests/test_icp_gate.py tests/test_daily_batch.py -q && uv run pytest tests/ -q`
Expected: 全绿（`test_daily_batch` 的 `icp_counts` 类断言若写死四键需补 `non_buyer`）

- [ ] **Step 5: 提交**

```bash
git add -A backend/app backend/tests
git commit -m "feat: ICP买家门——non_buyer第五态+五行业归类标签，媒体/社区/软件页默认不进销售池"
```

---

### Task 4: 三问生成器 `collectors/intent.py`

新纯函数模块：从行属性 + 联系人派生「为什么需要你 / 应该卖什么 / 应该找谁」，三问齐备度是今日商机的入选门槛（Task 6 接线）。

**Files:**
- Create: `backend/app/collectors/intent.py`
- Create: `backend/tests/test_intent.py`

**Interfaces:**
- Consumes: `recommend.detect_need_types` / `recommend.recommend_products`（Task 2 后无 `dim_saas` 参数）、`scenes.SCENE_LABELS_ZH`、`scenes.SAAS_LABELS_ZH`、`job_signals.JOB_SIGNAL_LABELS_ZH`
- Produces: `build_three_questions(lead: Any, *, contacts: Sequence[Any] | None = None) -> dict`，返回结构（Task 6 的 API 直接 `model_validate`/序列化）：

```python
{
    "why":  [{"key": "ctwa_ad", "label": "CTWA 私域获客…", "points": 40, "evidence_url": "https://…"}],  # 最多3条，按分值降序
    "what": {"need_types": [{"type": "ads", "label": "广告投放需求", "selling": "…"}],
             "products":   [{"key": "wa_cs", "name": "WhatsApp 客服 SaaS", "reason": "…"}],
             "scenes":     ["客服", "营销"],
             "saas_signals": ["CRM", "工单/客服系统"]},
    "who":  {"contacts": [{"name": "张三", "title": "海外客服主管", "seniority": "tier2", "email": "…"}],  # 最多3，tier 靠前
             "whatsapp_numbers": ["86139…"],       # 建联直达（最多3）
             "whatsapp_url": "https://wa.me/…",
             "roles": [{"role": "海外客服负责人", "hint": "在招海外客服岗 → 看招聘页/官网联系页"}]},  # 无联系人时规则派生，兜底「海外业务负责人」
    "complete": True,   # why≥2 ∧ what.products≥1 ∧ who 有任一答案
}
```

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_intent.py`：

```python
"""三问生成器：为什么需要你 / 应该卖什么 / 应该找谁（PRD 核心价值的直接实现）。"""


class _C:  # 最小联系人桩（属性访问与 ORM LeadContact 同构）
    def __init__(self, name, title, seniority, email=None):
        self.name, self.job_title, self.seniority, self.email = name, title, seniority, email


class _L:  # 最小 Lead 桩
    def __init__(self, **kw):
        base = dict(
            name="测试公司", whatsapp_hit=False, whatsapp_url=None, whatsapp_job=False,
            whatsapp_numbers=[], saas_signals={}, scenes=[], sources=[],
            industry=None, score_breakdown={}, job_signals={}, fb_whatsapp=False,
            email=None, website=None,
        )
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def test_why_top3_by_points_with_evidence():
    from app.collectors.intent import build_three_questions

    lead = _L(
        whatsapp_hit=True,
        whatsapp_url="https://wa.me/8613800138000",
        whatsapp_job=True,
        job_signals={
            "wa_ops": {"label": "WhatsApp 运营/客服", "points": 30},
            "overseas_cs": {"label": "海外/英文客服", "points": 20},
            "crm_ops": {"label": "CRM/Customer Success 运营", "points": 12},
        },
        overseas_signals={"languages": ["EN"]},
        website="https://x.com",
        score_breakdown={
            "total": 80,
            "items": [
                {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
                {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
                {"key": "overseas_cs_job", "label": "在招海外/英文客服岗", "points": 20},
                {"key": "crm_job", "label": "在招 CRM/客服系统运营岗", "points": 10},
            ],
        },
    )
    tq = build_three_questions(lead)
    assert [w["key"] for w in tq["why"]] == ["wa_ops_job", "site_whatsapp", "overseas_cs_job"]
    assert tq["why"][1]["evidence_url"] == "https://wa.me/8613800138000"  # site_whatsapp → whatsapp_url


def test_what_aggregates_products_needs_scenes():
    from app.collectors.intent import build_three_questions

    lead = _L(
        whatsapp_hit=True, whatsapp_url="https://wa.me/8613", whatsapp_job=True,
        scenes=["customer_service", "marketing"],
        saas_signals={"crm": 1, "helpdesk": 1},
        sources=[{"source": "meta_ads"}],
        score_breakdown={"total": 40, "items": [{"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25}]},
    )
    tq = build_three_questions(lead)
    assert any(p["key"] == "wa_cs" for p in tq["what"]["products"])
    assert "ads" in [n["type"] for n in tq["what"]["need_types"]]
    assert tq["what"]["scenes"] == ["客服", "营销"]
    assert "CRM" in tq["what"]["saas_signals"]


def test_who_contacts_first_then_wa_numbers_then_roles():
    from app.collectors.intent import build_three_questions

    lead = _L(whatsapp_numbers=["8613900000001", "8613900000002"])
    tq = build_three_questions(lead, contacts=[_C("张三", "海外客服主管", "tier2", "z@a.com")])
    assert tq["who"]["contacts"][0]["name"] == "张三"
    assert tq["who"]["whatsapp_numbers"] == ["8613900000001", "8613900000002"]

    # 无联系人：按信号派生角色——在招海外客服 → 海外客服负责人
    lead2 = _L(job_signals={"overseas_cs": {"label": "海外/英文客服", "points": 20}})
    tq2 = build_three_questions(lead2)
    roles = [r["role"] for r in tq2["who"]["roles"]]
    assert "海外客服负责人" in roles


def test_who_role_never_empty():
    from app.collectors.intent import build_three_questions

    tq = build_three_questions(_L())  # 啥都没有
    assert tq["who"]["roles"], "兜底角色必须存在（海外业务负责人）"
    assert tq["who"]["roles"][-1]["role"] == "海外业务负责人"


def test_completeness_rule():
    from app.collectors.intent import build_three_questions

    # 信号≥2 + 产品≥1 + who 兜底 → complete
    strong = _L(
        whatsapp_hit=True, whatsapp_url="https://wa.me/8613", whatsapp_job=True,
        score_breakdown={"total": 55, "items": [
            {"key": "site_whatsapp", "label": "官网 WhatsApp 入口", "points": 25},
            {"key": "wa_ops_job", "label": "在招 WhatsApp 运营岗", "points": 30},
        ]},
    )
    assert build_three_questions(strong)["complete"] is True

    # 单信号无产品 → 不齐备
    weak = _L(
        overseas_signals={"shipping": ["worldwide"]},
        score_breakdown={"total": 15, "items": [{"key": "overseas_biz", "label": "出海业务证据", "points": 15}]},
    )
    assert build_three_questions(weak)["complete"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_intent.py -q`
Expected: FAIL（`ModuleNotFoundError: app.collectors.intent`）

- [ ] **Step 3: 实现 intent.py**

创建 `backend/app/collectors/intent.py`：

```python
"""三问生成器（PRD 核心价值）：为什么需要你 / 应该卖什么 / 应该找谁。

纯函数组装层（无 IO、无新增采集）：输入 Lead 行属性 + 联系人序列，
输出可直接序列化的三问结构。「为什么」来自意向分明细（score_breakdown），
「卖什么」来自需求类型/推荐产品/场景，「找谁」两档——先真实联系人/WA 号码，
没有则按信号规则派生目标角色（永不空：销售至少知道该找什么职位的人）。
"""

from __future__ import annotations

from typing import Any, Sequence

from app.collectors.job_signals import JOB_SIGNAL_LABELS_ZH
from app.collectors.recommend import detect_need_types, recommend_products
from app.collectors.scenes import SAAS_LABELS_ZH, SCENE_LABELS_ZH


def _evidence_url(lead: Any, key: str) -> str | None:
    job_urls = list(getattr(lead, "job_urls", None) or [])
    if key == "site_whatsapp":
        return getattr(lead, "whatsapp_url", None)
    if key in ("wa_ops_job", "overseas_cs_job", "crm_job"):
        return job_urls[0] if job_urls else None
    if key in ("overseas_biz", "overseas_site"):
        return getattr(lead, "website", None)
    if key == "ctwa_ad":
        return getattr(lead, "website", None) or getattr(lead, "whatsapp_url", None)
    return None


# who 角色派生：信号 → 该找谁（PRD §七 联系人三级优先级的运营化落地）
_ROLE_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("wa_ops",), "WhatsApp/私域运营负责人", "在招 WhatsApp 运营岗 → 招聘页/官网联系页"),
    (("overseas_cs",), "海外客服负责人", "在招海外/英文客服岗 → 招聘页/官网联系页"),
    (("crm", "helpdesk", "chatbot", "ai_service", "marketing_automation", "omnichannel", "brand_stack"),
     "CRM/客服系统负责人", "检测到 SaaS 工具栈 → 官网联系页"),
    (("fb_whatsapp",), "海外营销负责人", "FB 主页挂 WhatsApp 私域 → FB 主页/官网联系页"),
]
_FALLBACK_ROLE = ("海外业务负责人", "官网联系页 / 招聘页")


def _derive_roles(lead: Any) -> list[dict[str, str]]:
    job_keys = set(getattr(lead, "job_signals", None) or {})
    saas_keys = set(getattr(lead, "saas_signals", None) or {})
    roles: list[dict[str, str]] = []
    for keys, role, hint in _ROLE_RULES:
        if any(k in job_keys or k in saas_keys for k in keys):
            if all(r["role"] != role for r in roles):
                roles.append({"role": role, "hint": hint})
    roles.append({"role": _FALLBACK_ROLE[0], "hint": _FALLBACK_ROLE[1]})
    return roles


def _top_contacts(contacts: Sequence[Any] | None) -> list[dict[str, Any]]:
    order = {"tier1": 0, "tier2": 1, "tier3": 2}
    items = [
        {
            "name": getattr(c, "name", None) or "（待补全）",
            "title": getattr(c, "job_title", None),
            "seniority": getattr(c, "seniority", None),
            "email": getattr(c, "email", None),
        }
        for c in (contacts or [])
    ]
    items.sort(key=lambda x: order.get(x["seniority"] or "", 9))
    return items[:3]


def build_three_questions(lead: Any, *, contacts: Sequence[Any] | None = None) -> dict[str, Any]:
    """行属性 + 联系人 → 三问结构（详见模块 docstring / spec §六）。"""
    breakdown = getattr(lead, "score_breakdown", None) or {}
    items = sorted(
        list(breakdown.get("items", [])),
        key=lambda it: -int(it.get("points", 0)),
    )
    why = [
        {
            "key": it["key"],
            "label": it.get("label", it["key"]),
            "points": int(it.get("points", 0)),
            "evidence_url": _evidence_url(lead, it["key"]),
        }
        for it in items[:3]
    ]

    products = recommend_products(
        whatsapp_hit=bool(getattr(lead, "whatsapp_hit", False)),
        whatsapp_url=getattr(lead, "whatsapp_url", None),
        whatsapp_job=bool(getattr(lead, "whatsapp_job", False)),
        scenes=list(getattr(lead, "scenes", None) or []),
        saas_signals=dict(getattr(lead, "saas_signals", None) or {}),
        industry=getattr(lead, "industry", None),
        sources=list(getattr(lead, "sources", None) or []),
    )
    need_types = detect_need_types(
        whatsapp_hit=bool(getattr(lead, "whatsapp_hit", False)),
        whatsapp_url=getattr(lead, "whatsapp_url", None),
        whatsapp_numbers=list(getattr(lead, "whatsapp_numbers", None) or []),
        whatsapp_job=bool(getattr(lead, "whatsapp_job", False)),
        scenes=list(getattr(lead, "scenes", None) or []),
        saas_signals=dict(getattr(lead, "saas_signals", None) or {}),
        sources=list(getattr(lead, "sources", None) or []),
    )
    what = {
        "need_types": need_types,
        "products": products,
        "scenes": [SCENE_LABELS_ZH.get(s, s) for s in (getattr(lead, "scenes", None) or [])],
        "saas_signals": [
            f"{SAAS_LABELS_ZH.get(k, k)}（在用）" if k == "wa_bsp" else SAAS_LABELS_ZH.get(k, k)
            for k in (getattr(lead, "saas_signals", None) or {})
        ],
    }

    wa_numbers = list(getattr(lead, "whatsapp_numbers", None) or [])[:3]
    who = {
        "contacts": _top_contacts(contacts),
        "whatsapp_numbers": wa_numbers,
        "whatsapp_url": getattr(lead, "whatsapp_url", None),
        "roles": _derive_roles(lead),
    }

    # 齐备度（spec §六）：why≥2 证据 ∧ what≥1 产品 ∧ who 有任一答案
    who_ok = bool(who["contacts"] or wa_numbers or who["whatsapp_url"])
    complete = len(why) >= 2 and len(products) >= 1 and who_ok
    return {"why": why, "what": what, "who": who, "complete": complete}
```

注意 `who_ok` 只认真实建联入口（联系人/号码/WA 入口）；`roles` 是兜底展示，不算齐备——「该找谁的角色」永远给，但「能直接联系上」才算齐备。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_intent.py -q`
Expected: PASS（`test_completeness_rule` 的 weak 用例：单出海信号无产品 → complete False ✓；strong 用例：whatsapp_hit+whatsapp_job 命中 wa_cs 产品 + why 2 条 → True ✓）

- [ ] **Step 5: 提交**

```bash
git add backend/app/collectors/intent.py backend/tests/test_intent.py
git commit -m "feat: 三问生成器——为什么需要你/应该卖什么/应该找谁（纯函数，含角色派生与齐备度）"
```

---

### Task 5: SaaS 品牌栈检测（brand_stack）

补 PRD §六要求的通用 SaaS 技术栈识别：品牌 widget 嵌在 `<script>` 标签里会被 `page_text` 剥掉（现有 crm/helpdesk 关键词只查正文），照 `wa_bsp` 的 raw-HTML 指纹模式补一类。

**Files:**
- Modify: `backend/app/collectors/scenes.py`（`SAAS_SIGNALS` 追加一项 + raw 匹配条件扩展）
- Test: `backend/tests/test_scenes.py`

**Interfaces:**
- Produces: saas_signals 新键 `brand_stack`（中文标签「通用 SaaS 技术栈」）——Task 4 的 `_ROLE_RULES` 已引用该键

- [ ] **Step 1: 写失败测试**

`backend/tests/test_scenes.py` 追加：

```python
def test_detect_brand_stack_in_raw_html():
    """品牌 widget 嵌在 script 标签里（正文剥掉后无痕），必须在 raw HTML 命中。"""
    html = """<html><body>Our shop
    <script src="https://widget.intercom.io/widget/abc123"></script>
    <script src="https://static.zdassets.com/ekr/snippet.js"></script>
    </body></html>"""
    hits = detect_saas_signals([html])
    assert "brand_stack" in hits


def test_brand_stack_not_matched_by_plain_text_brand_words():
    """正文里没有品牌词、raw 里也没有 → 不命中（空页面不误报）。"""
    assert "brand_stack" not in detect_saas_signals(["<p>we sell shoes</p>"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_scenes.py -q`
Expected: FAIL（`brand_stack` 不在 hits）

- [ ] **Step 3: 实施**

`scenes.py` `SAAS_SIGNALS` 列表末尾（`wa_bsp` 之后）追加：

```python
    # 通用 SaaS 技术栈（PRD §六：Zendesk/HubSpot/Intercom 等）：
    # 品牌 widget 嵌在 script/link 标签（intercom.io/zdassets/hs-scripts），
    # page_text 剥 script 后无痕——与 wa_bsp 同走 raw HTML 指纹匹配。
    # 命中 = 该公司在为海外客服/营销买 SaaS 工具（"应该卖什么"的强素材）
    (
        "brand_stack",
        "通用 SaaS 技术栈",
        [
            "intercom",
            "gorgias",
            "zdassets",       # Zendesk widget CDN
            "zendesk",
            "hubspot",
            "hs-scripts",     # HubSpot 埋点
            "salesforce",
            "zoho",
            "freshdesk",
            "crisp.chat",
            "tawk.to",
            "livechat",
            "livechatinc",
            "drift",
        ],
    ),
```

`detect_saas_signals` 里的 raw 匹配条件 `elif key == "wa_bsp" and kw in raw:` 改 `elif key in ("wa_bsp", "brand_stack") and kw in raw:`。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd backend && uv run pytest tests/test_scenes.py tests/test_intent.py -q && uv run pytest tests/ -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/app/collectors/scenes.py backend/tests/test_scenes.py
git commit -m "feat: SaaS品牌栈检测——raw HTML 指纹捕获 script 内嵌 widget（interkom/zdassets/hs-scripts 等）"
```

---

### Task 6: API 接线三问 + 今日商机齐备门槛

三问进详情页与今日商机行；今日商机只推「三问齐备」的线索（why≥2 ∧ what≥1 ∧ who 有建联入口）。

**Files:**
- Modify: `backend/app/schemas/collect.py`（`LeadDetailOut.three_questions`；`ThreeQuestionsOut`）
- Modify: `backend/app/api/v1/endpoints/collect.py`（detail 组装；daily-batch 行注入 + 齐备过滤）
- Test: `backend/tests/test_daily_batch.py`（追加三问断言与门槛用例）

**Interfaces:**
- Consumes: Task 4 `build_three_questions(lead, *, contacts=None)`
- Produces: `LeadDetailOut.three_questions: dict`；daily-batch `promoted`/`new_leads` 行内 `three_questions` 键；不齐备的行**不再出现**在 promoted/new_leads

- [ ] **Step 1: 写失败测试**

`backend/tests/test_daily_batch.py` 的 `test_daily_batch_and_claim` 里，在第 3 步断言后追加：

```python
    # 三问齐备（v3 重设计）：行内带 three_questions 且 complete=True
    fresh_row = next(x for x in data["new_leads"] if x["id"] == fresh.id)
    tq = fresh_row["three_questions"]
    assert tq["complete"] is True
    assert len(tq["why"]) >= 2
    assert tq["what"]["products"]
    assert tq["who"]["contacts"] or tq["who"]["whatsapp_numbers"]

    # 齐备门槛：够分（≥60 qualified）但三问不齐备 → 不进 new_leads。
    # 构造：wa_ops 招聘 30 + 出海 15 + 独立站 10 + 3国 10 + 社媒 5 = 70/A，
    # 但无 WA 入口/无 meta_ads/无 SaaS 信号 → recommend 全不命中 → products 空
    from app.collectors.base import LeadDraft as _LD
    weak, _ = await upsert_lead(
        db_session,
        _LD(
            name="今日批次不齐备科技（武汉）有限公司",
            source="seed_import",
            country="CN",
            website="https://v3gate-weak3q.com",
            is_cn=True,
            whatsapp_job=True,
            job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
            overseas_signals={"shipping": ["worldwide"]},
            target_countries=["US", "GB", "AE"],
            social={"facebook": "f", "instagram": "i"},
        ),
    )
    await db_session.commit()
    assert weak.score >= 60 and weak.icp_status == "qualified"
    r = await client.get("/api/v1/collect/leads/daily-batch", headers=h)
    data2 = r.json()["data"]
    assert weak.id not in [x["id"] for x in data2["new_leads"]]
    await client.delete(f"/api/v1/collect/leads/{weak.id}", headers=h)
```

```python
    weak, _ = await upsert_lead(
        db_session,
        _LD(
            name="今日批次不齐备科技（武汉）有限公司",
            source="seed_import",
            country="CN",
            website="https://v3gate-weak3q.com",
            is_cn=True,
            whatsapp_job=True,
            job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
            overseas_signals={"shipping": ["worldwide"]},
            target_countries=["US", "GB", "AE"],
            social={"facebook": "f", "instagram": "i"},
        ),
    )
    await db_session.commit()
    assert weak.score >= 60 and weak.icp_status == "qualified"
    r = await client.get("/api/v1/collect/leads/daily-batch", headers=h)
    data2 = r.json()["data"]
    assert weak.id not in [x["id"] for x in data2["new_leads"]]
    await client.delete(f"/api/v1/collect/leads/{weak.id}", headers=h)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_daily_batch.py -q`
Expected: FAIL（`KeyError: 'three_questions'`）

- [ ] **Step 3: 实施**

1. `schemas/collect.py`：`LeadDetailOut` 加字段 `three_questions: dict[str, Any] = {}`（放在 `score_breakdown` 附近，注释「三问：why/what/who/complete，spec §六」）
2. `endpoints/collect.py` 新增辅助函数（放在 `_fill_lead_list_fields` 后）：

```python
async def _attach_three_questions(db: SessionDep, leads: list[Lead]) -> dict[int, dict]:
    """批量为线索构建三问（一次 IN 查询取联系人，防 N+1）。返回 {lead_id: 三问}。"""
    from app.collectors.intent import build_three_questions

    contact_map: dict[int, list[LeadContact]] = {}
    if leads:
        rows = (
            await db.execute(
                select(LeadContact).where(LeadContact.lead_id.in_([i.id for i in leads]))
            )
        ).scalars().all()
        for c in rows:
            contact_map.setdefault(c.lead_id, []).append(c)
    return {
        i.id: build_three_questions(i, contacts=contact_map.get(i.id)) for i in leads
    }
```

3. detail 端点（`contacts` 已查出）：`out.three_questions = build_three_questions(lead, contacts=contacts)`（import 加到顶部 `from app.collectors.intent import build_three_questions`）
4. daily-batch 端点：`outs_promoted/outs_new` 组装后、构造响应 dict 前，插入齐备门槛与行注入。响应行由 `o.model_dump(mode="json")` 产出——**不要往 pydantic 对象上 setattr 未声明字段**，在 dump 出的 dict 上挂键：

```python
    tq_map = await _attach_three_questions(db, [*promoted, *new_leads])
    # 齐备门槛（spec §七）：不齐备的行不进今日商机
    rows_promoted = [
        {**o.model_dump(mode="json"), "three_questions": tq_map[o.id]}
        for o in outs_promoted
        if tq_map[o.id]["complete"]
    ]
    rows_new = [
        {**o.model_dump(mode="json"), "three_questions": tq_map[o.id]}
        for o in outs_new
        if tq_map[o.id]["complete"]
    ]
```

   响应体里 `"promoted": rows_promoted, "new_leads": rows_new` 替换原来的两个 `model_dump` 列表推导。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd backend && uv run pytest tests/test_daily_batch.py -q && uv run pytest tests/ -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/collect.py backend/app/api/v1/endpoints/collect.py backend/tests/test_daily_batch.py
git commit -m "feat: 三问接入详情与今日商机——齐备门槛（why≥2∧产品≥1∧有建联入口）"
```

---

### Task 7: job_posting 降级为信号巡检器

默认只对**库内已有公司**补招聘信号（career_site 同款模式），库外公司不再入库；「发现新线索」开关保留、默认关。

**Files:**
- Modify: `backend/app/crud/lead.py`（`upsert_lead` 加 `create_if_missing`）
- Modify: `backend/app/collectors/base.py`（`TaskContext.emit` 签名加 `create_if_missing`）
- Modify: `backend/app/services/task_runner.py`（emit 闭包透传 + skipped 计数）
- Modify: `backend/app/collectors/job_posting.py`（`discover_new` 参数 + logic_note + 跳过计数日志）
- Test: `backend/tests/test_collect_core.py`（upsert 巡检模式用例）

**Interfaces:**
- Produces: `upsert_lead(db, draft, *, create_if_missing: bool = True) -> tuple[Lead | None, bool]`（`create_if_missing=False` 且无匹配 → `(None, False)`，**不抛错**）；`ctx.emit(draft, create_if_missing=False)` 返回 `(0, False)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_collect_core.py` 追加（该文件已有 `upsert_lead`/`db_session` 用法，沿用其夹具；域名用 `patrol` 前缀防撞）：

```python
async def test_upsert_patrol_mode_no_create(db_session):
    """巡检模式：库外公司不建行（返回 None），库内公司照常合并信号。"""
    from app.collectors.base import LeadDraft
    from app.crud.lead import upsert_lead
    from sqlalchemy import select
    from app.models.lead import Lead

    # 库外公司 → 不创建
    lead, created = await upsert_lead(
        db_session,
        LeadDraft(
            source="job_posting", name="巡检模式外部公司（成都）有限公司",
            country="CN", city="成都", is_cn=True,
            job_signals={"wa_ops": {"label": "WhatsApp 运营/客服", "points": 30}},
            job_urls=["https://jobui.com/job/1/"],
        ),
        create_if_missing=False,
    )
    assert lead is None and created is False
    assert (
        await db_session.execute(
            select(func.count()).select_from(Lead).where(Lead.name == "巡检模式外部公司（成都）有限公司")
        )
    ).scalar_one() == 0

    # 先建一条库内公司，再以巡检模式合并 → 信号进来了
    seeded, _ = await upsert_lead(
        db_session,
        LeadDraft(source="seed_import", name="巡检模式库内公司（杭州）有限公司",
                  country="CN", website="https://patrol-seeded.com", is_cn=True),
    )
    await db_session.commit()
    merged, created = await upsert_lead(
        db_session,
        LeadDraft(
            source="job_posting", name="巡检模式库内公司（杭州）有限公司",
            website="https://patrol-seeded.com", country="CN", is_cn=True,
            job_signals={"overseas_cs": {"label": "海外/英文客服", "points": 20}},
            job_urls=["https://jobui.com/job/2/"],
        ),
        create_if_missing=False,
    )
    await db_session.commit()
    assert merged is not None and merged.id == seeded.id and created is False
    assert "overseas_cs" in (merged.job_signals or {})
    return seeded.id  # 用例改为调用方清理，或见下方说明
```

（用例签名带 `client, admin_credentials`：结尾用删除端点清理共享库——`h = {"Authorization": …}` 登录后 `await client.delete(f"/api/v1/collect/leads/{seeded.id}", headers=h)`，与 `test_daily_batch.py` 的清理方式一致。`func` 从该文件已有 import 取，没有就 `from sqlalchemy import func, select` 补上。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_collect_core.py -q`
Expected: FAIL（`TypeError: upsert_lead() got an unexpected keyword argument 'create_if_missing'`）

- [ ] **Step 3: 实施**

1. `crud/lead.py` `upsert_lead`：

```python
async def upsert_lead(
    db: AsyncSession, draft: LeadDraft, *, create_if_missing: bool = True
) -> tuple[Lead | None, bool]:
    """归一化 → dedupe_key → 新建或合并。返回 (lead, 是否新建)。

    create_if_missing=False（巡检模式，job_posting 信号巡检用）：
    三身份列反查不中 → (None, False)，不建行——招聘数据的价值是给库内
    公司补信号，不是发现新公司（92% 无官网 → 富化无米下锅 → 全员 C 的教训）。
    """
```

   `existing is None` 分支整体包在 `if not create_if_missing: return None, False` 之后：

```python
    if existing is None:
        if not create_if_missing:
            return None, False
        lead = _new_lead(...)
        ...
```

   （IntegrityError 重查后仍 None 的分支：`create_if_missing=False` 时同样 `return None, False`——重查其实不可能中（刚才没中），保持防御即可。）

2. `base.py` `TaskContext.emit` 类型改 `Callable[..., Awaitable[tuple[int, bool]]]`，docstring 注明可选 `create_if_missing` 透传
3. `task_runner.py` emit 闭包：

```python
        async def emit(draft: LeadDraft, *, create_if_missing: bool = True) -> tuple[int, bool]:
            if not draft.name or not draft.name.strip():
                return 0, False
            async with async_session() as s:
                lead, created = await _upsert(s, draft, create_if_missing=create_if_missing)
                await s.commit()
            if lead is None:  # 巡检模式库外公司：跳过不算新增/合并
                return 0, False
            counters["added" if created else "merged"] += 1
            return lead.id, created
```

   `_upsert`（line ~337）签名加 `*, create_if_missing: bool = True` 透传给 `upsert_lead`。
4. `job_posting.py`：
   - `param_schema` 追加：

```python
        {
            "key": "discover_new",
            "label": "发现新线索（默认关）",
            "required": False,
            "type": "switch",
            "placeholder": "默认只给库内已有公司补招聘信号；打开后才作为新线索来源",
            "default": "false",
        },
```

   - `run()`：`discover = str(ctx.params.get("discover_new") or "false").lower() in ("1", "true", "yes")`；emit 处改：

```python
                        skipped_offline = 0
                        for d in drafts:
                            lead_id, _created = await ctx.emit(d, create_if_missing=discover)
                            if lead_id == 0 and not discover:
                                skipped_offline += 1
                                continue
                            ...（原信号证据写入逻辑不变， lead_id 为 0 时跳过）
```

   页日志改为：`f"「{kw}」第 {pg} 页 → {len(drafts)} 个在招岗位，命中库内 {len(drafts) - skipped_offline} 家" + (f"，跳过库外 {skipped_offline} 家（巡检模式）" if skipped_offline else "") + ...`（发现模式 discover=True 时保持旧文案）
   - `logic_note` 的【抓什么】段改写为巡检口径：「监控中国招聘网站的在招岗位，**为库内已有线索的公司补充招聘信号**（在招海外客服=有海外客户、在招 WhatsApp 运营=在用 WA 做私域）。默认不产生新线索——招聘站公司大多无官网，价值在信号不在发现；需要扩量时打开『发现新线索』开关」
5. ` collectors/__init__.py` 不动（注册不变）

- [ ] **Step 4: 跑测试 + 全量**

Run: `cd backend && uv run pytest tests/test_collect_core.py -q && uv run pytest tests/ -q`
Expected: 全绿（`test_closedloop_fixes`/`test_signal_system` 若有 job_posting 建新线索的用例，确认它们显式传了 `create_if_missing` 语义不受影响——巡检默认只在 job_posting 采集器层，`upsert_lead` 默认值仍是 True）

- [ ] **Step 5: 提交**

```bash
git add backend/app/crud/lead.py backend/app/collectors/base.py backend/app/services/task_runner.py backend/app/collectors/job_posting.py backend/tests/test_collect_core.py
git commit -m "feat: job_posting降级为信号巡检器——默认只补库内公司信号，库外不入库"
```

---

### Task 8: web_search 定向词库 + 买家黑名单入库拦截 + meta_ads 默认词

**Files:**
- Modify: `backend/app/collectors/web_search.py`（`_NON_SITE_DOMAINS` 补缺 + 默认关键词五行业定向 + logic_note）
- Modify: `backend/app/collectors/meta_ad_library.py`（默认关键词补品类词 + logic_note 提示五行业）
- Test: `backend/tests/test_web_search.py`

**Interfaces:**
- Consumes: Task 3 的 `NON_BUYER_DOMAINS` 域名清单（这里做入库拦截的同源同步）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_web_search.py` 追加（沿用该文件对 `_is_blocked_domain` 的既有测法，没有就直接函数级测试）：

```python
def test_blocked_domains_include_cn_trade_media():
    """买家门同源黑名单：跨境媒体/社区/门户不再当企业官网入库（2026-08-31 实测漏网）。"""
    from app.collectors.web_search import _is_blocked_domain

    for d in ("ikjzd.com", "wearesellers.com", "cifnews.com", "kuajingyan.com",
              "kjtong.com", "mckinsey.com.cn", "www.cifnews.com"):
        assert _is_blocked_domain(d), d
    assert not _is_blocked_domain("anker.com")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_web_search.py -q`
Expected: FAIL

- [ ] **Step 3: 实施**

1. `web_search.py` `_NON_SITE_DOMAINS` 末尾追加注释块：

```python
    # 跨境行业媒体/社区/平台门户（2026-08-31 dev 库实测霸榜的"假线索"；
    # 与 collectors/icp.py NON_BUYER_DOMAINS 同源——这里入库拦截，那边存量兜底）
    "ikjzd.com", "wearesellers.com", "cifnews.com", "kuajingyan.com",
    "kjtong.com", "mckinsey.com.cn", "gizmodo.com", "whatsappbusiness.com",
```

2. 默认关键词（param_schema `keywords` 项 `default`）换五行业定向词库：

```python
            "default": "跨境电商 独立站 品牌,出海品牌 独立站,DTC 出海 品牌 官网,跨境 电商平台 卖家 服务,出海 游戏 公司,制造业 出海 工厂 外贸",
```

   placeholder 同步：「五行业定向词（跨境电商/品牌DTC/游戏/制造/出海服务），中文长尾业务词有效」；logic_note 的【关键词怎么填】段更新为同一口径。
3. `meta_ad_library.py` `keywords` 项 `default` 追加品类词（广告文案检索词，跨境电商为主）：`"smart watch,leggings,wig,shapewear,led strip light,phone case,jewelry,game"`；logic_note 补一句「关键词建议按目标行业品类词填（跨境电商品类/游戏/工具 App），挖的是『在投海外广告的中国企业』」

- [ ] **Step 4: 跑测试 + 全量**

Run: `cd backend && uv run pytest tests/test_web_search.py tests/test_meta_ads.py -q && uv run pytest tests/ -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add backend/app/collectors/web_search.py backend/app/collectors/meta_ad_library.py backend/tests/test_web_search.py
git commit -m "feat: web_search五行业定向词库+买家黑名单入库拦截；meta_ads默认品类词扩充"
```

---

### Task 9: 前端——六维展示退场，三问上一线

**Files:**
- Modify: `frontend/src/api/collect.ts`（类型：删 `dimensions`/`dimension_weights`，`LeadDetail` 加 `three_questions`；`score_signals` 注释；等级注释）
- Modify: `frontend/src/views/collect/lead/detail.vue`（六维评分卡 → 意向分构成卡；三问区块；line ~833 `dimensions.saas` 引用随删）
- Modify: `frontend/src/views/collect/daily.vue`（三问列改读 `three_questions`）
- Modify: `frontend/src/views/collect/lead/index.vue`（若 Task 2 未覆盖的导出/筛选文案）

**Interfaces:**
- Consumes: Task 6 的 `three_questions` 结构 `{why:[{key,label,points,evidence_url}], what:{need_types,products,scenes,saas_signals}, who:{contacts,whatsapp_numbers,whatsapp_url,roles}, complete}`；`score_breakdown = {total, items:[{key,label,points}]}`

- [ ] **Step 1: api/collect.ts 类型对齐**

```typescript
  /** v3 意向分 {命中信号键: 分值}（score_breakdown.items 是同一事实的明细形态） */
  score_signals: Record<string, number>
```

`LeadDetail`：删 `dimensions`/`dimension_weights`，加：

```typescript
  /** 三问（为什么需要你/应该卖什么/应该找谁/齐备度） */
  three_questions: {
    why: Array<{ key: string; label: string; points: number; evidence_url?: string | null }>
    what: {
      need_types: Array<{ type: string; label: string; selling: string }>
      products: Array<{ key: string; name: string; reason: string; priority: number }>
      scenes: string[]
      saas_signals: string[]
    }
    who: {
      contacts: Array<{ name: string; title?: string | null; seniority?: string | null; email?: string | null }>
      whatsapp_numbers: string[]
      whatsapp_url?: string | null
      roles: Array<{ role: string; hint: string }>
    }
    complete: boolean
  }
```

等级注释（line ~60）改 `/** 等级（意向分 v3：80+=S 60-79=A 40-59=B <40=C） */`。

- [ ] **Step 2: detail.vue 改造**

1. 「六维评分」卡片（line ~505-530 区域，`n-progress` 六条 + `dimensions`/`dimension_weights` 引用）整块删除
2. 已有的「加分明细」面板（line ~528-560）提升为唯一评分卡：标题改「意向分构成」，`加分明细参考分` 字样改 `意向分`，删除「与六维加权总分并存…」说明行（line ~560），补一句「每 1 分对应一条可核查证据，分值表见数据源管理页」
3. line ~833 `SaaS 需求维度分：{{ detail.dimensions.saas ?? 0 }}/100` 改为「SaaS 需求信号：{{ detail.three_questions.what.saas_signals.join('、') || '—' }}」
4. 推荐产品区上方插三问区块（Naive UI 描述列表三列，风格随现有页）。`<script setup>` 里先定义打开方法（模板里不能直接用 `window`）：

```typescript
const openEvidence = (url?: string | null) => url && window.open(url, '_blank')
```

```vue
<n-card title="销售三问" class="mt-3">
  <n-descriptions :column="3" bordered size="small">
    <n-descriptions-item label="为什么需要你">
      <div v-for="w in detail.three_questions.why" :key="w.key">
        <n-tag size="small" type="success">+{{ w.points }}</n-tag> {{ w.label }}
        <n-button v-if="w.evidence_url" text type="primary" size="tiny" @click="openEvidence(w.evidence_url)">证据</n-button>
      </div>
    </n-descriptions-item>
    <n-descriptions-item label="应该卖什么">
      <div v-for="p in detail.three_questions.what.products" :key="p.key">{{ p.name }}</div>
      <div v-if="detail.three_questions.what.scenes.length" class="text-gray">
        场景：{{ detail.three_questions.what.scenes.join('、') }}
      </div>
    </n-descriptions-item>
    <n-descriptions-item label="应该找谁">
      <template v-if="detail.three_questions.who.contacts.length || detail.three_questions.who.whatsapp_numbers.length">
        <div v-for="c in detail.three_questions.who.contacts" :key="c.email || c.name">
          {{ c.name }}{{ c.title ? `（${c.title}）` : '' }} {{ c.email }}
        </div>
        <div v-for="n in detail.three_questions.who.whatsapp_numbers" :key="n">WA：{{ n }}</div>
      </template>
      <div v-else class="text-gray">建议找：{{ detail.three_questions.who.roles.map(r => r.role).join(' / ') }}</div>
    </n-descriptions-item>
  </n-descriptions>
</n-card>
```

- [ ] **Step 3: daily.vue 三问列**

「为什么值得联系」chips 数据源从散列信号（line ~60-64 的 `row.fb_whatsapp` 等）换成 `row.three_questions.why.map(w => ({ label: w.label, type: 'success' }))`；「应该卖什么」列从 `recommended_products` 换 `row.three_questions.what.products.map(p => p.name)`；「应该找谁」列读 `row.three_questions.who`（contacts/WA 号码优先，无则 roles）。行类型 `DailyBatchRow`（或等价 interface）加 `three_questions` 字段。空态说明文案（「这份名单怎么来的」）补一句：「批次只收三问齐备的线索（≥2 条证据 + 有推荐产品 + 有建联入口或明确角色）」。

- [ ] **Step 4: 验证**

Run: `cd frontend && pnpm type-check && pnpm test && pnpm lint`
Expected: 全过（lint 若报既有问题不管，只修本任务引入的）

- [ ] **Step 5: 提交**

```bash
git add frontend/src
git commit -m "feat: 前端三问上一线——六维卡退场，意向分构成/销售三问/今日商机三问列"
```

---

### Task 10: 存量清洗 + 全库重评（dev 库）

**Files:**
- Create: `backend/scripts/clean_non_buyers.py`

**Interfaces:**
- Consumes: Task 3 `is_non_buyer`；`scripts/reeval_leads.py`（已有，不改）

- [ ] **Step 1: 写清洗脚本**

创建 `backend/scripts/clean_non_buyers.py`：

```python
"""存量清洗：删买家黑名单命中的污染线索（媒体/社区/软件页/门户），备份后执行。

用法：cd backend && uv run python scripts/clean_non_buyers.py [--dry-run]

删除范围 = is_non_buyer(name, domain) 命中（collectors/icp.py 词表+域名）。
预期首跑命中（2026-08-31 dev 库查实）：雨果跨境/知无不言/跨境知道/跨境眼/
跨境通/麦肯锡报告页/WhatsApp 软件页/gizmodo 下载页 共 8 条。
备份 CSV 写 /tmp/non_buyer_backup_<date>.csv，随后显式删子表（共享库无级联的
教训：leads 删除必须先清 contacts/signals/events/follow_ups）。
"""

import argparse
import asyncio
import csv
from datetime import date

from sqlalchemy import delete, select

from app.collectors.icp import is_non_buyer
from app.db.session import async_session
from app.models.lead import Lead, LeadContact, LeadEvent, LeadFollowUp, LeadSignal


async def main(dry_run: bool) -> None:
    async with async_session() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())
        victims = [
            l for l in leads
            if is_non_buyer(name=l.name, domain=l.domain) or l.icp_status == "non_buyer"
        ]
        print(f"命中 {len(victims)} / {len(leads)} 条：")
        for l in victims:
            print(f"  #{l.id} {l.name} | {l.domain or '-'} | {l.icp_status} | {l.score}")
        if dry_run:
            print("（dry-run，未删除）")
            return
        if not victims:
            print("无可删除项")
            return
        backup = f"/tmp/non_buyer_backup_{date.today().isoformat()}.csv"
        cols = [c.name for c in Lead.__table__.columns]
        with open(backup, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for l in victims:
                w.writerow([getattr(l, c) for c in cols])
        ids = [l.id for l in victims]
        for model in (LeadContact, LeadSignal, LeadEvent, LeadFollowUp):
            await session.execute(delete(model).where(model.lead_id.in_(ids)))
        await session.execute(delete(Lead).where(Lead.id.in_(ids)))
        await session.commit()
        print(f"已删除 {len(ids)} 条，备份：{backup}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    asyncio.run(main(p.parse_args().dry_run))
```

（先 grep `app/models/lead.py` 确认子表类名——`LeadSignal`/`LeadContact`/`LeadEvent`/`LeadFollowUp` 以实际为准，`lead_reviews` 表若 FK 指向 lead 也一并清。）

- [ ] **Step 2: dry-run 验证命中名单**

Run: `cd backend && uv run python scripts/clean_non_buyers.py --dry-run`
Expected: 命中 8 条左右，名单含 #9 雨果跨境 / #10 知无不言 / #13 跨境知道 / #12 跨境眼 / #14 跨境通 / #11 麦肯锡 / #4 WhatsApp 页 / #6 gizmodo。若明显多杀/漏杀，回看 `is_non_buyer` 词表再跑。

- [ ] **Step 3: 执行删除 + 全库重评**

```bash
cd backend && uv run python scripts/clean_non_buyers.py
uv run python scripts/reeval_leads.py
```

Expected: 重评输出「重评 N 条，变更 M 条」，ICP 分布里 qualified 数显著变化（出海证据弱的老 jobui 线索会掉分——v3 下无官网无 WA 证据的国内公司回落 C/培育池，这是设计预期）。

- [ ] **Step 4: 提交脚本**

```bash
git add backend/scripts/clean_non_buyers.py
git commit -m "feat: 存量清洗脚本——买家黑名单命中备份后删除（首跑清 8 条媒体/社区/软件页）"
```

---

### Task 11: 文档同步 + 全量回归收尾

**Files:**
- Modify: `docs/业务逻辑.md`

- [ ] **Step 1: 同步 docs/业务逻辑.md**

按 v3 事实更新以下节（保持该文档现有行文风格——表格+要点，不写迭代叙事）：
1. §1 链路图：「六维评分+分级」→「意向分加分制（v3）+分级」
2. §2.5 ICP 门：四态表 → 五态表（加 `non_buyer` 行：买家黑名单命中，默认列表/导出/今日商机排除），补买家门一段（黑名单域名/词表 + 白名单五行业是归类标签不是硬门）
3. §6 评分：整节替换为 v3——13 信号分值表（spec §3.2 原样）、互斥规则、锚点表、SaaS/规模信号不进主分的理由、`score_signals` v3 语义、`SCORING_DIM_WEIGHTS` 已删
4. §4.4 job_posting：巡检模式（默认只补库内信号、`discover_new` 开关、career_site 同款定位）
5. §4.1 web_search：五行业定向默认词 + 黑名单补缺说明
6. §8 今日商机：三问齐备门槛 + 行内 `three_questions`
7. 新增一小节「三问生成器」（`collectors/intent.py`）：why/what/who 口径、角色派生规则、齐备度定义
8. §12 已知边界：更新遗留项（TikTok/Google 广告库、LLM 语义层 V1.5 保留说法）

- [ ] **Step 2: 全量回归**

Run: `cd backend && uv run pytest tests/ -q && cd ../frontend && pnpm type-check && pnpm test`
Expected: 后端全绿 + 前端全过

- [ ] **Step 3: 提交**

```bash
git add docs/业务逻辑.md
git commit -m "docs: 业务逻辑文档同步意向分v3/买家门/巡检模式/三问生成器"
```

---

## 任务依赖

```
Task 1 (评分v3) → Task 2 (六维清理) → Task 3 (买家门) → Task 4 (三问生成器) → Task 6 (API接线)
                                                  ↘ Task 5 (品牌栈，独立可并行，但 Task 4 已引用 brand_stack 键，顺序放 4 后)
Task 7 (job_posting巡检) 依赖 Task 1（v3 分值下巡检收益才成立）
Task 8 (web_search/meta_ads 词库) 依赖 Task 3（黑名单同源）
Task 9 (前端) 依赖 Task 2 + Task 6
Task 10 (清洗+重评) 依赖 Task 3（is_non_buyer）+ Task 1（重评口径）——放最后跑库
Task 11 (文档) 收尾
```

执行顺序按编号 1→11 即可（4/5 可互换，7/8 可在 6 后并行）。

## 运营侧依赖（代码之外，完成后提醒用户）

1. `META_ADS_ACCESS_TOKEN`：https://www.facebook.com/ads/archive/api 免费申请（主通道，唯一 S/A 制造机）
2. `SCHEDULER_ENABLED=true`：三个 cron 任务自动跑，「每天收到一批」的前提
3. 质量优先小闭环验收：跑通后人工核 50–100 条三问齐备 S/A 商机
