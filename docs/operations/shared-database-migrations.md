# Shared production and beta database migrations

Production and beta intentionally use the same PostgreSQL database so testers keep
their account, conversation, and Work history across both application channels.
That makes the database schema a shared compatibility contract rather than a
branch-local implementation detail.

## Authority and ordering

1. The backend `master` branch is the only migration writer for the shared database.
2. A migration may be developed with a beta feature, but its migration-only commit
   must reach `master` before schema-dependent beta code is deployed.
3. The production release pipeline builds the `master` backend image and runs
   `alembic upgrade head` once against the shared database.
4. The beta pipeline never upgrades the database. Before deployment it runs the
   beta backend image with `alembic current --check-heads`. A mismatch blocks beta
   deployment until the migration has been promoted and applied through production.
5. Migration files are immutable after application. Both branches must retain every
   revision that the shared database may report as current.

## Compatibility rules

- Prefer additive or relaxing changes that work with both the current production
  backend and the beta backend.
- Use an expand/migrate/contract sequence for renames, type changes, and removals.
  Do not remove the old shape until both channels have stopped using it.
- Do not run `alembic upgrade` manually from a beta image or beta checkout.
- Do not automatically downgrade the shared database when rolling application code
  back. Roll code forward or deploy a new compatible migration.

## Recovering a divergent history

If the shared database reports a revision that `master` cannot locate:

1. Stop the production deployment before changing workloads.
2. Copy the exact already-applied revision file into `master`; never rewrite its ID,
   parent, or operations.
3. Include any forward-compatible descendant that current beta code requires.
4. Verify one Alembic head locally and render/test the upgrade path.
5. Run the production release pipeline. It reconciles the shared schema before
   deploying either production workload.
6. Run beta after production succeeds; its read-only head check must pass.

The August 2026 reconciliation restores the shared lineage
`vc1d2e3f4a5b -> xe2f3a4b5c6d -> xf3a4b5c6d7e`. The database had already
applied `xe2f3a4b5c6d`; production therefore applies only the forward-compatible
artifact constraint relaxation in `xf3a4b5c6d7e`.
