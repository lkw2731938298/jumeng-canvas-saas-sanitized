/** Human-readable display IDs for admin UI (BIGINT entity IDs). */

function asNumericId(id: string | number | undefined | null): number | null {
  if (id == null) return null;
  if (typeof id === "number" && id > 0) return id;
  const parsed = Number.parseInt(String(id), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function formatJobDisplayId(job: { id: string | number }): string {
  const id = asNumericId(job.id);
  return id != null ? `#${id}` : String(job.id);
}

export function formatJobDialogSubtitle(job: { id: string | number }): string {
  const id = asNumericId(job.id);
  return id != null ? `任务 #${id}` : `任务 ${job.id}`;
}

/** 用户编号展示：仅数字；兼容历史 U10032 → 10032。 */
export function normalizeUserNo(userNo?: string | null): string {
  const raw = userNo?.trim();
  if (!raw) return "";
  const match = /^U(\d+)$/i.exec(raw);
  return match ? match[1] : raw;
}

export function formatUserDisplayId(user: {
  userNo?: string | null;
  phone?: string | null;
  id: string | number;
}): string {
  const userNo = normalizeUserNo(user.userNo);
  if (userNo) return userNo;
  const id = asNumericId(user.id);
  if (id != null) return String(id);
  const phone = user.phone?.trim();
  if (phone) return phone;
  return String(user.id);
}

export function formatProjectDisplayId(project: {
  projectNo?: string | null;
  id: string | number;
}): string {
  const projectNo = project.projectNo?.trim();
  if (projectNo) return projectNo;
  const id = asNumericId(project.id);
  return id != null ? `PRJ-${id}` : String(project.id);
}

export function shortenUuid(id: string | number): string {
  if (id == null || id === "") return "—";
  const numeric = asNumericId(id);
  if (numeric != null) return String(numeric);
  const text = String(id);
  return text.length > 8 ? `${text.slice(0, 8)}…` : text;
}
