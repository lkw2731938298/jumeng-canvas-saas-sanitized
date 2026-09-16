"""点名短出片 vs 技能配方：不要互相误伤。"""

from app.services.agent_generate_intent import (
    filter_client_calls_for_intent,
    is_short_video_shot,
    is_storyboard_sheet_only,
    parse_named_nodes,
    resolve_video_generate_targets,
    should_stop_after_tool_results,
    specified_nodes,
    successful_generate_in_tool_results,
    successful_sheet_tool_in_tool_results,
    wants_generate,
    wants_video,
)


def test_parse_named_image_node():
    text = (
        "[节点:产品图|nodeId=image_input_1|type=image_input] "
        "asset 生成视频"
    )
    named = parse_named_nodes(text)
    assert named[0]["id"] == "image_input_1"
    assert wants_video(text)
    assert wants_generate(text)
    assert is_short_video_shot(text)


def test_focused_alone_is_not_named():
    snap = {
        "nodes": [{"id": "img1", "type": "image_input", "focused": True}],
        "edges": [],
    }
    assert specified_nodes("生成视频", snap) == []


def test_xuanchuanpian_is_not_short_shot():
    """做宣传片 / 确认生成 走技能配方，不是短出片硬拦。"""
    assert is_short_video_shot("做个电影级宣传片") is False
    assert is_short_video_shot("确认生成") is False
    assert is_short_video_shot("生成同款") is False
    assert is_short_video_shot("生成视频") is True


def test_named_image_generate_video_blocks_storyboard():
    snap = {
        "nodes": [
            {"id": "img1", "type": "image_input", "hasMedia": True},
            {"id": "vid1", "type": "video_input", "hasMedia": False},
        ],
        "edges": [{"source": "img1", "target": "vid1"}],
    }
    named = specified_nodes(
        "[节点:产品图|nodeId=img1|type=image_input] 生成视频", snap
    )
    targets, allow_new = resolve_video_generate_targets(named, snap, short_shot=True)
    assert targets == ["vid1"]
    assert allow_new is False

    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "video_input"}),
            ("c2", "run_canvas_tool", {"tool": "storyboard", "nodeId": "img1"}),
            ("c3", "generate_node", {"nodeId": "other"}),
        ],
        user_text="[节点:产品图|nodeId=img1|type=image_input] 生成视频",
        snapshot=snap,
        named=named,
        already_generated=False,
    )
    assert all(name != "add_node" for _, name, _ in out)
    assert all(
        not (name == "run_canvas_tool" and args.get("tool") == "storyboard")
        for _, name, args in out
    )
    gens = [c for c in out if c[1] == "generate_node"]
    assert len(gens) == 1
    assert gens[0][2]["nodeId"] == "vid1"


def test_recipe_confirm_with_ref_video_allows_batch():
    """爆款/出海：准星点着参考片说确认生成，仍允许 storyboard_batch_videos。"""
    snap = {
        "nodes": [
            {"id": "ref_vid", "type": "video_input", "hasMedia": True, "assetId": "a1"},
            {"id": "grid1", "type": "storyboard_grid"},
        ],
        "edges": [],
    }
    named = specified_nodes(
        "[节点:参考|nodeId=ref_vid|type=video_input|assetId=a1] 确认生成",
        snap,
    )
    out = filter_client_calls_for_intent(
        [
            ("c1", "run_canvas_tool", {"tool": "storyboard_batch_videos", "nodeId": "grid1"}),
            ("c2", "generate_node", {"nodeId": "ref_vid"}),
        ],
        user_text="确认生成",
        snapshot=snap,
        named=named,
        already_generated=False,
    )
    tools = [(n, a.get("tool")) for _, n, a in out if n == "run_canvas_tool"]
    assert ("run_canvas_tool", "storyboard_batch_videos") in tools
    # 不要对已有媒体的参考片再 generate
    assert all(
        not (n == "generate_node" and a.get("nodeId") == "ref_vid")
        for _, n, a in out
    )


