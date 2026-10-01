import { useEffect, useRef, useState } from 'react';

const API = 'http://localhost:8000';

// Pastel theme — soft travel-poster vibe: peach, mint, lavender, warm yellow.
const T = {
  pageBg: 'linear-gradient(135deg, #e9f7ef 0%, #fdf6ec 55%, #fdeef4 100%)',
  card: '#ffffff',
  cardBorder: '#f3e8d8',
  ink: '#3d3428',
  muted: '#8a7d6b',
  accent: '#ffc53d',
  accentInk: '#5b4a1e',
  peach: '#ffe9d6',
  mint: '#d9f2e4',
  lavender: '#e7e1ff',
  sky: '#dff0ff',
  radius: 18,
};

const CONF_STYLE = {
  LIVE: { bg: T.mint, fg: '#1e7a4c' },
  SANDBOX: { bg: T.lavender, fg: '#5b4fb5' },
  ESTIMATED: { bg: T.peach, fg: '#b25a1e' },
  USER: { bg: T.sky, fg: '#1e6fb5' },
};

function Icon({ d, size = 26 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}
const PlaneIcon = (p) => <Icon {...p} d="M10.5 13.5L3 11l18-7-7 18-2.5-7.5L10.5 13.5z M10.5 13.5L21 4" />;
const CarIcon = (p) => (
  <Icon {...p} d="M4 15v-3l1.8-4.2A2 2 0 0 1 7.7 6.5h8.6a2 2 0 0 1 1.9 1.3L20 12v3 M4 12h16 M7 15v2 M17 15v2 M7.5 17h9" />
);
const SparkleIcon = (p) => (
  <Icon {...p} d="M12 4l1.7 4.8L18.5 10.5l-4.8 1.7L12 17l-1.7-4.8L5.5 10.5l4.8-1.7z M19 3l.8 2.2L22 6l-2.2.8L19 9l-.8-2.2L16 6l2.2-.8z" />
);
const PinIcon = (p) => (
  <Icon {...p} size={16} d="M12 21s-6.5-5.4-6.5-10.5A6.5 6.5 0 0 1 12 4a6.5 6.5 0 0 1 6.5 6.5C18.5 15.6 12 21 12 21z M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" />
);

// Address field with type-ahead: debounced calls to GET /geocode/suggest,
// dropdown of concrete addresses ("350 5th St, …") so trips price
// house-to-hotel instead of city-centroid to city-centroid.
function PlaceInput({ label, value, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  useEffect(() => {
    if (value.trim().length < 3) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    const id = ++reqId.current;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch(
          `${API}/geocode/suggest?q=${encodeURIComponent(value.trim())}&limit=6`
        );
        if (reqId.current !== id) return; // stale keystroke — drop it
        setSuggestions(res.ok ? await res.json() : []);
        setOpen(true);
      } catch {
        if (reqId.current === id) {
          setSuggestions([]);
          setOpen(false);
        }
      } finally {
        if (reqId.current === id) setLoading(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <div style={{ position: 'relative' }}>
      <label style={styles.label}>
        {label}
        <input
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => suggestions.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
          style={styles.input}
          autoComplete="off"
        />
      </label>
      {loading && value.trim().length >= 3 && (
        <small style={{ color: T.muted }}>finding places…</small>
      )}
      {open && suggestions.length > 0 && (
        <ul style={styles.dropdown}>
          {suggestions.map((s, i) => (
            <li
              key={`${s.lat},${s.lon},${i}`}
              onMouseDown={() => {
                onChange(s.label);
                setOpen(false);
              }}
              style={styles.dropdownItem}
              onMouseEnter={(e) => (e.currentTarget.style.background = T.peach)}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <span style={{ color: T.muted, marginRight: 6 }}><PinIcon /></span>
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const MODES = [
  { value: 'either', title: 'Surprise me', blurb: 'Price everything', tint: T.lavender, icon: <SparkleIcon /> },
  { value: 'fly', title: 'Fly', blurb: 'Airports + rideshare', tint: T.peach, icon: <PlaneIcon /> },
  { value: 'drive', title: 'Drive', blurb: 'Own or rental car', tint: T.mint, icon: <CarIcon /> },
];

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
  const setDirect = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  async function search() {
    setStatus('searching');
    setPlan(null);
    const payload = { ...form };
    // Optional override: blank (or non-numeric) means "no override" — the key
    // is omitted so the backend falls back to EIA/estimate. Irrelevant for
    // fly mode, where fuel never enters the price.
    const price = parseFloat(form.fuel_price_per_gal);
    if (form.mode === 'fly' || form.fuel_price_per_gal === '' || Number.isNaN(price)) {
      delete payload.fuel_price_per_gal;
    } else {
      payload.fuel_price_per_gal = price;
    }
    if (form.mode === 'fly') delete payload.own_car;
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

  const driveFields = form.mode !== 'fly';

  return (
    <div style={{ ...styles.page, background: T.pageBg }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '2.5rem 1rem 4rem' }}>
        <header style={{ textAlign: 'center', marginBottom: 24 }}>
          <h1 style={{ margin: 0, fontSize: '2.2rem', color: T.ink }}>
            Fare Enough
          </h1>
          <p style={{ margin: '4px 0 0', color: T.muted, fontSize: '1.05rem' }}>
            fair enough — every way there, cheapest first.
          </p>
        </header>

        <div style={styles.card}>
          <div style={{ display: 'grid', gap: 14 }}>
            <PlaceInput
              label="From"
              value={form.origin}
              onChange={setDirect('origin')}
              placeholder="Your address — e.g. Auburn, WA 98092"
            />
            <PlaceInput
              label="To"
              value={form.destination_city}
              onChange={setDirect('destination_city')}
              placeholder="Hotel or address — e.g. Garden Grove, CA"
            />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={styles.label}>Depart
                <input type="date" value={form.depart_date} onChange={set('depart_date')} style={styles.input} />
              </label>
              <label style={styles.label}>Return
                <input type="date" value={form.return_date} onChange={set('return_date')} style={styles.input} />
              </label>
            </div>

            <div>
              <div style={{ ...styles.label, marginBottom: 8 }}>How do you want to go?</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                {MODES.map((m) => {
                  const active = form.mode === m.value;
                  return (
                    <button
                      key={m.value}
                      onClick={() => setForm({ ...form, mode: m.value })}
                      aria-pressed={active}
                      style={{
                        ...styles.modeCard,
                        background: active ? m.tint : '#fff',
                        border: active ? '2px solid #e8935a' : `1.5px solid ${T.cardBorder}`,
                        color: T.ink,
                      }}
                    >
                      <span style={{
                        display: 'inline-flex', padding: 8, borderRadius: 12,
                        background: m.tint, marginBottom: 6,
                      }}>{m.icon}</span>
                      <strong style={{ fontSize: '0.95rem' }}>{m.title}</strong>
                      <small style={{ color: T.muted }}>{m.blurb}</small>
                    </button>
                  );
                })}
              </div>
            </div>

            {driveFields && (
              <div style={{ display: 'grid', gap: 10, background: T.mint, borderRadius: 14, padding: 12 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, color: T.ink }}>
                  <input type="checkbox" checked={form.own_car} onChange={set('own_car')} />
                  I have my own car
                </label>
                <label style={{ ...styles.label, margin: 0 }}>
                  Fuel price ($/gal)
                  <input
                    type="number" min="0.01" max="30" step="0.01" placeholder="e.g. 4.29"
                    value={form.fuel_price_per_gal} onChange={set('fuel_price_per_gal')}
                    style={styles.input}
                  />
                  <small style={{ color: T.muted }}>Optional — leave blank to use the estimate.</small>
                </label>
              </div>
            )}

            <button onClick={search} disabled={status === 'searching'} style={styles.cta}>
              {status === 'searching' ? 'Pricing every option…' : 'Find the cheapest way'}
            </button>
          </div>
        </div>

        {status === 'searching' && (
          <p style={{ textAlign: 'center', color: T.muted }}>Pricing flights, rentals, rideshares…</p>
        )}
        {status === 'failed' && (
          <p style={{ textAlign: 'center', color: '#b25a1e' }}>Something broke on the backend. Check the API logs.</p>
        )}

        {recents.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{ fontSize: '1rem', margin: '0 0 10px', color: T.ink }}>Recent searches</h3>
            <div style={{ display: 'grid', gap: 8 }}>
              {recents.map((r) => (
                <button
                  key={r.job_id}
                  onClick={() => r.status === 'complete' && openRecent(r.job_id)}
                  disabled={r.status !== 'complete'}
                  title={r.status === 'complete' ? 'Load this plan' : `Job ${r.status}`}
                  style={{
                    ...styles.card, textAlign: 'left', padding: '10px 14px',
                    cursor: r.status === 'complete' ? 'pointer' : 'default',
                    opacity: r.status === 'complete' ? 1 : 0.6,
                  }}
                >
                  <strong style={{ color: T.ink }}>{r.origin} → {r.destination_city}</strong>{' '}
                  <small style={{ color: T.muted }}>
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
          <div style={{ display: 'grid', gap: 12, marginTop: 24 }}>
            {plan.warnings && plan.warnings.length > 0 && (
              <div style={{ ...styles.card, background: '#fff8e6', border: '1px solid #e8c96a' }}>
                {plan.warnings.map((w, i) => (
                  <div key={i} style={{ color: '#a60' }}>⚠ {w}</div>
                ))}
              </div>
            )}
            {plan.options.map((o, idx) => (
              <div
                key={o.id}
                style={{
                  ...styles.card,
                  border: idx === 0 ? '2px solid #e8b23a' : `1.5px solid ${T.cardBorder}`,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <strong style={{ color: T.ink }}>
                    {idx === 0 && (
                      <span style={styles.bestTag}>Best deal</span>
                    )}{' '}
                    {o.summary}
                  </strong>
                  <strong style={{ fontSize: '1.25rem', color: T.ink }}>${o.total_usd.toFixed(2)}</strong>
                </div>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, color: T.ink }}>
                  {o.legs.map((l, i) => {
                    const cs = CONF_STYLE[l.confidence] || CONF_STYLE.ESTIMATED;
                    return (
                      <li key={i} style={{ marginBottom: 4 }}>
                        {l.label}: ${l.amount_usd.toFixed(2)}{' '}
                        <span style={{
                          background: cs.bg, color: cs.fg, borderRadius: 999,
                          padding: '1px 8px', fontSize: '0.72rem', fontWeight: 600,
                        }}>
                          {l.confidence}
                        </span>{' '}
                        <small style={{ color: T.muted }}>{l.source}</small>
                      </li>
                    );
                  })}
                </ul>
                {o.warnings.map((w, i) => (
                  <small key={i} style={{ color: '#a60' }}>⚠ {w}</small>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const styles = {
  page: { minHeight: '100vh', fontFamily: 'system-ui, -apple-system, sans-serif', color: '#3d3428' },
  card: {
    background: '#ffffff', border: '1.5px solid #f3e8d8', borderRadius: 18,
    padding: 20, boxShadow: '0 6px 24px rgba(120, 90, 60, 0.08)',
  },
  label: { display: 'grid', gap: 6, fontSize: '0.9rem', fontWeight: 600, color: '#3d3428' },
  input: {
    width: '100%', boxSizing: 'border-box', padding: '10px 12px',
    borderRadius: 12, border: '1.5px solid #eadbc8', fontSize: '1rem',
    background: '#fffdf9', color: '#3d3428', outline: 'none',
  },
  dropdown: {
    position: 'absolute', zIndex: 20, left: 0, right: 0, top: '100%', margin: '4px 0 0',
    padding: 6, listStyle: 'none', background: '#fff', borderRadius: 14,
    border: '1.5px solid #f3e8d8', boxShadow: '0 12px 32px rgba(120, 90, 60, 0.16)',
    maxHeight: 240, overflowY: 'auto',
  },
  dropdownItem: {
    display: 'flex', alignItems: 'center', padding: '9px 10px', borderRadius: 10,
    cursor: 'pointer', fontSize: '0.92rem', color: '#3d3428',
  },
  modeCard: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
    padding: '12px 6px', borderRadius: 16, cursor: 'pointer',
  },
  cta: {
    padding: '13px', borderRadius: 14, border: 'none', cursor: 'pointer',
    background: '#ffc53d', color: '#5b4a1e', fontSize: '1.05rem', fontWeight: 700,
    boxShadow: '0 4px 14px rgba(232, 178, 58, 0.4)',
  },
  bestTag: {
    background: '#ffc53d', color: '#5b4a1e', borderRadius: 999,
    padding: '2px 10px', fontSize: '0.72rem', fontWeight: 700,
    marginRight: 6, verticalAlign: 'middle',
  },
};
