"""Tests for identity load/generate/persist."""

import json
import stat

import pytest

from aap.keys import encode_b64url

from aap.identity import IdentityFile, load_or_generate


@pytest.fixture
def tmp_hermes_dir(tmp_path):
    return tmp_path / "hermes_home"


def test_generates_on_first_run(tmp_hermes_dir):
    identity_path = tmp_hermes_dir / "aap.json"

    result = load_or_generate(
        identity_path=identity_path,
        env_seed_b64=None,
        address="chris^agentaddress.org",
    )

    assert isinstance(result, IdentityFile)
    assert len(result.private_seed) == 32
    assert len(result.public_key) == 32
    assert result.address == "chris^agentaddress.org"
    assert identity_path.exists()

    # Verify mode 0600
    mode = stat.S_IMODE(identity_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_loads_existing(tmp_hermes_dir):
    identity_path = tmp_hermes_dir / "aap.json"
    tmp_hermes_dir.mkdir()

    seed = bytes(range(32))
    public = bytes([0xAA] * 32)
    identity_path.write_text(json.dumps({
        "private_seed_b64": encode_b64url(seed),
        "public_key_b64": encode_b64url(public),
        "address": "chris^old.example",
        "created_at": "2026-01-01T00:00:00Z",
    }))
    identity_path.chmod(0o600)

    result = load_or_generate(
        identity_path=identity_path,
        env_seed_b64=None,
        address="chris^old.example",
    )

    assert result.private_seed == seed
    assert result.public_key == public
    assert result.address == "chris^old.example"


def test_env_seed_overrides_file(tmp_hermes_dir):
    identity_path = tmp_hermes_dir / "aap.json"
    tmp_hermes_dir.mkdir()

    # File has one seed
    file_seed = bytes([1] * 32)
    file_pub = bytes([2] * 32)
    identity_path.write_text(json.dumps({
        "private_seed_b64": encode_b64url(file_seed),
        "public_key_b64": encode_b64url(file_pub),
        "address": "chris^agentaddress.org",
        "created_at": "2026-01-01T00:00:00Z",
    }))

    # Env has a different seed
    env_seed = bytes([0xFF] * 32)
    env_seed_b64 = encode_b64url(env_seed)

    result = load_or_generate(
        identity_path=identity_path,
        env_seed_b64=env_seed_b64,
        address="chris^agentaddress.org",
    )

    assert result.private_seed == env_seed


def test_address_change_updates_file_keeps_seed(tmp_hermes_dir):
    """When user changes localpart/domain, address field rewrites but seed is preserved."""
    identity_path = tmp_hermes_dir / "aap.json"
    tmp_hermes_dir.mkdir()

    original_seed = bytes(range(32))
    original_pub = bytes([0xAA] * 32)
    identity_path.write_text(json.dumps({
        "private_seed_b64": encode_b64url(original_seed),
        "public_key_b64": encode_b64url(original_pub),
        "address": "chris^old.example",
        "created_at": "2026-01-01T00:00:00Z",
    }))

    result = load_or_generate(
        identity_path=identity_path,
        env_seed_b64=None,
        address="chris^new.example",
    )

    assert result.private_seed == original_seed
    assert result.address == "chris^new.example"

    # File rewritten with new address
    persisted = json.loads(identity_path.read_text())
    assert persisted["address"] == "chris^new.example"
    assert persisted["private_seed_b64"] == encode_b64url(original_seed)