def test_recipe_confirm_named_idle_videos_keeps_all():
    """电影级：准星点了空镜头 + 确认生成，可对点名镜头 generate，不拦。"""
    snap = {
        "nodes": [
            {"id": "v1", "type": "video_input", "hasMedia": False},
            {"id": "v2", "type": "video_input", "hasMedia": False},
        ],
        "edges": [],
    }
    named = specified_nodes(
        "[节点:镜1|nodeId=v1|type=video_input]\n[节点:镜2|nodeId=v2|type=video_input]\n确认生成",
        snap,
    )
    out = filter_client_calls_for_intent(
        [
            ("c1", "generate_node", {"nodeId": "v1"}),
            ("c2", "generate_node", {"nodeId": "v2"}),
        ],
        user_text="确认生成",
        snapshot=snap,
        named=named,
        already_generated=False,
    )
    gens = [a.get("nodeId") for _, n, a in out if n == "generate_node"]
    assert gens == ["v1", "v2"]


def test_xuanchuanpian_with_named_image_does_not_block_storyboard():
    """准星产品图 + 做宣传片：仍允许出板（配方），不是短出片。"""
    snap = {
        "nodes": [{"id": "img1", "type": "image_input", "hasMedia": True}],
        "edges": [],
    }
    named = specified_nodes(
        "[节点:产品|nodeId=img1|type=image_input] 做个电影级宣传片", snap
    )
    assert is_short_video_shot("做个电影级宣传片") is False
    out = filter_client_calls_for_intent(
        [
            ("c1", "run_canvas_tool", {"tool": "storyboard", "nodeId": "img1"}),
            ("c2", "add_node", {"type": "video_input"}),
            ("c3", "add_node", {"type": "video_input"}),
        ],
        user_text="做个电影级宣传片",
        snapshot=snap,
        named=named,
        already_generated=False,
    )
    assert any(
        n == "run_canvas_tool" and a.get("tool") == "storyboard" for _, n, a in out
    )
    assert len([c for c in out if c[1] == "add_node"]) == 2


def test_named_image_without_video_allows_one_add_on_short_shot():
    snap = {"nodes": [{"id": "img1", "type": "image_input", "hasMedia": True}], "edges": []}
    named = specified_nodes(
        "[节点:产品图|nodeId=img1|type=image_input] 生成视频", snap
    )
    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "video_input"}),
            ("c2", "add_node", {"type": "video_input"}),
            ("c3", "generate_node", {"nodeId": "tmp"}),
        ],
        user_text="生成视频",
        snapshot=snap,
        named=named,
        already_generated=False,
    )
    assert len([c for c in out if c[1] == "add_node"]) == 1
    assert len([c for c in out if c[1] == "generate_node"]) == 1


def test_already_generated_drops_more_ops():
    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "video_input"}),
            ("c2", "generate_node", {"nodeId": "vid2"}),
            ("c3", "run_canvas_tool", {"tool": "storyboard_batch_videos", "nodeId": "t"}),
            ("c4", "layout_hint", {"nodeId": "vid2", "x": 1, "y": 2}),
        ],
        user_text="生成视频",
        snapshot={},
        named=[{"id": "img1", "type": "image_input"}],
        already_generated=True,
    )
    assert [n for _, n, _ in out] == ["layout_hint"]


def test_successful_generate_detects_tool_result():
    inflight = {
        "assistant": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "generate_node",
                        "arguments": '{"nodeId":"vid1"}',
                    },
                }
            ],
        }
    }
    results = [{"toolCallId": "call_1", "ok": True, "result": "已提交生成", "nodeId": "vid1"}]
    assert successful_generate_in_tool_results(inflight, results) is True
    assert successful_generate_in_tool_results(None, [{"ok": True, "result": "已提交生成"}]) is True


def test_sheet_only_detects_duration_as_not_video():
    """「5s」口癖不算要视频；仍属只要一张板。"""
    text = "生成一张真实感的太空漂流故事板，5s的"
    assert is_storyboard_sheet_only(text) is True
    assert is_storyboard_sheet_only("生成故事板并出视频") is False
    assert is_storyboard_sheet_only("做个故事板再搭镜头") is False


