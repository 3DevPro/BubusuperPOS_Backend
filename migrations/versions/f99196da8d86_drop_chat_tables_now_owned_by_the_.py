"""drop chat tables now owned by the chatbot service

Revision ID: f99196da8d86
Revises: a56c424d307b
Create Date: 2026-08-09 16:45:27.871903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f99196da8d86'
down_revision: Union[str, None] = 'a56c424d307b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate ordered this alphabetically, which drops `conversations`
    # before `chat_messages` — but chat_messages.conversation_id still has a
    # live FK into conversations at that point, so the drop would fail.
    # chat_pending_actions and chat_messages have no FK to each other, so
    # only the conversations/chat_messages ordering below is load-bearing.
    op.drop_index('ix_chat_messages_conversation_id', table_name='chat_messages')
    op.drop_index('ix_chat_messages_tenant_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('ix_conversations_tenant_id', table_name='conversations')
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    op.drop_table('conversations')
    op.drop_index('ix_chat_pending_actions_tenant_id', table_name='chat_pending_actions')
    op.drop_table('chat_pending_actions')
    # DROP TABLE does not drop the Postgres enum types their columns used —
    # autogenerate misses these entirely. Left behind, they'd collide with
    # `CREATE TYPE` the next time anything (e.g. this same migration's own
    # downgrade()) tries to recreate a same-named enum.
    op.execute("DROP TYPE chatrole")
    op.execute("DROP TYPE chatactionstatus")


def downgrade() -> None:
    # Reordered from autogenerate's output for the same FK reason as
    # upgrade() above: conversations must exist before chat_messages, which
    # references it, can be created.
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('title', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='conversations_tenant_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='conversations_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='conversations_pkey')
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'], unique=False)
    op.create_index('ix_conversations_tenant_id', 'conversations', ['tenant_id'], unique=False)
    op.create_table('chat_messages',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('conversation_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('role', postgresql.ENUM('user', 'assistant', name='chatrole'), autoincrement=False, nullable=False),
    sa.Column('content', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('intent', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='chat_messages_conversation_id_fkey'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='chat_messages_tenant_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='chat_messages_pkey')
    )
    op.create_index('ix_chat_messages_tenant_id', 'chat_messages', ['tenant_id'], unique=False)
    op.create_index('ix_chat_messages_conversation_id', 'chat_messages', ['conversation_id'], unique=False)
    op.create_table('chat_pending_actions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('user_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('action_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False),
    sa.Column('summary', sa.VARCHAR(length=500), autoincrement=False, nullable=False),
    sa.Column('status', postgresql.ENUM('pending', 'confirmed', name='chatactionstatus'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='chat_pending_actions_tenant_id_fkey'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='chat_pending_actions_user_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='chat_pending_actions_pkey')
    )
    op.create_index('ix_chat_pending_actions_tenant_id', 'chat_pending_actions', ['tenant_id'], unique=False)
