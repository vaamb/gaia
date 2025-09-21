"""Initial migration

Revision ID: 5b748c447b29
Revises: 
Create Date: 2025-09-21 21:53:47.127205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b748c447b29'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass

def downgrade() -> None:
    """Downgrade schema."""
    pass
