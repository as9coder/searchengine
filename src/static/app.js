const API_BASE = window.location.origin;
const queryInput = document.getElementById('query');
const searchBtn = document.getElementById('search-btn');
const resultsDiv = document.getElementById('results');
const statsDiv = document.getElementById('stats');

if (window.location.protocol === 'file:') {
    resultsDiv.innerHTML = '<div class="error">You opened this file directly. You must visit <b>http://localhost:8000/</b> in your browser instead.</div>';
}

async function doSearch() {
    const q = queryInput.value.trim();
    if (!q) return;
    
    resultsDiv.innerHTML = '<div class="loading">Searching...</div>';
    statsDiv.textContent = '';
    
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    
    try {
        const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(q)}&limit=20`, {
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        
        const data = await res.json();
        
        if (!data.results || data.results.length === 0) {
            resultsDiv.innerHTML = '<div class="no-results">No results found</div>';
            statsDiv.textContent = `0 results for "${q}"`;
            return;
        }
        
        statsDiv.textContent = `${data.results.length} results for "${q}"`;
        
        resultsDiv.innerHTML = data.results.map(r => `
            <div class="result-item">
                <a class="result-title" href="/view/${r.id}">${escapeHtml(r.title || r.url)}</a>
                <div class="result-url">${r.url}</div>
                <div class="result-meta">
                    <span class="badge type-${r.content_type}">${r.content_type}</span>
                    <span class="badge quality">Q: ${r.quality_score?.toFixed ? r.quality_score.toFixed(2) : r.quality_score}</span>
                </div>
                <div class="result-desc">${escapeHtml(r.description || '')}</div>
            </div>
        `).join('');
        
    } catch (err) {
        if (err.name === 'AbortError') {
            resultsDiv.innerHTML = '<div class="error">Search timed out. Is the API running at <b>http://localhost:8000</b>?</div>';
        } else {
            resultsDiv.innerHTML = `<div class="error">Search failed: ${err.message}. Make sure <b>python main.py</b> is running.</div>`;
        }
        console.error(err);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

searchBtn.addEventListener('click', doSearch);
queryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doSearch();
});

queryInput.focus();
