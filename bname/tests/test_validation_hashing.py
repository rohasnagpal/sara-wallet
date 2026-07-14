import pytest

from app.hashing import record_hash
from app.validation import ValidationError, split_query_name, validate_root_name


def test_root_names():
    assert validate_root_name("a.b") == "a.b"
    assert validate_root_name("alice.wallet") == "alice.wallet"
    assert validate_root_name("my-name.sara") == "my-name.sara"


@pytest.mark.parametrize("name", ["-a.b", "a-.b", "a_b.c", "alice", "a..b", "a.b.c"])
def test_invalid_root_names(name):
    with pytest.raises(ValidationError):
        validate_root_name(name)


def test_split_query_name():
    assert split_query_name("rohas.sara") == ("rohas.sara", "@")
    assert split_query_name("www.rohas.sara") == ("rohas.sara", "www")


def test_record_hash_is_stable():
    first = record_hash("rohas.sara", "www", "URL", "default", "https://example.com", 300, 1)
    second = record_hash("rohas.sara", "www", "URL", "default", "https://example.com", 300, 1)
    assert first == second
    assert first.startswith("0x")
