import { moneyRound } from '../lib/ui.js';
import { AlertIcon } from './icons.jsx';

const MODE_LABEL = { fly: 'fly', drive: 'drive', either: 'surprise me' };

// "3411 S Las Vegas Blvd, Las Vegas, NV 89109, United States" → "3411 S Las Vegas Blvd"
const short = (place) => place.split(',')[0];

export default function Recents({ recents, loadingJobId, error, onOpen }) {
  if (!recents.length) return null;
  return (
    <section className="recents">
      <h2 className="section-title">Recent searches</h2>
      {error && <div className="alert error" style={{ marginBottom: 10 }}><AlertIcon />{error}</div>}
      <div className="recent-grid">
        {recents.map((r, i) => {
          const done = r.status === 'complete';
          const failed = r.status === 'failed';
          const loadingThis = loadingJobId === r.job_id;
          return (
            <button
              key={r.job_id}
              className="recent"
              style={{ '--delay': `${Math.min(i, 8) * 40}ms` }}
              onClick={() => done && !loadingThis && onOpen(r.job_id)}
              disabled={!done || loadingThis}
              title={done ? `${r.origin} → ${r.destination_city}` : `Job ${r.status}`}
            >
              <span className="route">{short(r.origin)} → {short(r.destination_city)}</span>
              <span className="sub">
                <span>
                  {r.depart_date}{r.return_date ? ` – ${r.return_date}` : ''}
                </span>
                {done && r.cheapest_usd != null && <span className="cheap">{moneyRound(r.cheapest_usd)}</span>}
              </span>
              <span>
                <span className={`tag ${r.mode}`}>{MODE_LABEL[r.mode] ?? r.mode}</span>{' '}
                {loadingThis && <span className="tag pending">loading…</span>}
                {failed && <span className="tag failed" title={r.error ?? ''}>failed</span>}
                {!done && !failed && <span className="tag pending" title="Is the worker running?">pricing…</span>}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
