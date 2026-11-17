# Decision: Guided investment bootstrap at default startup

Status: accepted and implemented

Date: 2026-11-17

## Context

The manual investment path required selecting a source, writing profile JSON,
initializing a journal and only then opening browser recording. The operator
requested guided first-run setup as normal startup, using the repository's
Git-ignored local workspace. Existing no-reader-initialization and explicit
recording boundaries must not become automatic creation or repair.

## Decision

Source-checkout startup without a source flag selects `local-investment` beneath
that checkout. Installed use outside a checkout requires `--workspace`.
Explicit synthetic demo and investment-journal selections remain distinct.
An absent/empty workspace shows a local wizard; an existing journal
remains accounting authority. Invalid/unavailable data never triggers reset.

A bounded local session and preview support creation of the reviewed
profile/opening after a separate confirmation. There is no user login;
same-origin and CSRF checks are browser request defenses, not authentication.
Drafts stay in memory before confirmation. Creation stages and replay-validates
a new opened journal before no-overwrite publication, without changing its
schema or accounting rules. Setup never enables subsequent recording, providers,
policy approval or trading.

## Consequences

Normal onboarding needs no hand-written JSON or separate init command.
Accounts/products evolve only under append-only journal rules. Unknown prices and late
evidence can leave valuation unavailable. Reloading loses unsubmitted drafts.

The journal, profile copy and setup receipt cannot be published in a single
filesystem transaction. Report committed/partial outcomes accurately and allow
only provenance-bound recovery. Never erase a published journal
because a later profile export or receipt failed. Operator-controlled storage,
Windows ACLs and protected external backups remain necessary; Git ignore is
not privacy enforcement or protection from deletion.

## Verification

Temporary-store tests cover no-write reads/previews, exact opening replay,
origin/CSRF gates, conflicts, retries, interruption and separate recording
opt-in. Publication followed by unavailable verification reports committed
evidence truthfully and can recover without changing journal bytes.
Desktop/mobile wizard acceptance, actual temporary-workspace creation and
read-only restart, full offline regression and frontend/wheel checks passed.
Independent safety review findings were addressed. No live broker or provider
validation was performed.
