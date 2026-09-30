import { useEffect, useState } from 'react';

const API = 'http://localhost:8000';

// Thin client over POST /trips (job model): submit, poll, render ranked options.
export default function App() {
  const [form, setForm] = useState({
    origin: 'Auburn, WA 98092',
    destination_city: 'Los Angeles',
    depart_date: '2026-10-16',
    return_date: '2026-10-19',
    mode: 'either',
    own_car: true,
    fuel_price_per_gal: '',
  });
  const [plan, setPlan] = useState(null);
  const [status, setStatus] = useState('idle');
  const [recents, setRecents] = useState([]);

  async function loadRecents() {
    try {
      setRecents(await (await fetch(`${API}/trips/recent`)).json());
    } catch {
      // Backend unreachable — the search form below still explains itself.
    }
  }

  useEffect(() => { loadRecents(); }, []);

  async function openRecent(job_id) {
    const job = await (await fetch(`${API}/trips/${job_id}`)).json();
    if (job.status === 'complete' && job.plan) {
      setPlan(job.plan);
      setStatus('done');
      window.scrollTo({ top: 0 });
    }
  }

  const set = (k) => (e) =>
    setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  async function search() {
    setStatus('searching');
    setPlan(null);
    // Optional override: blank (or non-numeric) means "no override" — the key
    // is omitted so the backend falls back to EIA/estimate.
    const payload = { ...form };
    const price = parseFloat(form.fuel_price_per_gal);
    if (form.fuel_price_per_gal === '' || Number.isNaN(price)) {
      delete payload.fuel_price_per_gal;
    } else {
      payload.fuel_price_per_gal = price;
    }
    const res = await fetch(`${API}/trips`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const { job_id } = await res.json();
    // Poll until the engine finishes pricing every branch.
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await (await fetch(`${API}/trips/${job_id}`)).json();
      if (job.status === 'complete') {
        setPlan(job.plan);
        setStatus('done');
        loadRecents();
        return;
      }
      if (job.status === 'failed') {
        setStatus('failed');
        loadRecents();
        return;
      }
    }
    setStatus('timeout');
    loadRecents();
  }

  return (
    <div style={{ maxWidth: 720, margin: '2rem auto', fontFamily: 'system-ui' }}>
      <h1>Fare Enough <span style={{ fontWeight: 400, fontSize: '1rem' }}>— fair enough.</span></h1>

      <div style={{ display: 'grid', gap: 8, marginBottom: 16 }}>
        <label>From <input value={form.origin} onChange={set('origin')} style={{ width: '100%' }} /></label>
        <label>To <input value={form.destination_city} onChange={set('destination_city')} style={{ width: '100%' }} /></label>
        <label>Depart <input type="date" value={form.depart_date} onChange={set('depart_date')} /></label>
        <label>Return <input type="date" value={form.return_date} onChange={set('return_date')} /></label>
        <label>Mode{' '}
          <select value={form.mode} onChange={set('mode')}>
            <option value="either">Either — surprise me</option>
            <option value="fly">Fly</option>
            <option value="drive">Drive</option>
          </select>
        </label>
        <label><input type="checkbox" checked={form.own_car} onChange={set('own_car')} /> I have my own car</label>
        <label>Fuel price ($/gal){' '}
          <input
            type="number" min="0.01" max="30" step="0.01" placeholder="e.g. 4.29"
            value={form.fuel_price_per_gal} onChange={set('fuel_price_per_gal')}
            style={{ width: '100%' }}
          />
          <small style={{ color: '#666' }}>Optional — leave blank to use the estimate.</small>
        </label>
        <button onClick={search} disabled={status === 'searching'}>
          {status === 'searching' ? 'Pricing every option…' : 'Find the cheapest way'}
        </button>
      </div>

      {status === 'searching' && <p>Pricing flights, rentals, rideshares…</p>}
      {status === 'failed' && <p>Something broke on the backend. Check the API logs.</p>}

      {recents.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h3 style={{ fontSize: '1rem', margin: '0 0 8px' }}>Recent searches</h3>
          <div style={{ display: 'grid', gap: 6 }}>
            {recents.map((r) => (
              <button
                key={r.job_id}
                onClick={() => r.status === 'complete' && openRecent(r.job_id)}
                disabled={r.status !== 'complete'}
                title={r.status === 'complete' ? 'Load this plan' : `Job ${r.status}`}
                style={{
                  textAlign: 'left', padding: '8px 12px', borderRadius: 8,
                  border: '1px solid #ccc', background: '#fff',
                  cursor: r.status === 'complete' ? 'pointer' : 'default',
                }}
              >
                <strong>{r.origin} → {r.destination_city}</strong>{' '}
                <small style={{ color: '#666' }}>
                  {r.depart_date}{r.return_date ? ` – ${r.return_date}` : ''} · {r.status}
                  {r.cheapest_usd != null && <> · cheapest ${r.cheapest_usd.toFixed(2)}</>}
                  {r.error && <> · {r.error.slice(0, 60)}</>}
                </small>
              </button>
            ))}
          </div>
        </div>
      )}

      {plan && (
        <div style={{ display: 'grid', gap: 12 }}>
          {plan.warnings && plan.warnings.length > 0 && (
            <div style={{ border: '1px solid #c90', borderRadius: 8, padding: 12, background: '#fff8e6' }}>
              {plan.warnings.map((w, i) => (
                <div key={i} style={{ color: '#a60' }}>⚠ {w}</div>
              ))}
            </div>
          )}
          {plan.options.map((o) => (
            <div key={o.id} style={{ border: '1px solid #ccc', borderRadius: 8, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <strong>{o.summary}</strong>
                <strong>${o.total_usd.toFixed(2)}</strong>
              </div>
              <ul>
                {o.legs.map((l, i) => (
                  <li key={i}>
                    {l.label}: ${l.amount_usd.toFixed(2)}{' '}
                    <small style={{ color: '#666' }}>
                      [{l.confidence} · {l.source}]
                    </small>
                  </li>
                ))}
              </ul>
              {o.warnings.map((w, i) => (
                <small key={i} style={{ color: '#a60' }}>⚠ {w}</small>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
