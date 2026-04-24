from plugins.memos_palace import MemosPalaceProvider, register


def test_prefetch_consumes_cached_result_once():
    provider = MemosPalaceProvider()
    provider._prefetch_result = "<memory>cached</memory>"

    assert provider.prefetch("") == "<memory>cached</memory>"
    assert provider.prefetch("") == ""


def test_explicit_store_fails_when_memos_disabled():
    provider = MemosPalaceProvider()
    provider._memos_enabled = False

    result = provider._memos_store_explicit("remember this", memory_type="note", tier="")

    assert result == {"success": False, "error": "memOS not configured"}


def test_register_is_noop_without_memory_provider_context():
    class GenericPluginContext:
        pass

    register(GenericPluginContext())
