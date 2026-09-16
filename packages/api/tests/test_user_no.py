from app.services.user_no import format_user_no, parse_user_no_seq


def test_format_user_no():
    assert format_user_no(10001) == "U10001"
    assert format_user_no(10002) == "U10002"


def test_parse_user_no_seq():
    assert parse_user_no_seq("U10001") == 10001
    assert parse_user_no_seq("u10001") == 10001
    assert parse_user_no_seq("not-a-user-no") is None
    assert parse_user_no_seq(None) is None