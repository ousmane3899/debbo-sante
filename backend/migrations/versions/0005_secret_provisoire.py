"""secret provisoire des comptes

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.add_column(sa.Column('secret_provisoire', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('secret_expire_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.drop_column('secret_expire_at')
        batch_op.drop_column('secret_provisoire')
