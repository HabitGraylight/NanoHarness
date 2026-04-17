from nanoharness.components.hooks.simple_hooks import SimpleHookManager


class TestSimpleHookManager:
    def test_register_and_trigger(self):
        hooks = SimpleHookManager()
        collected = []
        hooks.register("on_task_start", lambda d: collected.append(d))
        hooks.trigger("on_task_start", "hello")
        assert collected == ["hello"]

    def test_multiple_hooks_same_stage(self):
        hooks = SimpleHookManager()
        results = []
        hooks.register("on_step_end", lambda d: results.append(f"a:{d}"))
        hooks.register("on_step_end", lambda d: results.append(f"b:{d}"))
        hooks.trigger("on_step_end", "step0")
        assert results == ["a:step0", "b:step0"]

    def test_trigger_unregistered_stage(self):
        hooks = SimpleHookManager()
        # Should not raise
        hooks.trigger("nonexistent", "data")

    def test_reset(self):
        hooks = SimpleHookManager()
        hooks.register("on_task_start", lambda d: None)
        hooks.reset()
        # After reset, triggering should be a no-op
        hooks.trigger("on_task_start", "data")  # no error
