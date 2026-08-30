"""icp_status 二重门列 + 存量回填

Revision ID: c7a1e9f04b2d
Revises: a9d3e72f5c14
Create Date: 2026-08-31

ICP 二重门（业务重构）：qualified/cn_domestic/foreign/unknown，
判定逻辑见 app/collectors/icp.py（行属性纯函数，迁移与应用共用）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7a1e9f04b2d'
down_revision: str | None = 'a9d3e72f5c14'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('icp_status', sa.String(length=16), server_default='unknown', nullable=False),
    )
    op.create_index(op.f('ix_leads_icp_status'), 'leads', ['icp_status'], unique=False)
    _backfill_icp_status()


def _backfill_icp_status() -> None:
    """全表回填 ICP 资格（与 app.collectors.icp.compute_icp_status 同口径）。"""
    from app.collectors.icp import compute_icp_status

    conn = op.get_bind()
    leads_t = sa.Table("leads", sa.MetaData(), autoload_with=conn)
    rows = conn.execute(sa.select(leads_t)).mappings().all()
    for row in rows:
        status = compute_icp_status(
            is_cn=row["is_cn"],
            country=row["country"],
            phone_e164=row["phone_e164"],
            overseas_signals=row["overseas_signals"] or None,
            fb_whatsapp=row["fb_whatsapp"],
            target_countries=row["target_countries"] or None,
            enriched_at=row["enriched_at"],
            sources=row["sources"],
        )
        conn.execute(
            sa.update(leads_t)
            .where(leads_t.c.id == row["id"])
            .values(icp_status=status)
        )


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_icp_status'), table_name='leads')
    op.drop_column('leads', 'icp_status')
