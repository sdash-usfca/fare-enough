import { usePrefersReducedMotion } from '../lib/ui.js';

// Line-art postcard: two pins, a dashed flight arc with a plane gliding
// along it, a road with a car rolling below. SVG <animateMotion> keeps both
// on their curves at any width; with reduced motion they sit mid-route.
const FLIGHT = 'M110 132 C 250 18, 520 10, 690 112';
const ROAD = 'M20 176 C 170 160, 260 190, 400 174 S 640 162, 780 174';

function Plane() {
  return (
    <g fill="#a63f6b">
      <path d="M-15 0 L9 -2.2 Q16 0 9 2.2 Z" />
      <path d="M-2 -1.5 L-9 -13 L-4.5 -13 L5 -1.5 Z" />
      <path d="M-2 1.5 L-9 13 L-4.5 13 L5 1.5 Z" />
      <path d="M-12 -1 L-16.5 -7 L-13.5 -7 L-9.5 -1 Z" />
    </g>
  );
}

function Car() {
  return (
    <g transform="translate(0 -9)">
      <rect x="-17" y="-6" width="34" height="11" rx="5" fill="#22795a" />
      <path d="M-9 -6 L-5 -13 H6 L11 -6 Z" fill="#22795a" />
      <path d="M-6.5 -7 L-3.8 -11.6 H0 V-7 Z M1.5 -7 V-11.6 H5.3 L8.5 -7 Z" fill="#cdefe0" />
      <circle cx="-9" cy="5" r="4" fill="#2f2a3a" /><circle cx="-9" cy="5" r="1.6" fill="#fbf7f1" />
      <circle cx="9" cy="5" r="4" fill="#2f2a3a" /><circle cx="9" cy="5" r="1.6" fill="#fbf7f1" />
    </g>
  );
}

function Mover({ path, dur, children, rotate = 'auto' }) {
  return (
    <g>
      {children}
      <animateMotion dur={dur} repeatCount="indefinite" rotate={rotate}
        keyPoints="0;1" keyTimes="0;1" calcMode="spline" keySplines="0.45 0 0.55 1">
        <mpath href={path} />
      </animateMotion>
    </g>
  );
}

export default function Hero() {
  const reduced = usePrefersReducedMotion();
  return (
    <header className="hero">
      <svg className="hero-scene" viewBox="0 0 800 200" role="img"
        aria-label="A plane flying and a car driving between two places">
        <defs>
          <path id="flight" d={FLIGHT} />
          <path id="road" d={ROAD} />
          <radialGradient id="sunGlow">
            <stop offset="0%" stopColor="#ffe28a" />
            <stop offset="100%" stopColor="#ffe28a" stopOpacity="0" />
          </radialGradient>
        </defs>

        <g className="sun">
          <circle cx="700" cy="52" r="46" fill="url(#sunGlow)" />
          <circle cx="700" cy="52" r="20" fill="#ffd76a" />
        </g>
        <g className="cloud" fill="#fff" opacity="0.95">
          <ellipse cx="200" cy="52" rx="34" ry="12" /><ellipse cx="222" cy="44" rx="22" ry="13" />
        </g>
        <g className="cloud c2" fill="#fff" opacity="0.85">
          <ellipse cx="520" cy="76" rx="28" ry="10" /><ellipse cx="538" cy="69" rx="17" ry="10" />
        </g>
        <path className="twinkle" d="M380 30 l3 7 7 3 -7 3 -3 7 -3 -7 -7 -3 7 -3z" fill="#f7a1c4" />
        <path className="twinkle t2" d="M612 128 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z" fill="#b8a6ff" />
        <path className="twinkle t3" d="M70 70 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z" fill="#ffb49a" />

        <path className="flight-path" d={FLIGHT} fill="none" stroke="#c9bfe8" strokeWidth="2" strokeLinecap="round" />
        <path d={ROAD} fill="none" stroke="#e7dccd" strokeWidth="16" strokeLinecap="round" />
        <path d={ROAD} fill="none" stroke="#fbf7f1" strokeWidth="1.6" strokeDasharray="10 12" />

        <g className="pin">
          <path d="M110 142 c-9 -10 -14 -17 -14 -24 a14 14 0 0 1 28 0 c0 7 -5 14 -14 24z" fill="#ffb49a" />
          <circle cx="110" cy="118" r="5" fill="#fff" />
        </g>
        <g className="pin p2">
          <path d="M690 122 c-9 -10 -14 -17 -14 -24 a14 14 0 0 1 28 0 c0 7 -5 14 -14 24z" fill="#b8a6ff" />
          <circle cx="690" cy="98" r="5" fill="#fff" />
        </g>

        {reduced ? (
          <>
            <g transform="translate(400 40) rotate(4)"><Plane /></g>
            <g transform="translate(400 174)"><Car /></g>
          </>
        ) : (
          <>
            <Mover path="#flight" dur="7s"><Plane /></Mover>
            <Mover path="#road" dur="9s"><Car /></Mover>
          </>
        )}
      </svg>
      <h1>Fare <span className="amp">Enough</span></h1>
      <p>Every way there, priced honestly — cheapest first.</p>
    </header>
  );
}
