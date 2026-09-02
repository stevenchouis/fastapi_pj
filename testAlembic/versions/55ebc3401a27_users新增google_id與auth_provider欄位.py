"""users新增google_id與auth_provider欄位

Revision ID: 55ebc3401a27
Revises: fa327f9b6555
Create Date: 2026-08-31 13:08:24.058064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '55ebc3401a27'
down_revision: Union[str, Sequence[str], None] = 'fa327f9b6555'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('google_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_users_google_id'), 'users', ['google_id'], unique=True)
    op.add_column(
        'users',
        sa.Column(
            'auth_provider',
            sa.String(),
            nullable=False,
            server_default='password',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'auth_provider')
    op.drop_index(op.f('ix_users_google_id'), table_name='users')
    op.drop_column('users', 'google_id')
