# Additive schema for the private allowance beta

This release adds the four shared-allowance accounting tables at Alembic head
`xt7b8c9d0e1f` through the production schema-owner pipeline. No existing user,
tier, subscription, model catalog, pricing, balance or request row is changed.
The migration is repeat-safe. Production application behavior stays unchanged.

The paired beta application will verify this shared head before deployment and
use separately scoped beta grants for the existing authorized beta cohort.
Private-tier amounts and a later production application cutover remain subject
to the owner's agreement. Roll back beta behavior with its feature flag; never
downgrade the accounting schema after it contains usage.
