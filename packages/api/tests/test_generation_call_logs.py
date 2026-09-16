from app.services.generation_call_logs import resolve_submit_source


def test_resolve_submit_source_dedupe():
    assert resolve_submit_source(is_duplicate=True, idempotency_key="abc") == "dedupe"


def test_resolve_submit_source_manual():
    assert resolve_submit_source(is_duplicate=False, idempotency_key=None) == "manual"


def test_resolve_submit_source_auto_prefix():
    assert (
        resolve_submit_source(is_duplicate=False, idempotency_key="storyboard-sketch-node-1-hash")
        == "auto"
    )


def test_resolve_submit_source_explicit():
    assert resolve_submit_source(is_duplicate=False, idempotency_key=None, explicit="auto") == "auto"
