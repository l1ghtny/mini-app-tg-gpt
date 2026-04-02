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
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. Add chat_folder table
    if not inspector.has_table('chat_folder'):
        op.create_table('chat_folder',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('user_id', sa.Uuid(), nullable=False),
            sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column('prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
            sa.PrimaryKeyConstraint('id')
        )
    
    # Check indexes for chat_folder
    chat_folder_indexes = {idx["name"] for idx in inspector.get_indexes('chat_folder')}
    user_id_index = op.f('ix_chat_folder_user_id')
    if user_id_index not in chat_folder_indexes:
        op.create_index(user_id_index, 'chat_folder', ['user_id'], unique=False)
    
    name_index = op.f('ix_chat_folder_name')
    if name_index not in chat_folder_indexes:
        op.create_index(name_index, 'chat_folder', ['name'], unique=False)

    # 2. Add default_prompt to app_user
    app_user_columns = {col["name"] for col in inspector.get_columns('app_user')}
    if 'default_prompt' not in app_user_columns:
        op.add_column('app_user', sa.Column('default_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='Ты помощник, готовый ответить на вопросы.'))
        # Remove server_default after adding so it's managed by app logic if needed
        op.alter_column('app_user', 'default_prompt', server_default=None)

    # 3. Add folder_id to conversation
    conversation_columns = {col["name"] for col in inspector.get_columns('conversation')}
    if 'folder_id' not in conversation_columns:
        op.add_column('conversation', sa.Column('folder_id', sa.Uuid(), nullable=True))
    
    conversation_indexes = {idx["name"] for idx in inspector.get_indexes('conversation')}
    folder_id_index = op.f('ix_conversation_folder_id')
    if folder_id_index not in conversation_indexes:
        op.create_index(folder_id_index, 'conversation', ['folder_id'], unique=False)

    conversation_fks = inspector.get_foreign_keys('conversation')
    has_folder_fk = any(fk['referred_table'] == 'chat_folder' and 'folder_id' in fk['constrained_columns'] for fk in conversation_fks)
    if not has_folder_fk:
        op.create_foreign_key(None, 'conversation', 'chat_folder', ['folder_id'], ['id'])

    # 4. Drop system_prompt from conversation
    if 'system_prompt' in conversation_columns:
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
