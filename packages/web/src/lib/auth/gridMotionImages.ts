export const AUTH_GRID_ITEM_COUNT = 28;

/**
 * 由管理端上传的 CDN/OSS URL 铺满 4×7 网格。
 * 无配置时返回空数组（勿再用相对路径 /image/{uuid}：仓库无静态文件，
 * 且 c-admin Nginx 白名单外会 404）。
 */
export function buildAuthGridItems(sourceUrls: string[]): string[] {
  const cleaned = sourceUrls.map((url) => url.trim()).filter(Boolean);
  if (cleaned.length === 0) return [];
  return Array.from(
    { length: AUTH_GRID_ITEM_COUNT },
    (_, index) => cleaned[index % cleaned.length],
  );
}
