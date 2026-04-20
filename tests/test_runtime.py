from src.channel_runtime_impl import build_default_channel_runtime
from src.runtime import resolve_weixin_channel_runtime, set_weixin_runtime


class TestResolveWeixinChannelRuntime:
    async def test_prefers_explicit_channel_runtime(self):
        runtime = build_default_channel_runtime()

        resolved = await resolve_weixin_channel_runtime(runtime)

        assert resolved is runtime

    async def test_falls_back_to_registered_runtime_channel(self):
        runtime = build_default_channel_runtime()
        set_weixin_runtime({"channel": runtime})

        resolved = await resolve_weixin_channel_runtime(None)

        assert resolved is runtime

    async def test_falls_back_to_builtin_runtime(self):
        set_weixin_runtime({})

        resolved = await resolve_weixin_channel_runtime(None)

        assert resolved
        assert "reply" in resolved
        assert "routing" in resolved

    async def test_reply_dispatcher_runs_typing_lifecycle(self):
        runtime = build_default_channel_runtime()
        calls = []

        bundle = runtime["reply"]["create_reply_dispatcher_with_typing"](
            {
                "typing_callbacks": {
                    "start": lambda: calls.append("start"),
                    "stop": lambda: calls.append("stop"),
                },
                "typing_keepalive_interval_ms": 1000,
            }
        )

        await runtime["reply"]["with_reply_dispatcher"](
            {
                "dispatcher": bundle["dispatcher"],
                "run": lambda: calls.append("run"),
            }
        )

        assert calls == ["start", "run", "stop"]
