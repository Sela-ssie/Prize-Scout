import React, { useEffect, useRef, useState } from 'react'
import { assessmentLabel, assessmentReasonText, availabilityText, fetchCategories, fetchCoverage, fetchResults, formatDate, formatDateTime, PAGE_SIZE, resultUrl, videoLink } from './api.mjs'

function CoveragePanel() {
  const [coverage, setCoverage] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [sourcePage, setSourcePage] = useState(0)
  async function load() {
    setLoading(true); setError('')
    try { setCoverage(await fetchCoverage()) } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }
  function toggle(event) {
    const next = event.currentTarget.open
    if (next && !coverage && !loading) load()
  }
  const totals = coverage?.totals
  return <details className="coverage-panel" onToggle={toggle}>
    <summary>Index coverage</summary>
    <div className="coverage-content" aria-busy={loading}>
      <p className="coverage-note">Coverage grows through background discovery and source checks. These counts describe indexed videos and detected mentions; they do not measure extraction accuracy or confirm legitimacy.</p>
      {loading && <p className="coverage-state" role="status">Loading index coverage…</p>}
      {error && <div className="coverage-error" role="alert"><p>{error}</p><button type="button" className="secondary" onClick={load}>Try again</button></div>}
      {!loading && !error && totals && <>
        <p className="coverage-updated">Updated {formatDate(coverage.generated_at)}</p>
        <div className="coverage-metrics" aria-label="Index coverage totals">
          <span><strong>{totals.indexed_videos.toLocaleString()}</strong> fresh videos</span>
          <span><strong>{totals.source_count.toLocaleString()}</strong> indexed sources</span>
          <span><strong>{totals.detected_mentions.toLocaleString()}</strong> detected mentions</span>
          <span><strong>{totals.unknown_availability_mentions.toLocaleString()}</strong> with unknown availability</span>
        </div>
        {coverage.sources.length > 0 ? <div className="coverage-table-wrap"><table className="coverage-table">
          <caption>Coverage by source</caption>
          <thead><tr><th scope="col">Channel</th><th scope="col">Fresh videos</th><th scope="col">Detected mentions</th><th scope="col">Last scan</th><th scope="col">Backfill</th></tr></thead>
          <tbody>{coverage.sources.slice(sourcePage*25, (sourcePage+1)*25).map(source => <tr key={source.channel_id}>
            <th scope="row">{source.channel_title}</th><td>{source.fresh_videos.toLocaleString()}</td><td>{source.detected_mentions.toLocaleString()}</td>
            <td>{formatDate(source.last_ingested_at || source.last_fetched_at)}</td><td>{source.backfill_complete ? 'Complete' : source.last_ingested_at ? 'In progress' : 'Not recorded'}</td>
          </tr>)}</tbody>
        </table>{coverage.sources.length > 25 && <nav className="pagination" aria-label="Source pages"><button type="button" className="secondary" disabled={sourcePage === 0} onClick={() => setSourcePage(value => value-1)}>← Previous sources</button><span>{sourcePage*25+1}–{Math.min((sourcePage+1)*25, coverage.sources.length)} of {coverage.sources.length} sources</span><button type="button" className="secondary" disabled={(sourcePage+1)*25 >= coverage.sources.length} onClick={() => setSourcePage(value => value+1)}>Next sources →</button></nav>}</div> : <p className="coverage-state">No source coverage is available yet.</p>}
      </>}
    </div>
  </details>
}

