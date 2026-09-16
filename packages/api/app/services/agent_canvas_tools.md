# 画布工具说明书（AI 助手）

> 用 `run_canvas_tool` 调用。先读画布，对**已有节点**动手；没有图就 `add_node` 或请用户上传。  
> 图片类工具：用户说出**官方全名**后可以同轮执行（扣生图算力）。**视频出片**须用户明确确认。  
> **禁止模糊**：只认画布按钮上的名字（多角度、打光、九宫格、故事板…）。用户只说「侧面」「光不好」时先 `ask_user` 请其说全名。

调用格式：`run_canvas_tool(tool=<id>, nodeId=<真实id>, nodeName=<名称>, params={...})`

---

## 0. 官方名必须对上 tool id

对照表每轮已注入【画布工具速查】（官方名 / id / 特点 / **主模型=实配**）；完整主模型表见 `agent_canvas_tool_models.md`；`list_canvas_tools` 含说明书。要点：

- 「多角度」→ `multi_angle`，不是普通生图换角度。
- 「打光」→ `lighting`，不是「电影级光影校正」。
- 「九宫格」→ `grid_9`；子功能必须说全名（角色设定图、25宫格连贯分镜、多机位九宫格…）。
- 「故事板」→ `storyboard`（一张合成图）；「调度故事板」→ `blocking_storyboard`；不是分镜表。
- 点名后可以带参数：「多角度看背面」「打光，左侧轮廓光」。

---

## 1. 先分清三类「板」

只认按钮全名；「分镜图 / 机位图」等近义说法先 `ask_user` 确认，不要自动当故事板。

| 用户必须说出的名字 | 实际是什么 | 工具 / 节点 |
|--------------------|------------|-------------|
| 故事板 | **一张多格合成长图** | `storyboard` |
| 调度故事板 | **一张调度合成图**（人物/机位关系） | `blocking_storyboard` |
| 分镜表 | **表格节点**（多行镜头） | 节点类型 `storyboard_grid` + `storyboard_table` 等 |

一两张图、一支单镜头视频：**不要**建分镜表。产品电影级宣传片 / 已选该技能包：**用故事板合成图，不要分镜表**。爆款/出海多镜同款：**用分镜表**。

---

## 2. 故事板 / 调度故事板

### `storyboard` 故事板

- **是什么**：把剧情收成**一张**分镜合成图（多格画面排在同一张图上），方便对形、对光、对节奏。
- **何时用**：用户说了「故事板」（可再加「先出分镜图 / 按这张产品图」）。没说「故事板」三个字不要调用。
- **前置**：`image_input`。有产品/角色参考图更好；没有图也可文生，但外形容易漂。
- **params**：
  - `userPrompt` **必填**：镜头总谱（镜1…镜N，主体、光影、画幅）。不要只写「做个故事板」。
  - `referenceNodeIds` 可选：额外参考图节点 id。
- **之后**：看生成图数格 → 需要成片则建 `video_input` 并 `connect_nodes` 参考图+板图；**未确认不要** `generate_node`。
- **不要**：用 `generate_node` 普通生图冒充故事板；不要无故再出第二张板（画布已有合成图且用户没说重做）。

### `blocking_storyboard` 调度故事板

- **是什么**：偏**空间站位 / 机位 / 人物关系**的合成图，不是每格一个卖点特写。
- **何时用**：用户说了「调度故事板」。没点名不要调用。
- **params**：同故事板，`userPrompt` 必填，写清谁站哪、镜头从哪看。

---

## 3. 多角度

### `multi_angle`

- **是什么**：同一主体换机位再画一张图（绕着物体转、俯仰、远近），用来补参考、选镜头。
- **何时用**：用户说了「多角度」。可再带「侧面 / 背面 / 俯视 / 特写」。只说「换个角度」而没说「多角度」时先问用户。
- **前置**：`image_input` **必须已有图**。
- **params**（均可选，缺省正面中景）：
  - `azimuth` 水平角 0–359：`0` 正前，`90` 右，`180` 后，`270` 左；常用 0/45/90/135/180/225/270/315。
  - `elevation` 俯仰 -90～90：`0` 平视，`45`/`90` 俯视，`-45`/`-90` 仰视。
  - `shot`：`close` 特写 / `medium` 中景 / `wide` 全景。
  - `extraPrompt`：补充（如「只转机位，不要改产品外形」）。
