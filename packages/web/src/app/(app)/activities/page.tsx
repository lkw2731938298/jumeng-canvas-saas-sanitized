"use client";

import { useQuery } from "@tanstack/react-query";
import { Clock, Gift, Trophy } from "lucide-react";
import { HuabuPublicShell } from "@/components/huabu/HuabuPublicShell";
import { CreditActivitiesPanel } from "@/components/user/CreditActivitiesPanel";
import { getSiteActivities, isOngoingSiteActivity, type SiteCreditActivity } from "@/lib/api/site";

function CampaignCard({
  activity,
  past = false,
}: {
  activity: SiteCreditActivity;
  past?: boolean;
}) {
  return (
    <article className={`campaign-card ${past ? "campaign-card-sm" : "campaign-card-lg"}`}>
      <div className="campaign-media">
        {activity.coverUrl ? (
          <img src={activity.coverUrl} alt="" />
        ) : (
          <div
            className="absolute inset-0"
            style={{ background: "linear-gradient(135deg, #312e81, #7c3aed 55%, #db2777)" }}
          />
        )}
        <div className="campaign-media-mask" aria-hidden="true" />
        <span className={`campaign-tag ${past ? "campaign-tag-dark" : "campaign-tag-purple"}`}>
          {past ? <Trophy size={12} strokeWidth={2} /> : <Clock size={12} strokeWidth={2.2} />}
          {past ? "已结束" : "进行中"}
        </span>
        <div className="campaign-banner-copy">
          <strong>{activity.title}</strong>
          <span>{activity.description || `领取 ${activity.amount} 算力`}</span>
        </div>
      </div>
      <div className="campaign-meta">
        <h3>{activity.title}</h3>
        <div className="campaign-badges">
          <span className="campaign-badge">
            <Trophy size={12} strokeWidth={2} />
            {activity.amount} 算力
          </span>
          <span className="campaign-badge">{activity.claimedCount} 人已领取</span>
        </div>
      </div>
    </article>
  );
}

/** huabu 活动页：后台算力活动驱动进行中/往期，下方保留可领取面板 */
export default function ActivitiesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["site", "activities"],
    queryFn: getSiteActivities,
    staleTime: 30_000,
  });
  const items = data?.items ?? [];
  const ongoing = items.filter(isOngoingSiteActivity);
  const past = items.filter((item) => !ongoing.some((active) => active.id === item.id));

  return (
    <HuabuPublicShell>
      <section className="activities-page" aria-label="活动">
        <h1 className="activities-title">活动</h1>

        <div className="activities-block">
          <h2 className="activities-section-title">进行中</h2>
          <div className="campaign-grid campaign-grid-ongoing">
            {ongoing.map((item) => (
              <CampaignCard activity={item} key={item.id} />
            ))}
          </div>
          {!isLoading && ongoing.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无进行中的活动</p>
          ) : null}
        </div>

        <div className="activities-block">
          <h2 className="activities-section-title">往期</h2>
          <div className="campaign-grid campaign-grid-past">
            {past.map((item) => (
              <CampaignCard activity={item} past key={item.id} />
            ))}
          </div>
          {!isLoading && past.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无往期活动</p>
          ) : null}
        </div>

        <div className="activities-block">
          <h2 className="activities-section-title">
            <Gift size={18} strokeWidth={2} style={{ verticalAlign: "-3px", marginRight: 8 }} />
            算力活动
          </h2>
          <CreditActivitiesPanel />
        </div>
      </section>
    </HuabuPublicShell>
  );
}
