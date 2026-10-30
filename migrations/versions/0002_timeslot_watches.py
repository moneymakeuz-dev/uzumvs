"""timeslot_watches"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('timeslot_watches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('shop_id', sa.String(length=32), nullable=False),
    sa.Column('invoice_id', sa.BigInteger(), nullable=False),
    sa.Column('invoice_number', sa.String(length=64), nullable=False),
    sa.Column('stock_title', sa.String(length=200), nullable=False),
    sa.Column('date_from', sa.Date(), nullable=False),
    sa.Column('date_to', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('message', sa.String(length=300), nullable=False),
    sa.Column('checks', sa.Integer(), nullable=False),
    sa.Column('current_time_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('new_time_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active','done','failed','cancelled')"),
    sa.CheckConstraint('date_from <= date_to'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_timeslot_watches_one_active', 'timeslot_watches', ['shop_id', 'invoice_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index(op.f('ix_timeslot_watches_user_id'), 'timeslot_watches', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_timeslot_watches_user_id'), table_name='timeslot_watches')
    op.drop_index('ix_timeslot_watches_one_active', table_name='timeslot_watches', postgresql_where=sa.text("status = 'active'"))
    op.drop_table('timeslot_watches')
