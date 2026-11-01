"""Record whether an approval was manual or automatic.

Historical decisions stay unchanged and retain an unknown source.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("procurement_requests", sa.Column("approval_source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("procurement_requests", "approval_source")
