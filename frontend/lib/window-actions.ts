// Text summaries for run operator actions (evaluate + dispatch). The counts
// come straight from the backend responses; SMS execution counts (sent, failed,
// deferred, cooldown/cap skips) are presented separately from campaign
// effectiveness so the two never blur together.

import { formatNumber } from "@/lib/format";
import type { DispatchResult, EvaluateRunResponse } from "@/lib/types";

export function evaluateResultText(res: EvaluateRunResponse): string {
  return (
    `Evaluated ${formatNumber(res.candidates)} candidates. Added ${formatNumber(res.audience.added)} ` +
    `(${formatNumber(res.audience.campaign)} Campaign / ${formatNumber(res.audience.control)} Control); ` +
    `${formatNumber(res.audience.existing)} already present; ${formatNumber(res.audience.invalid_phone)} invalid phones. ` +
    `Eligible count is now ${res.eligible_count !== null ? formatNumber(res.eligible_count) : "unchanged"}.`
  );
}

export function dispatchResultText(res: DispatchResult): string {
  return (
    `Dispatch pass complete \u2014 targeted ${formatNumber(res.target)}. Sent ${formatNumber(res.sent)}, ` +
    `failed ${formatNumber(res.failed)}, deferred ${formatNumber(res.deferred)}; ${formatNumber(res.already_sent)} ` +
    `already sent; ${formatNumber(res.skipped_cooldown)} skipped (cooldown), ${formatNumber(res.skipped_cap)} skipped (limit).`
  );
}