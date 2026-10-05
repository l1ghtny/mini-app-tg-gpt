# UX readiness release

Scope: Q06, Q09, Q07 and focused M09. Account data must distinguish loading/failure from successful empty results; document workflows use current shared allowance terminology. Files gain search/filter/sort, original upload names for new files, and owner-scoped attachment context.

Approved retention: unpinned files renew on explicit attachment or successful file search. Pinned files remain until unpinned/deleted. Existing overdue files receive one full current-plan period, once. Old filenames are retained when the true original was never stored; no guesswork strips user-created names.

The grace job runs before rollout and before a single production cleanup CronJob is enabled. Files without the migration marker remain usable and cannot be auto-purged. Expired migrated files cannot be attached/retrieved; provider deletion waits at least 30 minutes (or configured request lifetime plus 60 seconds). Manual deletion removes chat/project links; supplier 404 is idempotent and other failures remain queued for retry. Storage stays occupied until deletion completes.

Versions: backend 2.2.0 -> 2.3.0; frontend 2.2.0 -> 2.3.0. Both retain shared major 2.

What's New required. Draft only until verified production availability:

**Files are easier to find and manage**
Find files by name, filter and sort your library, and see which chats and projects use a file. New uploads keep their original names. Temporary files now show their real retention state. Attaching a file or searching it renews its storage period; pinned files stay until you unpin or delete them. Existing overdue files receive a full grace period. Account settings also distinguish loading and failures from missing passkeys or payment methods, with retry.

**Файлы проще находить и хранить**
Ищите файлы по имени, фильтруйте и сортируйте библиотеку и смотрите, к каким чатам и проектам прикреплён файл. Новые файлы сохраняют исходные имена. Для временных файлов теперь видно, истёк ли срок хранения. Когда вы прикрепляете файл или ищете по нему, срок хранения начинается заново. Закреплённые файлы хранятся, пока вы не открепите или не удалите их. Для старых файлов с истёкшим сроком предусмотрен полный дополнительный период хранения. При загрузке настроек аккаунта ключи доступа и способы оплаты больше не выглядят пропавшими; если загрузка не удалась, её можно повторить.

Phase two follows the first verified release: M02/M03 settled per-answer customer allowance and separate Luna/audio allowance visibility, then Q16 mobile settings categories. Supplier costs must remain internal and must not be confused with customer allowance.

Deployment prerequisite: the existing TeamCity service account can create Jobs and patch CronJobs but cannot create CronJobs. Bootstrap `cleanup-documents` once with `suspend: true` using the existing operator context before the first pipeline. CI then updates its image and explicitly enables it only after the grace job succeeds; no RBAC expansion is required.

Validation: 39 expanded backend checks passed before the final locking fix; all seven focused library/retention tests passed afterward, including the concurrent deletion regression. PostgreSQL schema upgrade/downgrade and single Alembic head passed. Frontend full run passed 559 checks; a typo in a newly added API test was corrected and both API regressions passed. Rendered EN/RU mobile (390px) and desktop (1280px) files, expired state, attachment context and account retry were observed. Existing unrelated LazySyntaxHighlighter TypeScript error remains outside this scope. Final built-in reviews and release proof are pending.

The bounded production cleanup job also grants one-time grace to unmarked files left by old pods during rollout. This closes the interval between the initial grace job and completion of production/beta deployment without ever expiring an unmarked file.

Built-in backend review fixes: response reads do not retain mutation locks while FastAPI runs background ingestion; failed deletion advances the retry timestamp to prevent queue starvation; explicit deletion retries keep running if automatic expiry is disabled. Nine final lifecycle/library regressions pass, and the full frontend run now passes all 560 tests.
