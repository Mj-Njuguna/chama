/** Colour tokens. Sizes elsewhere are dp. */
export const c = {
  ink: '#0F172A',
  muted: '#475569',
  teal: '#0F766E',
  tealSoft: '#E6F2F1',
  surface: '#F6F8F7',
  line: '#F1F5F9',
  border: '#E2E8F0',
  borderSoft: '#EEF2F1',
  disabled: '#CBD5E1',
  white: '#FFFFFF',
  rowOpen: '#F8FAFC',
  red: '#B91C1C',
  redBg: '#FEE2E2',
  alertDot: '#EF4444',
  amber: '#B45309',
  amberBg: '#FEF3C7',
  green: '#15803D',
  greenBg: '#DCFCE7',
  warnBg: '#FFFBEB',
  warnBorder: '#FDE68A',
  warnText: '#92400E',
  selectedBg: '#F0FAF8',
  toastAction: '#5EEAD4',
  backdrop: 'rgba(15,23,42,0.45)',
  tabIdle: '#94A3B8',
} as const;

/** Bottom tab bar height. Scrolling screens leave this much room under their content. */
export const BAR_HEIGHT = 64;
export const CONTENT_BOTTOM = BAR_HEIGHT + 16;
