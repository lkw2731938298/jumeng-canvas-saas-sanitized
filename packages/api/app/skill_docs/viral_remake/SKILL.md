---
name: viral-remake
description: 爆款复刻。用户给了参考视频、要按片复刻成片时用。先分析再切段、问主体图、再问是否出视频。不要用于出海本地化、电影级宣传片主路径、无参考视频的纯文生视频。
title: 爆款复刻
metadata:
  jumeng.slug: viral_remake
  jumeng.execution: agent_recipe
  jumeng.entryKind: viral_remake
---

# 爆款复刻

> 本包给**画布 AI 助手**用。用系统已有工具执行；不要发明工具名，不要让用户去画布上点节点。  
> 配套：`references/generation.md` · `references/node-layout.md` · `references/canvas-tools.md` · `references/constraints.md`

## Agent 必读

对用户只说中文结论和产物。禁止解释 nodeId、tool id、JSON、params 字段名。

### 推荐流程（按序，可因画布已完成而跳过已做步骤）

1. **先分析参考视频**  
   必须先有带媒体的参考视频（附件落 `video_input`，或复用画布已有）。本轮视觉已能看见该视频：先在对话里讲清节奏、关键转折、大致时长、有哪些主体。**不要**一上来就出片。
2. **按关键点切段**  
   分析之后再切。每段时长必须是**整数秒**，**最短 5 秒、最长 12 秒**。不足 5 秒并入相邻段；超过 12 秒再按关键点切开。切段用 `run_canvas_tool` `storyboard_from_video`（系统会做镜头检测并写出分段参考）。切完后只对用户说「已切成 N 段，每段 5–12 秒」，不要让用户去改表。
3. **点名主体，询问是否出图**  
   根据分析列出主体（人物 / 场景 / 关键道具，名称短而稳定）。用 `ask_user` 问：**要不要生成这些主体的图片？**  
   在用户回答之前，禁止出主体图、禁止出视频。
4. **用户确认后才出主体图**  
   同意 → `text_subject` / `storyboard_subject_image`（或对主体 `image_input` `generate_node`）。等图生成完、对话里能看到产物后，再进入下一步。  
   拒绝 / 跳过 → 不出主体图，直接进入第 5 步。
5. **再询问是否出视频**  
   主体图已齐（或用户已跳过）之后，再用 `ask_user` 问：**要不要按这 N 段生成视频？**  
   未得到同意前，禁止对视频 `generate_node`，禁止 `storyboard_batch_videos`。
6. **同意后按段复刻**  
   同一轮、按切段顺序提交：每一段 = 该段参考切片 + **本段相关**主体图 + 该段提示词。用多个 `generate_node`（或一次 `storyboard_batch_videos`，但每段仍须带上对应参考与主体图）。不要只交第一段就停，等用户再说「下一段」——系统在第一段成功后会收束，必须**同轮全部提交**。
7. **自动合成一条成片**  
   各段节点写入同一批次镜号（见 node-layout）。全部成功后系统会拼成**一条**视频并写入资产。助手不要再让用户手动合并，也不要再开下一批。

### 黑盒

- 用户只在**对话框**里看说明和产物（分析、主体图、分段成片、最终成片）。
- 生成成功的图 / 视频都会进入**项目资产**；对话里用媒体卡片展示即可。
- 不要指挥用户去画布连线、改分镜表、点节点上的生成。画布节点只是执行载体。
- 准星点着参考原片说「确认生成」：仍按本流程出**复刻段**，禁止对参考原片 `generate_node`。

### 运行时约定（Agent Skills）

- 目录常驻；正文 `load_skill`；`references/` 用 `load_skill_file`。**不执行** `scripts/` / MCP。
- 点名：芯片 / `$viral-remake` / 口头「爆款复刻」。长流程可用 `update_plan`，勿当状态机。
- 出视频确认短语：`确认生成` / `确认并生成` / `生成同款` / `开始成片` / `批量成片`（与执行层硬闸一致）。

### 本轮怎么选

| 现状 | 本轮 |
|------|------|
| 无参考视频 | `ask_user` 请上传 |
| 有参考，还没分析 | 看视频，对话输出分析（主体名单 + 建议切几段） |
| 已分析，还没切段 | `storyboard_from_video`，切成 5–12 秒整数段 |
| 已切段，还没问主体图 | `ask_user`：是否生成这些主体的图片 |
| 用户要主体图，图未齐 | 出主体图；出完再问视频 |
| 图已齐或用户跳过，还没问视频 | `ask_user`：是否按段生成视频 |
| 用户已同意出视频 | 同轮按段提交生成（见 generation.md） |
| 本轮成片已提交成功 | 停。合并交给系统 |

缺省画幅 9:16、清晰度 1080p；用户另说的以用户为准。

不要用本技能做：从零原创短剧、电影级产品宣传片、出海本地化。

---

## pipeline

```yaml
execution: agent_recipe
entryKind: viral_remake
steps:
  - agent: orchestrator
    action: analyze_video
    note: 先看参考视频，对话里讲节奏与主体
  - agent: orchestrator
    action: split_clips
    note: storyboard_from_video；每段整数秒 5-12
  - agent: orchestrator
    action: ask_subject_images
    note: ask_user 是否生成主体图
  - agent: orchestrator
    action: subject_images
    note: 用户同意后才出图
  - agent: orchestrator
    action: ask_generate_video
    note: 图齐或跳过后 ask_user 是否出视频
  - agent: orchestrator
    action: remake_segments
    note: 同意后同轮按段 generate；自动合并成一条
```
