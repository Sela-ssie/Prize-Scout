"""Blind packet review validates provenance before accepting explicit human input."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import label
from scripts.prepare_review import export_review

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def packet(tmp_path, monkeypatch):
    monkeypatch.setattr(label, '_utcnow', lambda: NOW)
    monkeypatch.setattr(label, 'extract', lambda *args, **kwargs: pytest.fail('blind review called extractor'))
    # Find a genuine test partition to verify the final holdout path explicitly.
    group = next('group-' + str(i) for i in range(100) if int(hashlib.sha256(('group-' + str(i)).encode()).hexdigest()[:16], 16) % 100 >= 80)
    source = dict(video_id='v1', channel_id='channel', channel_title='Channel', title='Giveaway',
                  url='https://www.youtube.com/watch?v=v1', published_at='2026-10-10T00:00:00Z',
                  description='Closed promotion. Entries ended yesterday.', fetched_at=(NOW - timedelta(days=1)).isoformat())
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    item = dict(video_id='v1', group_id=group, suggested_split='test', source_snapshot_sha256=digest, source=source)
    return export_review(dict(manifest=dict(schema_version=1, now=NOW.isoformat(), selected_videos=1, selected_groups=1), items=[item]), tmp_path / 'packet')


def answers(monkeypatch, values):
    sequence = iter(values)
    monkeypatch.setattr('builtins.input', lambda prompt: next(sequence))


def test_packet_blind_explicit_input_provenance_and_resume(packet, monkeypatch, capsys):
    answers(monkeypatch, ['', 'garbage', 'n', 'y', 'expired', 'PC', '-', 'Previously subscribe'])
    assert label.main(['--packet', str(packet), '--reviewer', 'human-alex']) == 0
    result = json.loads((packet / 'human_labels.jsonl').read_text())
    assignment = json.loads((packet / 'assignments.jsonl').read_text())
    assert result['is_giveaway'] is False
    assert result['promotion_exists'] is True
    assert result['availability'] == 'expired'
    assert result['reviewer'] == 'human-alex'
    assert result['reviewed_at'] == NOW.isoformat()
    assert result['annotation_as_of'] == NOW.isoformat()
    assert result['group_id'] == assignment['group_id']
    assert result['source_snapshot_sha256'] == assignment['source_snapshot_sha256']
    assert result['split'] == 'test'
    assert not any(key.startswith('guess') for key in result)
    output = capsys.readouterr().out
    assert 'Choose explicitly' in output
    assert 'extractor guess' not in output
    answers(monkeypatch, [])
    assert label.main(['--packet', str(packet), '--reviewer', 'human-alex']) == 0
    assert len((packet / 'human_labels.jsonl').read_text().splitlines()) == 1


def test_packet_active_promotion(packet, monkeypatch):
    answers(monkeypatch, ['y', 'expired', 'active', 'PC', '2026-10-20', 'Enter on form'])
    assert label.main(['--packet', str(packet), '--reviewer', 'human']) == 0
    result = json.loads((packet / 'human_labels.jsonl').read_text())
    assert result['is_giveaway'] is True
    assert result['availability'] == 'active'
    assert result['prize'] == 'PC'


@pytest.mark.parametrize('tamper', ['source', 'assignment', 'clock', 'old', 'count'])
def test_stale_or_tampered_packet_refused_before_prompt(packet, monkeypatch, tamper):
    if tamper == 'source':
        path = packet / 'corpus.jsonl'
        value = json.loads(path.read_text())
        value['description'] = 'Tampered source'
    elif tamper == 'assignment':
        path = packet / 'assignments.jsonl'
        value = json.loads(path.read_text())
        value['group_id'] = 'other'
    else:
        path = packet / 'manifest.json'
        value = json.loads(path.read_text())
        if tamper == 'clock':
            value['now'] = '2026-10-14T00:00:00'
        if tamper == 'old':
            monkeypatch.setattr(label, '_utcnow', lambda: NOW + timedelta(days=31))
        if tamper == 'count':
            value['selected_videos'] = 99
    path.write_text(json.dumps(value) + '\n')
    answers(monkeypatch, [])
    assert label.main(['--packet', str(packet), '--reviewer', 'human']) == 2
    assert not (packet / 'human_labels.jsonl').exists()


def test_packet_cli_requires_reviewer_and_rejects_guess_filter(packet):
    with pytest.raises(SystemExit):
        label.main(['--packet', str(packet)])
    with pytest.raises(SystemExit):
        label.main(['--packet', str(packet), '--reviewer', 'human', '--only-guessed-giveaway'])


def test_legacy_blind_does_not_extract(tmp_path, monkeypatch):
    corpus = tmp_path / 'corpus.jsonl'
    labels = tmp_path / 'human.jsonl'
    corpus.write_text(json.dumps(dict(video_id='legacy', title='No offer', description='Travel vlog')) + '\n')
    monkeypatch.setattr(label, 'extract', lambda *a, **kw: pytest.fail('blind legacy review extracted'))
    answers(monkeypatch, ['n', 'n'])
    assert label.main(['--blind', '--corpus', str(corpus), '--labels', str(labels)]) == 0
    assert json.loads(labels.read_text())['promotion_exists'] is False
