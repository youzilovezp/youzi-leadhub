"""wa_business + score_breakdown（PRD §4.1/§五 MVP 加分制）

Revision ID: f8a4c31e9d02
Revises: e5f2d80a1b73
Create Date: 2026-08-31 00:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8a4c31e9d02'
down_revision: str | None = 'e5f2d80a1b73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # WhatsApp Business 使用（§4.1 代理判定，页面自述业务号）
    op.add_column('leads', sa.Column('wa_business', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f('ix_leads_wa_business'), 'leads', ['wa_business'], unique=False)
    # MVP 加分制明细（§五 13 条）：{"total": 参考总分, "items": [{key,label,points}]}
    op.add_column('leads', sa.Column('score_breakdown', sa.JSON(), nullable=False, server_default='{}'))
    # 存量行回填加分明细（复用评分纯函数口径）
    _backfill_breakdown()


def _backfill_breakdown() -> None:
    """存量 leads 回填 score_breakdown + wa_bsp 语义（无 IO 之外的依赖）。"""
    from app.collectors.scoring import bonus_breakdown

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, fb_whatsapp, whatsapp_hit, whatsapp_numbers, job_signals, "
            "saas_signals, sources, ad_count, is_cn, overseas_signals, "
            "target_countries, social FROM leads"
        )
    ).mappings()
    import json

    for r in rows:
        breakdown = bonus_breakdown(
            fb_whatsapp=bool(r["fb_whatsapp"]),
            whatsapp_hit=bool(r["whatsapp_hit"]),
            whatsapp_numbers=json.loads(r["whatsapp_numbers"] or "[]") if isinstance(r["whatsapp_numbers"], str) else (r["whatsapp_numbers"] or []),
            job_signals=json.loads(r["job_signals"] or "{}") if isinstance(r["job_signals"], str) else (r["job_signals"] or {}),
            saas_signals=json.loads(r["saas_signals"] or "{}") if isinstance(r["saas_signals"], str) else (r["saas_signals"] or {}),
            sources=json.loads(r["sources"] or "[]") if isinstance(r["sources"], str) else (r["sources"] or []),
            ad_count=int(r["ad_count"] or 0),
            is_cn=bool(r["is_cn"]),
            overseas_signals=json.loads(r["overseas_signals"] or "{}") if isinstance(r["overseas_signals"], str) else (r["overseas_signals"] or {}),
            target_countries=json.loads(r["target_countries"] or "[]") if isinstance(r["target_countries"], str) else (r["target_countries"] or []),
            social=json.loads(r["social"] or "{}") if isinstance(r["social"], str) else (r["social"] or {}),
        )
        is_pg = conn.dialect.name == "postgresql"
        cast = "CAST(:bd AS JSON)" if is_pg else ":bd"
        conn.execute(
            sa.text(f"UPDATE leads SET score_breakdown = {cast} WHERE id = :id"),
            {"bd": json.dumps(breakdown), "id": r["id"]},
        )


def downgrade() -> None:
    op.drop_column('leads', 'score_breakdown')
    op.drop_index(op.f('ix_leads_wa_business'), table_name='leads')
    op.drop_column('leads', 'wa_business')
