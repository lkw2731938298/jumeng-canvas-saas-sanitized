"""助手 list_models 能力卡：输入类型与上限。"""

from app.services.agent_model_caps import list_models_for_agent


def test_list_models_includes_input_limits():
    text = list_models_for_agent(category="video")
    assert "`rh_seedance_25_r2v`" in text
    assert "图≤30" in text
    assert "视频≤10" in text
    assert "时长4-30s" in text or "4–30" in text
    assert "`huahu_seedance_20_r2v`" in text
    assert "图≤9" in text


def test_list_models_detail_by_id():
    text = list_models_for_agent(model_id="qwen_image_30_pro")
    assert "最多参考图：3" in text
    assert "qwen_image_30_pro" in text
    assert "图生图" in text or "参考图" in text


def test_list_models_unknown_id():
    text = list_models_for_agent(model_id="not_a_real_model")
    assert "没有模型" in text


def test_list_models_image_t2i_no_fake_ref_cap():
    text = list_models_for_agent(category="image")
    assert "`nano_g_t2i`" in text
    assert "`nano_g_i2i`" in text
    # 图生图变体有上限；文生图变体不应写成图≤10
    i2i_line = next(x for x in text.splitlines() if "`nano_g_i2i`" in x)
    t2i_line = next(x for x in text.splitlines() if "`nano_g_t2i`" in x)
    assert "图≤10" in i2i_line
    assert "图≤10" not in t2i_line
