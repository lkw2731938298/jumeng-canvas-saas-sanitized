export interface AdminUser {
  id: string;
  sourceUserId: string;
  phone?: string;
  displayName: string;
  avatarUrl?: string;
  role: string;
  isSuperAdmin?: boolean;
  permissions?: string[];
  canManagePermissions?: boolean;
}

export interface AdminUserListItem {
  id: string;
  userNo?: string;
  displayName: string;
  phone?: string;
  role: string;
  isSuperAdmin?: boolean;
  isActive: boolean;
  balance: number;
  projectCount: number;
  jobCount: number;
  createdAt: string;
  lastLoginAt?: string;
}

export interface AdminUserList {
  items: AdminUserListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminUserRecentJob {
  id: string;
  status: string;
  jobType: string;
  lane: string;
  model?: string;
  creditCost: number;
  createdAt: string;
}

export interface AdminAuthEventBrief {
  id: string;
  action: string;
  result: string;
  ip: string;
  createdAt: string;
}

export interface AdminUserDetail {
  id: string;
  userNo?: string;
  sourceUserId: string;
  displayName: string;
  phone?: string;
  role: string;
  isSuperAdmin?: boolean;
  permissions?: string[];
  isActive: boolean;
  avatarUrl?: string;
  balance: number;
  projectCount: number;
  jobCount: number;
  workflowCount: number;
  sessionCount: number;
  jobsByStatus: Record<string, number>;
  createdAt: string;
  lastLoginAt?: string;
  lastLoginIp?: string;
  recentAuthEvents: AdminAuthEventBrief[];
  recentJobs: AdminUserRecentJob[];
}

export interface AdminAuthSettings {
  registrationEnabled: boolean;
  envRegistrationDefault: boolean;
  updatedAt: string;
}

export interface AdminRegisterBonusSettings {
  enabled: boolean;
  amount: number;
  updatedAt: string;
}

export interface AdminRegisterBonusGrant {
  id: string;
  userId: string;
  displayName: string;
  phone: string;
  phoneMasked: string;
  amount: number;
  status: string;
  createdAt: string | null;
  grantedAt: string | null;
}

export interface AdminRegisterBonusGrantList {
  items: AdminRegisterBonusGrant[];
  total: number;
  page: number;
  pageSize: number;
}

export interface LegalDocumentMeta {
  configured: boolean;
  title: string;
  sourceFormat?: string;
  updatedAt?: string | null;
}

export interface AdminLegalDocuments {
  userAgreement: LegalDocumentMeta;
  privacyPolicy: LegalDocumentMeta;
}

export interface AdminHomepageSettings {
  authGridImages: AuthGridImageItem[];
  loginModalOssKey?: string;
  loginModalImageUrl?: string;
  legalDocuments?: AdminLegalDocuments;
  updatedAt: string;
}

export interface AuthGridImageItem {
  id: string;
  ossKey: string;
  imageUrl: string;
  sortOrder: number;
}

export interface SiteHomepageConfig {
  authGridImageUrls: string[];
  loginModalImageUrl?: string;
}

/** 发现页运营配置（管理端读写 / 公开读取） */
export interface DiscoverHeroVideo {
  id: string;
  url: string;
  sortOrder: number;
}

export interface DiscoverPromptSuggestion {
  id: string;
  label: string;
  imageUrl: string;
  sortOrder: number;
}

export interface DiscoverStoryFeature {
  label: string;
  imageUrl: string;
  href: string;
}

export interface DiscoverHighlightFeature {
  id: string;
  label: string;
  /** 迷你技能卡小文案（副标题），后台可配 */
  subtitle?: string;
  tone: string;
  isNew: boolean;
  href: string;
  sortOrder: number;
}

export interface DiscoverSkillMachine {
  label: string;
  href: string;
  imageUrl: string;
}

/** 发现页右上角运营弹窗（后台可关；每次进入发现页再出现） */
export interface DiscoverCornerPopup {
  enabled: boolean;
  text: string;
  imageUrl: string;
  /** 图片铺满弹窗（cover）；关闭则为适应比例 contain */
  imageFill: boolean;
  /** 弹窗比例：16:9 横版 / 9:16 竖版 */
  aspectRatio: "16:9" | "9:16";
  showWhenLoggedIn: boolean;
  showWhenLoggedOut: boolean;
}

export interface DiscoverGalleryItem {
  id: string;
  title: string;
  author: string;
  avatarUrl: string;
  caption: string;
  imageUrl: string;
  category: string;
  scope: string;
  sortOrder: number;
  isActive: boolean;
}

export interface DiscoverPageSettings {
  heroTitle: string;
  heroEm: string;
  creationPlaceholder: string;
  heroVideos: DiscoverHeroVideo[];
  promptSuggestions: DiscoverPromptSuggestion[];
  storyFeature: DiscoverStoryFeature;
  highlightFeatures: DiscoverHighlightFeature[];
  skillMachine: DiscoverSkillMachine;
  galleryFilters: string[];
  galleryItems: DiscoverGalleryItem[];
  cornerPopup?: DiscoverCornerPopup;
  updatedAt?: string;
}

/** 首页 Footer 运营配置（关于我们跳转 / 联系我们二维码 / 社交链接） */
export interface SiteFooterQrCode {
  id: string;
  label: string;
  imageUrl: string;
  sortOrder: number;
}

export interface SiteFooterAboutUs {
  label: string;
  href: string;
}

export interface SiteFooterContactUs {
  label: string;
  qrCodes: SiteFooterQrCode[];
}

export interface SiteFooterSocialLink {
  id: string;
  label: string;
  href: string;
  sortOrder: number;
}

/** 发现页底部友情链接 */
export interface SiteFooterFriendLink {
  id: string;
  label: string;
  href: string;
  sortOrder: number;
}

export interface SiteFooterSettings {
  brandText: string;
  copyright: string;
  tagline: string;
  /** 顶栏 / 侧栏「教程」打开的链接（https 或站内路径） */
  helpUrl: string;
  aboutUs: SiteFooterAboutUs;
  contactUs: SiteFooterContactUs;
  socialLinks: SiteFooterSocialLink[];
  /** 友情链接；无有效项时前端不展示整行 */
  friendLinks: SiteFooterFriendLink[];
  /** 友链前缀文案，默认「友情链接」 */
  friendLinksLabel: string;
  /** ICP 备案号；空则不展示。由部署方自行填写，开源副本不预置真实备案号 */
  icpNumber: string;
  /** 备案查询链接，默认工信部备案系统 */
  icpHref: string;
  updatedAt?: string;
}

export interface AdminAuthEvent {
  id: string;
  userId?: string;
  phone?: string;
  action: string;
  result: string;
  ip: string;
  userAgent?: string;
  detail?: Record<string, unknown>;
  createdAt: string;
}

export interface AdminAuthEventList {
  items: AdminAuthEvent[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminCreditTransaction {
  id: string;
  userId: string;
  userNo?: string;
  userDisplayName?: string;
  userPhone?: string;
  operatorId?: string;
  delta: number;
  balanceAfter: number;
  source: string;
  sourceLabel?: string;
  creditType?: string;
  modelName?: string;
  modelDisplayName?: string;
  jobId?: string;
  reason?: string;
  createdAt: string;
  expiresAt?: string | null;
  expiresLabel?: string | null;
}

export interface AdminCreditTransactionList {
  items: AdminCreditTransaction[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminRechargeOrder {
  id: string;
  userId: string;
  userDisplayName?: string;
  userPhone?: string;
  userNo?: string;
  outTradeNo: string;
  orderType: string;
  orderTypeLabel: string;
  paymentChannel: string;
  paymentChannelLabel: string;
  amount: number;
  payAmountFen: number;
  planId?: string | null;
  planName?: string | null;
  alipayTradeNo?: string | null;
  status: string;
  statusLabel: string;
  createdAt: string;
  completedAt?: string | null;
  /** 支付单过期时间（充值创建起 24h）；会员已完成时为账期结束 */
  expiresAt?: string | null;
  expiresLabel?: string | null;
}

export interface AdminRechargeOrderList {
  items: AdminRechargeOrder[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminRechargeTierItem {
  creditsAmount: number;
  payAmountFen: number;
  sortOrder: number;
  isActive: boolean;
}

export interface AdminRechargeTiersConfig {
  items: AdminRechargeTierItem[];
  updatedAt: string;
}

export interface AdminUserCredits {
  userId: string;
  displayName: string;
  phone?: string;
  balance: number;
}

/** 运营仪表盘按日趋势点 */
export interface AdminStatsDailyPoint {
  date: string;
  count: number;
}

export interface AdminStats {
  userCount: number;
  projectCount: number;
  workflowCount: number;
  jobs: Record<string, number>;
  usersToday: number;
  usersWeek: number;
  usersMonth: number;
  projectsToday: number;
  projectsWeek: number;
  projectsMonth: number;
  jobsToday: number;
  jobsWeek: number;
  jobsMonth: number;
  userTrend: AdminStatsDailyPoint[];
  projectTrend: AdminStatsDailyPoint[];
  jobTrend: AdminStatsDailyPoint[];
  jobsByLane: Record<string, number>;
  jobsSucceededWeek: number;
  jobsFailedWeek: number;
  creditsRemainingTotal: number;
  creditsByType: Record<string, number>;
  creditsConsumedToday: number;
  creditsConsumedWeek: number;
  creditsConsumedMonth: number;
  creditsGrantedToday: number;
  creditsGrantedWeek: number;
  creditsGrantedMonth: number;
  creditConsumeTrend: AdminStatsDailyPoint[];
  creditGrantTrend: AdminStatsDailyPoint[];
}

export interface AdminJob {
  id: string;
  userId: string;
  userNo?: string;
  userDisplayName?: string;
  userPhone?: string;
  projectId?: string;
  projectNo?: string;
  projectTitle?: string;
  workflowId?: string;
  nodeId?: string;
  jobType: string;
  scene?: string;
  lane: string;
  status: string;
  provider?: string;
  requestId?: string;
  providerRequestId?: string;
  providerTaskId?: string;
  providerJobId?: string;
  processingId?: string;
  upstreamJobId?: string;
  assetId?: string;
  model?: string;
  modelDisplayName?: string;
  /** primary | fallback — 实际命中的上游通道 */
  upstreamChannel?: string | null;
  upstreamChannelModel?: string | null;
  upstreamChannelProvider?: string | null;
  creditCost: number;
  upstreamCreditCost?: number | null;
  creditStatus?: string;
  pricingVersion?: number;
  creditBreakdown?: Array<{
    groupId: string;
    itemId: string;
    label: string;
    cost: number;
  }>;
  errorMessage?: string;
  anomalyReason?: string;
  anomalyDetectedAt?: string;
  anomalyDetail?: string;
  lastAdminAction?: string;
  lastAdminActionAt?: string;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  durationSeconds?: number | null;
  executionSeconds?: number | null;
  actualDeductedCredits?: number;
  platformQuotedCredits?: number;
}

export interface AdminJobAsset {
  id: string;
  projectId: string;
  title: string;
  category: string;
  fileUrl?: string;
  thumbnailUrl?: string;
  fileType?: string;
  fileSize?: number;
  createdAt?: string;
}

export interface AdminJobAction {
  id: string;
  jobId: string;
  operatorId: string;
  action: string;
  beforeStatus?: string;
  afterStatus?: string;
  beforeCreditStatus?: string;
  afterCreditStatus?: string;
  note: string;
  payload?: Record<string, unknown>;
  createdAt: string;
}

export interface AdminJobActionResult {
  ok: boolean;
  action: string;
  jobId: string;
  status: string;
  creditStatus?: string;
  assetId?: string;
  errorMessage?: string;
  anomalyReason?: string;
  auditId: string;
  message?: string;
  upstreamState?: string;
  unchanged?: boolean;
}

export interface AdminJobDetail extends AdminJob {
  inputParams: Record<string, unknown>;
  outputAssets?: Record<string, unknown>[];
  resultText?: string;
  startedAt?: string;
  traceJson?: Record<string, unknown>;
  projectAsset?: AdminJobAsset;
  adminActions?: AdminJobAction[];
}

export interface AdminJobList {
  items: AdminJob[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminModel {
  id: string;
  name: string;
  displayName: string;
  provider: string;
  modelType: string;
  category: string;
  description?: string;
  isAvailable: boolean;
  sortOrder: number;
  isConfigured?: boolean;
  isImplemented?: boolean;
  providerGroup?: string;
  parameters?: Record<string, unknown>;
  createdAt: string;
}

export interface AdminModelTestResult {
  ok: boolean;
  message: string;
  isConfigured: boolean;
  isImplemented: boolean;
}

export interface AdminProviderUiField {
  key: string;
  label: string;
  required?: boolean;
  secret?: boolean;
  advanced?: boolean;
  placeholder?: string;
  hint?: string;
}

export interface AdminProviderUiProfile {
  key: string;
  label: string;
  hint?: string;
}

export interface AdminProviderUi {
  docsUrl?: string;
  summary?: string;
  fields?: AdminProviderUiField[];
  profiles?: AdminProviderUiProfile[];
}

export interface AdminModelProvider {
  code: string;
  displayName: string;
  defaultApiBase?: string;
  isEnabled: boolean;
  sortOrder: number;
  hasCredential: boolean;
  credentialProfiles: string[];
  apiKeyHint?: string | null;
  modelCount?: number;
  status?: "unconfigured" | "configured" | "tested_ok" | "tested_fail" | string;
  lastTestOk?: boolean | null;
  lastTestedAt?: string | null;
  lastTestMessage?: string | null;
  defaultCredentialUpdatedAt?: string | null;
  ui?: AdminProviderUi | null;
}

export interface AdminProviderCredential {
  profileKey: string;
  profileLabel?: string | null;
  apiKeyHint?: string;
  endpointIdMasked: string;
  apiBase?: string;
  isActive: boolean;
  updatedAt?: string;
  updatedBy?: number | null;
  lastTestOk?: boolean | null;
  lastTestedAt?: string | null;
  lastTestMessage?: string | null;
}

export interface AdminProviderCredentialTestResult {
  ok: boolean;
  message: string;
  apiBase?: string;
  hasEndpointId?: boolean;
  testedAt?: string;
}

export interface AdminProviderReferencedModel {
  id: string;
  name: string;
  displayName: string;
  category: string;
  isAvailable: boolean;
}

export interface AdminProject {
  id: string;
  projectNo: string;
  title: string;
  ownerId: string;
  ownerDisplayName: string;
  ownerPhone?: string;
  createdAt: string;
  updatedAt: string;
}

export interface AdminProjectList {
  items: AdminProject[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminProjectNode {
  id: string;
  type: string;
  label: string;
}

export interface AdminProjectAssetStats {
  image: number;
  video: number;
  audio: number;
  total: number;
}

export interface AdminProjectDetail {
  id: string;
  projectNo: string;
  title: string;
  description?: string;
  ownerId: string;
  ownerDisplayName: string;
  ownerPhone?: string;
  createdAt: string;
  updatedAt: string;
  workflowCount: number;
  latestWorkflowId?: string;
  nodes: AdminProjectNode[];
  assetStats: AdminProjectAssetStats;
}

export interface AdminGenerationLog {
  id: string;
  jobId?: string;
  phase: "submit" | "response";
  submitSource?: "manual" | "dedupe" | "auto";
  lane?: string;
  category: string;
  projectId?: string;
  projectTitle?: string;
  nodeId?: string;
  workflowId?: string;
  actorUserId?: string;
  actorDisplayName?: string;
  billingUserId?: string;
  billingDisplayName?: string;
  model: string;
  outcome: "success" | "failure";
  requestPayload?: Record<string, unknown>;
  responsePayload?: Record<string, unknown>;
  errorMessage?: string;
  idempotencyKey?: string;
  dedupeKey?: string;
  createdAt: string;
}

export interface AdminGenerationLogList {
  items: AdminGenerationLog[];
  total: number;
  page: number;
  pageSize: number;
}
