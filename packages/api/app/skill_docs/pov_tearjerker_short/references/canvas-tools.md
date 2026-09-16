# 系统工具 · 第一视角催泪短片

只使用画布 AI 助手已有工具。

| 目的 | 怎么调 | 要点 |
|------|--------|------|
| 看画布 | `get_canvas_state` / `inspect_node` | 每轮先读，避免重复搭 |
| 落参考图 | `add_node` type=`image_input` | `assetId`；已有则复用 |
| 问一句 | `ask_user` | 收题 / 是否出板 / 是否出片；每轮最多 1 次 |
| 出故事板 | `run_canvas_tool` `storyboard` | 主路径；`userPrompt` 必填见 storyboard-prompt |
| 调度故事板 | `run_canvas_tool` `blocking_storyboard` | 需要机位/站位/镜像关系时 |
| 改参数 | `update_node_params` | 改某一镜 prompt / duration |
| 连参考 | `connect_nodes` | 故事板 → **仅镜 1 或唯一 video**；禁止整板连多镜并行 |
| 出片 | `generate_node` | 单节点一次，或串行续拍；禁整板并行多镜 |
| 读配方细节 | `load_skill_file` | 优先 `script-and-stories.md`、dialogue-flow、shot-duration 等 |

> **剧本与定镜**在对话完成，不靠额外工具。  
> **尾帧续接**：同批镜号齐备后由系统自动处理；勿发明截帧工具名。


## 不要

- 用 `storyboard_batch_videos` / `storyboard_table` / `storyboard_grid` 当本技能主出片路径  
- 未同意出板就 `storyboard`  
- 未确认出片就 `generate_node` 视频  
- 确认后只回复计划文字、不调用 generate  
- 对用户解释内部字段  
- 让用户去画布上点官方按钮才能继续  
- 改成产品宣传片、爆款复刻、出海本地化流程  
- 发明不存在的工具名  

## 工具与阶段对照

| 阶段 | 允许 | 禁止 |
|------|------|------|
| 收题 | ask_user、add 参考图 | storyboard、generate 视频 |
| 方案 | ask_user（出板确认） | storyboard（未同意时） |
| 出板 | storyboard / blocking_storyboard | generate 视频 |
| 搭镜确认 | add video、connect、ask_user | generate（未确认时） |
| 出片 | generate_node 全部 | 再 ask「要不要出片」 |
