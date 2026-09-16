from app.core.entity_ids import format_user_display_id, parse_user_display_id


def test_format_user_display_id():
    assert format_user_display_id(10001) == "U10001"
    assert format_user_display_id(10002) == "U10002"


def test_parse_user_display_id():
    assert parse_user_display_id("U10001") == 10001
    assert parse_user_display_id("u10001") == 10001
    assert parse_user_display_id("10001") == 10001
    assert parse_user_display_id("not-a-user-no") is None
    assert parse_user_display_id(None) is None
