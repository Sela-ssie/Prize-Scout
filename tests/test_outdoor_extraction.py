"""Paraphrased synthetic regression cases; no measured population-quality claims."""
from datetime import datetime, timezone

import pytest

from src.categories import classify_prizes
from src.evidence import build_evidence
from src.extractor import analyse, extract, extract_prizes, find_howto

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize('item', [
    'compound bow', 'compound bows', 'Summit R5 compound bow',
    'archery stabilizer', 'bow stabilizers', 'Cedar Archery Products 10-inch front-facing stabilizer',
    'trail camera', 'trail cameras', 'FieldPro trail cameras', 'cameras',
])
def test_narrow_outdoor_prize_names_survive_exactly(item):
    result = extract(f'We are giving away a {item}. To enter, submit the form. Entries close November 20, 2026.', now=NOW)
    assert result['is_giveaway']
    assert [prize['item'] for prize in result['prizes']] == [item]
    assert result['availability'] == 'active'
    if item != 'cameras':
        assert 'sports' in classify_prizes(result['prizes'])


def test_offer_list_and_required_episode_answer_condition_have_exact_evidence():
    text = ('We are giving away equipment to viewers this month. '
            'You need to keep track of the clues in each episode. '
            'We will ask you about a specific episode. '
            'If you have the right answer, you win. '
            'There will be a Summit R5 compound bow, a Cedar Archery Products 10-inch front-facing stabilizer, '
            'and 3 FieldPro trail cameras.\n'
            'Links to see these products: affiliate links below.\n'
            'Accessory reviews: compare our camping tent and fishing rod.')
    result = extract(text, now=NOW)
    assert result['is_giveaway']
    assert [prize['item'] for prize in result['prizes']] == [
        'Summit R5 compound bow', 'Cedar Archery Products 10-inch front-facing stabilizer', 'FieldPro trail cameras']
    assert result['howto'] == ('You need to keep track of the clues in each episode. '
                                'We will ask you about a specific episode. If you have the right answer, you win.')
    evidence = build_evidence('', text, dict(prizes_structured=result['prizes'], howto=result['howto']))
    assert len(evidence['prizes']) == 3
    assert len(evidence['howto']) == 3
    assert all(span['quote'] == text[span['start']:span['end']] for spans in evidence.values() for span in spans)


@pytest.mark.parametrize('list_text', [
    'There will be a compound bow and two trail cameras.',
    'Prizes include a compound bow and trail cameras.',
    '- compound bow\n- trail cameras',
    '1. Compound bow\n2. Trail cameras',
])
def test_adjacent_award_lists_need_not_repeat_giveaway_keyword(list_text):
    prizes = extract_prizes('We are giving away prizes to two viewers.\n\n' + list_text)
    assert {prize['item'].lower() for prize in prizes} == {'compound bow', 'trail cameras'}


@pytest.mark.parametrize('condition,consequence', [
    ('You must submit the answer shown in this episode.', 'Correct answers win a prize.'),
    ('Participants are required to send their response by email.', 'The right response wins the prize.'),
    ('You have to keep track of the code in each clip.', 'If you know the code, you will win.'),
    ('Submit your answer using the form.', 'Correct responses win the prize.'),
])
def test_non_social_required_conditions_with_local_winning_linkage(condition, consequence):
    text = 'We are giving away a compound bow. ' + condition + ' ' + consequence
    result = extract(text, now=NOW)
    assert result['is_giveaway']
    assert result['howto'] == condition + ' ' + consequence


@pytest.mark.parametrize('text', [
    'There will be a compound bow review and three trail cameras in our equipment comparison.',
    'We review a compound bow. You must keep track of your scores. Correct answers win at the trivia game.',
    'We are giving away a compound bow.\n\nRemember to subscribe for more videos.',
    'We are giving away a compound bow. You need to remember to subscribe for more videos. Correct answers win.',
    'We are giving away a compound bow. Affiliate links below. You must submit your answer. Correct answers win.',
    'We are giving away a compound bow. Here is our cooking tutorial. You need to submit an answer. Correct answers win.',
])
def test_unrelated_conditions_and_generic_footers_are_not_entry_instructions(text):
    assert find_howto(text) is None


@pytest.mark.parametrize('trailing', [
    'There will be an archery stabilizer review in the next upload.',
    'Affiliate links: There will be a trail camera.',
    'Products reviewed: a trail camera and archery stabilizer.',
    'There will be a discount on trail cameras.',
    'Unrelated story about our weekend. There will be a trail camera.',
])
def test_offer_scope_does_not_swallow_accessory_reviews_or_shopping_footers(trailing):
    prizes = extract_prizes('We are giving away a compound bow. ' + trailing)
    assert [prize['item'] for prize in prizes] == ['compound bow']


def test_offer_list_scope_has_sentence_bound_and_does_not_reanchor_on_bare_win():
    bridge = 'You must keep the code. ' * 7
    assert extract_prizes('We are giving away prizes. ' + bridge + 'There will be a trail camera.') == []


@pytest.mark.parametrize('item', ['bow tie', 'rainbow', 'stabilizer', 'camera stabilizer', 'compound bowstring', 'trail camerawork'])
def test_outdoor_allowlist_does_not_match_ambiguous_or_partial_names(item):
    prizes = extract_prizes('Enter to win a ' + item + '.')
    assert not any('compound bow' in prize['item'].lower() or 'archery' in prize['item'].lower()
                   or 'trail camera' in prize['item'].lower() for prize in prizes)


def test_expired_and_closed_offer_guards_still_apply_to_new_nouns():
    for ending in ('The giveaway has ended.', 'Entries close January 1, 2020.'):
        result = extract('We are giving away a compound bow. You must submit the correct answer. Correct answers win. ' + ending, now=NOW)
        assert not result['is_giveaway']


def test_synthetic_outdoor_analysis_does_not_infer_a_deadline():
    result = analyse('We are giving away a compound bow. You must submit your answer. Correct answers win.', now=NOW)
    assert result['deadline'] is None


def test_prize_allocation_restatements_do_not_duplicate_named_camera_awards():
    prizes = extract_prizes('We are giving away outdoor equipment. There will be three FieldPro trail cameras. '
                            'There will be three winners for the trail cameras, each getting to pick one camera.')
    assert [prize['item'] for prize in prizes] == ['FieldPro trail cameras']


def test_allocation_sentence_can_still_introduce_a_different_prize():
    prizes = extract_prizes('We are giving away a compound bow. There will be three winners for trail cameras.')
    assert {prize['item'] for prize in prizes} == {'compound bow', 'trail cameras'}


def test_gameplay_winning_language_does_not_anchor_an_award_list():
    assert extract_prizes('Correct answers win the game. There will be a trail camera in our next review.') == []
