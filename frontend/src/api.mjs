export const PAGE_SIZE = 12

export const FALLBACK_CATEGORIES = [
  ['all', 'All categories'], ['tech', 'Technology'], ['gaming', 'Gaming'], ['books', 'Books'], ['beauty', 'Beauty'],
  ['fashion', 'Fashion'], ['home', 'Home'], ['food', 'Food'], ['travel', 'Travel'], ['sports', 'Sports'],
  ['arts', 'Arts'], ['pets', 'Pets'], ['automotive', 'Automotive'], ['cash', 'Cash & gift cards'], ['other', 'Other'],
].map(([id, label]) => ({ id, label })).sort((a, b) => a.id === 'all' ? -1 : b.id === 'all' ? 1 : a.id === 'other' ? 1 : b.id === 'other' ? -1 : a.label.localeCompare(b.label))

export function resultUrl(mode, { query = '', availability = 'all', confidence = '0', category = 'all', sort = 'confidence', page = 1, groupCampaigns = false, current_only = false }) {
  const params = new URLSearchParams({ q: query.trim(), availability, min_confidence: confidence,
    category, sort, page: String(page), page_size: String(PAGE_SIZE), group_campaigns: String(Boolean(groupCampaigns)), current_only: String(Boolean(current_only)) })
  return `/api/${mode === 'saved' ? 'giveaways' : 'search'}?${params}`
}

export async function fetchResults(url, fetcher = fetch) {
  let response
  try { response = await fetcher(url) } catch {
    throw new Error('Could not reach PrizeScout. Check that the server is running and try again.')
  }
  if (!response.ok) {
    throw new Error(`Could not load results (HTTP ${response.status}). Please try again.`)
  }
  let data
  try { data = await response.json() } catch {
    throw new Error('The server returned an unreadable response. Please try again.')
  }
  if (!data || typeof data !== 'object' || !Array.isArray(data.results) || !Number.isInteger(data.total) || data.total < 0) {
    throw new Error('The server returned an unexpected response. Please try again.')
  }
  data.has_more = Boolean(data.has_more)
  return data
}

export async function fetchCategories(fetcher = fetch, { current_only = false } = {}) {
  try {
    const params = new URLSearchParams({ current_only: String(Boolean(current_only)) })
    const response = await fetcher(`/api/categories?${params}`)
    if (!response.ok) return FALLBACK_CATEGORIES
    const data = await response.json()
    if (!data || !Array.isArray(data.categories)) return FALLBACK_CATEGORIES
    const valid = data.categories.filter(item => item && typeof item.id === 'string' && typeof item.label === 'string')
    return [{ id: 'all', label: 'All categories' }, ...valid.filter(item => item.id !== 'all')]
  } catch { return FALLBACK_CATEGORIES }
}

export async function fetchCoverage(fetcher = fetch) {
  let response
  try { response = await fetcher('/api/coverage') } catch {
    throw new Error('Could not load index coverage. Check the connection and try again.')
  }
  if (!response.ok) throw new Error(`Could not load index coverage (HTTP ${response.status}). Please try again.`)
  let data
  try { data = await response.json() } catch {
    throw new Error('The server returned an unreadable coverage response. Please try again.')
  }
  const countKeys = ['indexed_videos', 'stale_videos', 'analyzed_videos', 'unanalyzed_videos', 'detected_mentions', 'available_mentions', 'unknown_availability_mentions', 'expired_mentions', 'campaigns', 'source_count']
  const validCount = value => Number.isInteger(value) && value >= 0
  if (!data || typeof data !== 'object' || !data.totals || typeof data.totals !== 'object' ||
      !countKeys.every(key => validCount(data.totals[key])) || !Array.isArray(data.sources) || !Array.isArray(data.categories) ||
      !data.sources.every(source => source && typeof source === 'object' && typeof source.channel_id === 'string' && typeof source.channel_title === 'string' &&
        ['fresh_videos', 'stale_videos', 'analyzed_videos', 'detected_mentions', 'active_mentions', 'unknown_mentions', 'expired_mentions'].every(key => validCount(source[key])) &&
        (source.last_fetched_at == null || typeof source.last_fetched_at === 'string') && (source.last_ingested_at == null || typeof source.last_ingested_at === 'string') && typeof source.backfill_complete === 'boolean') ||
      !data.categories.every(item => item && typeof item.id === 'string' && typeof item.label === 'string' && validCount(item.count))) {
    throw new Error('The server returned an unexpected coverage response. Please try again.')
  }
  return data
}

export function formatDate(value) {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatDateTime(value) {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function assessmentLabel(state) {
  return {
    source_reported_open: 'Source reports a future entry deadline',
    expired: 'Deadline passed',
    closed: 'Source reports entry is closed',
    unconfirmed: 'Entry status unconfirmed',
    needs_recheck: 'Needs a fresh source check',
  }[state] || 'Entry status unconfirmed'
}

export function assessmentReasonText(reason) {
  if (typeof reason !== 'string' || !reason.trim()) return ''
  const known = {
    entry_opening_requires_review: 'A separate opening-period claim needs review before entries can be treated as open.',
    stored_expired: 'The indexed record marks the entry period as expired.',
    source_reports_closed: 'The source says the entry period is closed.',
    deadline_missing: 'No entry deadline was identified in the source.',
    deadline_invalid: 'The extracted deadline could not be interpreted as a valid date.',
    deadline_evidence_unsupported: 'The deadline could not be matched to exact entry-period text in the source.',
    supported_deadline_passed: 'The source-supported entry deadline has passed.',
    source_check_missing_or_invalid: 'A valid source check time is unavailable; check the source again.',
    source_check_in_future: 'The recorded source check time is in the future, so current status is unconfirmed.',
    source_cache_stale: 'The cached source is past its 30-day retention window and needs a fresh source check.',
    source_check_too_old: 'The source check is more than 48 hours old and needs to be refreshed.',
    supported_future_entry_deadline: 'The source contains exact text supporting an entry deadline that is still in the future.',
    recent_source_check: 'The source was checked within the 48-hour freshness window.',
    campaign_reports_closed: 'A related source reports that the campaign is closed.',
  }
  if (known[reason]) return known[reason]
  return reason.replace(/[_-]+/g, ' ').replace(/^./, char => char.toUpperCase()).replace(/\.$/, '') + (reason.endsWith('.') ? '' : '.')
}

export function availabilityText(value) {
  return { active: 'Deadline in the future · availability unverified', expired: 'Deadline passed',
    unknown: 'Availability unknown' }[value] || 'Availability unknown'
}

export function videoLink(videoId) {
  return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`
}