function ResultCard({ result, demo }) {
  const title = result.video_title || result.title || 'Watch source video'
  const howto = result.howto
  const isDemo = demo || result.is_demo
  const campaign = result.campaign
  const sources = Array.isArray(campaign?.sources) ? campaign.sources : []
  const evidence = result.evidence && typeof result.evidence === 'object' ? result.evidence : {}
  const assessment = result.availability_assessment && typeof result.availability_assessment === 'object' ? result.availability_assessment : null
  const assessmentReasons = Array.isArray(assessment?.reasons) ? assessment.reasons.map(assessmentReasonText).filter(Boolean) : []
  const fieldLabels = { prizes: 'Prize', deadline: 'Deadline', howto: 'How to enter' }
  const displayValue = value => Array.isArray(value) ? value.join('; ') : value || 'Not identified'
  const sourceTitle = source => source.video_title || 'Watch source video'
  const sourceIsDemo = source => isDemo || source.is_demo
  const sourceLink = source => videoLink(source.video_id || '')
  return <article className="result">
    {campaign && sources.length > 1 && <div className="campaign-badge">{sources.length} related videos</div>}
    {campaign?.conflicting_fields?.length > 0 && <p className="difference-note">Sources report different details. Compare their descriptions below.</p>}
    <div className={`badge ${assessment?.state === 'expired' || result.availability === 'expired' ? 'expired' : ''}`}>{assessment ? assessmentLabel(assessment.state) : availabilityText(result.availability)}</div>
    {assessment && <div className="assessment-details">
      {assessmentReasons.length > 0 ? <ul>{assessmentReasons.map((reason, i) => <li key={`${reason}-${i}`}>{reason}</li>)}</ul> : <p>Current entry status has not been confirmed from the source.</p>}
      {assessment.deadline && <p>Reported entry deadline: {formatDate(assessment.deadline)}</p>}
      <div className="assessment-times">
        {assessment.source_checked_at && <span>Source checked {formatDateTime(assessment.source_checked_at)}</span>}
        {assessment.recheck_due_at && <span>Fresh check due {formatDateTime(assessment.recheck_due_at)}</span>}
      </div>
    </div>}
    <h3>{isDemo ? <span>{title}</span> : <a href={`https://www.youtube.com/watch?v=${encodeURIComponent(result.video_id || '')}`} target="_blank" rel="noreferrer">{title} <span aria-hidden="true">↗</span></a>}</h3>
    {result.channel_title && <p className="channel">From {result.channel_title}</p>}
    <dl>
      <dt>Prize</dt><dd>{result.prizes || 'Prize not identified — check the source video.'}</dd>
      <dt>Reported deadline</dt><dd>{formatDate(assessment?.deadline || result.deadline) === 'Unknown' ? (assessment?.deadline || result.deadline || 'No clear deadline found.') : formatDate(assessment?.deadline || result.deadline)}</dd>
      <dt>How to enter</dt><dd>{Array.isArray(howto) && howto.length ? <ul>{howto.map((step, i) => <li key={i}>{step}</li>)}</ul> : typeof howto === 'string' && howto ? howto : 'Entry details not identified — check the source video.'}</dd>
    </dl>
    <div className="result-meta"><span>Published {formatDate(result.published_at)}</span><span>Indexed {formatDate(result.fetched_at)}</span>
      <span>Extraction score: {typeof result.confidence === 'number' ? result.confidence.toFixed(3) : 'unknown'}</span>
      {Array.isArray(result.categories) && result.categories.length > 0 && <span>Categories: {result.categories.join(', ')}</span>}
    </div>
    {Object.entries(evidence).some(([, spans]) => Array.isArray(spans) && spans.length > 0) && <details className="evidence">
      <summary>Source evidence</summary>
      {Object.entries(evidence).filter(([, spans]) => Array.isArray(spans) && spans.length > 0).map(([field, spans]) => <div key={field}>
        <h4>{fieldLabels[field] || field}</h4>
        {spans.map((span, i) => span?.quote ? <blockquote key={`${span.source}-${span.start}-${i}`}><q>{span.quote}</q><span>{span.source === 'title' ? 'Title' : 'Description'}</span></blockquote> : null)}
      </div>)}
    </details>}
    {campaign && sources.length > 1 && <details className="related-videos">
      <summary>Related videos and extracted details ({campaign.source_count || sources.length})</summary>
      <p className="grouping-note">Grouped because these videos share an entry link. This is an inferred connection, not a legitimacy check.</p>
      {Array.isArray(campaign.conflicting_fields) && campaign.conflicting_fields.length > 0 && <p className="difference-note">Sources differ on: {campaign.conflicting_fields.map(field => fieldLabels[field] || field).join(', ')}.</p>}
      <ul>{sources.map((source, i) => <li key={source.video_id || i}>
        <div>{sourceIsDemo(source) ? <span>{sourceTitle(source)}</span> : <a href={sourceLink(source)} target="_blank" rel="noreferrer">{sourceTitle(source)} <span aria-hidden="true">↗</span></a>}
          {source.channel_title && <span className="related-channel"> · {source.channel_title}</span>}</div>
        <p>{assessmentLabel(source.availability_assessment?.state)} · Source checked {formatDateTime(source.availability_assessment?.source_checked_at || source.fetched_at)}</p>
        <dl>{(campaign.conflicting_fields || []).map(field => <React.Fragment key={field}><dt>{fieldLabels[field] || field}</dt><dd>{displayValue(source[field])}</dd></React.Fragment>)}</dl>
      </li>)}</ul>
    </details>}
  </article>
}

