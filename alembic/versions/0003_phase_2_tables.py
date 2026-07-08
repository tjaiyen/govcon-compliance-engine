"""phase 2 tables + unallowable-category seed

Adds contract_clause_exceptions (the fifth allowability test) and
gsa_per_diem_rates (the §4a travel-split reference table), and seeds
unallowable_cost_categories with the 18 FAR 31.205 rows from architecture
spec §4 — a working set, NOT a completeness claim (see reg-ref §10 on the
unverified "52 categories" figure). Rows are frozen here and mirrored by
govcon/seeds/unallowable_categories.py with a drift test.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08 16:04:59.464101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import govcon.db.types


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# FROZEN copy of govcon/seeds/unallowable_categories.py at authoring time —
# tests/test_unallowable_seed.py pins DB rows == the importable constants.
SEED_CATEGORIES = [{'far_citation': '31.205-1',
  'category_name': 'Advertising & Public Relations',
  'detection_method': 'keyword_pattern',
  'trap_logic_description': 'Flag promotional/marketing/sponsorship spend; note the '
                            'narrow help-wanted-advertising and required-trade-show '
                            'exceptions rather than blanket-flagging all advertising'},
 {'far_citation': '31.205-3',
  'category_name': 'Bad Debts',
  'detection_method': 'account_code',
  'trap_logic_description': 'Dedicated account code; excluded from G&A base'},
 {'far_citation': '31.205-5',
  'category_name': 'Depreciation (excess/unallowable portion)',
  'detection_method': 'rate_lookup',
  'trap_logic_description': 'Flag depreciation exceeding CAS-allowable methods or on '
                            'assets not used in contract performance'},
 {'far_citation': '31.205-6(p)',
  'category_name': 'Executive Compensation Limit',
  'detection_method': 'rate_lookup',
  'trap_logic_description': 'Flag compensation above the statutory annual cap; route '
                            'excess to unallowable'},
 {'far_citation': '31.205-8',
  'category_name': 'Contributions/Donations',
  'detection_method': 'account_code',
  'trap_logic_description': 'Flag all civic/charitable/political donations'},
 {'far_citation': '31.205-14',
  'category_name': 'Entertainment & Recreation',
  'detection_method': 'keyword_pattern',
  'trap_logic_description': 'Keyword/category flag (tickets, parties, golf, social '
                            'events)'},
 {'far_citation': '31.205-15',
  'category_name': 'Fines and Penalties',
  'detection_method': 'account_code',
  'trap_logic_description': 'Auto-flag regulatory fines, tax penalties, late fees'},
 {'far_citation': '31.205-19',
  'category_name': 'Insurance & Indemnification',
  'detection_method': 'account_code',
  'trap_logic_description': 'Flag self-insurance reserves and indemnification costs '
                            'exceeding allowable limits'},
 {'far_citation': '31.205-20',
  'category_name': 'Interest Expense',
  'detection_method': 'account_code',
  'trap_logic_description': 'Auto-flag interest on borrowings, bond discounts, '
                            'financing fees'},
 {'far_citation': '31.205-22',
  'category_name': 'Lobbying & Political Activity',
  'detection_method': 'keyword_pattern',
  'trap_logic_description': 'Flag legal/executive costs tied to influencing '
                            'elections/legislation'},
 {'far_citation': '31.205-27',
  'category_name': 'Organization Costs',
  'detection_method': 'account_code',
  'trap_logic_description': 'Flag costs of organizing/reorganizing the corporate '
                            'structure (raising capital, mergers)'},
 {'far_citation': '31.205-32',
  'category_name': 'Pre-contract Costs',
  'detection_method': 'account_code',
  'trap_logic_description': 'Flag costs incurred before contract effective date '
                            'without written CO authorization'},
 {'far_citation': '31.205-38',
  'category_name': 'Selling Costs',
  'detection_method': 'account_code',
  'trap_logic_description': 'Flag and require the bid-and-proposal (B&P) vs. '
                            'selling-cost distinction rather than a single bucket'},
 {'far_citation': '31.205-43',
  'category_name': 'Trade, Business, or Technical Activity Costs',
  'detection_method': 'keyword_pattern',
  'trap_logic_description': 'Flag membership/subscription costs not tied to a '
                            'documented business purpose'},
 {'far_citation': '31.205-44',
  'category_name': 'Training & Conference Costs',
  'detection_method': 'keyword_pattern',
  'trap_logic_description': 'Require documented business purpose; flag '
                            'general-education tuition as distinct from job-related '
                            'training'},
 {'far_citation': '31.205-46',
  'category_name': 'Excess Travel Costs',
  'detection_method': 'rate_lookup',
  'trap_logic_description': 'Cross-reference against a per-diem reference table; flag '
                            'excess as unallowable'},
 {'far_citation': '31.205-47',
  'category_name': 'Costs of legal/other proceedings',
  'detection_method': 'account_code',
  'trap_logic_description': 'Claim-prosecution costs are generally unallowable, '
                            'REA-prep costs generally are not (REA-vs-Claim module)'},
 {'far_citation': '31.205-51',
  'category_name': 'Alcoholic Beverages',
  'detection_method': 'receipt_parsing',
  'trap_logic_description': 'Require line-item receipt detail; isolate alcohol into '
                            'unallowable code'}]


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('gsa_per_diem_rates',
    sa.Column('rate_id', sa.Integer(), nullable=False),
    sa.Column('location', sa.String(length=120), nullable=False),
    sa.Column('lodging_rate', govcon.db.types.SafeNumeric(precision=18, scale=2), nullable=False),
    sa.Column('meals_incidentals_rate', govcon.db.types.SafeNumeric(precision=18, scale=2), nullable=False),
    sa.Column('effective_start_date', sa.Date(), nullable=False),
    sa.Column('effective_end_date', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('rate_id', name=op.f('pk_gsa_per_diem_rates'))
    )
    op.create_table('contract_clause_exceptions',
    sa.Column('exception_id', sa.Integer(), nullable=False),
    sa.Column('contract_id', sa.Integer(), nullable=False),
    sa.Column('far_citation_overridden', sa.String(length=30), nullable=False),
    sa.Column('override_reason', sa.Text(), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.contract_id'], name=op.f('fk_contract_clause_exceptions_contract_id_contracts')),
    sa.PrimaryKeyConstraint('exception_id', name=op.f('pk_contract_clause_exceptions'))
    )
    # ### end Alembic commands ###

    categories = sa.table(
        "unallowable_cost_categories",
        sa.column("far_citation", sa.String),
        sa.column("category_name", sa.String),
        sa.column("trap_logic_description", sa.Text),
        sa.column("detection_method", sa.String),
    )
    op.bulk_insert(categories, SEED_CATEGORIES)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("DELETE FROM unallowable_cost_categories"))
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('contract_clause_exceptions')
    op.drop_table('gsa_per_diem_rates')
    # ### end Alembic commands ###
