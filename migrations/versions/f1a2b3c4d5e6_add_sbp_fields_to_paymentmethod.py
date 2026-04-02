"""add SBP fields to PaymentMethod

Revision ID: f1a2b3c4d5e6
Revises: 69a355643294
Create Date: 2026-02-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e5b7d7f2a9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns('payment_methods')}

    # Add new columns
    if 'account_token' not in columns:
        op.add_column('payment_methods', sa.Column('account_token', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    if 'type' not in columns:
        op.add_column('payment_methods', sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='card'))
    if 'phone' not in columns:
        op.add_column('payment_methods', sa.Column('phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    # Create indexes for new columns
    indexes = {idx["name"] for idx in inspector.get_indexes('payment_methods')}
    account_token_index = op.f('ix_payment_methods_account_token')
    if account_token_index not in indexes:
        op.create_index(account_token_index, 'payment_methods', ['account_token'], unique=False)
    
    type_index = op.f('ix_payment_methods_type')
    if type_index not in indexes:
        op.create_index(type_index, 'payment_methods', ['type'], unique=False)

    # Make rebill_id nullable because SBP doesn't have it
    op.alter_column('payment_methods', 'rebill_id',
               existing_type=sa.VARCHAR(),
               nullable=True)


def downgrade() -> None:
    # Revert rebill_id to not null (CAUTION: Data loss if SBP methods exist)
    # We might want to filter or just warn, but strictly speaking downgrade should restore schema.
    # If we have SBP rows, this will fail unless we delete them.
    op.execute("DELETE FROM payment_methods WHERE type != 'card'")
    op.alter_column('payment_methods', 'rebill_id',
               existing_type=sa.VARCHAR(),
               nullable=False)

    # Drop indexes and columns
    op.drop_index(op.f('ix_payment_methods_type'), table_name='payment_methods')
    op.drop_index(op.f('ix_payment_methods_account_token'), table_name='payment_methods')
    op.drop_column('payment_methods', 'phone')
    op.drop_column('payment_methods', 'type')
    op.drop_column('payment_methods', 'account_token')
