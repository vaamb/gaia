"""Manage actuators by "groups" rather than "types"

Revision ID: e6f6bac2aac8
Revises: 5b748c447b29
Create Date: 2025-09-21 22:07:55.907367

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f6bac2aac8'
down_revision: Union[str, Sequence[str], None] = '5b748c447b29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_actuator_group(context) -> str:
    params = context.get_current_parameters()
    return str(params["type"])


def upgrade() -> None:
    with op.batch_alter_table('actuator_records') as batch_op:
        batch_op.add_column(
            sa.Column('group', sa.String(length=16), default=_get_actuator_group))

    with op.batch_alter_table('actuator_buffers') as batch_op:
        batch_op.add_column(
            sa.Column('group', sa.String(length=16), default=_get_actuator_group))

def downgrade() -> None:
    with op.batch_alter_table('actuator_records') as batch_op:
        batch_op.drop_column('group')

    with op.batch_alter_table('actuator_buffers') as batch_op:
        batch_op.drop_column('group')
