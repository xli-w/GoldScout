# Investment journal lifecycle

The separate GBP investment store is the authority for its own recorded
evidence; it is not the intelligence or monitoring store. It is an
append-only opening-evidence journal with explicit initialization, bounded reads,
hash-linked events and replay-verified committed states. Readers never migrate,
repair or create it. Only wire `schema_version=2` is supported; older journals
are explicitly rejected with no migration. Journals containing retired policy
review-acknowledgement events are refused even when marked version 2; no event
migration is provided. These checks detect inconsistency, not forgery or an
omitted later backup.

## Confirmed first-run creation

The [default wizard](investment.md#guided-first-run-setup) is an explicitly
confirmed local bootstrap path with no login. Workspace discovery, status and
preview are read-only and never initialize storage. The operator confirms the exact
profile and opening event before any workspace directory, staging file or
receipt is written. Drafts are not persisted before that confirmation.

Confirmed creation stages a new journal, appends the reviewed opening through
the existing accounting code, closes handles and validates replay before
no-overwrite publication. The journal's embedded profile is authoritative;
the separate profile JSON is a supporting copy. Journal publication, profile
export and setup receipts are separate filesystem operations, not one atomic
transaction. A failure after journal publication must be reported as committed
or partially completed, never rolled back, and must not delete that journal.

Recovery requires exact setup provenance and validated artifact identities.
Arbitrary empty databases are not proof of interrupted setup. A matching
completed creation is recognized without a second opening; a verifiably
wizard-created empty staging journal requires fresh evidence review before a
resumed opening. Unavailable, conflicting or invalid artifacts are preserved
with guidance rather than automatically removed or repaired.

The repository ignores `local-investment/`, including setup artifacts and SQLite
sidecars. This is not encryption, an access-control policy or a backup. Inspect
Windows inherited ACLs and cloud-sync settings; do not force-add private data.
Ignored data can be removed by Git cleanup commands. Keep separately protected
backups and evidence. Internal hashes do not establish origin against a party
able to rewrite local files, and publication is not a power-loss certification.

## Synthetic onboarding rehearsal

Before entering account evidence, rehearse the command flow with isolated,
synthetic fixtures:

```powershell
python scripts/offline_tests.py --pattern test_investment_onboarding.py
```

The regression creates a temporary journal, opens it with synthetic balances
and holdings, assigns roles, records dated marks and a cash observation,
compares complete synthetic account observations, generates monitoring output,
then backs up, restores and compares reports. It does not read operational
stores, contact providers or submit orders. Its fixture identifiers and values
are not account recommendations or data to import into a real journal.

## Backup and restore

The portfolio dashboard also offers an explicit verified backup download and a
non-destructive restore rehearsal. It packages the verified SQLite snapshot with
its receipt in an unencrypted ZIP; the browser controls the download destination.
A rehearsal verifies the selected archive and copies it to a fresh temporary
location before removing that temporary copy. It never installs or overwrites
the active journal. This user workflow does not select an off-device location,
encrypt the archive, prove that it is the latest copy, or certify power-loss
durability. Preserve protected copies and supporting setup evidence separately.

The following are explicit maintenance operations and require task-specific
operator approval. Stop dashboard recording, CLI imports and other writers
before operational maintenance. Choose existing, operator-controlled source
and destination directories with appropriate filesystem permissions, protected
against concurrent changes by any process, including the same user. Ensure
exclusive control of the destination namespace for the entire operation and
subsequent use. On Windows check inherited ACLs:
POSIX-style file modes do not establish private access there. These commands do
not defend against a hostile actor able to alter those directories. Use new
filenames, not an existing file, symlink or production store. Paths are examples,
not implicit defaults.

```powershell
python -m investment backup --database .\investment.sqlite3 --destination .\backups\investment-2026-11-17.sqlite3
python -m investment restore --database .\backups\investment-2026-11-17.sqlite3 --destination .\restore-check\investment.sqlite3
python -m investment report --database .\restore-check\investment.sqlite3
```

Both copy commands have the same mechanics: validate a pinned source snapshot,
copy with SQLite's backup API inside a fresh temporary directory under the
destination's parent, then validate the copy's schema, SQLite quick-check,
evidence chain, commit boundaries and replay against that snapshot. A
non-overwriting hard link publishes the verified copy; the destination
filesystem must support hard links. SQLite sidecar names and destinations
with existing sidecars are refused.
They print a receipt containing portfolio ID, schema version, revision, chain
head, commit-history hash, replay-state hash, event count, serialized-evidence
bytes and copied-file size alongside their limits. The source remains untouched,
and an existing destination is refused. Temporary cleanup failures are reported
explicitly and may leave an already verified destination. An interruption
requires inspecting the destination before retrying. A new copy is not a
migration or a repair of damaged evidence.
If a sidecar appears during publication, the command reports the conflict and
leaves the destination and sidecar for inspection, not automatic cleanup.
The receipt verifies the snapshot at publication, not power-loss durability:
the command does not synchronize the containing directory. Use an approved
backup/storage procedure to establish durable copies and rehearse recovery.

Keep receipts separately from backups and compare the selected backup against
the latest independently recorded receipt. A valid older copy can omit later
events; internal hashes alone cannot establish that it is the latest or genuine.
Inspect the restored report and reconcile any missing activity with original
evidence before an operator explicitly selects that copy for use. A restored
database is never installed over an existing store automatically. Preserve the
original, any SQLite sidecars and evidence during an incident; do not delete
or overwrite them to bypass a failure. Recovering a corrupted or incomplete
journal requires a separately approved, evidence-backed procedure.

Retain multiple verified copies in protected, access-controlled storage, including
an off-machine copy, and periodically rehearse a restore to a fresh path. Protect
the receipt, supporting evidence files and selected profile separately; the
database copy does not include source documents, awareness profiles or provider
records. The commands do not encrypt backups or implement retention. Never use
chat, hosted AI or an unapproved remote destination for private records.

## Capacity and evolution

Current code limits the journal to 10,000 events, 8 MiB of
serialized profile and event evidence, and a 32 MiB database. A bound can stop
new imports or reads before another is reached; there is no automatic rollover,
pruning, migration or capacity forecast. A copy receipt measures each
bound at the pinned snapshot; file size alone does not measure serialized
evidence. Monitor receipts and rehearse recovery before reaching a limit.
Imports check the database size before opening and recheck SQLite's allocated
page count inside the write transaction before commit. If the limit would be
exceeded, the entire append is rolled back and the prior revision remains
available. The pre-open check still refuses an already oversized database;
there is no automatic repair or pruning.
Do not delete events, edit the schema, raise
limits or restart from invented opening balances as a workaround. A future
archive/migration design needs explicit approval, evidence continuity, full
replay and independent review.
