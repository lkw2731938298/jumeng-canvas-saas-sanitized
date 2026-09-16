from app.services.project_scope import oss_key_belongs_to_project

FOLDER = "2af137ab3b4949d08193af529e2e28b8"
KEY = f"Ihuabu/projects/{FOLDER}/assets/image/test.jpg"


def test_oss_key_belongs_with_storage_folder():
    assert oss_key_belongs_to_project(KEY, "22", storage_folder=FOLDER)


def test_oss_key_belongs_legacy_numeric_folder():
    legacy_key = "Ihuabu/projects/22/assets/image/test.jpg"
    assert oss_key_belongs_to_project(legacy_key, "22")


def test_oss_key_rejects_other_project():
    assert not oss_key_belongs_to_project(KEY, "99", storage_folder=FOLDER)


def test_oss_key_belongs_without_storage_folder_for_legacy_numeric():
    legacy_key = "canvas/projects/42/assets/a.png"
    assert oss_key_belongs_to_project(legacy_key, "42")
