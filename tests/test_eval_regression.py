"""Regression gate on extraction quality.

Fails if is_giveaway precision drops below the threshold recorded in eval_thresholds.json.
Precision is the priority: a false positive costs far more user trust than a miss. A
min_recall floor is also enforced so precision cannot be bought by rejecting everything.

Scores data/labels.jsonl when it exists, otherwise the committed seed fixtures.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval import build_dataset, evaluate, fixture_now, load_jsonl  # noqa: E402
from src.extractor import extract  # noqa: E402

THRESHOLDS = json.loads((ROOT / "eval_thresholds.json").read_text())
REAL_LABELS = ROOT / "data" / "labels.jsonl"
SEED_LABELS = ROOT / "tests" / "fixtures" / "seed_labels.jsonl"
CORPUS = ROOT / "data" / "corpus.jsonl"


@pytest.fixture(scope="module")
def metrics():
    labels_path = REAL_LABELS if REAL_LABELS.exists() else SEED_LABELS
    labels = load_jsonl(labels_path)
    rows, _ = build_dataset(labels, {i["video_id"]: i for i in load_jsonl(CORPUS)})
    assert rows, f"no evaluable rows in {labels_path}"
    return evaluate(rows, now=fixture_now(labels))


def test_precision_meets_recorded_threshold(metrics):
    floor = THRESHOLDS["min_precision"]
    assert metrics["precision"] >= floor, (
        f"is_giveaway precision {metrics['precision']:.3f} is below the recorded floor {floor}. "
        f"Fix the extractor; do not lower the threshold. "
        f"Worst false positives:\n" + "\n".join(
            f"  conf={c:.3f} {r['video_id']} {r['text'][:120]!r}"
            for c, r, _ in metrics["_false_positives"][:5]
        )
    )


def test_recall_meets_recorded_floor(metrics):
    floor = THRESHOLDS["min_recall"]
    assert metrics["recall"] >= floor, (
        f"is_giveaway recall {metrics['recall']:.3f} is below the recorded floor {floor}. "
        f"Precision must not be bought by rejecting everything."
    )


@pytest.mark.parametrize("text,reason", [
    (
        "The information in this video is for general educational purposes only and does not "
        "constitute legal advice. So, it would be totally unreasonable for you to conclude we "
        "have an attorney-client relationship just because you're viewing this information.",
        "'win' inside 'viewing'",
    ),
    (
        "Underground Racing Twin Turbo Huracan STO. 1100 HP on the 5.2L Twin Turbo V10, 0-60 "
        "MPH in 2.5s, quarter mile in 9.8s, top speed 205 MPH at the airstrip this weekend.",
        "'win' inside 'twin'",
    ),
    (
        "Gil Scott-Heron performing live in concert, remastered from the original tapes. "
        "Album Winter in America (1974). Recorded at a small club in New York City.",
        "'win' inside 'winter'",
    ),
    (
        "A quiet winter vlog from the cabin. We spent the weekend by the window watching the "
        "snow come down, rewinding old tapes and drinking far too much coffee together.",
        "'win' inside winter, window and rewinding",
    ),
    (
        "Beginner swing dance tutorial covering the basic six-count step. We break down the "
        "rock step, the triple step and how to lead a simple turn without losing balance.",
        "'win' inside 'swing'",
    ),
])
def test_substring_traps_are_not_giveaways(text, reason):
    """These are the exact strings the v1 substring matcher stored as PRIZES."""
    result = extract(text)
    assert not result["is_giveaway"], f"regressed on {reason}: {result}"
    assert not result["prizes"], f"extracted a prize from {reason}: {result['prizes']}"


def test_real_tag_dumps_from_the_v1_database_are_rejected():
    """Every SEO/hashtag wall the old extractor stored as a prize must now be rejected."""
    from src.extractor import is_tag_dump

    strings = json.loads((ROOT / "tests" / "fixtures" / "legacy_prize_strings.json").read_text())
    walls = [s for s in strings if s.count("#") >= 3]
    assert walls, "fixture lost its hashtag walls"
    for wall in walls:
        assert is_tag_dump(wall), f"no longer detected as a tag dump: {wall[:80]!r}"


def test_closed_giveaways_are_rejected():
    text = (
        "Giveaway! Win a PlayStation 5 and a $200 gift card. To enter, subscribe, like and "
        "comment below. The giveaway ended on March 1, 2020 and the winner has already been "
        "announced and contacted. Leaving this up for the record."
    )
    result = extract(text)
    assert not result["is_giveaway"]
    assert result["rejected_because"] == "already_closed"
