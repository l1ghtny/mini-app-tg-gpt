"""add chat folders

Revision ID: 9e4afff3d0fc
Revises: fff111222333
Create Date: 2026-02-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import uuid6

# revision identifiers, used by Alembic.
revision: str = '9e4afff3d0fc'
down_revision: Union[str, Sequence[str], None] = 'fff111222333'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add chat_folder table
    op.create_table('chat_folder',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_folder_user_id'), 'chat_folder', ['user_id'], unique=False)
    op.create_index(op.f('ix_chat_folder_name'), 'chat_folder', ['name'], unique=False)

    # 2. Add default_prompt to app_user
    op.add_column('app_user', sa.Column('default_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='Ты помощник, готовый ответить на вопросы.'))
    # Remove server_default after adding so it's managed by app logic if needed, but keeping it is also fine.
    # Usually better to remove server_default if it's just for backfilling.
    op.alter_column('app_user', 'default_prompt', server_default=None)

    # 3. Add folder_id to conversation
    op.add_column('conversation', sa.Column('folder_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_conversation_folder_id'), 'conversation', ['folder_id'], unique=False)
    op.create_foreign_key(None, 'conversation', 'chat_folder', ['folder_id'], ['id'])

    # 4. Drop system_prompt from conversation
    op.drop_column('conversation', 'system_prompt')


def downgrade() -> None:
    # Reverse order
    op.add_column('conversation', sa.Column('system_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.drop_constraint(None, 'conversation', type_='foreignkey')
    op.drop_index(op.f('ix_conversation_folder_id'), table_name='conversation')
    op.drop_column('conversation', 'folder_id')
    op.drop_column('app_user', 'default_prompt')
    op.drop_index(op.f('ix_chat_folder_name'), table_name='chat_folder')
    op.drop_index(op.f('ix_chat_folder_user_id'), table_name='chat_folder')
    op.drop_table('chat_folder')
