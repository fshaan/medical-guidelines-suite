"""Tests for --profile slim mode."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from batch_pipeline import ProfileConfig, PROFILE_FULL, PROFILE_SLIM, get_profile


class TestProfileConfig:
    def test_full_profile_defaults(self):
        config = get_profile("full")
        assert config.name == "full"
        assert config.dimension_groups is None
        assert config.min_rec_length == 50
        assert config.skip_anti_laziness is False
        assert config.flat_json is False

    def test_slim_profile_values(self):
        config = get_profile("slim")
        assert config.name == "slim"
        assert len(config.dimension_groups) == 4
        assert config.min_rec_length == 20
        assert config.skip_anti_laziness is True
        assert config.flat_json is True
        assert config.org_filter_by_disease is True
        assert config.micro_checkpoints is True

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError):
            get_profile("unknown")
