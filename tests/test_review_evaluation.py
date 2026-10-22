"""Frozen human packet provenance and clocks survive evaluation/calibration joins."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from scripts import calibrate, eval as evaluation

CLOCK = '2026-10-14T00:00:00+00:00'


def reviewed(video_id='v', split='validation', clock=CLOCK):
    source = dict(video_id=video_id, channel_id='channel', channel_title='Channel', title='Offer ' + video_id,
                  url='https://www.youtube.com/watch?v=' + video_id, published_at='2026-10-12T00:00:00Z',
                  description='A different source ' + video_id, fetched_at='2026-10-13T00:00:00+00:00')
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    corpus = source | dict(group_id='group-' + video_id, suggested_split=split, source_snapshot_sha256=digest)
    label = dict(video_id=video_id, is_giveaway=False, label_source='human', reviewer='alex',
                 reviewed_at=CLOCK, annotation_as_of=clock, group_id=corpus['group_id'], split=split,
                 source_snapshot_sha256=digest)
    return label, corpus


def make_row(video_id, text=None, **label):
    return dict(video_id=video_id, title='', text=text or 'Unique source ' + video_id, published_at=None,
                label=dict(is_giveaway=False, **label))


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def test_explicit_splits_are_frozen_instead_of_rehashed():
    rows = [make_row(str(i), split=partition, group_id='g' + str(i))
            for i, partition in enumerate(('test', 'train', 'validation', 'test'))]
    train, validation, test = calibrate.split(rows)
    assert [row['video_id'] for row in train] == ['1']
    assert [row['video_id'] for row in validation] == ['2']
    assert [row['video_id'] for row in test] == ['0', '3']


@pytest.mark.parametrize('rows', [
    [make_row('a', split='train'), make_row('b')],
    [make_row('a', split='train', group_id='shared'), make_row('b', split='test', group_id='shared')],
    [make_row('a', text='Same promo!', split='train'), make_row('b', text=' same PROMO ', split='test')],
    [make_row('same', text='First', split='train'), make_row('same', text='Different', split='test')],
])
def test_frozen_split_leakage_and_mixed_assignments_refused(rows):
    with pytest.raises(ValueError):
        calibrate.split(rows)


def test_evaluation_and_calibration_use_each_rows_annotation_clock(monkeypatch):
    seen = []
    rows = [make_row('a', annotation_as_of=CLOCK),
            make_row('b', annotation_as_of='2026-10-15T00:00:00+00:00')]

    def extraction(*args, **kwargs):
        seen.append(kwargs['now'])
        return dict(is_giveaway=False, confidence=0, features={'feature': 1})

    monkeypatch.setattr(evaluation, 'extract', extraction)
    monkeypatch.setattr(calibrate.E, 'analyse', extraction)
    monkeypatch.setattr(calibrate.E, 'extract', extraction)
    assert evaluation.evaluate(rows)['tn'] == 2
    for row in rows:
        assert calibrate.featurise(row, None) == {'feature': 1}
    assert calibrate.runtime_scores(rows, {}, 0, None) == [-1, -1]
    assert seen == [datetime.fromisoformat(row['label']['annotation_as_of']) for row in rows] * 3
    with pytest.raises(ValueError, match='conflicts'):
        evaluation.evaluate(rows, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='timezone-aware'):
        evaluation.evaluate([make_row('naive', annotation_as_of='2026-10-14T00:00:00')])


def test_review_evaluation_defaults_validation_and_test_requires_explicit_selection(monkeypatch):
    rows = [make_row(partition, split=partition) for partition in ('train', 'validation', 'test')]
    seen = []
    monkeypatch.setattr(evaluation, 'extract', lambda text, **kwargs: seen.append(text) or dict(is_giveaway=False, confidence=0))
    assert evaluation.evaluate(rows)['n'] == 1
    assert seen == ['Unique source validation']
    seen.clear()
    assert evaluation.evaluate(rows, split='test')['n'] == 1
    assert seen == ['Unique source test']
    assert evaluation.evaluate(rows, split='all')['n'] == 3


def test_snapshot_join_refuses_tampering_provisional_or_unidentified_labels():
    label, source = reviewed()
    rows, missing = evaluation.build_dataset([label], {'v': source})
    assert not missing and rows[0]['text'] == source['description']
    for altered_label, altered_source in [
        (label, source | {'description': 'changed text'}),
        (label | {'label_source': 'agent'}, source),
        (label | {'needs_review': True}, source),
        (label | {'reviewer': None}, source),
        (label | {'split': 'test'}, source),
        (label | {'is_giveaway': None}, source),
    ]:
        with pytest.raises(ValueError):
            evaluation.build_dataset([altered_label], {'v': altered_source})


def test_eval_cli_split_selection_and_clock_refusal(tmp_path, capsys):
    pairs = [reviewed(partition, partition) for partition in ('train', 'validation', 'test')]
    labels, corpus = tmp_path / 'labels.jsonl', tmp_path / 'corpus.jsonl'
    write_rows(labels, [pair[0] for pair in pairs])
    write_rows(corpus, [pair[1] for pair in pairs])
    arguments = ['--labels', str(labels), '--corpus', str(corpus), '--json']
    assert evaluation.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)['n'] == 1
    assert evaluation.main(arguments + ['--split', 'test']) == 0
    assert json.loads(capsys.readouterr().out)['n'] == 1
    assert evaluation.main(arguments + ['--now', '2026-10-15T00:00:00Z']) == 2
    assert 'conflicts' in capsys.readouterr().err


def test_calibration_refuses_tampered_source_before_fitting(tmp_path, monkeypatch, capsys):
    label, source = reviewed()
    labels, corpus = tmp_path / 'labels.jsonl', tmp_path / 'corpus.jsonl'
    write_rows(labels, [label])
    write_rows(corpus, [source | {'description': 'changed'}])
    monkeypatch.setattr(calibrate, 'fit', lambda *a, **kw: pytest.fail('should never fit'))
    assert calibrate.main(['--labels', str(labels), '--corpus', str(corpus), '--dry-run']) == 2
    assert 'snapshot hash mismatch' in capsys.readouterr().err


def test_legacy_inline_fixtures_and_split_still_work():
    labels = [dict(video_id='legacy', description='A normal cooking video', is_giveaway=False)]
    rows, missing = evaluation.build_dataset(labels, {})
    assert not missing
    assert evaluation.evaluate(rows)['n'] == 1
    assert sum(map(len, calibrate.split(rows))) == 1
