import React, { useState } from 'react'

export default function App(){
  const [q, setQ] = useState('giveaway')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [saved, setSaved] = useState([])
  const [stats, setStats] = useState(null)

  async function search(){
    setLoading(true); setResults([]); setStats(null)
    try{
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`)
      const data = await res.json()
      setResults(data.results || [])
      if(data.searched && data.found !== undefined) {
        setStats({ searched: data.searched, found: data.found })
      }
    }catch(err){
      console.error(err)
    }finally{ setLoading(false) }
  }

  async function loadSaved(){
    try{
      const res = await fetch('/api/giveaways')
      const data = await res.json()
      setSaved(data.results || [])
    }catch(err){ console.error(err) }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      search()
    }
  }

  return (
    <div className="container">
      <h1>💎 Prize Scout 💎</h1>
      <p className="subtitle">Discover the latest YouTube giveaways in seconds</p>
      
      <div className="search">
        <input 
          value={q} 
          onChange={e=>setQ(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="Search for giveaways..."
          disabled={loading}
        />
        <button onClick={search} disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </button>
      </div>

      {stats && (
        <div className="stats">
          Searched {stats.searched} videos • Found {stats.found} high-quality giveaways
        </div>
      )}

      {loading && <div className="loading">🔍 Searching YouTube for giveaways...</div>}
      
      {results.length === 0 && !loading && stats && (
        <div className="error">No giveaways found. Try a different search term!</div>
      )}

      {results.length > 0 && (
        <>
          <h2 style={{color: '#d4af37', marginBottom: '1rem', fontSize: '1.8rem'}}>
            ✨ Active Giveaways ({results.length})
          </h2>
          <div className="results-grid">
            {results.map(r=> (
              <div key={r.video_id} className="result">
                <h3>
                  <a href={r.video_url} target="_blank" rel="noreferrer">
                    {r.video_title}
                  </a>
                </h3>
                
                {r.prizes && (
                  <div className="result-section">
                    <h4>🏆 Prizes</h4>
                    <p>{r.prizes}</p>
                  </div>
                )}
                
                {r.deadline && (
                  <div className="result-section">
                    <h4>⏰ Deadline</h4>
                    <p>{r.deadline}</p>
                  </div>
                )}
                
                {r.howto && (
                  <div className="result-section">
                    <h4>📝 How to Enter</h4>
                    {Array.isArray(r.howto) ? (
                      <ul>
                        {r.howto.map((step, i) => (
                          <li key={i}>{step}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>{r.howto}</p>
                    )}
                  </div>
                )}
                
                {r.published_at && (
                  <div className="published-date">
                    Posted: {new Date(r.published_at).toLocaleDateString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric'
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {saved.length > 0 && (
        <div style={{marginTop: '3rem'}}>
          <h2 style={{color: '#d4af37', marginBottom: '1rem'}}>📚 Saved Giveaways</h2>
          <div className="results-grid">
            {saved.map(s => (
              <div key={s.id} className="result">
                <p><strong>Video ID:</strong> {s.video_id}</p>
                <p><strong>Prizes:</strong> {s.prizes}</p>
                <p><strong>Deadline:</strong> {s.deadline}</p>
                <p><strong>Howto:</strong> {s.howto}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

