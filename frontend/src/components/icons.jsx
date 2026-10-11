function Icon({ d, size = 22, strokeWidth = 1.8 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true"
      stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

export const PlaneIcon = (p) => <Icon {...p} d="M10.5 13.5L3 11l18-7-7 18-2.5-7.5L10.5 13.5z M10.5 13.5L21 4" />;
export const CarIcon = (p) => (
  <Icon {...p} d="M4 15v-3l1.8-4.2A2 2 0 0 1 7.7 6.5h8.6a2 2 0 0 1 1.9 1.3L20 12v3 M4 12h16 M7 15v2 M17 15v2 M7.5 17h9" />
);
export const SparkleIcon = (p) => (
  <Icon {...p} d="M12 4l1.7 4.8L18.5 10.5l-4.8 1.7L12 17l-1.7-4.8L5.5 10.5l4.8-1.7z M19 3l.8 2.2L22 6l-2.2.8L19 9l-.8-2.2L16 6l2.2-.8z" />
);
export const PinIcon = (p) => (
  <Icon size={16} {...p} d="M12 21s-6.5-5.4-6.5-10.5A6.5 6.5 0 0 1 12 4a6.5 6.5 0 0 1 6.5 6.5C18.5 15.6 12 21 12 21z M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" />
);
export const SwapIcon = (p) => <Icon size={18} {...p} d="M7 4v16 M7 20l-3-3 M7 20l3-3 M17 20V4 M17 4l-3 3 M17 4l3 3" />;
export const ChevronIcon = (p) => <Icon size={14} strokeWidth={2.4} {...p} d="M9 6l6 6-6 6" />;
export const ClockIcon = (p) => <Icon size={13} strokeWidth={2.2} {...p} d="M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z" />;
export const AlertIcon = (p) => <Icon size={16} {...p} d="M12 9v4 M12 17h.01 M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />;
