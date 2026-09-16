/** 开发阶段默认关闭管理后台权限校验；上线时设置 NEXT_PUBLIC_ADMIN_AUTH_DISABLED=false */
export const isAdminAuthDisabled =
  process.env.NEXT_PUBLIC_ADMIN_AUTH_DISABLED !== "false";
