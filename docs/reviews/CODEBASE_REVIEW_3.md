# Third Codebase Review

Date: 2026-11-13. Read-only review of the current code after the second review's repair cycle. This report supplements [CODEBASE_REVIEW_2.md](CODEBASE_REVIEW_2.md); it does not replace the earlier findings or certify that the system is bug-free or ready for live trading.

## Verdict

The second review's reported repairs are supported by the current source, focused regression coverage, and the full offline suite. Two additional order-ownership gaps remain in the fresh-entry path:

1. A broker position appearing after the last reconciliation can be combined with a new entry and booked as if the entire position came from that entry.
2. A coverage-matching same-symbol stop can be adopted without proving it belongs to the new entry, after which the bot may cancel it as its own stop.

Both findings are source-confirmed control-flow risks. They were not reproduced with new bespoke probes in this review. A read-only independent Safety Reviewer found the same two gaps; it found no basis to reopen the prior R2 findings and no additional concrete defect in the nearby cancellation path.

## Prior Review Verification

The current portfolio, data, session, action, research, and simulation code retains the stated repairs for the prior review findings. Existing regression tests exercise wrong-side close/monitor guards, stale position snapshots, cancellation fills, replacement ambiguity, durable stop terms, session and action ordering, stale entry handling, and stop timing. The second-review entry-recovery tests also verify that a zero-fill recovered intent cannot claim an unrelated broker position. That recovery guard does not cover the separate normal `open_position()` path described below.

No prior R2 finding is reopened by this review. Venue order-group behavior, replacement fill semantics, and provider completeness remain unverified integration assumptions, as already documented in the previous review and [cycle validation](CODEBASE_CYCLE_VALIDATION.md).

## Findings

### R3-01. High: Fresh entry can claim an unmanaged broker position

Locations: [bot/portfolio.py](bot/portfolio.py#L465), [bot/portfolio.py](bot/portfolio.py#L547), [bot/portfolio.py](bot/portfolio.py#L553), [bot/portfolio.py](bot/portfolio.py#L622). Evidence: source-confirmed; not separately reproduced in this review.

`open_position()` checks `entries_safe()`, whose unmanaged-position gate is the cached `unmanaged_symbols` set. It does not refresh broker positions before sizing or submitting. The broker position is fetched only after the new order fills; the method then sets local `qty` to the broker's entire same-side quantity while setting `entry_price` to the new order's average fill price.

Trigger: an external/manual same-symbol position appears after the last `reconcile()` and before strategy execution. The cached unmanaged set remains empty, and a new same-side entry fills. The combined broker quantity can then be recorded as wholly owned by the new intent, with an incorrect entry basis and risk estimate. Subsequent exits, P&L, and protection can apply to exposure the bot did not establish. An opposite-side surprise is detected later, but does not close this same-side ownership gap.

The scheduler's preceding monitor pass does not close the race: broker state can change after that pass, including while strategy data is being fetched. Existing recovered-entry tests cover `_recover_entries()`, not the live `open_position()` path.

Suggested regression: begin with no cached unmanaged symbols and a broker snapshot containing a same-side position for the target symbol immediately before entry. Assert no submission occurs unless ownership and existing quantity/basis are explicitly reconciled. Also cover a position appearing between the last monitor reconciliation and the entry call.

Recommended repair direction: refresh and validate same-symbol broker exposure immediately before durable entry intent/submission. Refuse to add to exposure whose ownership and accounting basis are not already established. If adding to an owned position is supported, preserve execution-level quantities and basis rather than assigning the aggregate broker position to the latest entry fill.

### R3-02. Medium: Entry can adopt a matching stop without establishing order ownership

Locations: [bot/portfolio.py](bot/portfolio.py#L363), [bot/portfolio.py](bot/portfolio.py#L565), [bot/portfolio.py](bot/portfolio.py#L626), [bot/portfolio.py](bot/portfolio.py#L275). Evidence: source-confirmed; not separately reproduced in this review.

After entry, `_attach_existing_stop()` scans all open same-symbol stop orders. `_check_stop()` validates status, symbol, side, quantity, duration, and price/limit coverage, but not that the order belongs to the new entry's OTO group or is otherwise known to this portfolio. A matching order is stored as `pos.stop_order_id`; its group, if any, is copied from the order-list response. Later `_cancel_orders_for()` treats the stored stop ID as owned and allows cancellation.

Trigger: an unrelated same-symbol stop happens to match the new managed position's required coverage, including remaining quantity and protection terms. The bot may count that order as its own protection and later cancel it during stop movement or exit handling. This can remove externally managed protection without authorization. The pre-existing guard against canceling untracked orders does not protect an order after `_attach_existing_stop()` has incorrectly promoted it to the tracked stop ID.

The tests cover refusing to cancel an unrecognized order and flattening/deduplicating grouped orders, but do not cover a matching foreign stop passing the attachment check. The explicit `stop_group_id` recorded for equity entries gives the code a relationship signal that should be checked rather than inferred from terms alone.

Suggested regression: provide a same-symbol stop with matching terms but no relationship to the entry's parent/group identity. Assert attachment fails closed and the foreign stop is never canceled as the managed position's stop. Also test a matching stop in a different order group.

Recommended repair direction: require verifiable ownership/group linkage before adopting an existing stop for a newly submitted entry. Keep external orders untracked; if their presence prevents safe protection conversion, quarantine and require reconciliation rather than claiming them.

## Simplification And Follow-Up

The smallest useful simplification is a single explicit entry preflight that returns a reconciled ownership result for the symbol and its open orders, rather than relying on a cached unmanaged-symbol set plus a later aggregate position read. It should distinguish confirmed bot-owned exposure, foreign exposure, and unknown exposure. The same ownership evidence should gate stop attachment. This is a focused ownership contract, not a recommendation for a broad portfolio rewrite.

No application or test changes were made as part of this review. Do not treat passing offline checks as evidence of venue semantics, profitability, or live readiness.

## Validation And Limits

- Python 3.11.9: `scripts/offline_tests.py` passed, 132 tests.
- `scripts/validate_ai_setup.py` passed.
- `powershell -NoProfile -File scripts/test_agent_guard.ps1` passed, 29 policy checks.
- `git diff --check` passed.
- Editor diagnostics reported no errors in `bot/portfolio.py`, `bot/main.py`, `bot/ledger.py`, `bot/data.py`, and `bot/backtest.py`.
- The independent Safety Reviewer inspected source and tests only; it did not run commands or make edits. No authenticated broker calls or operational state writes occurred in this review.

Offline mocks cannot establish broker order-group linkage behavior, atomicity of position reads around submissions, or every external/manual account mutation race. These remain reasons to keep the bot in paper/research mode pending controlled integration evidence and explicit reconciliation.

## Implementation Follow-Up

R3-01 and R3-02 have been implemented. Fresh entry now performs a same-symbol position/open-order preflight before
durable intent, and both fresh and recovered entries require broker quantity to be explained by their fills. Crypto
quantity reduction is bounded by the configured estimated taker fee; this does not establish settled fee semantics.
Recovered equity entries retain their parent order ID for OTO stop linkage. Stops without matching group evidence are
not adopted, and foreign orders remain untouched; configured-position adoption is quarantined when such orders prevent
safe protection.

Validation on Python 3.11.9: 10 focused ownership tests and 143 full offline tests passed. Repository setup validation,
29 PowerShell safety-policy checks, editor diagnostics, and `git diff --check` passed. The independent read-only Safety
Reviewer found no concrete remaining defect. No authenticated broker calls or operational state writes occurred.
Broker order-group reporting, crypto fee treatment, and non-atomic broker read/order timing remain unverified.