"""新增magic_link_tokens表

Revision ID: 9413b82a0f95
Revises: 55ebc3401a27
Create Date: 2026-08-31 22:36:46.982146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9413b82a0f95'
down_revision: Union[str, Sequence[str], None] = '55ebc3401a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'magic_link_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_magic_link_tokens_id'), 'magic_link_tokens', ['id'], unique=False
    )
    op.create_index(
        op.f('ix_magic_link_tokens_email'), 'magic_link_tokens', ['email'], unique=False
    )
    op.create_index(
        op.f('ix_magic_link_tokens_token_hash'),
        'magic_link_tokens',
        ['token_hash'],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_magic_link_tokens_token_hash'), table_name='magic_link_tokens')
    op.drop_index(op.f('ix_magic_link_tokens_email'), table_name='magic_link_tokens')
    op.drop_index(op.f('ix_magic_link_tokens_id'), table_name='magic_link_tokens')
    op.drop_table('magic_link_tokens')
