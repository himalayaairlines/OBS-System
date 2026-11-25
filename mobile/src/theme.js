import { Platform, StyleSheet } from 'react-native';

/*
 * Design tokens. The brand blues are unchanged; neutrals and status colours
 * were tuned so body and secondary text meet WCAG AA (4.5:1) on every surface.
 */
export const COLORS = {
  primary: '#0B4F8A',
  primaryDark: '#073761',
  primarySoft: '#EAF1F9',   // selected rows, active filters
  accent: '#E4572E',
  success: '#0F7552',
  successSoft: '#E4F4EC',
  warning: '#9A5B00',
  warningSoft: '#FCF1DE',
  danger: '#B3261E',
  dangerSoft: '#FCE9E7',
  info: '#2F6FED',
  infoText: '#1A56C9',
  infoSoft: '#E8F0FE',
  background: '#F4F6F9',
  surface: '#FFFFFF',
  surfaceAlt: '#F0F3F7',
  border: '#DCE2EA',
  borderStrong: '#C6CFDA',
  text: '#16202B',
  muted: '#5B6778',        // 5.3:1 on the page background
  faint: '#6B778A',        // 4.5:1 on white: placeholders, meta text
  overlay: 'rgba(10, 22, 40, 0.55)',
  focus: '#2F6FED',
};

/** Spacing scale (px). Use these instead of one-off values. */
export const SPACE = { xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 };

/** Corner radii. Inputs, buttons and cards share one family. */
export const RADIUS = { sm: 6, md: 8, lg: 10, xl: 12, pill: 999 };

/** Control heights, so a button and an input on one row line up. */
export const CONTROL = { height: 44, heightSmall: 34 };

/**
 * Type scale. One size per role: page 22, section 16, body 14, small 13,
 * caption 12, badge 11. Weights stay at 400 / 500 / 600; 700 is reserved for
 * headline figures.
 */
export const TYPE = {
  pageTitle: { fontSize: 22, lineHeight: 28, fontWeight: '600' },
  sectionTitle: { fontSize: 16, lineHeight: 22, fontWeight: '600' },
  body: { fontSize: 14, lineHeight: 20, fontWeight: '400' },
  bodyStrong: { fontSize: 14, lineHeight: 20, fontWeight: '600' },
  small: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '400' },
  label: { fontSize: 13, lineHeight: 18, fontWeight: '500' },
  badge: { fontSize: 11, lineHeight: 14, fontWeight: '600' },
  metric: { fontSize: 22, lineHeight: 28, fontWeight: '600' },
};

/** Aligned digits for quantities, money and counts. */
export const NUMERIC = { fontVariant: ['tabular-nums'] };

/** Widest the content column grows on large screens. */
export const CONTENT_MAX_WIDTH = 1240;

export const CHART_COLORS = [
  '#0B4F8A', '#E4572E', '#12805C', '#B26A00', '#6D4AA8',
  '#2F6FED', '#C2255C', '#0E7490', '#7C7F00', '#9A3412',
];

export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

// Retired roles keep a label so old audit rows still read properly.
export const LEGACY_ROLE_LABELS = { inventory_supervisor: 'Inventory User (old role)' };
export const ROLE_LABELS = {
  crew: 'Crew',
  crew_supervisor: 'Crew Supervisor',
  inventory: 'Inventory User',
  management: 'Management',
  finance: 'Finance',
  admin: 'Administrator',
};

export function npr(value) {
  const amount = Number(value || 0);
  return `NPR ${amount.toLocaleString('en-NP', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function money(value, code = 'NPR') {
  const amount = Number(value || 0);
  return `${code} ${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function compact(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 1_000_000) return `${(amount / 1_000_000).toFixed(1)}M`;
  if (Math.abs(amount) >= 1_000) return `${(amount / 1_000).toFixed(1)}K`;
  return amount.toFixed(0);
}

export function todayString() {
  return new Date().toISOString().slice(0, 10);
}

export function initials(name) {
  return String(name || '?')
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('');
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: COLORS.background },
  // Content column: full width on phones and tablets, capped and centred on
  // wide screens so rows stay scannable.
  scroll: {
    padding: SPACE.lg, paddingBottom: 48, width: '100%', maxWidth: CONTENT_MAX_WIDTH, alignSelf: 'center',
  },

  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: SPACE.lg,
    marginBottom: SPACE.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  cardTight: { padding: SPACE.md },

  pageTitle: { ...TYPE.pageTitle, color: COLORS.text },
  pageDescription: { ...TYPE.small, color: COLORS.muted, marginTop: SPACE.xs },
  sectionTitle: { ...TYPE.sectionTitle, color: COLORS.text },
  sectionDescription: { ...TYPE.small, color: COLORS.muted, marginTop: SPACE.xxs },

  label: { ...TYPE.label, color: COLORS.text, marginBottom: 6 },
  value: { ...TYPE.body, color: COLORS.text },
  // Historically named "mono"; it is the secondary/meta text style.
  mono: { ...TYPE.caption, color: COLORS.muted },
  numeric: NUMERIC,

  input: {
    borderWidth: 1,
    borderColor: COLORS.borderStrong,
    borderRadius: RADIUS.md,
    paddingHorizontal: SPACE.md,
    minHeight: CONTROL.height,
    paddingVertical: 10,
    fontSize: 14,
    color: COLORS.text,
    backgroundColor: COLORS.surface,
    ...(Platform.OS === 'web' ? { outlineStyle: 'none' } : null),
  },
  inputFocused: { borderColor: COLORS.focus, borderWidth: 1.5 },
  inputMultiline: { minHeight: 84, textAlignVertical: 'top' },

  row: { flexDirection: 'row', alignItems: 'center' },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  gap8: { gap: 8 },
  gap12: { gap: 12 },
  flex1: { flex: 1 },

  divider: { height: 1, backgroundColor: COLORS.border, marginVertical: SPACE.md },

  listRow: {
    paddingVertical: SPACE.md,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.surfaceAlt,
  },

  banner: {
    borderRadius: RADIUS.md,
    paddingVertical: 10,
    paddingHorizontal: SPACE.md,
    marginBottom: SPACE.md,
    borderWidth: 1,
    borderLeftWidth: 3,
  },
  bannerText: { ...TYPE.small },

  empty: { ...TYPE.small, color: COLORS.muted, paddingVertical: SPACE.xl, textAlign: 'center' },

  // Crew sales tiles. minWidth keeps two per row on a phone and more on a tablet.
  tileGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: {
    flexGrow: 1,
    flexBasis: 150,
    maxWidth: 260,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.lg,
    padding: SPACE.md,
    backgroundColor: COLORS.surface,
  },
  tileActive: { borderColor: COLORS.primary, backgroundColor: COLORS.primarySoft },
});
