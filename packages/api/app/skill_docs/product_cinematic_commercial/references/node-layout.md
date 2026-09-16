# 节点 · 电影级宣传片

画布上只放执行需要的最少节点。用户不需要在画布上操作；产物以对话和资产为准。

## 1. 拓扑

```
[产品参考图 image_input]
        │
        │  （可选）过长参考视频 → 切段 5–12s
        ▼
[故事板 image_input]  ← storyboard / blocking_storyboard
        │
        │  读板 → 用户同意后
        ▼
[镜头1 video_input] … [镜头N video_input]
        │
        │  全部成功后系统拼接
        ▼
[一条成片] → 资产 + 对话框
```

| 节点 | type | 建议名称 | 说明 |
|------|------|----------|------|
| 产品图 | `image_input` | 产品参考 | 必须有媒体；外形锁 |
| 参考视频 | `video_input` | 参考视频 | 可选；过长才切段 |
| 故事板 | `image_input` | 故事板 | `storyboard` 产物 |
| 镜头 i | `video_input` | 镜{i} | 整数时长；连故事板 + 产品图 |

## 2. 位置（避免重叠）

锚点 `(ax, ay)` = 产品图；没有则画布中心附近。

| 节点 | 位置 |
|------|------|
| 产品图 | `(ax, ay)` |
| 参考视频 / 切段相关 | `(ax, ay + 260)` |
| 故事板 | `(ax + 480, ay)` |
| 镜头 i | `(ax + 960, ay + (i-1)*220)` |

## 3. 落点顺序

### 产品与需求

有附件产品图 → `add_node` `image_input`。无图则 `ask_user`。先分析需求再出板。

### 过长参考视频

`storyboard_from_video` 切段后，切段清单仅作参考；不要据此改走分镜表出片。

### 故事板

`run_canvas_tool` `storyboard`（或 `blocking_storyboard`），参考接到产品图。

### 镜头（仅用户同意出视频后提交生成；可先搭空节点再问）

本轮生成一个批次 id（例如 `cinematic-` + 时间戳），**所有镜头共用**。

每个 `add_node` `video_input`：

- `prompt`：该镜画面 + 运镜 + 产品出镜；与故事板对应格一致
- `cinematicShotIndex`：从 1 到 N（系统按此顺序拼接；不要对用户念字段名）
- `cinematicBatchId`：上面的批次 id
- `generationOptions.duration`：该镜整数秒（常规 5–12；30 秒单镜用 MiniMax 或支持 30s 的模型）
- `model`：常规用画布首选视频模型；30 秒单镜优先 `rh_minimax_hailuo_h3_r2v`

连线：

- 故事板 → 各镜 `ref_in`
- 产品图 → 各镜 `ref_in`
- 若有该镜对应切段切片，可再接一条参考（注意模型参考路数上限）

然后**同一轮**按 i=1…N `generate_node`。

## 4. 同轮打包

| 阶段 | 可以一起发的工具 |
|------|------------------|
| 有附件未落点 | `add_node` 产品图 / 参考视频 |
| 过长片切段 | 一次 `storyboard_from_video` |
| 出板 | 一次 `storyboard` 或 `blocking_storyboard` |
| 用户同意出片 | 多个 add 镜头 + connect + 全部 generate_node |
