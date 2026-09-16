from app.services.project_scope import (
    _storage_folder_lookup_candidates,
    oss_projects_folder_from_key,
    project_id_from_oss_key,
)


def test_oss_projects_folder_from_key():
    key = "Ihuabu/projects/2af137ab3b4949d08193af529e2e28b8/assets/video/x.mp4"
    assert oss_projects_folder_from_key(key) == "2af137ab3b4949d08193af529e2e28b8"


def test_project_id_from_oss_key_numeric_folder():
    assert project_id_from_oss_key("canvas/projects/42/assets/a.png") == "42"


def test_project_id_from_oss_key_storage_folder_requires_db():
    key = "Ihuabu/projects/2af137ab3b4949d08193af529e2e28b8/assets/video/x.mp4"
    assert project_id_from_oss_key(key) is None


def test_storage_folder_lookup_candidates():
    folder = "2af137ab3b4949d08193af529e2e28b8"
    candidates = _storage_folder_lookup_candidates(folder)
    assert folder in candidates
    assert "2af137ab-3b49-49d0-8193-af529e2e28b8" in candidates
