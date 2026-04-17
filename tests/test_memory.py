from nanoharness.components.memory.simple_memory import SimpleMemoryManager
from nanoharness.components.tools.dict_registry import DictToolRegistry
from nanoharness.components.memory.simple_memory import MemoryToolMixin


class TestSimpleMemoryManager:
    def test_store_and_recall(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        mem.store("fact", "the sky is blue")
        results = mem.recall("sky")
        assert len(results) == 1
        assert results[0].content == "the sky is blue"

    def test_recall_no_match(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        mem.store("fact", "the sky is blue")
        assert mem.recall("ocean") == []

    def test_recall_top_k(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        for i in range(5):
            mem.store(f"item{i}", f"test content {i}")
        results = mem.recall("test", top_k=2)
        assert len(results) == 2

    def test_persistence(self, tmp_path):
        path = tmp_path / "mem.json"
        mem1 = SimpleMemoryManager(str(path))
        mem1.store("key1", "value1")
        del mem1

        mem2 = SimpleMemoryManager(str(path))
        results = mem2.recall("value1")
        assert len(results) == 1

    def test_working_memory(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        mem.set_working({"step": 1, "plan": "think hard"})
        assert mem.get_working()["step"] == 1

    def test_clear_working(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        mem.set_working({"x": 1})
        mem.clear_working()
        assert mem.get_working() == {}

    def test_reset(self, tmp_path):
        path = tmp_path / "mem.json"
        mem = SimpleMemoryManager(str(path))
        mem.store("k", "v")
        mem.set_working({"x": 1})
        mem.reset()
        assert mem.get_working() == {}
        assert mem.recall("v") == []
        assert not path.exists()


class TestMemoryToolMixin:
    def test_register_and_call(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        reg = DictToolRegistry()
        MemoryToolMixin.register(mem, reg)

        schemas = reg.get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "memory_store" in names
        assert "memory_recall" in names

        reg.call("memory_store", {"key": "test", "content": "hello"})
        result = reg.call("memory_recall", {"query": "hello"})
        assert "hello" in result

    def test_recall_empty(self, tmp_path):
        mem = SimpleMemoryManager(str(tmp_path / "mem.json"))
        reg = DictToolRegistry()
        MemoryToolMixin.register(mem, reg)

        result = reg.call("memory_recall", {"query": "nothing"})
        assert "No matching memories" in result
