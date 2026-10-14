from datetime import datetime, timezone

from scripts.calibrate import split, pick_threshold, runtime_scores, classification_metrics


def row(video_id, text, **label):
    return {'video_id': video_id, 'text': text, 'title': '', 'published_at': None,
            'label': {'is_giveaway': True, **label}}


def test_grouped_split_prevents_duplicate_and_campaign_leakage():
    rows = [row(str(i), f'Unique description {i}') for i in range(150)]
    rows += [row('duplicate-a', 'Same source'), row('duplicate-b', ' same   SOURCE '),
             row('campaign-a', 'First promo', campaign_id='promo'),
             row('campaign-b', 'Second promo', campaign_id='promo')]
    partitions = split(rows)
    locations = {r['video_id']: i for i, part in enumerate(partitions) for r in part}
    assert all(partitions)
    assert len(locations) == len(rows)
    assert locations['duplicate-a'] == locations['duplicate-b']
    assert locations['campaign-a'] == locations['campaign-b']
    reverse = {r['video_id']: i for i, part in enumerate(split(list(reversed(rows)))) for r in part}
    assert reverse == locations


def test_threshold_is_not_a_reject_everything_success():
    assert pick_threshold([0.9, 0.1], [0, 1], 0.9)[0] is None
    threshold, precision, recall = pick_threshold([0.91, 0.72, 0.2], [1, 1, 0], 0.9)
    assert precision == recall == 1
    assert classification_metrics([0.95, 0.8], [0, 1], threshold)['precision'] == 0.5


def test_calibration_scores_include_runtime_rejection_gates():
    rows = [row('expired', 'Giveaway! Win a Nintendo Switch. To enter subscribe and comment. Ends January 1, 2020.'),
            row('open', 'Giveaway! Win a Nintendo Switch. To enter subscribe and comment. Ends January 1, 2030.')]
    scores = runtime_scores(rows, {}, 10, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert scores[0] == -1
    assert scores[1] > 0.99


def test_cli_refuses_provisional_agent_labels(tmp_path, monkeypatch, capsys):
    import json
    from scripts import calibrate
    labels = tmp_path / 'labels.jsonl'
    labels.write_text(json.dumps({'video_id': 'provisional', 'description': 'Giveaway',
                                 'is_giveaway': True, 'label_source': 'agent'}) + '\n')
    monkeypatch.setattr('sys.argv', ['calibrate', '--labels', str(labels), '--dry-run'])
    assert calibrate.main() == 2
    assert 'provisional' in capsys.readouterr().err