export default function App() {
  const [query, setQuery] = useState('')
  const [availability, setAvailability] = useState('all')
  const [confidence, setConfidence] = useState('0')
  const [category, setCategory] = useState('all')
  const [sort, setSort] = useState('confidence')
  const [groupCampaigns, setGroupCampaigns] = useState(true)
  const [includeUnconfirmed, setIncludeUnconfirmed] = useState(false)
  const [categories, setCategories] = useState([{ id: 'all', label: 'All categories' }])
  const [data, setData] = useState(null)
  const [request, setRequest] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const sequence = useRef(0)

  useEffect(() => {
    let active = true
    fetchCategories(undefined, { current_only: !includeUnconfirmed }).then(value => { if (active) setCategories(value) })
    return () => { active = false }
  }, [includeUnconfirmed])
  async function load(next) {
    const id = ++sequence.current
    setLoading(true); setError(''); setRequest(next)
    try {
      const result = await fetchResults(resultUrl(next.mode, next))
      if (id === sequence.current) setData(result)
    } catch (err) { if (id === sequence.current) setError(err.message) }
    finally { if (id === sequence.current) setLoading(false) }
  }
  useEffect(() => { load({ mode: 'saved', query: '', availability, confidence, category, sort, groupCampaigns, current_only: true, page: 1 }) }, [])
  function submit(event) { event.preventDefault(); load({ mode: 'search', query, availability, confidence, category, sort, groupCampaigns, current_only: !includeUnconfirmed, page: 1 }) }
  function changeFilter(key, value) {
    const next = { mode: request?.mode || 'saved', query: request?.mode === 'search' ? query : '', availability, confidence, category, sort, groupCampaigns, current_only: !includeUnconfirmed, page: 1, [key]: value }
    if (key === 'availability') setAvailability(value)
    if (key === 'confidence') setConfidence(value)
    if (key === 'category') setCategory(value)
    if (key === 'sort') setSort(value)
    load(next)
  }
  function changeGrouping(value) {
    setGroupCampaigns(value)
    load({ mode: request?.mode || 'saved', query: request?.mode === 'search' ? query : '', availability, confidence, category, sort, groupCampaigns: value, current_only: !includeUnconfirmed, page: 1 })
  }
  function changeCurrentEvidence(value) {
    setIncludeUnconfirmed(value)
    const nextAvailability = !value && ['unknown', 'expired'].includes(availability) ? 'all' : availability
    if (nextAvailability !== availability) setAvailability(nextAvailability)
    load({ mode: request?.mode || 'saved', query: request?.mode === 'search' ? query : '', availability: nextAvailability, confidence, category, sort, groupCampaigns, current_only: !value, page: 1 })
  }
  const demo = Boolean(data?.index_stats?.demo)
  const indexedVideos = data?.index_stats?.indexed_videos
  const hasNoIndexedVideos = Number.isInteger(indexedVideos) && indexedVideos === 0
  const hasNoIndexedMentions = Number.isInteger(indexedVideos) && indexedVideos > 0 && data?.index_stats?.giveaways === 0
  const hasNoCurrentEvidence = !includeUnconfirmed && data?.index_stats && data.index_stats.current_evidence_mentions === 0

  function reset() {
    setQuery(''); setAvailability('all'); setConfidence('0'); setCategory('all'); setSort('confidence'); setGroupCampaigns(true); setIncludeUnconfirmed(false)
    load({ mode: 'saved', query: '', availability: 'all', confidence: '0', category: 'all', sort: 'confidence', groupCampaigns: true, current_only: true, page: 1 })
  }

  return <main className="container">
    <header><div className="eyebrow">YOUTUBE GIVEAWAY INDEX</div><h1>PrizeScout<span aria-hidden="true">.</span></h1>
      <p className="subtitle">Find the prize. Check the details.</p>
      <p>Browse giveaway mentions collected from watched channels.</p>
      {data?.index_stats && <div className="index-stats" aria-label="Index status"><span>{data.index_stats.current_evidence_mentions ?? 0} mentions with current evidence</span><span>{data.index_stats.giveaways ?? data.total} indexed mentions</span><span>{data.index_stats.watched_channels ?? 0} watched channels</span>{data.index_stats.last_ingested_at && <span>Last updated {formatDate(data.index_stats.last_ingested_at)}</span>}</div>}
      <CoveragePanel />
    </header>
    {demo && <aside className="notice" role="status"><strong>Fictional demo.</strong> These examples show how PrizeScout works. There are no real prizes or entries in this preview.</aside>}
    <form onSubmit={submit} className="search-panel">
      <label htmlFor="query">What are you looking for?</label>
      <div className="search-row"><input id="query" value={query} onChange={e => setQuery(e.target.value)} placeholder="Try books, skincare, cookware, or a trip" maxLength={200} />
        <button type="submit">Search index</button></div>
      <div className="category-chips" aria-label="Filter by category">{categories.map(item => <button key={item.id} type="button" className={`chip ${category === item.id ? 'selected' : ''}`} aria-pressed={category === item.id} onClick={() => changeFilter('category', item.id)}>{item.label}{Number.isInteger(item.count) ? ` (${item.count})` : ''}</button>)}</div>
      <div className="filters">
        <label htmlFor="availability">Availability<select id="availability" value={availability} onChange={e => changeFilter('availability', e.target.value)}><option value="all">{includeUnconfirmed ? 'Future or unknown deadline' : 'Current source evidence'}</option><option value="active">Future deadline (unverified)</option><option value="unknown" disabled={!includeUnconfirmed}>Unknown availability{!includeUnconfirmed ? ' (include unconfirmed mentions)' : ''}</option><option value="expired" disabled={!includeUnconfirmed}>Deadline passed{!includeUnconfirmed ? ' (include unconfirmed mentions)' : ''}</option></select></label>
        <label htmlFor="confidence">Extraction score<select id="confidence" value={confidence} onChange={e => changeFilter('confidence', e.target.value)}><option value="0">Any score</option><option value="0.7">0.7 or higher</option><option value="0.9">0.9 or higher</option></select></label>
        <label htmlFor="sort">Sort by<select id="sort" value={sort} onChange={e => changeFilter('sort', e.target.value)}><option value="confidence">Extraction score</option><option value="newest">Newest</option><option value="deadline">Deadline</option></select></label>
        <button className="secondary" type="button" onClick={reset}>Clear filters</button>
      </div>
      <label className="group-toggle"><input type="checkbox" checked={groupCampaigns} onChange={e => changeGrouping(e.target.checked)} /> Group related videos</label>
      <label className="group-toggle"><input type="checkbox" checked={includeUnconfirmed} onChange={e => changeCurrentEvidence(e.target.checked)} /> Include unconfirmed mentions</label>
      <p className="hint">By default, results need current source evidence. Including unconfirmed mentions adds indexed entries whose current status is not established.</p>
      <p className="hint">Filters update the shared local index. The experimental score measures text signals; it is not a verified accuracy rate.</p>
    </form>
    <aside className="notice">Details are extracted automatically. A listed prize or future deadline does not verify that a giveaway is still available or legitimate. Check the creator’s source and rules.</aside>
    <section aria-label="Search results" aria-busy={loading}>
      {loading && <p className="state" role="status">Loading the saved index…</p>}
      {error && <div className="error" role="alert"><p>{error}</p><button className="secondary" onClick={() => request && load(request)}>Try again</button></div>}
      {!loading && !error && data && <>
        <div className="results-heading"><h2>{request?.mode === 'saved' ? 'Browse giveaways' : 'Search results'}</h2><p role="status">{data.total} {data.total === 1 ? 'result' : 'results'}{request?.query ? ` for “${request.query}”` : ''}{demo && <span className="demo-label"> · demo data</span>}</p></div>
        {data.results.length === 0 ? <div className="state"><h3>{hasNoIndexedVideos ? 'The index is waiting for its first scan' : hasNoIndexedMentions ? 'No giveaway mentions have been detected yet' : hasNoCurrentEvidence ? 'No current source evidence found' : 'No matching giveaways'}</h3><p>{hasNoIndexedVideos ? 'No source videos have been indexed yet. Try again after watched channels have been scanned.' : hasNoIndexedMentions ? `${indexedVideos.toLocaleString()} source videos have been indexed, but none currently contain an identified giveaway mention.` : hasNoCurrentEvidence ? `${data.index_stats.unconfirmed_mentions ?? 0} indexed mentions have unconfirmed entry status. Turn on “Include unconfirmed mentions” to review them.` : 'Try a broader keyword, another category, or a lower confidence filter.'}</p></div> : <div className="results-grid">{data.results.map((result, i) => <ResultCard key={result.video_id || `${i}`} result={result} demo={demo} />)}</div>}
        {data.total > PAGE_SIZE && <nav className="pagination" aria-label="Result pages"><button className="secondary" disabled={request.page === 1} onClick={() => load({ ...request, page: request.page - 1 })}>← Previous</button><span>Page {request.page} of {Math.ceil(data.total / PAGE_SIZE)}</span><button className="secondary" disabled={!data.has_more} onClick={() => load({ ...request, page: request.page + 1 })}>Next →</button></nav>}
      </>}
    </section>
    <footer>PrizeScout · Follow the source, read the rules.</footer>
  </main>
}
