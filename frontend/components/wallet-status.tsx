// Wallet balance presentation. The live balance from /stats/balance is the
// primary value; a failed live request does not masquerade as current — it is
// clearly labelled as an error with the last known value, which preserves
// freshness/error/loading semantics for the operator.

import { formatMoney, formatTime } from "@/lib/format";
import type { WalletSnapshot } from "@/lib/types";

interface Props {
  balance: { balance: number; currency: string } | null;
  balanceAt: number | null;
  nowTs: number;
  error: string | null;
  historical: WalletSnapshot | null;
}

export default function WalletStatus({ balance, balanceAt, nowTs, error, historical }: Props) {
  return (
    <>
      {balance && (
        <div className="balance-row">
          Live balance{" "}
          <strong>{formatMoney(balance.balance, balance.currency)}</strong>
          {balanceAt
            ? ` · Updated ${Math.max(0, Math.round((nowTs - balanceAt) / 1000))}s ago · as of ${formatTime(
                new Date(balanceAt).toISOString(),
              )}`
            : " · checking…"}
        </div>
      )}
      {error && (
        <p className="balance-error">
          Live balance unavailable ({error}); showing last known value — retries automatically.
        </p>
      )}
      {historical && (
        <div className="balance-row">
          Balance{" "}
          <strong>{formatMoney(historical.balance, historical.currency)}</strong> · recorded {formatTime(historical.fetched_at)}
        </div>
      )}
    </>
  );
}