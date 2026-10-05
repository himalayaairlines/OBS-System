import { StyleSheet } from 'react-native';

export const COLORS = {
  primary: '#0B4F8A',
  primaryDark: '#073761',
  accent: '#E4572E',
  success: '#12805C',
  warning: '#B26A00',
  danger: '#B3261E',
  info: '#2F6FED',
  background: '#F4F6F9',
  surface: '#FFFFFF',
  surfaceAlt: '#EEF2F7',
  border: '#D8DFE8',
  text: '#16202B',
  muted: '#64748B',
  faint: '#94A3B8',
};

export const CHART_COLORS = [
  '#0B4F8A', '#E4572E', '#12805C', '#B26A00', '#6D4AA8',
  '#2F6FED', '#C2255C', '#0E7490', '#7C7F00', '#9A3412',
];

export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export const ROLE_LABELS = {
  crew: 'Crew',
  crew_supervisor: 'Crew Supervisor',
  inventory: 'Inventory',
  inventory_supervisor: 'Inventory Supervisor',
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
  scroll: { padding: 16, paddingBottom: 48 },

  card: {
    backgroundColor: COLORS.surface,
    borderRadius: 14,
    padding: 16,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  cardTight: { padding: 12 },

  pageTitle: { fontSize: 21, fontWeight: '700', color: COLORS.text },
  pageDescription: { fontSize: 13, color: COLORS.muted, marginTop: 3, lineHeight: 18 },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: COLORS.text },
  sectionDescription: { fontSize: 12, color: COLORS.muted, marginTop: 2, lineHeight: 17 },

  label: { fontSize: 12, fontWeight: '600', color: COLORS.muted, marginBottom: 6 },
  value: { fontSize: 14, color: COLORS.text },
  mono: { fontSize: 12, color: COLORS.muted },

  input: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: COLORS.text,
    backgroundColor: COLORS.surface,
  },
  inputMultiline: { minHeight: 74, textAlignVertical: 'top' },

  row: { flexDirection: 'row', alignItems: 'center' },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  gap8: { gap: 8 },
  gap12: { gap: 12 },
  flex1: { flex: 1 },

  divider: { height: 1, backgroundColor: COLORS.border, marginVertical: 12 },

  listRow: {
    paddingVertical: 11,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.surfaceAlt,
  },

  banner: {
    borderRadius: 10,
    padding: 11,
    marginBottom: 12,
    borderWidth: 1,
  },
  bannerText: { fontSize: 12.5, lineHeight: 18 },

  empty: { color: COLORS.faint, fontSize: 13, paddingVertical: 18, textAlign: 'center' },

  // Crew sales tiles. minWidth keeps two per row on a phone and more on a tablet.
  tileGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: {
    flexGrow: 1,
    flexBasis: 150,
    maxWidth: 260,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: 12,
    padding: 11,
    backgroundColor: COLORS.surface,
  },
  tileActive: { borderColor: COLORS.primary, backgroundColor: '#F2F7FD' },
});