def test_sheet_only_strips_video_and_second_board():
    text = "生成一张真实感的太空漂流故事板，5s的"
    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "image_input", "label": "故事板锚点"}),
            ("c2", "add_node", {"type": "video_input", "label": "太空漂流视频"}),
            ("c3", "run_canvas_tool", {"tool": "storyboard", "nodeId": "a"}),
            ("c4", "run_canvas_tool", {"tool": "storyboard", "nodeId": "b"}),
            ("c5", "connect_nodes", {"source": "a", "target": "v"}),
            ("c6", "generate_node", {"nodeId": "v"}),
        ],
        user_text=text,
        snapshot={"nodes": [], "edges": []},
        named=[],
        already_generated=False,
    )
    assert len([c for c in out if c[1] == "add_node"]) == 1
    assert out[0][2].get("type") == "image_input"
    sheets = [
        c
        for c in out
        if c[1] == "run_canvas_tool" and c[2].get("tool") == "storyboard"
    ]
    assert len(sheets) == 1
    assert all(c[1] != "connect_nodes" for c in out)
    assert all(c[1] != "generate_node" for c in out)


def test_sheet_success_stops_continue():
    text = "生成一张真实感的太空漂流故事板，5s的"
    inflight = {
        "assistant": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_sb",
                    "function": {
                        "name": "run_canvas_tool",
                        "arguments": '{"tool":"storyboard","nodeId":"a"}',
                    },
                }
            ],
        }
    }
    results = [
        {
            "toolCallId": "call_sb",
            "ok": True,
            "result": "工具 storyboard 已执行；结果节点 n1",
        }
    ]
    assert successful_sheet_tool_in_tool_results(inflight, results) is True
    stop, msg = should_stop_after_tool_results(
        user_text=text, inflight=inflight, tool_results=results
    )
    assert stop is True
    assert "故事板" in msg


def test_sheet_only_stops_when_snapshot_has_board():
    text = "生成一张真实感的太空漂流故事板，5s的"
    snap = {
        "nodes": [
            {
                "id": "n1",
                "type": "image_input",
                "label": "太空漂流故事板",
                "role": "storyboard_sheet",
                "hasMedia": True,
                "assetId": "a1",
            }
        ]
    }
    stop, _ = should_stop_after_tool_results(
        user_text=text, inflight=None, tool_results=None, snapshot=snap
    )
    assert stop is True


def test_style_words_still_sheet_only():
    """电影级/出海风/出片感 ≠ 要视频或整段配方。"""
    assert is_storyboard_sheet_only("做一张电影级故事板") is True
    assert is_storyboard_sheet_only("出海风的故事板") is True
    assert is_storyboard_sheet_only("出片感的太空漂流故事板") is True
    assert is_storyboard_sheet_only("做个故事板再搭镜头") is False
    assert is_storyboard_sheet_only("故事板然后做宣传片") is False


def test_atomic_tool_success_stops_continue():
    """多角度等单工具成功后必须收束，禁止续跑再铺点。"""
    inflight = {
        "assistant": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_ma",
                    "function": {
                        "name": "run_canvas_tool",
                        "arguments": '{"tool":"multi_angle","nodeId":"img1"}',
                    },
                }
            ],
        }
    }
    results = [
        {
            "toolCallId": "call_ma",
            "ok": True,
            "result": "工具 multi_angle 已执行；结果节点 n2",
        }
    ]
    stop, msg = should_stop_after_tool_results(
        user_text="多角度看背面",
        inflight=inflight,
        tool_results=results,
    )
    assert stop is True
    assert "工具" in msg or "执行" in msg


def test_multi_angle_strips_video_layout():
    """只说多角度时禁止顺手搭空视频。"""
    out = filter_client_calls_for_intent(
        [
            ("c1", "run_canvas_tool", {"tool": "multi_angle", "nodeId": "img1"}),
            ("c2", "add_node", {"type": "video_input", "label": "镜头1"}),
            ("c3", "generate_node", {"nodeId": "vid_tmp"}),
        ],
        user_text="多角度看背面",
        snapshot={
            "nodes": [
                {"id": "img1", "type": "image_input", "hasMedia": True},
                {"id": "vid_tmp", "type": "video_input", "hasMedia": False},
            ]
        },
        named=[],
        already_generated=False,
    )
    assert any(
        n == "run_canvas_tool" and a.get("tool") == "multi_angle" for _, n, a in out
    )
    assert all(
        not (n == "add_node" and a.get("type") == "video_input") for _, n, a in out
    )
    assert all(n != "generate_node" for _, n, _ in out)


