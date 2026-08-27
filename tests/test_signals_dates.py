from screen.signals import (
    DateRange,
    compute_years,
    extract_date_ranges,
    has_gap_over,
    short_stints,
)

TODAY = (2026, 8)

MD = """
## Experience

### Staff Engineer, Northwind (2019-03 - Present)
- did things

### Senior Engineer, Beacon (2015-06 - 2019-02)
- did other things
"""

MD_YEARS_ONLY = """
## Experience
### Engineer, Acme (2018 - 2023)
- work
### Engineer, Globex (2014 - 2017)
- work
"""


def test_extract_date_ranges_month_year_and_present():
    ranges = extract_date_ranges(MD)
    assert len(ranges) == 2
    assert ranges[0].start == (2019, 3)
    assert ranges[0].end is None
    assert ranges[0].precise is True
    assert ranges[1].start == (2015, 6)
    assert ranges[1].end == (2019, 2)


def test_extract_date_ranges_named_months():
    md = "### Engineer, Acme (Mar 2019 - Feb 2021)\n- work\n"
    ranges = extract_date_ranges(md)
    assert ranges[0].start == (2019, 3)
    assert ranges[0].end == (2021, 2)
    assert ranges[0].precise is True


def test_extract_date_ranges_year_only_marks_imprecise():
    ranges = extract_date_ranges(MD_YEARS_ONLY)
    assert len(ranges) == 2
    assert all(r.precise is False for r in ranges)
    assert ranges[0].start == (2018, 1)


def test_extract_date_ranges_handles_en_dash_and_to():
    md = "### A (2019-01 – 2020-01)\n### B (2015-01 to 2016-01)\n"
    assert len(extract_date_ranges(md)) == 2


def test_extract_date_ranges_ignores_education_only_years():
    md = "## Education\nBSc Computer Science, State University, 2014\n"
    assert extract_date_ranges(md) == []


def test_compute_years_sums_non_overlapping_ranges():
    result = compute_years(MD, today=TODAY)
    # 2015-06..2019-02 = 45 months; 2019-03..2026-08 = 90 months; total 135 = 11.25y
    assert result["computed"] == 11.2 or abs(result["computed"] - 11.25) < 0.1
    assert result["confidence"] == "high"


def test_compute_years_merges_overlapping_ranges():
    md = """
## Experience
### A (2018-01 - 2022-01)
### B (2020-01 - 2024-01)
"""
    result = compute_years(md, today=TODAY)
    # Union is 2018-01..2024-01 = 72 months = 6.0y, not 4+4=8.
    assert abs(result["computed"] - 6.0) < 0.1


def test_compute_years_confidence_medium_for_year_only():
    result = compute_years(MD_YEARS_ONLY, today=TODAY)
    assert result["confidence"] == "medium"


def test_compute_years_confidence_low_with_single_or_no_range():
    assert compute_years("## Experience\n### A (2020-01 - 2021-01)\n", TODAY)["confidence"] == "low"
    assert compute_years("## Experience\nno dates here\n", TODAY)["confidence"] == "low"
    assert compute_years("## Experience\nno dates here\n", TODAY)["computed"] == 0.0


def test_extract_date_ranges_end_month_10_11_12_not_truncated():
    # Regression: the month-group alternation in _MY previously tried the
    # single-digit branch (0?[1-9]) before the two-digit branch (1[0-2]).
    # Python's re alternation takes the first matching branch, not the
    # longest, so "0?[1-9]" alone matched just the "1" in "12" and stopped.
    # For the *end* date this month-number group is the last construct in
    # _RANGE_RE, so nothing downstream ever failed to force a retry -- every
    # Oct/Nov/Dec end-month silently collapsed to January, understating
    # years of experience that feeds the 4-year elimination gate. The
    # ordering "1[0-2]|0?[1-9]" in _MY is load-bearing; do not reorder it.
    assert extract_date_ranges("### A (2019-01 - 2019-09)\n")[0].end == (2019, 9)
    assert extract_date_ranges("### A (2019-01 - 2019-10)\n")[0].end == (2019, 10)
    assert extract_date_ranges("### A (2019-01 - 2019-11)\n")[0].end == (2019, 11)
    assert extract_date_ranges("### A (2019-01 - 2019-12)\n")[0].end == (2019, 12)


def test_extract_date_ranges_start_month_10_and_12_not_truncated():
    # Same alternation, start position: this one happened to self-correct via
    # backtracking (the separator after it had to match), but pin it anyway
    # so a future edit to _MY can't quietly break the start side either.
    assert extract_date_ranges("### A (2019-09 - 2020-01)\n")[0].start == (2019, 9)
    assert extract_date_ranges("### A (2019-10 - 2020-01)\n")[0].start == (2019, 10)
    assert extract_date_ranges("### A (2019-12 - 2020-01)\n")[0].start == (2019, 12)


def test_compute_years_reversed_range_downgrades_confidence_to_low():
    # A transposed range ("2021-01 - 2019-01" instead of "2019-01 - 2021-01")
    # is a typo, not a legitimate zero-length role. _merged_intervals already
    # drops it from the total, so reporting "high" confidence anyway would
    # hide the resulting undercount from gate G4 exactly when the gate stops
    # consulting the judge's own estimate.
    md = "## Experience\n### A (2015-01 - 2018-01)\n### B (2021-01 - 2019-01)\n"
    result = compute_years(md, today=TODAY)
    assert result["confidence"] == "low"
    assert result["malformed_ranges"] == 1
    assert abs(result["computed"] - 3.08) < 0.01


def test_compute_years_valid_ranges_report_zero_malformed_ranges():
    result = compute_years(MD, today=TODAY)
    assert result["malformed_ranges"] == 0
    assert result["confidence"] == "high"


def test_short_stints_counts_sub_year_roles_in_window():
    ranges = [
        DateRange(start=(2025, 1), end=(2025, 6), precise=True, raw="a"),
        DateRange(start=(2024, 1), end=(2024, 8), precise=True, raw="b"),
        DateRange(start=(2023, 1), end=(2023, 5), precise=True, raw="c"),
        DateRange(start=(2015, 1), end=(2015, 4), precise=True, raw="old"),
    ]
    assert short_stints(ranges, today=TODAY, years_back=5) == 3


def test_short_stints_ignores_current_role_and_long_roles():
    ranges = [
        DateRange(start=(2026, 6), end=None, precise=True, raw="current"),
        DateRange(start=(2022, 1), end=(2025, 1), precise=True, raw="long"),
    ]
    assert short_stints(ranges, today=TODAY, years_back=5) == 0


def test_has_gap_over_detects_long_break():
    ranges = [
        DateRange(start=(2024, 6), end=None, precise=True, raw="a"),
        DateRange(start=(2018, 1), end=(2022, 1), precise=True, raw="b"),
    ]
    assert has_gap_over(ranges, today=TODAY, months=12, years_back=6) is True


def test_has_gap_over_false_for_continuous_history():
    ranges = [
        DateRange(start=(2022, 2), end=None, precise=True, raw="a"),
        DateRange(start=(2018, 1), end=(2022, 1), precise=True, raw="b"),
    ]
    assert has_gap_over(ranges, today=TODAY, months=12, years_back=6) is False


def test_has_gap_over_false_with_fewer_than_two_ranges():
    assert has_gap_over([], TODAY, 12, 6) is False
