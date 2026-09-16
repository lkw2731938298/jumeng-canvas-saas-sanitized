# 安全说明

## 支持范围

请只针对本 GitHub 仓库的**当前默认分支**报告安全问题。

## 如何报告

请使用 GitHub 的 **Private vulnerability reporting**（仓库 Security → Advisories → Report a vulnerability），不要在公开 Issue 里贴利用细节、密钥或用户数据。

若无法使用私密报告，可在 Issue 中只描述影响面，不要附带 PoC 与凭证。

## 部署方须知

自建上线前请至少做到：

- 使用高强度且**固定**的 `JWT_SECRET` / `CANVAS_SESSION_SECRET`（禁止每次发版随机生成）
- `ADMIN_AUTH_DISABLED=false`，关闭短信调试
- 密钥只放在服务器环境变量或加密配置中，不要写入 Git
- 媒体走私有对象存储；不要把生产 Bucket 凭证提交到仓库
- 备案号、统计脚本、支付回调域名由你自己的主体配置，不要使用他人备案信息
