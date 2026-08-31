"""lead_reviews 抽检标注表（§十二 验证闭环）

Revision ID: e3b8c61a7d40
Revises: c7a1e9f04b2d
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3b8c61a7d40'
down_revision: str | None = 'c7a1e9f04b2d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'lead_reviews',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('field', sa.String(length=16), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=512), nullable=True),
        sa.Column('reviewer_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_lead_reviews_lead_id'), 'lead_reviews', ['lead_id'], unique=False)
    op.create_index(op.f('ix_lead_reviews_field'), 'lead_reviews', ['field'], unique=False)
    op.create_index(op.f('ix_lead_reviews_reviewer_id'), 'lead_reviews', ['reviewer_id'], unique=False)
    op.create_index(op.f('ix_lead_reviews_created_at'), 'lead_reviews', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_lead_reviews_created_at'), table_name='lead_reviews')
    op.drop_index(op.f('ix_lead_reviews_reviewer_id'), table_name='lead_reviews')
    op.drop_index(op.f('ix_lead_reviews_field'), table_name='lead_reviews')
    op.drop_index(op.f('ix_lead_reviews_lead_id'), table_name='lead_reviews')
    op.drop_table('lead_reviews')
