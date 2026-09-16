# 系统工具 · 电影级宣传片

只使用画布 AI 助手已有工具。

| 目的 | 怎么调 | 要点 |
|------|--------|------|
| 看画布 | `get_canvas_state` / `inspect_node` | 每轮先读 |
| 落产品图 | `add_node` type=`image_input` | `assetId`；已有则复用 |
| 落参考视频 | `add_node` type=`video_input` | 仅当用户提供参考片 |
| 过长参考片切段 | `run_canvas_tool` `storyboard_from_video` | 只作切段参考；每段 5–12 整数秒 |
| 问一句 | `ask_user` | 缺产品图 / 需求不清 / 是否出视频 |
| 出故事板 | `run_canvas_tool` `storyboard` | 主路径；`userPrompt` 必填 |
| 调度故事板 | `run_canvas_tool` `blocking_storyboard` | 需要机位/站位关系时 |
| 改参数 | `update_node_params` | |
| 连参考 | `connect_nodes` | 产品图、故事板 → 各镜 `ref_in` |
| 按镜出片 | `generate_node`（同轮全部提交） | 用户同意之后 |
| 30 秒单镜 | `generate_node` + model=`rh_minimax_hailuo_h3_r2v` | 用户明确要 30s 时；以模型目录上限为准 |

不要：

- 用分镜表批量（`storyboard_batch_videos`）当本技能主出片路径
- 用 `text_subject` / 定妆图当产品身份锁
- 未同意就出视频
- 对用户解释内部字段
- 让用户去画布上点官方按钮才能继续
