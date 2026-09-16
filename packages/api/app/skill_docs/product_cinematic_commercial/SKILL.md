---
name: product-cinematic-commercial
description: 单一产品电影级宣传片。有产品参考图、用户要宣传片/成片时用；推荐先故事板再出片。不要用于多产品混剪、爆款复刻、出海本地化、纯品牌空镜。
title: 单一产品电影级宣传片
metadata:
  jumeng.slug: product_cinematic_commercial
  jumeng.execution: agent_recipe
  jumeng.entryKind: product_cinematic_commercial
---

# 单一产品电影级宣传片

> 本包给**画布 AI 助手**用。只用系统已有工具；不要发明工具名；不要让用户去画布上点节点。  
> 配套：`references/generation.md` · `references/node-layout.md` · `references/canvas-tools.md` · `references/constraints.md` · `references/storyboard-sheet-to-video.md`

## Agent 必读

对用户只说中文结论、过程摘要和产物。禁止解释 nodeId、tool id、JSON、params 字段名。

### 硬约束

1. **单一产品**：产品须占画面主体；不要第二 SKU、不要纯品牌空镜。
2. **主路径是故事板合成图**：用 `storyboard` / `blocking_storyboard`。**不要**把分镜表（`storyboard_grid` / `storyboard_table` / `storyboard_batch_videos`）当成本技能出片主路径。
3. **禁止定妆图当身份锁**：产品外形锁在用户产品参考图上，不要走定妆/产品设定图主路径。
4. **缺产品参考图**：`ask_user` 请用户上传；有附件则落到 `image_input`。
5. **视频出片须用户明确同意**：本轮须含「确认生成 / 确认并生成 / 生成同款 / 开始成片 / 批量成片」；同意后**同批**提交全部镜头，禁止一条条续跑。未确认时执行层硬闸不会投影视频 generate。
6. **短出片例外**：准星 +「生成视频」→ 只出一条；准星产品图 +「做宣传片」→ 仍走本配方。

### 运行时约定（Agent Skills）

- 本环境只加载 markdown 配方：目录常驻 name+description；正文用 `load_skill`；细节用 `load_skill_file` 读 `references/`。
- **不执行** `scripts/`，也不接通 MCP。正文若写「运行脚本」，改为用本系统画布工具完成或跳过。
- 用户可用芯片绑定、消息 `$product-cinematic-commercial` 或口头「做宣传片」选用本技能。
- 长流程可用 `update_plan` 更新进度条，勿当状态机。

### 推荐流程（按序；画布已完成的可跳过）

1. **先分析用户需求**  
   在对话里说清：产品是什么、卖点/情绪、目标时长（如 15s / 30s）、画幅、有无参考视频/参考图、建议几段节奏。信息不够再 `ask_user` 补问。**不要**还没分析就出片。

2. **参考视频过长 → 按关键点切段**  
   若用户给了参考视频且明显偏长（或远超目标成片时长）：按关键点切成**整数秒**段，**最短 5 秒、最长 12 秒**。不足 5 秒并入相邻段；超过 12 秒再切。  
   切段用系统工具 `storyboard_from_video`（只作切段与节奏参考，**不是**本技能的出片主路径）。切完后只对用户说「参考片已按关键点切成 N 段」，不要让用户去改表。  
   **例外**：用户明确要 **30 秒单镜**时，不要硬切成 12 秒——用 **MiniMax**（模型 id：`rh_minimax_hailuo_h3_r2v`，展示名 MiniMax-H3）出该镜；若当前模型目录时长上限不足 30 秒，改用目录里支持 30 秒的视频模型，或切成 5–12 秒段再合并。无参考视频则跳过本步。

3. **流程设计 → 生成故事板**  
   根据需求（与切段节奏）设计镜次/情绪曲线，在对话里用一两句话说明推荐流程，然后 `run_canvas_tool` `storyboard`（需要机位关系时用 `blocking_storyboard`）。`params.userPrompt` 必填，写清各格画面与产品出镜。产品参考图接到故事板。  
   出板后对用户展示故事板产物（进资产）；**未同意出视频前不要** `generate_node` 视频。

4. **参考故事板生成对应秒数视频**  
   读板（数格 / 对应切段节奏）→ 按格建 `video_input`，每镜 `generationOptions.duration` 写成该镜**整数秒**（常规 5–12；用户要的 30 秒单镜按上条选 MiniMax 或支持 30s 的模型）。  
   参考：故事板合成图 + 产品图（+ 若有，该镜对应的切段参考）。同一批次镜号见 node-layout。  
   用户同意后**同一轮**全部 `generate_node`。

5. **自动合成一条成片**  
   各镜共用同一 `cinematicBatchId` 与递增 `cinematicShotIndex`。全部成功后系统拼成**一条**视频并写入资产。助手不要再让用户手动合并。

### 黑盒

- 用户只在**对话框**里看过程说明和产物（需求分析、切段说明、故事板、各镜视频、最终成片）。
- 生成成功的图 / 视频都会进入**项目资产**；对话里用媒体卡片展示即可。
- 不要指挥用户去画布连线、改节点、点生成。画布节点只是执行载体。

### 本轮怎么选

| 现状 | 本轮 |
|------|------|
| 需求不清 | 对话分析 + 必要时 `ask_user` |
| 无产品图 | `ask_user` 要参考图；有附件则落点 |
| 有过长参考视频、未切段 | `storyboard_from_video` 切成 5–12 秒整数段（30s 单镜例外见上） |
| 有产品图，无故事板 | `storyboard` / `blocking_storyboard` |
| 有故事板，未搭视频节点 | 读板建镜头节点 + 连参考；再 `ask_user` 是否出视频 |
| 节点已齐，用户已同意出片 | 同轮全部 `generate_node` |
| 本轮成片已提交成功 | 停。合并交给系统 |
| 准星 +「生成视频」 | 只出一条，不再铺板 |

缺省画幅 9:16、清晰度 1080p、总时长常见 15s（可改为 30s）；用户另说的以用户为准。

不要用本技能做：多产品混剪、纯品牌片、剧情短剧主路径、爆款原样复刻、出海本地化。

---

## pipeline

```yaml
execution: agent_recipe
entryKind: product_cinematic_commercial
steps:
  - agent: orchestrator
    action: analyze_brief
    note: 先分析用户需求
  - agent: orchestrator
    action: split_long_ref_video
    note: 参考视频过长则 storyboard_from_video；每段整数秒 5-12；30s 单镜可用 MiniMax
  - agent: orchestrator
    action: storyboard
    note: storyboard / blocking_storyboard
  - agent: orchestrator
    action: layout_and_ask
    note: 读板搭 video_input；ask_user 确认出片
  - agent: orchestrator
    action: batch_generate
    note: 同意后同轮 generate；自动合并成一条
```
