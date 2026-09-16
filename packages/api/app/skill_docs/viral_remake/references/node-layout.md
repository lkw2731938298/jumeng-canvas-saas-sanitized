# 节点 · 爆款复刻

画布上只放执行需要的最少节点。用户不需要在画布上操作；产物以对话和资产为准。

## 1. 拓扑

```
[参考视频 video_input]
        │
        │  分析（对话）→ storyboard_from_video 切段（5–12s）
        ▼
[切段清单 / 各段参考切片]
        │
        │  问过之后才出
        ▼
[主体图 image_input × 主体数]（用户跳过则没有）
        │
        │  再问过、用户同意之后
        ▼
[复刻段1 video_input] … [复刻段N video_input]
        │
        │  全部成功后系统拼接
        ▼
[一条成片] → 资产 + 对话框
```

| 节点 | type | 建议名称 | 说明 |
|------|------|----------|------|
| 参考视频 | `video_input` | 参考视频 | 必须有 `assetId`。只作蓝本，禁止对它 generate |
| 切段清单 | `storyboard_grid` | 切段 | `storyboard_from_video` 写入；对用户不当成要去编辑的表 |
| 主体图 | `image_input` | 主体·{短名} | 仅用户同意出图后才建/生成 |
| 复刻段 i | `video_input` | 段{i} | 整数时长 5–12；连本段切片 + 本段主体图 |

## 2. 位置（避免叠在一起）

锚点 `(ax, ay)` = 参考视频；没有则画布中心附近。

| 节点 | 位置 |
|------|------|
| 参考视频 | `(ax, ay)` 已有则不动 |
| 切段清单 | `(ax + 480, ay)` |
| 主体图 k | `(ax, ay + 260 + (k-1)*200)` |
| 复刻段 i | `(ax + 860, ay + (i-1)*220)` |

## 3. 落点顺序

### 参考

有附件 → `add_node` `video_input` + `assetId`。已有带媒体的参考 → 复用。

### 切段

`run_canvas_tool` `storyboard_from_video`，`nodeId`=参考视频。然后 `inspect_node` 核对每段时长是否落在 5–12 整数秒；过短的在提示词 / `generationOptions.duration` 里并到合法时长再提交成片。

### 主体图（仅用户点了「生成这些主体图」）

`text_subject` 如需名单；`storyboard_subject_image` 出图。或 `add_node` `image_input` 后 `generate_node`。图要进资产，对话里能看到。

### 复刻段（仅用户同意出视频）

本轮生成一个批次 id（例如 `remake-` + 时间戳），**所有复刻段共用**。

每个 `add_node` `video_input`：

- `prompt`：该段画面 + 运镜，主体用与主体图一致的短名
- `cinematicShotIndex`：从 1 到 N（系统按此顺序拼接成一条；不要对用户念这个字段名）
- `cinematicBatchId`：上面的批次 id
- `generationOptions.duration`：该段整数秒（5–12）
- `model`：画布首选视频模型

连线：

- 该段参考**切片**（切段后的片段节点或表内参考）→ 该复刻段 `ref_in`
- 本段出场的主体图 → 同一 `ref_in`
- 对照本轮模型参考路数上限；超了只留本段最必要的主体图 + 切片
- 不要把完整参考原片连到复刻段上当生成目标

然后**同一轮**按 i=1…N `generate_node`。

## 4. 同轮打包

| 阶段 | 可以一起发的工具 |
|------|------------------|
| 有附件尚未落点 | `add_node` 参考视频 |
| 分析后切段 | 一次 `storyboard_from_video` |
| 用户同意出主体图 | 抽主体 + 出图（不要夹带视频 generate） |
| 用户同意出视频 | 多个 add 复刻段 + connect + 全部 generate_node |
