import { useEffect, useRef, useState } from 'react';
import Hero from './components/Hero.jsx';
import Loading from './components/Loading.jsx';
import PlaceInput from './components/PlaceInput.jsx';
import Recents from './components/Recents.jsx';
import ResultCard from './components/ResultCard.jsx';
import { AlertIcon, CarIcon, PlaneIcon, SparkleIcon, SwapIcon } from './components/icons.jsx';
import { API, money } from './lib/ui.js';

const MODES = [
  { value: 'either', title: 'Surprise me', blurb: 'Price everything', tint: 'var(--lavender)', Icon: SparkleIcon },
  { value: 'fly', title: 'Fly', blurb: 'Airports + rides', tint: 'var(--peach)', Icon: PlaneIcon },
  { value: 'drive', title: 'Drive', blurb: 'Own or rental', tint: 'var(--mint)', Icon: CarIcon },
];

// Turns a FastAPI 422 body into one readable line.
async function errorText(res) {
  try {
    const body = await res.json();
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => d.msg.replace(/^Value error, /, '')).join('; ');
    }
    return body.detail || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

// Thin client over POST /trips (job model): submit, poll, render ranked options.
export default function App() {
  const [form, setForm] = useState({
    origin: '',
    destination_city: '',
    depart_date: '',
    return_date: '',
    mode: 'either',
    travelers: '1',
    own_car: true,
    fuel_price_per_gal: '',
  });
  const [plan, setPlan] = useState(null);
  const [status, setStatus] = useState('idle');
  const [errorMsg, setErrorMsg] = useState(null);
  const [recents, setRecents] = useState([]);
  const [loadingJobId, setLoadingJobId] = useState(null);
  const [recentError, setRecentError] = useState(null);
  const [spun, setSpun] = useState(false);
  const resultsRef = useRef(null);

  async function loadRecents() {
    try {
      setRecents(await (await fetch(`${API}/trips/recent`)).json());
    } catch {
      // Backend unreachable — the search form below still explains itself.
    }
  }

  useEffect(() => { loadRecents(); }, []);

  function scrollToResults() {
    // Let React paint the new plan first, then glide to it.
    setTimeout(() => {
      resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 60);
  }

  async function openRecent(job_id) {
    setLoadingJobId(job_id);
    setRecentError(null);
    try {
      const job = await (await fetch(`${API}/trips/${job_id}`)).json();
      if (job.status === 'complete' && job.plan) {
        setPlan(job.plan);
        setStatus('done');
        scrollToResults();
      } else {
        setRecentError("Couldn't load that saved search — try running it again.");
      }
    } catch {
      setRecentError("Couldn't reach the backend to load that search.");
    } finally {
      setLoadingJobId(null);
    }
  }

  const set = (k) => (e) =>
    setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const setDirect = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));

  const travelers = Math.min(9, Math.max(1, parseInt(form.travelers, 10) || 1));
  const bump = (d) => setForm((f) => ({ ...f, travelers: String(Math.min(9, Math.max(1, travelers + d))) }));

  function swapPlaces() {
    setForm((f) => ({ ...f, origin: f.destination_city, destination_city: f.origin }));
    setSpun((s) => !s);
  }

  // No pre-filled defaults: the traveler types their own trip. The backend
  // requires From, To, and Depart, so the button stays off until set.
  // Basic date sanity: depart can't be in the past, return can't precede depart.
  const today = new Date().toISOString().slice(0, 10);
  const dateError =
    form.depart_date && form.depart_date < today
      ? 'Depart date can’t be in the past.'
      : form.return_date && form.depart_date && form.return_date < form.depart_date
        ? 'Return can’t be before your depart date.'
        : null;
  const canSearch =
    status !== 'searching' &&
    !dateError &&
    form.origin.trim() !== '' &&
    form.destination_city.trim() !== '' &&
    form.depart_date !== '';

  async function search() {
    if (!canSearch) return;
    setStatus('searching');
    setErrorMsg(null);
    setPlan(null);
    const payload = { ...form, travelers };
    // Optional override: blank (or non-numeric) means "no override" — the key
    // is omitted so the backend falls back to its own sources. Irrelevant for
    // fly mode, where fuel never enters the price.
    const price = parseFloat(form.fuel_price_per_gal);
    if (form.mode === 'fly' || form.fuel_price_per_gal === '' || Number.isNaN(price)) {
      delete payload.fuel_price_per_gal;
    } else {
      payload.fuel_price_per_gal = price;
    }
    if (form.mode === 'fly') delete payload.own_car;
    if (!payload.return_date) delete payload.return_date;
    try {
      const res = await fetch(`${API}/trips`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        setErrorMsg(await errorText(res));
        setStatus('failed');
        return;
      }
      const { job_id } = await res.json();
      // Poll until the engine finishes pricing every branch.
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await (await fetch(`${API}/trips/${job_id}`)).json();
        if (job.status === 'complete') {
          setPlan(job.plan);
          setStatus('done');
          loadRecents();
          scrollToResults();
          return;
        }
        if (job.status === 'failed') {
          setErrorMsg(job.error || 'The pricing job failed — check the worker logs.');
          setStatus('failed');
          loadRecents();
          return;
        }
      }
      setErrorMsg('Still pricing after a minute — is the worker running? (python -m app.worker)');
      setStatus('failed');
      loadRecents();
    } catch {
      setErrorMsg(`Can’t reach the API at ${API} — is it running?`);
      setStatus('failed');
    }
  }

  const driveFields = form.mode !== 'fly';
  const modeIdx = MODES.findIndex((m) => m.value === form.mode);
  const cheapest = plan?.options?.[0]?.total_usd;
  const destShort = plan?.destination_city?.split(',')[0];

  return (
    <>
      <div className="backdrop" aria-hidden="true">
        <div className="blob b1" /><div className="blob b2" /><div className="blob b3" /><div className="blob b4" />
      </div>
      <main className="shell">
        <Hero />

        <section className="card search-card" aria-label="Plan a trip">
          <div className="form">
            <div className="route-fields">
              <PlaceInput id="from" label="From" value={form.origin} onChange={setDirect('origin')}
                placeholder="Street, city or ZIP" />
              <button type="button" className={`swap${spun ? ' spun' : ''}`} onClick={swapPlaces}
                aria-label="Swap From and To" title="Swap">
                <SwapIcon />
              </button>
              <PlaceInput id="to" label="To" value={form.destination_city} onChange={setDirect('destination_city')}
                placeholder="Hotel, address or city" />
            </div>

            <div className="row-3">
              <div className="field">
                <label className="field-label" htmlFor="depart">Depart</label>
                <input id="depart" className="input" type="date" value={form.depart_date} min={today}
                  onChange={set('depart_date')} />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="return">Return</label>
                <input id="return" className="input" type="date" value={form.return_date}
                  min={form.depart_date || today} onChange={set('return_date')} />
              </div>
              <div className="field stepper-field">
                <span className="field-label" id="travelers-label">Travelers</span>
                <div className="stepper" role="group" aria-labelledby="travelers-label">
                  <button type="button" onClick={() => bump(-1)} disabled={travelers <= 1} aria-label="One fewer traveler">−</button>
                  <output aria-live="polite"><span className="n" key={travelers}>{travelers}</span> {travelers === 1 ? 'person' : 'people'}</output>
                  <button type="button" onClick={() => bump(1)} disabled={travelers >= 9} aria-label="One more traveler">+</button>
                </div>
              </div>
            </div>
            {travelers > 1 && (
              <div className="hint" style={{ marginTop: -8 }}>Flights are priced per seat; cars, rideshares and hotel rooms are shared.</div>
            )}
            {dateError && <div className="alert"><AlertIcon />{dateError}</div>}

            <div className="field">
              <span className="field-label">How do you want to go?</span>
              <div className="modes" role="group" aria-label="Travel mode">
                <span className="mode-pill" aria-hidden="true" style={{
                  transform: `translateX(${modeIdx * 100}%)`, background: MODES[modeIdx].tint,
                }} />
                {MODES.map(({ value, title, blurb, Icon }) => (
                  <button key={value} type="button" className="mode" aria-pressed={form.mode === value}
                    onClick={() => setForm({ ...form, mode: value })}>
                    <Icon />
                    <strong>{title}</strong>
                    <small>{blurb}</small>
                  </button>
                ))}
              </div>
            </div>

            <div className={`reveal${driveFields ? ' open' : ''}`} {...(!driveFields && { inert: '' })}>
              <div>
                <div className="drive-panel">
                  <label className="switch">
                    <span>I have my own car</span>
                    <input type="checkbox" checked={form.own_car} onChange={set('own_car')} />
                    <span className="track" aria-hidden="true" />
                  </label>
                  <div className="field">
                    <label className="field-label" htmlFor="fuel">Fuel price per gallon <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>(optional)</span></label>
                    <div className="hint">Leave blank to use nearby station prices, or an estimate.</div>
                    <div className="prefix">
                      <span>$</span>
                      <input id="fuel" className="input" type="number" min="0.01" max="30" step="0.01"
                        placeholder="4.29"
                        value={form.fuel_price_per_gal} onChange={set('fuel_price_per_gal')} />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <button type="button" className="cta" onClick={search} disabled={!canSearch}>
              {status === 'searching'
                ? <><span className="spin" aria-hidden="true" />Pricing every option…</>
                : <>Find the cheapest way <span aria-hidden="true">→</span></>}
            </button>
            {!canSearch && status !== 'searching' && !dateError && (
              <div className="cta-hint">Fill in From, To and your depart date to start.</div>
            )}
            {status === 'failed' && errorMsg && <div className="alert error"><AlertIcon />{errorMsg}</div>}
          </div>
        </section>

        {status === 'searching' && <Loading />}

        {plan && (
          <section ref={resultsRef} className="results" aria-label="Results">
            {plan.options.length > 0 && (
              <div className="results-head">
                <h2>{plan.options.length} ways to <em>{destShort}</em></h2>
                <span className="from">from {money(cheapest)}{plan.travelers > 1 ? ` for ${plan.travelers}` : ''}</span>
              </div>
            )}
            {plan.warnings?.length > 0 && (
              <div className="alert" style={{ display: 'grid', gap: 4 }}>
                {plan.warnings.map((w, i) => <div key={i} style={{ display: 'flex', gap: 8 }}><AlertIcon />{w}</div>)}
              </div>
            )}
            {plan.options.length === 0 && (
              <div className="card empty">
                <h3>No options found for this search.</h3>
                <p className="hint" style={{ margin: 0 }}>
                  The notes above say what went missing and why — try a nearby major city as the destination.
                </p>
              </div>
            )}
            {plan.options.map((o, idx) => (
              <ResultCard key={o.id} option={o} index={idx} best={cheapest} travelers={plan.travelers} />
            ))}
          </section>
        )}

        <Recents recents={recents} loadingJobId={loadingJobId} error={recentError} onOpen={openRecent} />

        <footer className="footer">
          <div className="legend">
            <span className="conf live">live</span>
            <span className="conf sandbox">sandbox</span>
            <span className="conf user">yours</span>
            <span className="conf estimated">estimated</span>
          </div>
          <div>Every price says where it came from. Estimates turn live as API keys are added.</div>
        </footer>
      </main>
    </>
  );
}
