/** 与后端 ADMIN_AUTH_DISABLED 对齐；开发时可免登录 */
export const isAuthRelaxed =
  process.env.NEXT_PUBLIC_ADMIN_AUTH_DISABLED !== "false";
