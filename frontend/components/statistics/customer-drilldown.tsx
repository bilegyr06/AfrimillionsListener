// Paginated per-customer drill-down for a campaign on the Statistics surface.
//
// Each row is one opportunity (a login) with that customer's outcome: delivery
// status of the accepted SMS, conversion window (response), and their
// qualifying activity inside the attribution window (play count, attributed
// play amount, games played). The backend enriches the same customer list it
// serves to the campaign page, so numbers here always match the report above.

"use client";

import { useState } from "react";
import Pager from "@/components/pager";
import StatSection from "@/components/statistics/section";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  customerOutcomePresentation,
  deliveryStatusLabel,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatNumber,
  isCustomerConverted,
} from "@/lib/format";
import type { CampaignCustomersResponse } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export function CustomerDrilldown({ campaignId }: { campaignId: number }) {
  const [page, setPage] = useState(1);

  const customers = useQuery<CampaignCustomersResponse>(
    () =>
      apiGet(`/campaign/${campaignId}/customers`, {
        page,
        page_size: 50,
      }),
    [campaignId, page],
  );

  return (
    <StatSection
      title="Customer detail"
      aside={
        customers.data ? (
          <span className="muted small">{formatNumber(customers.data.total)} customers</span>
        ) : (
          " "
        )
      }
    >
      {customers.loading && !customers.data ? (
        <div className="section-body">
          <Loading text={"Loading customers..."} />
        </div>
      ) : customers.error ? (
        <div className="section-body">
          <ErrorBlock
            message="We couldn't load this campaign's customers."
            onRetry={customers.reload}
          />
        </div>
      ) : !customers.data || customers.data.items.length === 0 ? (
        <div className="section-body">
          <Empty text="No customers have been targeted in this campaign." />
        </div>
      ) : (
        <div className="section-body" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Phone</th>
                <th>Status</th>
                <th>SMS sent</th>
                <th>Delivery</th>
                <th>Response</th>
                <th className="num right">Qualifying plays</th>
                <th className="num right">Attributed amount</th>
                <th className="num right">Games</th>
              </tr>
            </thead>
            <tbody>
              {customers.data.items.map((row) => (
                <tr key={`${row.user_id}-${row.login_at}`}>
                  <td className="small">
                    <span className="strong">{row.user_id}</span>
                    {row.first_name ? <span className="muted">{`${row.first_name}`}</span> : null}
                  </td>
                  <td className="small muted">{row.phone_normalized ?? row.phone_raw ?? "\u2014"}</td>
                  <td className="small">{row.opportunity_status}</td>
                  <td className="small">{row.sent_at ? formatDateTime(row.sent_at) : "\u2014"}</td>
                  <td className="small">{deliveryStatusLabel(row.delivery_status)}</td>
                  <td className="small">
                    {row.intervention_status ? (
                      <>
                        <div>
                          {isCustomerConverted(row) ? customerOutcomePresentation(row).label : row.intervention_status}
                          {row.response_seconds != null
                            ? `${formatDuration(row.response_seconds)}`
                            : ""}
                        </div>
                        {row.play_at ? (
                          <div className="muted">
                            {formatDateTime(row.play_at)}
                          </div>
                        ) : null}
                      </>
                    ) : (
                      "\u2014"
                    )}
                  </td>
                  <td className="small num right">{formatNumber(row.qualifying_plays)}</td>
                  <td className="small num right">{formatMoney(row.attributed_amount)}</td>
                  <td className="small num right">{formatNumber(row.games_played)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={customers.data.page} pages={customers.data.pages} onChange={setPage} />
          <p className="hint" style={{ margin: "10px 20px 0" }}>
            Qualifying plays, attributed amount, and games are each customer’s
            activity inside the campaign attribution window; a customer is
            “Converted” on their first qualifying play after the Welcome SMS.
          </p>
        </div>
      )}
    </StatSection>
  );
}