- **例子**：用户「多角度看背面」→ `azimuth=180, elevation=0, shot=medium`。
- **之后**：新图会出现在画布上；需要切开宫格才用 `grid_split`（多角度一般不是宫格）。

---

## 4. 打光

### `lighting`

- **是什么**：在**现有图**上改主光方向、亮度、轮廓光，不换主体。
- **何时用**：用户说了「打光」。可再带方向（侧光 / 逆光 / 轮廓光）。只说「光不好」而没说「打光」时先问用户。
- **前置**：`image_input` 必须已有图。
- **params**：
  - `direction`：`front` 正 / `left` 左 / `right` 右 / `back` 逆光 / `top` 顶 / `bottom` 底。
  - `brightness`：0–100，默认约 50。
  - `rimLight`：true 加轮廓光（主体与背景分离）。
  - `smartMode`：true 智能补光（用户没指定方向时可用）。
  - `color`：色温/灯光色（有则传）。
  - `extraPrompt`：如「伦勃朗光、暗部保留材质」。
- **例子**：「打光，左侧轮廓光」→ `direction=left, rimLight=true`。
- **对比**：只要「电影级光影校正」一张宫格式校正图，用 `cinematic_lighting`，不是 `lighting`。

---

## 5. 九宫格及子功能

九宫格是**一张图里排出多格**（设定、推演、多机位）。结果是宫格图；要单独用某一格再 `grid_split`。

| id | 中文 | 是什么 | 何时用 | params |
|----|------|--------|--------|--------|
| `grid_9` | 九宫格 | 3×3 多格变化 | 用户说了「九宫格」 | 可选 `userPrompt` |
| `grid_25` | 25宫格连贯分镜 | 更密的连贯分镜格 | 用户说了「25宫格连贯分镜」 | 可选 `userPrompt` |
| `plot_grid_4` | 剧情推演四宫格 | 起承转合四格 | 用户说了「剧情推演四宫格」 | 建议 `userPrompt` 写四格内容 |
| `frame_forward_3s` | 画面推演3秒后 | 推演下一瞬间 | 用户说了「画面推演3秒后」 | 可选 `userPrompt` |
| `frame_back_5s` | 画面推演5秒前 | 推演上一瞬间 | 用户说了「画面推演5秒前」 | 可选 `userPrompt` |
| `cinematic_lighting` | 电影级光影校正 | 宫格式光影方案 | 用户说了「电影级光影校正」 | 可选 `userPrompt` |
| `multi_cam_grid_9` | 多机位九宫格 | 多机位同场 | 用户说了「多机位九宫格」 | 可选 `userPrompt` |
| `face_tri_view` | 角色脸部三视图 | 正侧 3/4 | 用户说了「角色脸部三视图」 | 可选 `userPrompt` |
| `character_sheet` | 角色设定图 | 人设三视图/服装 | 用户说了「角色设定图」 | 可无参考图（文生）；有图更稳 |
| `character_tri_view` | 角色三视图 | 全身三视图 | 用户说了「角色三视图」 | 同上 |
| `scene_sheet` | 场景设定图 | 场景概念 | 用户说了「场景设定图」 | 可无参考图 |
| `product_sheet` | 产品设定图 | 产品多视角设定 | 用户说了「产品设定图」 | 可无参考图；电影级宣传片**不要**用这个当身份锁 |

- **前置**：多数要 `image_input`。设定图类（character/scene/product sheet、三视图）允许无图文生。
- **params.userPrompt**：用户的具体要求；会与后台模板拼接。用户说了主体/风格一定要写进去。
- **之后**：`grid_split`（默认 3×3，可用 `rows`/`cols`）切成多张小图再选用、连到视频。

### `panorama` / `panorama_720`

- **是什么**：全景/环绕空间图。
- **何时用**：用户说了「全景」或「720° 全景 / 720全景」。只说「环境环绕」时先问全名。
- **前置**：`image_input`；`userPrompt` 可选。

---

## 6. 修图类

| id | 是什么 | 何时用 | 前置 / params |
|----|--------|--------|----------------|
| `cutout` | 抠主体、去背景 | 用户说了「抠图」 | 必须有图 |
| `outpaint` | 扩画布 | 用户说了「扩图」 | 有图；复杂边距可能需用户在画布上拖 |
| `hd_upscale` | 变清晰 | 用户说了「高清」 | 有图；可选 `hdScale`（默认 2） |
| `portrait_adjust` | 人像质感 | 用户说了「人像调节」 | 有图；**必填** `userPrompt` |
| `emotion_adjust` | 表情 | 用户说了「情绪调节」 | 有图；**必填** `userPrompt` |
| `visual_style` | 套画风 | 用户点名画风且节点支持 | 有图；`userPrompt` 或节点已有风格 |
| `drawing_board_ai` | 画板辅助 | 用户在画板里改 | 强依赖画板 UI，优先请用户自己点 |

