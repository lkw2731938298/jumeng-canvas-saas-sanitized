/** 东八区日历日 → API 筛选 ISO（+08:00） */
export function cstDateStartToIso(date: string): string {
  return `${date}T00:00:00+08:00`;
}

function addCalendarDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00+08:00`);
  d.setDate(d.getDate() + days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** 所选东八区日期的次日 00:00（排他上界） */
export function cstDateEndExclusiveToIso(date: string): string {
  return `${addCalendarDays(date, 1)}T00:00:00+08:00`;
}

/** @deprecated 使用 cstDateStartToIso */
export const cstDateStartToUtcIso = cstDateStartToIso;

/** @deprecated 使用 cstDateEndExclusiveToIso */
export const cstDateEndExclusiveToUtcIso = cstDateEndExclusiveToIso;

export interface AdminJobFilterParams {
  status?: string;
  lane?: string;
  userId?: string;
  projectId?: string;
  jobId?: string;
  projectSearch?: string;
  createdFrom?: string;
  createdTo?: string;
  anomalyReason?: string;
  hasAdminAction?: boolean;
  page?: number;
  pageSize?: number;
}

export function buildAdminJobQuery(params: AdminJobFilterParams = {}): string {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.lane) search.set("lane", params.lane);
  if (params.userId) search.set("user_id", params.userId);
  if (params.projectId) search.set("project_id", params.projectId);
  if (params.jobId) search.set("job_id", params.jobId);
  if (params.projectSearch) search.set("project_search", params.projectSearch);
  if (params.createdFrom) search.set("created_from", params.createdFrom);
  if (params.createdTo) search.set("created_to", params.createdTo);
  if (params.anomalyReason) search.set("anomalyReason", params.anomalyReason);
  if (params.hasAdminAction) search.set("hasAdminAction", "true");
  if (params.page) search.set("page", String(params.page));
  if (params.pageSize) search.set("page_size", String(params.pageSize));
  return search.toString();
}
