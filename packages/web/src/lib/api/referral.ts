/** 用户邀请 API */
import { apiFetch } from "@/lib/api/client";

export type InviteCampaignPublic = {
  id: string;
  title: string;
  description: string;
  coverUrl: string;
  inviterRewardAmount: number;
  inviteeRewardAmount: number;
  rewardCreditType: string;
  rewardValidDays: number;
  inviterRewardOn: string;
  maxRewardsPerInviter?: number | null;
  totalInviteQuota?: number | null;
  remainingQuota?: number | null;
  startsAt?: string | null;
  endsAt?: string | null;
  status: string;
};

export type ReferralInviteItem = {
  inviteeUserId: string;
  displayName: string;
  phoneMasked: string;
  registeredAt?: string | null;
  inviteeRewardStatus: string;
  inviterRewardStatus: string;
  statusLabel: string;
  createdAt?: string | null;
};

export type ReferralMe = {
  inviteCode: string;
  shareUrl: string;
  inviteCount: number;
  rewardedCredits: number;
  campaign: InviteCampaignPublic | null;
  invites: ReferralInviteItem[];
};

export function getReferralMe() {
  return apiFetch<ReferralMe>("/api/v1/referral/me");
}

export function getReferralCampaign() {
  return apiFetch<{ campaign: InviteCampaignPublic | null }>("/api/v1/referral/campaign");
}
