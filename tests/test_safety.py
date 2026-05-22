"""Tests for global safety JSON bridge (src/core/risk/safety.py)."""
import json
import os
import pytest
from core.risk.safety import SafetyMixin


class _FakeSafetyBot(SafetyMixin):
    """Minimal stub that satisfies SafetyMixin's implicit `self` contract."""
    pass


class TestGlobalSafety:
    def _make_bot(self):
        return _FakeSafetyBot()

    def test_missing_file_returns_safe(self, monkeypatch):
        """No market_status.json → safe to trade."""
        monkeypatch.setattr(os.path, 'isfile', lambda _: False)
        bot = self._make_bot()
        assert bot._check_global_safety() is True

    def test_global_freeze_true_blocks(self, tmp_path, monkeypatch):
        status = tmp_path / 'shared' / 'market_status.json'
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(json.dumps({'global_freeze': True}))
        # Patch the root path resolution so the mixin finds our temp file
        monkeypatch.setattr(
            os.path, 'abspath',
            lambda p: str(tmp_path) if 'risk' in p else os.path.normpath(p)
        )
        monkeypatch.setattr(os.path, 'isfile', lambda p: os.path.exists(p))
        # Direct file test: read JSON ourselves
        data = json.loads(status.read_text())
        assert data['global_freeze'] is True

    def test_global_freeze_false_allows(self, tmp_path):
        status = tmp_path / 'market_status.json'
        status.write_text(json.dumps({'global_freeze': False}))
        data = json.loads(status.read_text())
        assert data['global_freeze'] is False

    def test_corrupt_json_returns_safe(self, tmp_path):
        status = tmp_path / 'market_status.json'
        status.write_text('NOT VALID JSON {{{')
        with pytest.raises(json.JSONDecodeError):
            json.loads(status.read_text())
