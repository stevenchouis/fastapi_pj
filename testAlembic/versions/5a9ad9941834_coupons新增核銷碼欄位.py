"""coupons新增核銷碼欄位

Revision ID: 5a9ad9941834
Revises: 9413b82a0f95
Create Date: 2026-08-31 23:38:55.286123

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a9ad9941834'
down_revision: Union[str, Sequence[str], None] = '9413b82a0f95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('coupons', sa.Column('redeem_code_hash', sa.String(), nullable=True))
    op.add_column(
        'coupons',
        sa.Column('redeem_code_expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f('ix_coupons_redeem_code_hash'), 'coupons', ['redeem_code_hash'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_coupons_redeem_code_hash'), table_name='coupons')
    op.drop_column('coupons', 'redeem_code_expires_at')
    op.drop_column('coupons', 'redeem_code_hash')
