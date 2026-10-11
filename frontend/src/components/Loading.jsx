import { useEffect, useState } from 'react';
import { PlaneIcon } from './icons.jsx';

// What the worker is actually doing, in plain words, rotated while we poll.
const STEPS = [
  'Finding your nearest airport…',
  'Asking the airlines…',
  'Mapping the roads…',
  'Checking the pump down the street…',
  'Pricing rentals and rideshares…',
  'Ranking every option…',
];

export default function Loading() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setStep((s) => (s + 1) % STEPS.length), 1700);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="journey" role="status" aria-live="polite">
      <div className="card">
        <div className="journey-track" aria-hidden="true">
          <span className="journey-dot a" />
          <span className="journey-plane"><PlaneIcon size={28} /></span>
          <span className="journey-dot b" />
        </div>
        <div className="journey-msg"><span key={step}>{STEPS[step]}</span></div>
      </div>
      <div className="skeleton" /><div className="skeleton" /><div className="skeleton" />
    </div>
  );
}
