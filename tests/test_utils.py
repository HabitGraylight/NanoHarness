from nanoharness.utils.logger import get_logger
from nanoharness.utils.token_counter import count_messages_tokens, count_tokens


class TestTokenCounter:
    def test_count_tokens_nonempty(self):
        assert count_tokens("hello") >= 1

    def test_count_tokens_longer_text(self):
        short = count_tokens("hi")
        long = count_tokens("a" * 100)
        assert long > short

    def test_count_messages_tokens(self):
        msgs = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        total = count_messages_tokens(msgs)
        assert total > 0

    def test_count_messages_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "search", "arguments": {"q": "test"}}],
            },
        ]
        total = count_messages_tokens(msgs)
        assert total > 0


class TestLogger:
    def test_get_logger(self):
        log = get_logger("test")
        assert log.name == "test"
        assert len(log.handlers) >= 1

    def test_get_logger_idempotent(self):
        log1 = get_logger("test.idem")
        log2 = get_logger("test.idem")
        assert log1 is log2
        assert len(log1.handlers) == 1