def test_xuanchuanpian_still_allows_video_layout():
    """做宣传片仍可搭空视频节点，但未确认前不 generate。"""
    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "video_input"}),
            ("c2", "generate_node", {"nodeId": "v1"}),
        ],
        user_text="做个电影级宣传片",
        snapshot={"nodes": [{"id": "v1", "type": "video_input", "hasMedia": False}]},
        named=[],
        already_generated=False,
    )
    assert len([c for c in out if c[1] == "add_node"]) == 1
    assert all(c[1] != "generate_node" for c in out)


def test_video_generate_hard_gate_collects_blocks():
    """未确认时 generate / batch 不进投影，并记入硬闸反馈。"""
    from app.services.agent_generate_intent import apply_client_intent_filter

    filtered = apply_client_intent_filter(
        [
            ("c1", "add_node", {"type": "video_input"}),
            ("c2", "generate_node", {"nodeId": "v1"}),
            ("c3", "run_canvas_tool", {"tool": "storyboard_batch_videos", "nodeId": "g1"}),
        ],
        user_text="做个电影级宣传片",
        snapshot={"nodes": [{"id": "v1", "type": "video_input", "hasMedia": False}]},
        named=[],
        already_generated=False,
    )
    assert len([c for c in filtered.calls if c[1] == "add_node"]) == 1
    assert all(c[1] != "generate_node" for c in filtered.calls)
    assert all(
        not (c[1] == "run_canvas_tool" and c[2].get("tool") == "storyboard_batch_videos")
        for c in filtered.calls
    )
    assert len(filtered.video_gate_blocks) >= 2
    assert all("硬闸" in note for _, note in filtered.video_gate_blocks)


def test_confirm_generate_clears_video_hard_gate():
    from app.services.agent_generate_intent import apply_client_intent_filter

    filtered = apply_client_intent_filter(
        [
            ("c1", "generate_node", {"nodeId": "v1"}),
            ("c2", "run_canvas_tool", {"tool": "storyboard_batch_videos"}),
        ],
        user_text="确认生成",
        snapshot={"nodes": [{"id": "v1", "type": "video_input"}]},
        named=[],
        already_generated=False,
    )
    assert filtered.video_gate_blocks == []
    assert any(c[1] == "generate_node" for c in filtered.calls)
    assert any(
        c[1] == "run_canvas_tool" and c[2].get("tool") == "storyboard_batch_videos"
        for c in filtered.calls
    )


def test_image_only_poster_strips_video():
    """做个海报 / 生一张：禁止搭视频与分镜管线。"""
    from app.services.agent_generate_intent import is_image_only_request

    assert is_image_only_request("做个海报") is True
    assert is_image_only_request("生一张") is True
    assert is_image_only_request("做个海报再出视频") is False
    out = filter_client_calls_for_intent(
        [
            ("c1", "add_node", {"type": "image_input"}),
            ("c2", "add_node", {"type": "video_input"}),
            ("c3", "run_canvas_tool", {"tool": "storyboard_table", "nodeId": "t"}),
            ("c4", "generate_node", {"nodeId": "img1"}),
        ],
        user_text="做个海报",
        snapshot={"nodes": [{"id": "img1", "type": "image_input"}]},
        named=[],
        already_generated=False,
    )
    assert len([c for c in out if c[1] == "add_node"]) == 1
    assert out[0][2].get("type") == "image_input"
    assert all(
        not (n == "run_canvas_tool" and a.get("tool") == "storyboard_table")
        for _, n, a in out
    )
    assert any(n == "generate_node" for _, n, _ in out)


def test_named_table_stops_without_chain():
    """点名做分镜表且工具成功 → 收束；带「然后出视频」不收束。"""
    inflight = {
        "assistant": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_t",
                    "function": {
                        "name": "run_canvas_tool",
                        "arguments": '{"tool":"storyboard_table","nodeId":"g1"}',
                    },
                }
            ],
        }
    }
    results = [
        {"toolCallId": "call_t", "ok": True, "result": "工具 storyboard_table 已执行"}
    ]
    stop, _ = should_stop_after_tool_results(
        user_text="做个分镜表",
        inflight=inflight,
        tool_results=results,
    )
    assert stop is True
    stop2, _ = should_stop_after_tool_results(
        user_text="做个分镜表然后出视频",
        inflight=inflight,
        tool_results=results,
    )
    assert stop2 is False
