"""
Tests for application configuration loading.
"""
import pytest
from cloudsense.core.config import AppSettings, Settings, get_settings


class TestAppSettings:
    def test_default_environment_is_development(self):
        s = AppSettings()
        assert s.environment == "development"

    def test_environment_accepts_lowercase(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        from cloudsense.core.config import AppSettings
        s = AppSettings()
        assert s.environment == "staging"

    def test_default_log_level(self):
        s = AppSettings()
        assert s.log_level == "INFO"

    def test_default_api_port(self):
        s = AppSettings()
        assert s.api_port == 8000


class TestSettingsSingleton:
    def test_get_settings_returns_settings_instance(self):
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
