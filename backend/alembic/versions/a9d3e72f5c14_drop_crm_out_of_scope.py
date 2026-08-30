"""drop opportunities / sales_messages（PRD §三 范围收缩：V1 不做复杂 CRM/AI Agent）

Revision ID: a9d3e72f5c14
Revises: f8a4c31e9d02
Create Date: 2026-08-31 01:20:00.000000

按需求边界移除：商机 CRM（漏斗/金额/阶段）与话术审核队列属 V3 范畴
（PRD §三「V1 明确不做：复杂 CRM、自动发送 WhatsApp、AI Agent」）。
线索成交回传由 follow_status 状态机（won/invalid）承担，数据飞轮不受影响。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9d3e72f5c14'
down_revision: str | None = 'f8a4c31e9d02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table('opportunities')
    op.drop_table('sales_messages')


def downgrade() -> None:
    # 结构可恢复（数据不可——drop 即删除，downgrade 只重建空表）
    op.create_table(
        'sales_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('generated_by', sa.String(length=16), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'opportunities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(length=16), nullable=False),
        sa.Column('expected_close_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('won_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
