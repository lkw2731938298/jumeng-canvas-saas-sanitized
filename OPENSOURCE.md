# 聚梦画布 · 开源清洗副本

本目录由正式仓库 `canvas/` **复制并清洗**而来，用于开源准备。
**未修改**原仓库中的 `canvas/` 运行目录。

## 已清除 / 脱敏内容

- 全部真实环境文件：`.env`、`llm-keys.env`、`alipay.env`、`lan.env`、`env.sh`、`env.test.sh`
- 本地用户项目与媒体：`data/oss-local/`（约 2.4GB 用户素材）、本地 `assets.json` / `workflow-presets.json`
- API / OSS / 短信 / 支付宝 / JWT / 加密主密钥 / SSH / 数据库口令等真实凭证
- 生产与测试主机 IP、RDS/Redis 实例名、OSS Bucket、数据库口令
- 超管真实手机号、运维临时日志 / `_tmp_*` 诊断产物
- 文本中的 PEM / 长 Base64 私钥、`sk-` 形态密钥
- **公开域名已替换为 example.com 占位**（用户站 / CDN / 文档站 / 管理域等）
- **公司法定与追踪信息已清除**：ICP 备案号、百度统计 ID、百度/搜索引擎站点验证文件；Footer 备案改为空，由部署方自行填写
- **模型目录空壳**：`model_registry` / `generation_presets` 无预置型号与定价表；画布工具默认主模型已清空
- **生产部署脚本已移除**：`scripts/deploy/` 仅保留 `env*.example` 与说明（无远端发布/探测脚本）

## 关于「模型」

- 本副本 **不包含** 任何生产模型条目、上游型号 ID、按型号预设与工具默认主模型
- 前端兜底列表（`mediaModels.ts` / `textModels.ts` / 参考指南）亦已清空
- 运行时需自行在管理后台录入模型、配置供应商密钥与上下架
- 保留类型定义与通用计价/预设合成 API，便于二次开发接入
- 供应商集成适配代码（`integrations/`）可能仍含通道名字符串，属协议适配而非目录种子

## 保留内容

- 应用源码（API / Web / Worker）
- `scripts/deploy/env.sh.example`、`env.test.sh.example`
- 本地 Docker Compose（库口令已改为 `change-me`）

## 使用前必做

1. 复制 `config/llm-keys.example.env` → `config/llm-keys.env` 并自行填写
2. 复制 `scripts/deploy/env.sh.example` → `env.sh`（若需自建部署流程）
3. 复制 `packages/api/.env.example` → `packages/api/.env`
4. 自行准备 MySQL / Redis / OSS，**勿**使用任何占位口令上线
5. 管理后台自行添加模型与供应商密钥

## 说明

- 使用与贡献请看仓库根目录 [README.md](./README.md)。
- 本副本**不是**可直接连生产的配置包，也**不能**原样用于现网发版。
- 原仓库中的 `ai_read/deploy_ledger/`、服务器资源笔记等内部运维文档**未**纳入本目录。
- 可重复生成：`python ai_read/_prepare_opensource_copy.py`
- 审计：`python ai_read/_audit_opensource_copy.py`（应输出 `hits=0`）