---

## 7. 分镜表工具（多镜叙事 / 爆款 / 出海）

节点：先 `add_node` type=`storyboard_grid`。剧本用 `text_input` 连到表的 `ref_in`。

| id | 是什么 | 何时用 | 前置 |
|----|--------|--------|------|
| `storyboard_table` | 剧本 → 表内镜头行 | 「按剧本拆镜」 | 分镜表；最好已连剧本文本 |
| `storyboard_from_image` | 图 → 填表 | 「从图读分镜」 | 表或有图节点 |
| `storyboard_from_video` | 视频拉片填表 | 「拉片 / 复刻」 | 有视频的 `video_input` |
| `storyboard_overseas_localize` | 按市场改文案/主体 | 出海 | 已有行的表；`params.targetMarketId`（US/JP/KR/…） |
| `text_subject` | 抽角色/场景/道具 | 要一致性 | 表或剧本 |
| `storyboard_subject_image` | 主体定妆图 | 「出人设图」 | 已有主体设定 |
| `storyboard_camera` | 补运镜词 | 表已有行 | 表 |
| `storyboard_video` | 补每镜视频提示词 | 表已有行 | 表 |
| `storyboard_sketch` | 每镜草图 | 「出分镜草图」 | 表已有画面描述 |
| `storyboard_batch_videos` | 按表批量出视频 | **须用户确认生成** | 表已就绪 |

---

## 8. 视频 / 音频菜单

前置：对应节点**已有媒体**。

| id | 是什么 | 何时用 | 注意 |
|----|--------|--------|------|
| `hd_upscale_video` | 变清晰 | 用户说了「视频高清」 | |
| `video_smart_matting` | 抠像 | 用户说了「智能抠像」 | 时长宜短 |
| `video_subject_remove` | 消除画面主体 | 「主体消除」或去掉片里的人/物 | `params.userPrompt` 说明去掉什么；源片 ≤12 秒 |
| `video_subject_edit` | 修改主体 | 「主体修改」或把片里的人改成动漫/换造型 | **必填** `params.userPrompt`（改成什么样）。源片 ≤12 秒，超长会自动截取前 12 秒。主模型见速查表 / `agent_canvas_tool_models.md`（默认 Seedance，后台可改）。 |
| `video_subject_replace` | 替换主体 | 「主体替换」或换成参考图里的人 | `params.userPrompt` + 参考图：先 `connect_nodes` 图片→视频 `ref_in`，或 `params.refImageNodeId`。源片 ≤12 秒。 |
| `video_subtitle_smart_erase` | 智能去字幕·智能擦除 | 用户说了该全名或「去字幕」 | |
| `video_subtitle_box_erase` | 智能去字幕·框选擦除 | 用户说了该全名 | 需框选，请用户手动；助手改用智能擦除 |
| `vocal_separate` | 人声分离 | 用户说了「人声分离」 | 音频或视频音轨 |
| `vocal_remove` | 消除人声 | 用户说了「消除人声」 | |

---

## 9. 选用口诀（须先点名）

- 用户说「多角度」→ `multi_angle`（再写 azimuth/elevation/shot）
- 用户说「打光」→ `lighting`
- 用户说「九宫格」→ `grid_9`；说了子功能全名才用对应 id
- 用户说「故事板」→ `storyboard`；说「调度故事板」→ `blocking_storyboard`
- 用户说「主体修改」或要把片里的人改成动漫/换造型 → `video_subject_edit`（`params.userPrompt` 必填，对已有 `video_input` 调用；主模型见速查表）
- 用户说「主体替换」→ `video_subject_replace`（先把参考图连到视频 `ref_in`）
- 用户说「宫格切分」→ `grid_split`
- 没说官方名、又不是技能包/整段创作目标 → `ask_user` 请说全名，不要猜
- 已选技能包或用户说的是整段创作目标（宣传片/复刻/出海）→ 按配方布点，不必每步问「请说故事板」
- 画布已有能用的结果 → 改参或对那张图跑**用户点名的**工具，不要重复出板
