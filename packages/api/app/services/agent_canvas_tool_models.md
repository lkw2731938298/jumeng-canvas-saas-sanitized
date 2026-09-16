# 画布工具 · 主模型对照

助手调用 `run_canvas_tool(tool=…)` 时，使用下表该功能的**主模型**（后台「模型开关」可改；
每轮【画布工具速查】以实配为准）。前端执行时也会按同一配置提交。

| 官方名 | tool id | 类别 | 主模型 | 展示名 |
|--------|---------|------|--------|--------|
| 多角度 | `multi_angle` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 打光 | `lighting` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 全景 | `panorama` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 九宫格 | `grid_9` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 25宫格连贯分镜 | `grid_25` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 剧情推演四宫格 | `plot_grid_4` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 画面推演 - 3秒后 | `frame_forward_3s` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 画面推演 - 5秒前 | `frame_back_5s` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 电影级光影校正 | `cinematic_lighting` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 多机位九宫格 | `multi_cam_grid_9` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 角色脸部三视图 | `face_tri_view` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 角色设定图 | `character_sheet` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 角色三视图 | `character_tri_view` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 场景设定图 | `scene_sheet` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 产品设定图 | `product_sheet` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 扩图 | `outpaint` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 抠图 | `cutout` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 高清 | `hd_upscale` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 人像调节 | `portrait_adjust` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 情绪调节 | `emotion_adjust` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 视频高清 | `hd_upscale_video` | video | `rh_seedance_20_r2v` | RH Seedance 2.0 多模态 |
| 画板 AI | `drawing_board_ai` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 故事板 | `storyboard` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 调度故事板 | `blocking_storyboard` | image | `nano_pro_i2i` | 全能图片 Pro 图生图 |
| 智能抠像 | `video_smart_matting` | video | `rh_seedance_20_r2v` | RH Seedance 2.0 多模态 |
| 主体消除 | `video_subject_remove` | video | `rh_seedance_20_r2v` | RH Seedance 2.0 多模态 |
| 主体修改 | `video_subject_edit` | video | `rh_seedance_20_r2v` | RH Seedance 2.0 多模态 |
| 主体替换 | `video_subject_replace` | video | `rh_seedance_20_r2v` | RH Seedance 2.0 多模态 |
| 智能去字幕·智能擦除 | `video_subtitle_smart_erase` | video | `jumengai_volc_subtitle_erase` | 火山字幕擦除（精准版） |
| 智能去字幕·框选擦除 | `video_subtitle_box_erase` | video | `jumengai_volc_subtitle_erase` | 火山字幕擦除（精准版） |
| 人声分离 | `vocal_separate` | audio | `rh_audio_extract_vocals` | 分离音频-Vocals |
| 消除人声 | `vocal_remove` | audio | `rh_audio_extract_other` | 分离音频-Other |
| 分镜表解析 | `storyboard_table` | text | `doubao_pro` | 豆包 Pro |
| 爆款拉片复刻 | `storyboard_from_video` | text | `doubao_pro` | 豆包 Pro |
| 图片提取分镜 | `storyboard_from_image` | text | `doubao_pro` | 豆包 Pro |
| 一键出海本地化 | `storyboard_overseas_localize` | text | `doubao_pro` | 豆包 Pro |
| 主体提取 | `text_subject` | text | `doubao_pro` | 豆包 Pro |
| 运镜提示词 | `storyboard_camera` | text | `doubao_pro` | 豆包 Pro |
| 视频提示词 | `storyboard_video` | text | `doubao_pro` | 豆包 Pro |
| 分镜草图 | `storyboard_sketch` | image | `doubao_image` | 即梦 图片 |
| 主体生图 | `storyboard_subject_image` | image | `doubao_image` | 即梦 图片 |

## 无独立主模型的工具

| tool id | 说明 |
|---------|------|
| `grid_split` | 本地切图不调模型 |
| `storyboard_batch_videos` | 用各镜视频节点已有模型 |
