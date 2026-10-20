from scripts.a21_author_glm_config import AuthorConfigError, parse_author_messages, partition_credential_pools


def test_latest_complete_author_glm_message_is_selected_without_merging_keys():
    keys = [f"sk-testcredential{i:02d}abcdefghijkl" for i in range(6)]
    config = parse_author_messages([
        "base_url https://gateway.example.invalid/v1 glm-5.2 sk-too-short",
        "base_url glm-5.2 https://gateway.example.invalid/v1 " + " ".join(keys),
    ])
    assert config["base_url"] == "https://gateway.example.invalid/v1"
    assert config["api_keys"] == keys


def test_rejects_incomplete_or_cross_message_credentials():
    try:
        parse_author_messages([
            "base_url glm-5.2 https://gateway.example.invalid/v1 sk-testcredential00abcdefghijkl",
            "base_url glm-5.2 https://gateway.example.invalid/v1 sk-testcredential01abcdefghijkl",
        ])
    except AuthorConfigError:
        return
    raise AssertionError("incomplete credential message must be rejected")


def test_credential_pools_are_disjoint_and_cover_all_slots():
    pools = partition_credential_pools([f"key-{index}" for index in range(6)], 2)
    assert pools == [["key-0", "key-2", "key-4"], ["key-1", "key-3", "key-5"]]


def test_authorized_three_worker_pools_remain_disjoint():
    pools = partition_credential_pools([f"key-{index}" for index in range(6)], 3)
    assert pools == [["key-0", "key-3"], ["key-1", "key-4"], ["key-2", "key-5"]]


def test_authorized_six_worker_pools_are_singletons():
    keys = [f"key-{index}" for index in range(6)]
    pools = partition_credential_pools(keys, 6)
    assert pools == [[key] for key in keys]
    assert len({key for pool in pools for key in pool}) == 6
