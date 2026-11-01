"""uzum_accounts"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('uzum_accounts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('login_hint', sa.String(length=80), nullable=False),
    sa.Column('login_secret', sa.Text(), nullable=False),
    sa.Column('password_secret', sa.Text(), nullable=False),
    sa.Column('api_token_secret', sa.Text(), nullable=False),
    sa.Column('access_token_secret', sa.Text(), nullable=True),
    sa.Column('shops_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('shops_checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('status_message', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active','token_failed','login_failed')"),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )


def downgrade() -> None:
    op.drop_table('uzum_accounts')
