---
name: overseas-localize
description: 一键出海。用户选好目标市场并上传参考视频、要做成该市场本地化成片时用。不要用于原片原样复刻（用爆款复刻）、电影级宣传片、未选市场的泛用剪辑。
title: 一键出海
metadata:
  jumeng.slug: overseas_localize
  jumeng.execution: agent_recipe
  jumeng.entryKind: overseas_localize
---

# 一键出海

> 本包给**画布 AI 助手**用。只用系统已有工具；不要发明工具名；不要让用户去画布上点节点。  
> 配套：`references/generation.md` · `references/node-layout.md` · `references/canvas-tools.md` · `references/constraints.md` · `references/markets.md`

## Agent 必读

对用户只说中文结论和产物。禁止解释 nodeId、tool id、JSON、params 字段名。

### 用户的选择（目标市场）

出海必须先有**目标市场**（工具栏或用户口头，如美国 / 日本 → `US` / `JP`，见 `markets.md`）。

- 还没有市场：先用 `ask_user` 问清国家/地区，再往下走。不要猜人种与对白语言。
- 本轮文字与工具栏不一致时，以**本轮文字**为准，并写入本地化参数里的市场 id。
- 后续主体外形、对白语言、街景与道具，一律按该市场本地化，不要做成「原片原样复刻」。

### 推荐流程（按序；画布已完成的步骤可跳过）

1. **确认市场 + 参考视频**  
   市场已选；参考视频必须落到带媒体的 `video_input`（附件落点，或复用画布已有）。都没有则只问缺的那一项。**不要**一上来就出片。

2. **先分析参考视频**  
   本轮视觉已能看见该视频：在对话里讲清节奏、关键转折、大致时长、有哪些主体，并说明将做成「{市场}版」同款。仍不要出片。

3. **按关键点切段**  
   分析之后再切。每段时长必须是**整数秒**，**最短 5 秒、最长 12 秒**。不足 5 秒并入相邻段；超过 12 秒再按关键点切开。切段用 `run_canvas_tool` `storyboard_from_video`。切完后只对用户说「已切成 N 段，每段 5–12 秒」，不要让用户去改表。

4. **按市场本地化切段文案与主体**  
   切段后立刻 `run_canvas_tool` `storyboard_overseas_localize`（带上目标市场 id）。把对白语言、主体人设/族裔与场景改成目标市场版本。不要只用口头说「已翻译」却不调该工具。

5. **点名主体，询问是否出图**  
   根据分析 + 本地化结果，列出主体（人物 / 场景 / 关键道具，名称短而稳定，符合目标市场）。用 `ask_user` 问：**要不要生成这些主体的图片？**  
   用户回答之前，禁止出主体图、禁止出视频。

6. **用户确认后才出主体图**  
   同意 → `text_subject` / `storyboard_subject_image`（或对主体 `image_input` `generate_node`），外形按目标市场 casting。等图生成完、对话里能看到产物后，再进入下一步。  
   拒绝 / 跳过 → 不出主体图，直接进入第 7 步。

7. **再询问是否出视频**  
   主体图已齐（或用户已跳过）之后，再用 `ask_user` 问：**要不要按这 N 段生成「{市场}版」视频？**  
   未得到同意前，禁止对视频 `generate_node`，禁止 `storyboard_batch_videos`。

8. **同意后按段复刻**  
   同一轮、按切段顺序提交：每一段 = 该段参考切片 + **本段相关**主体图 + 该段（已本地化）提示词。用多个 `generate_node`（或一次 `storyboard_batch_videos`，但每段仍须带上对应参考与主体图）。不要只交第一段就停——系统在第一段成功后会收束，必须**同轮全部提交**。

9. **自动合成一条成片**  
   各段写入同一批次镜号（见 node-layout）。全部成功后系统拼成**一条**视频并写入资产。助手不要再让用户手动合并，也不要再开下一批。

### 黑盒

- 用户只在**对话框**里看说明和产物（分析、主体图、分段成片、最终成片）。
- 生成成功的图 / 视频都会进入**项目资产**；对话里用媒体卡片展示即可。
- 不要指挥用户去画布连线、改分镜表、点节点上的生成。画布节点只是执行载体。
- 准星点着参考原片说「确认生成」：仍按本流程出**出海复刻段**，禁止对参考原片 `generate_node`。

### 运行时约定（Agent Skills）

- 目录常驻；正文 `load_skill`；`references/` 用 `load_skill_file`。**不执行** `scripts/` / MCP。
- 点名：芯片 / `$overseas-localize` / 口头「出海到某市场」。须先有目标市场。
- 出视频确认短语：`确认生成` / `确认并生成` / `生成同款` / `开始成片` / `批量成片`。长流程可用 `update_plan`。

### 本轮怎么选

| 现状 | 本轮 |
|------|------|
| 无目标市场 | `ask_user` 问国家/地区 |
| 无参考视频 | `ask_user` 请上传 |
| 有市场与参考，还没分析 | 看视频，对话输出分析（主体名单 + 建议切几段 + 将做哪国版） |
| 已分析，还没切段 | `storyboard_from_video`，切成 5–12 秒整数段 |
| 已切段，还没本地化 | `storyboard_overseas_localize` + 市场 id |
| 已本地化，还没问主体图 | `ask_user`：是否生成这些主体的图片 |
| 用户要主体图，图未齐 | 出主体图（按市场）；出完再问视频 |
| 图已齐或用户跳过，还没问视频 | `ask_user`：是否按段生成该市场版视频 |
| 用户已同意出视频 | 同轮按段提交生成（见 generation.md） |
| 本轮成片已提交成功 | 停。合并交给系统 |

缺省画幅 9:16、清晰度 1080p；用户另说的以用户为准。

不要用本技能做：不换市场的原样复刻、从零原创短剧、从零做电影级产品宣传片。

---

## pipeline

```yaml
execution: agent_recipe
entryKind: overseas_localize
steps:
  - agent: orchestrator
    action: confirm_market_and_video
    note: 目标市场 + 参考视频齐备
  - agent: orchestrator
    action: analyze_video
    note: 先看参考视频，对话里讲节奏与主体
  - agent: orchestrator
    action: split_clips
    note: storyboard_from_video；每段整数秒 5-12
  - agent: orchestrator
    action: localize
    note: storyboard_overseas_localize + targetMarketId
  - agent: orchestrator
    action: ask_subject_images
    note: ask_user 是否生成主体图
  - agent: orchestrator
    action: subject_images
    note: 用户同意后才出图（按市场 casting）
  - agent: orchestrator
    action: ask_generate_video
    note: 图齐或跳过后 ask_user 是否出视频
  - agent: orchestrator
    action: remake_segments
    note: 同意后同轮按段 generate；自动合并成一条
```
