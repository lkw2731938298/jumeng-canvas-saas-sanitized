# 部署说明（开源副本）

本开源副本**已移除**生产/测试远端发布、探测、切流等脚本。

保留文件：

- `env.sh.example` / `env.test.sh.example` — 环境变量占位模板

本地开发请优先使用仓库根目录 `docker-compose.yml` 与 `packages/api/.env.example`。

若要自建部署流程，请自行编写脚本，**不要**从本商业仓库回拷生产 `scripts/deploy/*.py`。
