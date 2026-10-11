import { useEffect, useRef, useState } from 'react';

// Build-time override for a deployed API: VITE_API_URL=https://api.example npm run build
export const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
export const money = (n) => usd.format(n);
export const moneyRound = (n) => usd0.format(n);

export function usePrefersReducedMotion() {
  const query = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return undefined;
    const on = () => setReduced(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

// Eases a number up from 0 when it first appears (or changes). Instant
// under reduced motion — the value is what matters, the roll is garnish.
export function useCountUp(target, { duration = 900, delay = 0 } = {}) {
  const reduced = usePrefersReducedMotion();
  const [value, setValue] = useState(reduced ? target : 0);
  const raf = useRef(0);
  useEffect(() => {
    if (reduced) { setValue(target); return undefined; }
    let start;
    const tick = (t) => {
      if (start === undefined) start = t + delay;
      const p = Math.min(Math.max((t - start) / duration, 0), 1);
      setValue(target * (1 - Math.pow(1 - p, 3))); // ease-out cubic
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration, delay, reduced]);
  return value;
}

// Each kind of cost gets one pastel, shared by the cost bar and the
// breakdown list so the colors double as the legend.
const KINDS = [
  { test: (l) => l.startsWith('Flight'), name: 'Flight', color: '#b8a6ff' },
  { test: (l) => l.startsWith('Rideshare'), name: 'Rideshare', color: '#8ec5ff' },
  { test: (l) => l.startsWith('Park at') || l.startsWith('Drive home'), name: 'Parking + drive', color: '#ffb49a' },
  { test: (l) => l.startsWith('Rental'), name: 'Rental', color: '#7fd6ae' },
  { test: (l) => l.startsWith('Fuel'), name: 'Fuel', color: '#ffd76a' },
  { test: (l) => l.startsWith('Wear'), name: 'Wear & tear', color: '#f7a1c4' },
  { test: (l) => l.startsWith('Overnight'), name: 'Hotels', color: '#cfa8ff' },
];
export function legKind(label) {
  return KINDS.find((k) => k.test(label)) ?? { name: 'Other', color: '#cfc8da' };
}
