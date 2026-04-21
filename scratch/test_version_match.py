"""Test that the prefix-aware version matching works correctly."""

def versions_match(existing: str, candidate: str) -> bool:
    e = (existing or "").strip().lower()
    c = (candidate or "").strip().lower()
    return (
        e == c
        or e.startswith(c + ".")
        or c.startswith(e + ".")
    )

test_cases = [
    # (existing_in_db, candidate_from_install, expected_match)
    ("2",       "2",           True),   # exact
    ("2",       "2.177.1",     True),   # NXM "2" vs drag-drop "2.177.1"
    ("2.177.1", "2",           True),   # reverse direction
    ("2",       "3",           False),  # different major
    ("1.0",     "1.0.1747334812", True),  # MrME case
    ("1.0",     "2.0",         False),  # different
    ("",        "",            True),   # both empty
    ("2",       "21",          False),  # "2" should NOT match "21"
    ("2",       "2.",          False),  # edge case: trailing dot shouldn't match
]

print(f"{'Existing':>15} | {'Candidate':>15} | {'Expected':>8} | {'Got':>8} | Status")
print("-" * 75)
for existing, candidate, expected in test_cases:
    result = versions_match(existing, candidate)
    status = "OK" if result == expected else "FAIL"
    print(f"{existing:>15} | {candidate:>15} | {str(expected):>8} | {str(result):>8} | {status}")
