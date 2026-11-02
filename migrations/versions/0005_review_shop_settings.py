"""review_shop_settings"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('review_shop_settings',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.String(length=32), nullable=False),
    sa.Column('text_method', sa.String(length=16), nullable=True),
    sa.Column('empty_method', sa.String(length=16), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("empty_method IN ('ai','template','skip')"),
    sa.CheckConstraint("text_method IN ('ai','template','skip')"),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'shop_id')
    )
    # Umumiy sozlamada ham matnli sharhlarga "javob yozilmasin" tanlash mumkin.
    op.drop_constraint('review_settings_text_method_check', 'review_settings', type_='check')
    op.create_check_constraint('review_settings_text_method_check', 'review_settings', "text_method IN ('ai','template','skip')")


def downgrade() -> None:
    # Eski kodda "skip" yo'q: o'rniga javob yuborib yubormaslik uchun avtomatik rejim o'chiriladi.
    op.execute("UPDATE review_settings SET text_method = 'ai', auto_reply = false WHERE text_method = 'skip'")
    op.drop_constraint('review_settings_text_method_check', 'review_settings', type_='check')
    op.create_check_constraint('review_settings_text_method_check', 'review_settings', "text_method IN ('ai','template')")
    op.drop_table('review_shop_settings')
