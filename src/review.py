"""Deterministic, group-safe review sampling from retained local source data."""
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .campaigns import normalize_entry_url
from .db import Giveaway, Video


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _normalized(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold()))


def _utc(value):
    # SQLite loses timezone metadata; source writes are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_review_queue(engine, *, limit=100, seed='prizescout-review-v1', now=None, reviewed_ids=None):
    """Return {manifest, items}; item sources are API cache and must expire too.

    Suggested partitions are a stable hash of the complete connected group, not
    guaranteed exact proportions. Test assignments omit all model predictions.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('limit must be a nonnegative integer')
    now = now.astimezone(timezone.utc)
    seed = str(seed)
    reviewed_ids = set(reviewed_ids or ())
    with Session(engine) as session:
        rows = session.exec(select(Video).order_by(Video.video_id)).all()
        campaigns = {g.video_id: g.campaign_key for g in session.exec(select(Giveaway)).all() if g.campaign_key}
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        a, b = find(a), find(b)
        parent[max(a, b)] = min(a, b)

    owners, keys, content_hashes = {}, [], []
    for i, video in enumerate(rows):
        content = _hash(json.dumps([_normalized(video.title), _normalized(video.raw_description)]))
        content_hashes.append(content)
        links = {url for raw in re.findall(r'https?://[^\s<>"\[\]]+', video.raw_description or '', re.I)
                 if (url := normalize_entry_url(raw))}
        campaign_keys = {'entry-v1-' + _hash(url)[:24] for url in links}
        if video.video_id in campaigns:
            campaign_keys.add(campaigns[video.video_id])
        keys.append(campaign_keys)
        tokens = {'content:' + content} | {'campaign:' + key for key in campaign_keys}
        # Identical substantive descriptions with edited titles are also grouped.
        description = _normalized(video.raw_description)
        if description:
            tokens.add('description:' + _hash(description))
        for token in tokens:
            if token in owners:
                union(i, owners[token])
            else:
                owners[token] = i
    groups = defaultdict(list)
    for i in range(len(rows)):
        groups[find(i)].append(i)
    exclusions = Counter()
    strata = defaultdict(list)
    eligible_buckets = Counter()
    source_fields = ('video_id', 'channel_id', 'channel_title', 'title', 'url', 'published_at')
    for indices in groups.values():
        reasons = Counter()
        for i in indices:
            fetched = rows[i].fetched_at
            if fetched is None:
                reasons['missing_fetched_at'] += 1
            elif _utc(fetched) < now - timedelta(days=30):
                reasons['stale'] += 1
            elif _utc(fetched) > now:
                reasons['future_fetched_at'] += 1
        if reasons:
            exclusions.update(reasons)
            exclusions['fresh_members_with_excluded_group'] += len(indices) - sum(reasons.values())
            continue
        if any(rows[i].video_id in reviewed_ids for i in indices):
            exclusions['previously_reviewed_group'] += len(indices)
            continue
        all_keys = sorted({key for i in indices for key in keys[i]})
        group_id = all_keys[0] if all_keys else 'content-sha256:' + min(content_hashes[i] for i in indices)
        # Split is seed-independent so choosing another sampling seed cannot move
        # a known campaign from held-out test into training.
        split_value = int(_hash(group_id)[:16], 16) % 100
        split = 'train' if split_value < 60 else 'validation' if split_value < 80 else 'test'
        members, member_strata = [], []
        for i in indices:
            video = rows[i]
            prediction = getattr(video, 'extraction_is_giveaway', None)
            reason = getattr(video, 'extraction_reason', None)
            bucket = 'positive' if prediction is True else ('rejection:' + reason if reason else 'negative') if prediction is False else 'unextracted'
            stratum = (video.channel_id or '(unknown)', bucket)
            member_strata.append(stratum)
            eligible_buckets[bucket] += 1
            source = {field: getattr(video, field) for field in source_fields}
            source.update(description=video.raw_description or '', fetched_at=_utc(video.fetched_at).isoformat())
            item = dict(video_id=video.video_id, group_id=group_id, suggested_split=split,
                        source_snapshot_sha256=_hash(json.dumps(source, sort_keys=True, ensure_ascii=False)), source=source)
            if split != 'test':
                item['prediction'] = dict(is_giveaway=prediction, confidence=getattr(video, 'extraction_confidence', None),
                    reason=reason, extractor_version=getattr(video, 'extractor_version', None),
                    extracted_at=_utc(video.extracted_at).isoformat() if getattr(video, 'extracted_at', None) else None)
            members.append(item)
        # One deterministic anchor stratum per indivisible group. Mixed groups
        # remain intact; per-video achieved balance is disclosed below.
        anchor = min(member_strata, key=lambda s: (_hash(seed + repr(s) + group_id), s))
        strata[anchor].append((group_id, members, member_strata))
    for values in strata.values():
        values.sort(key=lambda g: (_hash(seed + '\0' + g[0]), g[0]))
    order = sorted(strata, key=lambda s: (_hash(seed + repr(s)), s))
    items, selected_strata = [], Counter()
    while any(strata.values()):
        for stratum in order:
            if not strata[stratum]:
                continue
            _, members, member_strata = strata[stratum].pop(0)
            if len(items) + len(members) > limit:
                exclusions['over_remaining_limit_groups'] += 1
                exclusions['over_remaining_limit_videos'] += len(members)
                continue
            items.extend(members)
            selected_strata.update(member_strata)
    # Prediction balance is operator-only: even aggregate channel/bucket counts
    # can identify a singleton held-out item's prediction by subtraction.
    sampling_audit = dict(eligible_bucket_counts=dict(sorted(eligible_buckets.items())),
        selected_balance=[dict(channel_id=channel, bucket=bucket, count=count)
                          for (channel, bucket), count in sorted(selected_strata.items())])
    manifest = dict(schema_version=1, seed=seed, now=now.isoformat(), limit=limit, selected_videos=len(items),
        selected_groups=len({item['group_id'] for item in items}), exclusions=dict(sorted(exclusions.items())),
        partition_policy='sha256(group_id) modulo 100: train <60; validation <80; test otherwise; seed independent',
        sampling_policy='round-robin channel/prediction strata; indivisible connected groups; not representative',
        grouping_limitations='Normalized full content or identical descriptions and supported campaign links; paraphrases, edited descriptions, unsupported links and future unseen bridges may escape grouping.')
    return dict(manifest=manifest, items=items, sampling_audit=sampling_audit)
