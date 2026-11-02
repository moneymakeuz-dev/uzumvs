"""review_replies"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('review_settings',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('auto_reply', sa.Boolean(), nullable=False),
    sa.Column('text_method', sa.String(length=16), nullable=False),
    sa.Column('empty_method', sa.String(length=16), nullable=False),
    sa.Column('since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_message', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("empty_method IN ('ai','template','skip')"),
    sa.CheckConstraint("text_method IN ('ai','template')"),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('review_templates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('rating_min', sa.SmallInteger(), nullable=False),
    sa.Column('rating_max', sa.SmallInteger(), nullable=False),
    sa.Column('text_uz', sa.String(length=500), nullable=False),
    sa.Column('text_ru', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('rating_min BETWEEN 1 AND 5 AND rating_max BETWEEN 1 AND 5 AND rating_min <= rating_max'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_templates_user_id'), 'review_templates', ['user_id'], unique=False)
    op.create_table('review_replies',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('review_id', sa.BigInteger(), nullable=False),
    sa.Column('shop_title', sa.String(length=200), nullable=False),
    sa.Column('product_title', sa.String(length=300), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('template_id', sa.Uuid(), nullable=True),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('moderation', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("method IN ('ai','template','manual')"),
    sa.CheckConstraint("source IN ('auto','manual')"),
    sa.CheckConstraint("status IN ('draft','sending','sent','failed')"),
    sa.ForeignKeyConstraint(['template_id'], ['review_templates.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'review_id')
    )
    op.add_column('daily_usage', sa.Column('reply_ai_calls', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('daily_usage', 'reply_ai_calls')
    op.drop_table('review_replies')
    op.drop_index(op.f('ix_review_templates_user_id'), table_name='review_templates')
    op.drop_table('review_templates')
    op.drop_table('review_settings')
