"""signal system: overseas/job/ad 信号 + lead_signals 证据链（PRD §4）

Revision ID: e5f2d80a1b73
Revises: c3e8a91b6f04
Create Date: 2026-08-30 23:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f2d80a1b73'
down_revision: str | None = 'c3e8a91b6f04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 出海信号（§4.2）/ 招聘信号细分（§4.3）/ 广告信号（§4.1）
    op.add_column('leads', sa.Column('overseas_signals', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('leads', sa.Column('job_signals', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('leads', sa.Column('ad_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('leads', sa.Column('last_ad_at', sa.DateTime(timezone=True), nullable=True))

    # 信号级证据链（§4.1：类型/值/来源页面/原文/置信度/发现时间）
    op.create_table(
        'lead_signals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('signal_type', sa.String(length=32), nullable=False),
        sa.Column('value', sa.String(length=512), nullable=False),
        sa.Column('evidence_url', sa.String(length=512), nullable=True),
        sa.Column('evidence_raw', sa.String(length=1024), nullable=True),
        sa.Column('confidence', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('source', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('first_seen', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lead_id', 'signal_type', 'value', name='uq_lead_signals_type_value'),
    )
    op.create_index(op.f('ix_lead_signals_lead_id'), 'lead_signals', ['lead_id'], unique=False)
    op.create_index(op.f('ix_lead_signals_signal_type'), 'lead_signals', ['signal_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_lead_signals_signal_type'), table_name='lead_signals')
    op.drop_index(op.f('ix_lead_signals_lead_id'), table_name='lead_signals')
    op.drop_table('lead_signals')
    op.drop_column('leads', 'last_ad_at')
    op.drop_column('leads', 'ad_count')
    op.drop_column('leads', 'job_signals')
    op.drop_column('leads', 'overseas_signals')
