# 贡献指南

感谢你考虑为聚梦画布开源版做贡献。

## 开发流程

1. Fork 本仓库并创建分支：`git checkout -b feat/your-topic`
2. 按 [README.md](./README.md) 在本地跑通前端与 API
3. 保持改动聚焦；不要提交 `.env`、密钥、真实域名或备案号
4. 新增接口使用统一错误码，不要裸抛无 `code` 的 HTTP 异常
5. 提交说明写清「为什么改」，例如：`fix: 页脚无备案号时不渲染链接`
6. 发起 Pull Request，描述行为变化与验证步骤

## 不接受的改动

- 回填真实密钥、手机号、ICP、统计 ID、生产主机
- 把本机 `node_modules` / `.next` / `data/oss-local` 打进 PR
- 与议题无关的大规模格式化

## 讨论

功能建议与缺陷请先开 GitHub Issue，确认方向后再提 PR。
