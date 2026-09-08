"""代理式销售运营：外联序列 + 消息 + 回复 + 商机预测 + 快照

Revision ID: 9c1d2e3f4a5b
Revises: 88366a3fd466
Create Date: 2026-09-07

5 张新表：
    outreach_sequences    序列模板（场景多步节奏）
    outreach_messages     单条外联（草稿/已发/已回复）+ 状态机
    replies               客户回复（含 LLM 标注 + 处理状态）
    forecast_deals        商机预测基线（金额×概率）
    forecast_snapshots    预测快照（按 weekly/monthly 不可变追加）

⚠️ outreach_messages 与 replies 有循环 FK（消息→reply_id；回复→message_id）。
   SQLite/PG inline FK 要求双方表都已存在——先建两张表不带相互 FK，再 ALTER 补上。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '9c1d2e3f4a5b'
down_revision: str | None = '88366a3fd466'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. outreach_sequences（无循环 FK）
    op.create_table(
        'outreach_sequences',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.String(512), nullable=True),
        sa.Column('scenario', sa.String(32), nullable=False, server_default='first_touch'),
        sa.Column('channel', sa.String(16), nullable=False, server_default='email'),
        sa.Column('steps', sa.JSON, nullable=False),
        sa.Column('active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_outreach_sequences_name', 'outreach_sequences', ['name'])
    op.create_index('ix_outreach_sequences_scenario', 'outreach_sequences', ['scenario'])
    op.create_index('ix_outreach_sequences_active', 'outreach_sequences', ['active'])
    op.create_index('ix_outreach_sequences_owner_id', 'outreach_sequences', ['owner_id'])

    # 2. outreach_messages — 先不带 reply_id FK（指向还没建的 replies）
    op.create_table(
        'outreach_messages',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('lead_id', sa.Integer, sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_id', sa.Integer, sa.ForeignKey('lead_contacts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('sequence_id', sa.Integer, sa.ForeignKey('outreach_sequences.id', ondelete='SET NULL'), nullable=True),
        sa.Column('step_index', sa.Integer, nullable=True),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('subject', sa.String(255), nullable=True),
        sa.Column('body', sa.Text, nullable=False, server_default=''),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('llm_generated', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('generated_by', sa.String(16), nullable=False, server_default='template'),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_error', sa.String(512), nullable=True),
        sa.Column('reply_id', sa.Integer, nullable=True),  # 暂不带 FK；下面 ALTER 补
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            'lead_id', 'sequence_id', 'step_index', name='uq_outreach_lead_seq_step'
        ),
    )
    op.create_index('ix_outreach_lead_status', 'outreach_messages', ['lead_id', 'status'])
    op.create_index('ix_outreach_owner_status', 'outreach_messages', ['owner_id', 'status'])
    op.create_index('ix_outreach_scheduled', 'outreach_messages', ['scheduled_at'])
    op.create_index('ix_outreach_messages_channel', 'outreach_messages', ['channel'])
    op.create_index('ix_outreach_messages_status', 'outreach_messages', ['status'])
    op.create_index('ix_outreach_messages_contact_id', 'outreach_messages', ['contact_id'])
    op.create_index('ix_outreach_messages_sequence_id', 'outreach_messages', ['sequence_id'])
    op.create_index('ix_outreach_messages_reply_id', 'outreach_messages', ['reply_id'])
    op.create_index('ix_outreach_messages_owner_id', 'outreach_messages', ['owner_id'])
    op.create_index('ix_outreach_messages_approved_by', 'outreach_messages', ['approved_by'])

    # 3. replies — FK 完整（含 message_id → outreach_messages）
    op.create_table(
        'replies',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('lead_id', sa.Integer, sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'message_id',
            sa.Integer,
            sa.ForeignKey('outreach_messages.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('channel', sa.String(16), nullable=False),
        sa.Column('from_address', sa.String(255), nullable=True),
        sa.Column('subject', sa.String(255), nullable=True),
        sa.Column('body', sa.Text, nullable=False, server_default=''),
        sa.Column('sentiment', sa.String(16), nullable=True),
        sa.Column('intent', sa.String(32), nullable=True),
        sa.Column('summary', sa.String(512), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('crm_action', sa.String(512), nullable=True),
        sa.Column('llm_labeled', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('overridden', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('handled_by', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_replies_lead_id', 'replies', ['lead_id'])
    op.create_index('ix_replies_message_id', 'replies', ['message_id'])
    op.create_index('ix_replies_channel', 'replies', ['channel'])
    op.create_index('ix_replies_from_address', 'replies', ['from_address'])
    op.create_index('ix_replies_sentiment', 'replies', ['sentiment'])
    op.create_index('ix_replies_intent', 'replies', ['intent'])
    op.create_index('ix_replies_received_at', 'replies', ['received_at'])
    op.create_index('ix_replies_handled_by', 'replies', ['handled_by'])
    op.create_index('ix_replies_lead_received', 'replies', ['lead_id', 'received_at'])
    op.create_index(
        'ix_replies_unprocessed',
        'replies',
        ['received_at', 'processed_at'],
    )

    # 4. 现在补 outreach_messages.reply_id 的 FK（replies 表已存在）
    with op.batch_alter_table('outreach_messages') as batch_op:
        batch_op.create_foreign_key(
            'fk_outreach_messages_reply_id',
            'replies',
            ['reply_id'],
            ['id'],
            ondelete='SET NULL',
        )

    # 5. forecast_deals（独立表）
    op.create_table(
        'forecast_deals',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('lead_id', sa.Integer, sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('stage', sa.String(16), nullable=False),
        sa.Column('amount', sa.Float, nullable=False, server_default='0'),
        sa.Column('probability', sa.Integer, nullable=False, server_default='0'),
        sa.Column('close_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_primary', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('note', sa.Text, nullable=True),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_forecast_deals_lead_id', 'forecast_deals', ['lead_id'])
    op.create_index('ix_forecast_deals_stage', 'forecast_deals', ['stage'])
    op.create_index('ix_forecast_deals_close_date', 'forecast_deals', ['close_date'])
    op.create_index('ix_forecast_deals_owner_id', 'forecast_deals', ['owner_id'])
    op.create_index('ix_forecast_stage_close', 'forecast_deals', ['stage', 'close_date'])
    op.create_index('ix_forecast_owner_stage', 'forecast_deals', ['owner_id', 'stage'])

    # 6. forecast_snapshots（独立表）
    op.create_table(
        'forecast_snapshots',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('period', sa.String(16), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('data', sa.JSON, nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('period', 'period_start', name='uq_forecast_period_start'),
    )
    op.create_index(
        'ix_forecast_snapshots_period',
        'forecast_snapshots',
        ['period', 'period_start'],
    )


def downgrade() -> None:
    op.drop_table('forecast_snapshots')
    op.drop_table('forecast_deals')
    op.drop_table('replies')
    op.drop_table('outreach_messages')
    op.drop_table('outreach_sequences')
