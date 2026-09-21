"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { useRouter } from "next/navigation";
import { Loading } from "@/components/state-ui";

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();

  useEffect(() => {
    router.push("/windows");
  }, [router]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Campaign</h1>
      </div>
      <div className="section">
        <div className="section-body">
          <p className="muted" style={{ marginBottom: 12 }}>
            Campaign Runs are now managed within Campaign Windows. Redirecting to Campaign Windows...
          </p>
          <Loading text="Redirecting to Campaign Windows..." />
        </div>
      </div>
    </div>
  );
}