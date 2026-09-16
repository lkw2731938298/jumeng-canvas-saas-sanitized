"""用户点名画布工具必须对上官方中文名；高置信口语走软映射。"""

from app.services.agent_canvas_tool_names import (
    format_named_tools_hint,
    match_named_canvas_tools,
    match_soft_effect_tools,
)


def test_exact_names_map_to_tool_ids():
    assert match_named_canvas_tools("帮我做多角度") == [("多角度", "multi_angle")]
    assert match_named_canvas_tools("打光") == [("打光", "lighting")]
    assert match_named_canvas_tools("做九宫格") == [("九宫格", "grid_9")]
    assert match_named_canvas_tools("出一张故事板") == [("故事板", "storyboard")]
    assert match_named_canvas_tools("抠图") == [("抠图", "cutout")]


def test_longer_name_wins_over_substring():
    assert match_named_canvas_tools("调度故事板") == [("调度故事板", "blocking_storyboard")]
    assert match_named_canvas_tools("多机位九宫格") == [("多机位九宫格", "multi_cam_grid_9")]
    assert match_named_canvas_tools("九宫格·角色设定图") == [("九宫格·角色设定图", "character_sheet")]
    assert match_named_canvas_tools("视频高清") == [("视频高清", "hd_upscale_video")]


def test_child_full_name_not_grid_9():
    assert match_named_canvas_tools("角色设定图") == [("角色设定图", "character_sheet")]
    assert match_named_canvas_tools("电影级光影校正") == [("电影级光影校正", "cinematic_lighting")]
    assert match_named_canvas_tools("25宫格连贯分镜") == [("25宫格连贯分镜", "grid_25")]


def test_fuzzy_phrases_do_not_match_official():
    """官方名匹配器仍不认口语；由软映射承接。"""
    assert match_named_canvas_tools("看背面") == []
    assert match_named_canvas_tools("光不好") == []
    assert match_named_canvas_tools("换个角度") == []
    assert match_named_canvas_tools("做几个变体") == []
    assert match_named_canvas_tools("人设图") == []
    assert match_named_canvas_tools("去背景") == []


def test_soft_effect_maps_common_phrases():
    soft = match_soft_effect_tools("帮我看背面")
    assert soft[0][1] == "multi_angle"
    assert "180" in soft[0][2]
    assert match_soft_effect_tools("光不好")[0][1] == "lighting"
    assert match_soft_effect_tools("改一下这张图的光")[0][1] == "lighting"
    assert match_soft_effect_tools("换个角度")[0][1] == "multi_angle"
    assert match_soft_effect_tools("做几个变体")[0][1] == "grid_9"
    assert match_soft_effect_tools("去背景")[0][1] == "cutout"
    assert match_soft_effect_tools("人设图")[0][1] == "character_sheet"
    assert match_soft_effect_tools("做个分镜表")[0][1] == "storyboard_table"
    assert match_soft_effect_tools("把西装男换成动漫角色")[0][1] == "video_subject_edit"
    assert match_soft_effect_tools("换主体")[0][1] == "video_subject_replace"
    assert match_soft_effect_tools("随便聊聊") == []


def test_official_video_frame_edit_names():
    assert match_named_canvas_tools("主体修改") == [("主体修改", "video_subject_edit")]
    assert match_named_canvas_tools("主体替换") == [("主体替换", "video_subject_replace")]
    assert match_named_canvas_tools("主体消除") == [("主体消除", "video_subject_remove")]


def test_named_plus_params_still_hits_tool():
    hits = match_named_canvas_tools("多角度看背面")
    assert hits == [("多角度", "multi_angle")]


def test_two_named_tools_in_order():
    hits = match_named_canvas_tools("先打光再多角度")
    assert hits == [("打光", "lighting"), ("多角度", "multi_angle")]


def test_hint_block_lists_tool_id():
    text = format_named_tools_hint("请用故事板")
    assert "tool=storyboard" in text
    assert "故事板" in text
    soft_hint = format_named_tools_hint("看背面")
    assert "tool=multi_angle" in soft_hint
    assert "推断工具" in soft_hint
    assert "azimuth=180" in soft_hint


def test_subject_edit_soft_and_hint():
    soft = match_soft_effect_tools("把穿黑色西服的男生改成穿白色裙子的女生")
    assert soft[0][1] == "video_subject_edit"
    hint = format_named_tools_hint("主体修改")
    assert "tool=video_subject_edit" in hint
    assert "run_canvas_tool" in hint


def test_soft_outfit_change_maps_subject_edit():
    assert match_soft_effect_tools("改成穿白色裙子")[0][1] == "video_subject_edit"
    assert match_soft_effect_tools("西装男改成白裙女生")[0][1] == "video_subject_edit"
