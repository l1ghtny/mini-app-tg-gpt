# Next launch scope after the current UX batches

This is a focused reassessment from current source and rendered screens, not an assertion that every item in the original 60-point review is unresolved. Do not reimplement already shipped sharing, exact/semantic search, document prerequisites or partial-source ordering just because old review text remains.

## Release state

Q06/Q09/Q07/focused M09 are deployed: backend2.3.1/frontend2.3.0, production #125 and beta #231. Cleanup has one production scheduler against the shared database; the one-time grace and private-source deletion paths are verified. M02/M03/Q16 are the new paired 2.4.0 PRs, not yet deployed. Source/UI checks for the second batch use a local synthetic API; they are not live settlement proof.

## Recommended next scope

1. **Q05: mobile action targets.** The current real MessageBubble still renders Copy and Share at 26×26px at 390px width (measured in the in-app browser). Enlarge hit areas while retaining small icons; review Copy/Edit/Regenerate/Share/attachment actions together, long labels and large text. This is a reproduced usability gap, not a device-specific hypothesis.
2. **M15 + focused M13: acceptance on actual phones and interrupted work.** Responsive desktop-browser checks cannot establish iOS/Android Telegram WebView keyboard, safe areas, gallery, downloads or background/reconnect behavior. Run a disposable-account matrix: start a text/document task, background it, return/resume, retry a failure, verify one billable request and the correct final usage. Existing streaming and no-hold/tool-loop foundations are implemented; this asks for proof and corrections of observed failures, not a replacement architecture.
3. **M04/L09: coherent account return and recovery.** Current auth already rejects removing the last linked identity and shows passkey/device metadata. The remaining launch gate is a fresh-user journey across web and Telegram, identity conflict, another-device return and safe recovery, including a comprehensible route from the account screen. Treat missing acceptance evidence separately from a proven auth defect.
4. **M05: export/deletion decision flow.** Current settings download raw JSON and use a native typed DELETE prompt. Give users a reviewable export inventory and precise deletion consequences/timing grounded in actual backend cleanup/retained accounting. Test a disposable account and cancellation; do not delete a real owner's account to establish acceptance.
5. **Billing and referrals, when opening the public paid product.** The real allowance catalog still returns purchase_available=false; the current plan view says purchases are unavailable. That is deliberate current behavior, not a failed checkout. Before paid launch, prove checkout/renewal/receipt/refund/reconciliation and clear pricing together. Referral reward promises need once-only attribution/reversal and a visible trustworthy balance. For an invitation-only free launch, these are a separate product decision rather than an automatic blocker.

## Useful refinements after the launch gates

Q10 attachment labels, Q11 allowed tools versus a one-answer requirement, Q12 complete image-task estimate, Q17 one sending shortcut with derived newline behavior, and short contextual help (Q18) remain reasonable follow-ups. Validate current interactions before implementation: source and UI already changed substantially since the original review. Large artifact/document-workspace and branching projects can wait for user-task evidence.

The smallest practical next batch is Q05 plus a recorded real-device/recovery acceptance pass. That improves everyday use and identifies genuine launch blockers without widening into another redesign.
