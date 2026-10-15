import { test } from 'node:test'
import assert from 'node:assert/strict'
import { availabilityText, FALLBACK_CATEGORIES, fetchCategories, fetchCoverage, fetchResults, formatDate, resultUrl, videoLink } from '../src/api.mjs'

const coverageFixture = {
  generated_at: '2026-10-14T12:00:00Z',
  totals: { indexed_videos: 12, stale_videos: 1, analyzed_videos: 10, unanalyzed_videos: 2, detected_mentions: 4, available_mentions: 2, unknown_availability_mentions: 1, expired_mentions: 1, campaigns: 3, source_count: 2 },
  sources: [{ channel_id: 'channel-1', channel_title: 'Example channel', fresh_videos: 8, stale_videos: 1, analyzed_videos: 7, detected_mentions: 3, active_mentions: 1, unknown_mentions: 1, expired_mentions: 1, last_fetched_at: '2026-10-13T12:00:00Z', last_ingested_at: null, backfill_complete: false }],
  categories: [{ id: 'books', label: 'Books', count: 2 }],
}

test('search and saved routes preserve query/filter/pagination contract', () => {
  const url = new URL(resultUrl('search', { query: ' PC & console ', availability: 'unknown', confidence: '0.9', page: 3 }), 'http://localhost')
  assert.equal(url.pathname, '/api/search')
  assert.equal(url.searchParams.get('q'), 'PC & console')
  assert.equal(url.searchParams.get('availability'), 'unknown')
  assert.equal(url.searchParams.get('min_confidence'), '0.9')
  assert.equal(url.searchParams.get('page'), '3')
  assert.equal(url.searchParams.get('page_size'), '12')
  assert.equal(url.searchParams.get('category'), 'all')
  assert.equal(url.searchParams.get('sort'), 'confidence')
  assert.ok(resultUrl('saved', {}).startsWith('/api/giveaways?'))
})
test('category and sort filters are encoded in the index request', () => {
  const url = new URL(resultUrl('saved', { category: 'arts', sort: 'deadline' }), 'http://localhost')
  assert.equal(url.searchParams.get('category'), 'arts')
  assert.equal(url.searchParams.get('sort'), 'deadline')
})
test('campaign grouping flag is explicit and backward-compatible by default', () => {
  for (const [mode, path] of [['search', '/api/search'], ['saved', '/api/giveaways']]) {
    const enabled = new URL(resultUrl(mode, { groupCampaigns: true }), 'http://localhost')
    const disabled = new URL(resultUrl(mode, { groupCampaigns: false }), 'http://localhost')
    const legacy = new URL(resultUrl(mode, {}), 'http://localhost')
    assert.equal(enabled.pathname, path)
    assert.equal(enabled.searchParams.get('group_campaigns'), 'true')
    assert.equal(disabled.searchParams.get('group_campaigns'), 'false')
    assert.equal(legacy.searchParams.get('group_campaigns'), 'false')
  }
})
test('zero results is a successful response', async () => {
  const data = { results: [], total: 0, has_more: false }
  assert.deepEqual(await fetchResults('/api/search', async () => ({ ok: true, json: async () => data })), data)
})
test('network, HTTP, malformed JSON and invalid shapes give visible error messages', async () => {
  await assert.rejects(fetchResults('/', async () => { throw new Error('offline') }), /Could not reach/)
  await assert.rejects(fetchResults('/', async () => ({ ok: false, status: 503 })), /HTTP 503/)
  await assert.rejects(fetchResults('/', async () => ({ ok: true, json: async () => { throw new Error('JSON') } })), /unreadable/)
  await assert.rejects(fetchResults('/', async () => ({ ok: true, json: async () => ({ results: [] }) })), /unexpected/)
  await assert.rejects(fetchResults('/', async () => ({ ok: true, json: async () => null })), /unexpected/)
})
test('coverage endpoint returns a validated payload', async () => {
  assert.deepEqual(await fetchCoverage(async path => {
    assert.equal(path, '/api/coverage')
    return { ok: true, json: async () => coverageFixture }
  }), coverageFixture)
})
test('coverage fetch reports network, HTTP, unreadable and malformed responses', async () => {
  await assert.rejects(fetchCoverage(async () => { throw new Error('offline') }), /Could not load index coverage/)
  await assert.rejects(fetchCoverage(async () => ({ ok: false, status: 503 })), /HTTP 503/)
  await assert.rejects(fetchCoverage(async () => ({ ok: true, json: async () => { throw new Error('bad json') } })), /unreadable coverage/)
  await assert.rejects(fetchCoverage(async () => ({ ok: true, json: async () => ({ totals: {}, sources: [], categories: [] }) })), /unexpected coverage/)
  await assert.rejects(fetchCoverage(async () => ({ ok: true, json: async () => ({ ...coverageFixture, sources: [{ ...coverageFixture.sources[0], backfill_complete: 'unknown' }] }) })), /unexpected coverage/)
})
test('categories fall back when the endpoint fails and preserve server counts', async () => {
  assert.deepEqual(await fetchCategories(async () => { throw new Error('offline') }), FALLBACK_CATEGORIES)
  assert.deepEqual(await fetchCategories(async () => ({ ok: true, json: async () => ({ categories: [{ id: 'tech', label: 'Technology', count: 4 }] }) })), [
    { id: 'all', label: 'All categories' }, { id: 'tech', label: 'Technology', count: 4 },
  ])
})
test('dates are readable and malformed values stay unknown', () => {
  assert.equal(formatDate(null), 'Unknown')
  assert.equal(formatDate('not a date'), 'Unknown')
  assert.equal(formatDate('2026-04-01T00:00:00Z'), new Date('2026-04-01T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }))
})
test('availability and safe source links never claim verification', () => {
  assert.match(availabilityText('active'), /unverified/)
  assert.equal(availabilityText('unknown'), 'Availability unknown')
  assert.equal(availabilityText(null), 'Availability unknown')
  assert.equal(videoLink('id&x=1'), 'https://www.youtube.com/watch?v=id%26x%3D1')
})
