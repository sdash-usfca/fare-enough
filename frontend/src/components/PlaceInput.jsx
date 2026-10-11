import { useEffect, useRef, useState } from 'react';
import { API } from '../lib/ui.js';
import { PinIcon } from './icons.jsx';

// Address field with type-ahead: debounced calls to GET /geocode/suggest,
// dropdown of concrete addresses ("350 5th St, …") so trips price
// house-to-hotel instead of city-centroid to city-centroid.
// Keyboard: ArrowUp/ArrowDown to move, Enter to pick, Escape to close.
export default function PlaceInput({ id, label, value, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const reqId = useRef(0);
  // Set when the user picks a suggestion: the resulting onChange would
  // otherwise re-trigger a fetch for the just-picked label and pop the
  // dropdown back open (the "have to click twice" bug).
  const skipFetch = useRef(false);
  // Suggestions land ~300ms after typing stops; if the user has already
  // moved to another field, they must not pop open over it.
  const focused = useRef(false);

  function choose(s) {
    skipFetch.current = true;
    onChange(s.label);
    setSuggestions([]);
    setActiveIdx(-1);
    setOpen(false);
  }

  useEffect(() => {
    if (skipFetch.current) {
      skipFetch.current = false;
      return undefined;
    }
    if (value.trim().length < 3) {
      setSuggestions([]);
      setOpen(false);
      return undefined;
    }
    const reqNo = ++reqId.current;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch(
          `${API}/geocode/suggest?q=${encodeURIComponent(value.trim())}&limit=6`
        );
        if (reqId.current !== reqNo) return; // stale keystroke — drop it
        const list = res.ok ? await res.json() : [];
        setSuggestions(list);
        setActiveIdx(-1);
        setOpen(list.length > 0 && focused.current);
      } catch {
        if (reqId.current === reqNo) {
          setSuggestions([]);
          setOpen(false);
        }
      } finally {
        if (reqId.current === reqNo) setLoading(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [value]);

  function onKeyDown(e) {
    if (e.key === 'ArrowDown' && suggestions.length > 0) {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % suggestions.length);
      setOpen(true);
    } else if (e.key === 'ArrowUp' && suggestions.length > 0) {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + suggestions.length) % suggestions.length);
      setOpen(true);
    } else if (e.key === 'Enter' && open && activeIdx >= 0 && suggestions[activeIdx]) {
      e.preventDefault();
      choose(suggestions[activeIdx]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => { focused.current = true; if (suggestions.length > 0) setOpen(true); }}
        onBlur={() => { focused.current = false; setTimeout(() => setOpen(false), 120); }}
        onKeyDown={onKeyDown}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-activedescendant={activeIdx >= 0 ? `${id}-opt-${activeIdx}` : undefined}
      />
      {loading && value.trim().length >= 3 && (
        <span className="finding">finding places <span className="dots"><span /><span /><span /></span></span>
      )}
      {open && suggestions.length > 0 && (
        <ul className="dropdown" role="listbox" id={`${id}-list`}>
          {suggestions.map((s, i) => (
            <li
              key={`${s.lat},${s.lon},${i}`}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === activeIdx}
              onMouseDown={(e) => { e.preventDefault(); choose(s); }}
              onMouseEnter={() => setActiveIdx(i)}
            >
              <span className="pin-icon"><PinIcon /></span>
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
