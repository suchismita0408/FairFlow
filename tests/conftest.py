import pytest
@pytest.fixture(autouse=True)
def isolate_legacy_configuration(monkeypatch,tmp_path):
    monkeypatch.setenv('AI_MODE','demo')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('AUTH_ENABLED','false')
    monkeypatch.setenv('ENABLE_LEGACY_ACCOUNTS','true')
    # Never load developer platform credentials into isolated tests or their
    # browser subprocesses. Platform tests supply an explicit fake adapter.
    monkeypatch.setenv('PAYPAL_PAYMENT_FLOW','legacy')
    monkeypatch.setenv('PAYPAL_PLATFORM_CONFIG',str(tmp_path/'unused-platform.env'))
