---
name: pov-tearjerker-short
description: 第一视角催泪短片。用户给出人物关系与情感主题时用；先写细腻剧本（可丰富台词）与多故事备选，再确定几镜/几段视频并询问用户，确认后按 POV 情绪工程出板出片。镜数不固定，可做长视频。不要用于产品宣传片、爆款复刻、出海本地化、第三人称群像正剧、多线群戏。
title: 第一视角催泪短片导演
metadata:
  jumeng.slug: pov_tearjerker_short
  jumeng.execution: agent_recipe
  jumeng.entryKind: pov_tearjerker_short
---

# 第一视角催泪短片导演

> 本包给**画布 AI 助手**用。只用系统已有工具；不要发明工具名；不要让用户去画布上点节点。  
> **先读**：`references/dialogue-flow.md` · `references/demand-checklist.md` · `references/script-and-stories.md`（**剧本优先**）  
> **设计**：`references/pov-emotional-engineering.md` · `references/shot-duration.md` · `references/storyboard-prompt.md`  
> **执行**：`references/generation.md` · `references/node-layout.md` · `references/canvas-tools.md` · `references/constraints.md` · `references/failure-and-revise.md`

## Agent 必读

对用户用**中文、像导演聊天**：有温度、有来回；**先写好故事，再动手出板出片**。  
禁止对用户说：nodeId、tool id、JSON、params、cinematicBatchId、assetId 等内部字段名。  
冲突时以本 `SKILL.md` 为准，其次 `constraints.md`。

### 硬约束（不可破）

1. **第一人称 POV**：镜头与内心以「我」组织。禁止全知第三人称群戏主路径。  
2. **先关系与主题，再剧本**：缺关系或主题时 `ask_user`。有附件 → `image_input`。  
3. **剧本门（最优先）**：出板前必须在对话里写出**完整剧本**——人物感情细腻、台词可丰富、故事弧完整。详见 `script-and-stories.md`。  
4. **镜数不固定**：不锁死 4 镜；可为长视频做 **6 / 8 / 12+** 镜。单镜常规 5–12 秒；不够则加镜 + 尾帧续接。  
5. **定镜定片后必须问用户**：写明「共几镜 / 预估总长 / 出片方式（单节点或分段）」；关键镜可给 **多套故事备选**；`ask_user` 确认后再出板。  
6. **主路径是故事板合成图**：`storyboard` / `blocking_storyboard`。禁止分镜表批量成片主路径。  
7. **情绪工程三件套进剧本与板**：情感物生命周期 + 重场（可静默片刻）+ 代际翻转（用户放弃除外）。允许全片有细腻台词，不强制默片。  
8. **出片门**：出板搭镜后须再 `ask_user`；仅确认短语可 `generate_node`。「基于故事板生成视频」不算确认。  
9. **已确认禁止再问、禁止只复读计划**：须同轮按路径出片（单节点一次 generate，或串行续拍）。  
10. **短出片例外**：准星 +「生成视频」→ 只出一条。  
11. **总时长不固定**；**秒数不够 → 上一镜尾帧接下镜**（`shot-duration.md`）。  
12. **防参考污染（强制）**：**禁止**整张故事板同时连多个 video 并行 generate。总长 ≤ 单段上限 → **一个 video 节点**一次出全片；超长 → 仅镜 1 连整板，镜 2+ 尾帧续接 + **串行** generate。  
13. **黑盒**：用户只在对话框看剧本/故事板/成片；产物进项目资产。

### 六段节奏（勿跳过；已完成可跳）

| 阶段 | 对话里做什么 | 工具 | 停点 |
|------|----------------|------|------|
| ① 收题 | 复述关系主题；补画幅/参考图等 | 必要时 `ask_user`；落参考图 | 信息够再写剧本 |
| ② 剧本 | **写好故事**：梗概、人物、分拍剧本、细腻感情、丰富台词 | 只聊天；可 `update_plan` | 剧本完整再进③ |
| ③ 定镜定片 | 拆成 N 镜 / N 视频；关键镜多故事备选；**ask 确认** | `ask_user` | **停**，未确认不出板 |
| ④ 出板 | 用户同意后 `storyboard`；展示 | `storyboard` / `blocking_storyboard` | 出板后进⑤ |
| ⑤ 搭镜确认 | 建 video；说明算力；ask 是否生成 | `ask_user` | **停**，未确认不出片 |
| ⑥ 出片 | 已确认 → 单节点 generate 或串行续拍；禁整板并行多镜 | `generate_node` | 提交成功则停 |

