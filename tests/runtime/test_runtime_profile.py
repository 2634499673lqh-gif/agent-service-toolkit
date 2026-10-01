"""Focused semantics for the persisted runtime profile."""

from types import SimpleNamespace

from core.settings import settings
from schema.models import DeepseekModelName, FakeModelName
from service.task_runtime import _runtime_profile


def _observations(*metadata: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        observations=[SimpleNamespace(provider_metadata=item) for item in metadata]
    )


def test_fake_execution_profile_is_non_live(monkeypatch):
    monkeypatch.setattr(settings, "USE_FAKE_MODEL", True)
    monkeypatch.setattr(settings, "DEFAULT_MODEL", FakeModelName.FAKE)

    assert _runtime_profile(_observations()) == {
        "provider": "fake",
        "model": "fake",
        "live_provider": False,
    }


def test_configured_deepseek_profile_resolves_provider_without_a_call(monkeypatch):
    monkeypatch.setattr(settings, "USE_FAKE_MODEL", False)
    monkeypatch.setattr(settings, "DEFAULT_MODEL", DeepseekModelName.DEEPSEEK_V4_FLASH)

    assert _runtime_profile(_observations()) == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "live_provider": False,
    }


def test_external_provider_observation_marks_execution_live(monkeypatch):
    monkeypatch.setattr(settings, "USE_FAKE_MODEL", False)
    monkeypatch.setattr(settings, "DEFAULT_MODEL", DeepseekModelName.DEEPSEEK_V4_FLASH)

    assert _runtime_profile(_observations({"provider": "deepseek", "model": "deepseek-v4-flash"})) == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "live_provider": True,
    }
