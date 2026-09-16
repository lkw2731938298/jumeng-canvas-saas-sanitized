# 系统工具 · 一键出海

只使用画布 AI 助手已有工具。没有的能力不要假装有。

| 目的 | 怎么调 | 要点 |
|------|--------|------|
| 看画布 | `get_canvas_state` / `inspect_node` | 每轮先读；切段后看每段时长 |
| 落参考视频 | `add_node` type=`video_input` | `assetId`；已有则复用 |
| 分析画面 | 本轮用户消息已带视频时直接看 | 先说话，再切段 |
| 按关键点切段 | `run_canvas_tool` `storyboard_from_video` | `nodeId`=参考视频；目标每段 5–12 整数秒 |
| 按市场本地化 | `run_canvas_tool` `storyboard_overseas_localize` | 切段后必调；带目标市场 id |
| 问一句 | `ask_user` | 必须问：①是否出主体图 ②是否出视频；缺市场时也要问 |
| 抽主体名单 | `run_canvas_tool` `text_subject` | 仅用户同意出图或需要名单时 |
| 出主体图 | `run_canvas_tool` `storyboard_subject_image` | 用户同意之后；按市场 casting |
| 改某段词 | `inspect_node` → `update_node_params` | 保持目标市场语言与设定 |
| 按段复刻 | `generate_node`（同轮按段全部提交） | 用户同意出视频之后；每段带切片 + 相关主体图 + 本地化 prompt |
| 按表一次出齐 | `run_canvas_tool` `storyboard_batch_videos` | 仅当切段清单已按段写好参考与词，且用户已同意出视频 |
| 单段失败重试 | `generate_node` 失败的那一个 | 不要重交已成功的段 |

不要：

- 对参考原片 `generate_node`
- 未问、未同意就出主体图或出视频
- 跳过 `storyboard_overseas_localize` 做原片原样复刻
- 电影级故事板合成图路径（`storyboard` / `blocking_storyboard`）当本技能主路径
- 让用户去画布上点官方按钮名才能继续
- 向用户解释内部字段