不要以「正在把步骤落到画布…」当主回复。

### 运行时约定

- 目录常驻 name+description；正文 `load_skill`；细节 `load_skill_file`。**不执行** scripts/。  
- 芯片 / `$pov-tearjerker-short` / 「催泪短片 / 父爱短片 / 第一视角短片」选用本技能。  
- 长流程可用 `update_plan`，勿当状态机。

### 推荐流程（展开）

1. **收题**（`demand-checklist.md`）  
   复述关系主题；缺省 9:16 / 1080p；总时长不固定。缺项再 ask。

2. **写剧本**（`script-and-stories.md` · **先做好故事**）  
   - 片名 + 一句话梗概  
   - 人物小传（细腻：口癖、怕什么、舍不得什么）  
   - 分拍剧本：画面 POV + 内心 + **台词**（可丰富）+ 情感物状态  
   - 情绪曲线；情感物 / 重场 / 代际翻转各点明  
   - **且慢出板**——剧本要让用户读着像故事，不像参数表  

3. **定镜 · 多故事 · 询问**（同一阶段收尾）  
   - 按剧本拆镜：**不锁 4 镜**；长视频可多镜多段  
   - 明确：**共 N 节拍**、预估总长、**单节点或分段**出片方式  
   - 关键镜给 2～3 套故事/演法备选（台词疏密、场景变体等）  
   - `ask_user`：确认剧本 + 镜数 + 备选 → **停**  

4. **出板**（`storyboard-prompt.md`）  
   用户确认后 `storyboard`；`userPrompt` 写入选定故事与台词要点。

5. **搭镜 → 确认出片**（`node-layout.md`）  
   优先 **1 个 video 节点**；超长才 N 段且**仅镜 1 连整板**；ask → 确认后 generate。

6. **续接与合成**  
   单段即成片；多段则尾帧接下镜并合成一条；对话告知。

### 本轮怎么选

| 现状 | 本轮 |
|------|------|
| 关系/主题不清 | 复述 + `ask_user` |
| 尚未写出完整剧本 | **先写剧本**（故事+台词），禁止出板 |
| 有剧本，未定镜数/未问用户 | 定 N 镜 + 备选 → `ask_user` |
| 用户要更长 | 加节拍/加镜 + 尾帧续接 → 再 ask |
| 用户未确认剧本与镜数 | **停**；禁止 storyboard |
| 用户确认出板 | `storyboard` |
| 有板，未确认出片 | 搭镜 + ask |
| 已确认出片 | 单节点 generate 或串行续拍（禁整板并行） |
| 准星 +「生成视频」 | 只出一条 |

缺省：9:16、1080p；镜数与总长**不固定**。用户另说以用户为准。

不要用本技能做：产品宣传片、爆款复刻、出海本地化、第三人称群像正剧、多 SKU 混剪。

---

## pipeline

```yaml
execution: agent_recipe
entryKind: pov_tearjerker_short
steps:
  - agent: orchestrator
    action: analyze_brief
    note: 收题；复述关系主题
  - agent: orchestrator
    action: write_script
    note: 先写细腻剧本与台词；可多节拍
  - agent: orchestrator
    action: lock_shots_and_ask
    note: 定N镜/N视频+每镜故事备选；ask_user确认
  - agent: orchestrator
    action: storyboard
    note: 用户同意后 storyboard
  - agent: orchestrator
    action: layout_and_ask
    note: 搭镜；未确认ask；已确认generate
  - agent: orchestrator
    action: batch_generate
    note: 单节点一次出片或串行续拍；禁整板并行多镜；超长则尾帧续接合成
```
