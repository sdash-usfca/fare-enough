import { legKind, money, useCountUp } from '../lib/ui.js';
import { AlertIcon, CarIcon, ChevronIcon, ClockIcon, PlaneIcon } from './icons.jsx';

const CONFETTI_COLORS = ['#ffb49a', '#f7a1c4', '#b8a6ff', '#8ec5ff', '#7fd6ae', '#ffd76a'];

// Deterministic burst: 18 pieces fanned across the top of the best card.
function Confetti() {
  return (
    <div className="confetti" aria-hidden="true">
      {Array.from({ length: 18 }, (_, i) => {
        const angle = (-160 + (i * 140) / 17) * (Math.PI / 180);
        const dist = 70 + (i % 4) * 22;
        return (
          <i key={i} style={{
            background: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
            '--x': `${Math.cos(angle) * dist * 1.6}px`,
            '--y': `${Math.sin(angle) * dist}px`,
            '--r': `${(i % 2 ? 1 : -1) * (200 + i * 25)}deg`,
          }} />
        );
      })}
    </div>
  );
}

function CostBar({ legs, total }) {
  const priced = legs.filter((l) => l.amount_usd > 0);
  if (!priced.length || total <= 0) return null;
  return (
    <div className="costbar" role="img"
      aria-label={priced.map((l) => `${legKind(l.label).name} ${money(l.amount_usd)}`).join(', ')}>
      {priced.map((l, i) => (
        <span key={i} title={`${l.label}: ${money(l.amount_usd)}`}
          style={{ width: `${(l.amount_usd / total) * 100}%`, background: legKind(l.label).color }} />
      ))}
    </div>
  );
}

export default function ResultCard({ option: o, index, best, travelers }) {
  const delay = index * 80;
  const total = useCountUp(o.total_usd, { delay: delay + 150 });
  const isBest = index === 0;
  const extra = o.total_usd - best;
  const fly = o.mode === 'fly';

  return (
    <article className={`card option${isBest ? ' best' : ''}`} style={{ '--delay': `${delay}ms` }}>
      {isBest && <><span className="ribbon">Best deal</span><Confetti /></>}
      <div className="option-head">
        <div className={`mode-badge ${fly ? 'fly' : 'drive'}`}>
          {fly ? <PlaneIcon /> : <CarIcon />}
        </div>
        <div>
          <h3>{o.summary}</h3>
          <div className="chips">
            {o.travel_note && <span className="chip"><ClockIcon />{o.travel_note}</span>}
            {!isBest && extra > 0.005 && <span className="chip delta">+{money(extra)} vs best</span>}
          </div>
        </div>
        <div className="price">
          <div className="total">{money(total)}</div>
          {travelers > 1 && o.per_person_usd != null && (
            <small>{money(o.per_person_usd)} per person</small>
          )}
        </div>
      </div>

      <CostBar legs={o.legs} total={o.total_usd} />

      <details className="breakdown" open={isBest}>
        <summary><ChevronIcon />Cost breakdown</summary>
        <ul className="legs">
          {o.legs.map((l, i) => (
            <li key={i} style={{ animationDelay: `${i * 40}ms` }}>
              <span className="swatch" style={{ background: l.amount_usd > 0 ? legKind(l.label).color : 'transparent' }} />
              <span>
                {l.label}
                <span className={`conf ${l.confidence}`}>{l.confidence}</span>
                <span className="meta" title={l.detail}>{l.source}{l.detail ? ` · ${l.detail}` : ''}</span>
              </span>
              <span className="amt">{l.amount_usd > 0 ? money(l.amount_usd) : '—'}</span>
            </li>
          ))}
        </ul>
      </details>

      {(o.warnings || []).map((w, i) => (
        <div key={i} className="option-warn"><AlertIcon />{w}</div>
      ))}
    </article>
  );
}
