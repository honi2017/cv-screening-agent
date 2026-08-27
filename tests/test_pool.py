import json

from screen.pool import load_pool_duplicates, pool_duplicates, write_pool_duplicates

SHARED = "Spearheaded cross-functional initiatives resulting in 40% efficiency gains"


def test_finds_bullet_shared_between_two_candidates():
    result = pool_duplicates(
        {
            1: [SHARED, "Built a PostgreSQL replication service for reporting"],
            2: [SHARED, "Ran SMTP deliverability improvements across three regions"],
        }
    )
    assert len(result[1]) == 1
    assert result[1][0]["with_candidate"] == 2
    assert result[1][0]["bullet"] == SHARED
    assert result[2][0]["with_candidate"] == 1


def test_no_duplicates_when_all_bullets_unique():
    result = pool_duplicates(
        {
            1: ["Built a PostgreSQL replication service for reporting"],
            2: ["Ran SMTP deliverability improvements across three regions"],
        }
    )
    assert result == {}


def test_ignores_short_generic_bullets():
    # "Python and Go" appearing twice is not evidence of a shared template.
    result = pool_duplicates({1: ["Python and Go"], 2: ["Python and Go"]})
    assert result == {}


def test_normalization_means_punctuation_and_case_do_not_hide_a_match():
    result = pool_duplicates(
        {
            1: ["Spearheaded cross-functional initiatives resulting in 40% efficiency gains"],
            2: ["spearheaded cross functional initiatives, resulting in 40% efficiency gains!"],
        }
    )
    assert 1 in result and 2 in result


def test_three_way_share_lists_every_other_candidate():
    result = pool_duplicates({1: [SHARED], 2: [SHARED], 3: [SHARED]})
    assert {d["with_candidate"] for d in result[1]} == {2, 3}


def test_candidate_repeating_own_bullet_is_not_a_pool_duplicate():
    result = pool_duplicates({1: [SHARED, SHARED]})
    assert result == {}


def test_write_and_load_roundtrip(tmp_path):
    result = pool_duplicates({1: [SHARED], 2: [SHARED]})
    path = tmp_path / "pool_duplicates.json"
    write_pool_duplicates(result, path)
    assert json.loads(path.read_text())["1"][0]["with_candidate"] == 2
    assert load_pool_duplicates(path) == result


def test_load_missing_file_returns_empty(tmp_path):
    assert load_pool_duplicates(tmp_path / "absent.json") == {}
