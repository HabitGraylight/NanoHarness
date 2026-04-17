import pytest

from nanoharness.components.state.json_store import JsonStateStore


class TestJsonStateStore:
    def test_save_and_load(self, tmp_path):
        store = JsonStateStore(str(tmp_path / "state.json"))
        store.save_state({"step": 1, "status": "running"})
        loaded = store.load_state()
        assert loaded == {"step": 1, "status": "running"}

    def test_load_nonexistent(self, tmp_path):
        store = JsonStateStore(str(tmp_path / "nope.json"))
        assert store.load_state() == {}

    def test_overwrite(self, tmp_path):
        store = JsonStateStore(str(tmp_path / "state.json"))
        store.save_state({"v": 1})
        store.save_state({"v": 2})
        assert store.load_state() == {"v": 2}

    def test_reset(self, tmp_path):
        path = tmp_path / "state.json"
        store = JsonStateStore(str(path))
        store.save_state({"x": 1})
        assert path.exists()
        store.reset()
        assert not path.exists()

    def test_unicode_content(self, tmp_path):
        store = JsonStateStore(str(tmp_path / "state.json"))
        store.save_state({"msg": "你好世界"})
        assert store.load_state()["msg"] == "你好世界"
