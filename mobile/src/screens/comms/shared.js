import React from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Path, Circle } from 'react-native-svg';

import { COLORS, ROLE_LABELS, styles } from '../../theme';
import { Pill } from '../../ui';

export const TRIGGER_LABELS = {
  BELOW: 'Stock below a threshold',
  AT_OR_BELOW: 'Stock at or below a threshold',
  OUT_OF_STOCK: 'Out of stock',
  PERCENT_OF_REORDER: 'Below a percentage of the reorder level',
  BELOW_FOR_DURATION: 'Below a threshold for a length of time',
};

export const FREQUENCY_LABELS = {
  ONCE: 'Once per new breach',
  REPEAT_HOURS: 'Repeat every few hours while low',
  DAILY: 'Once a day while low',
  DAILY_DIGEST: 'Daily digest email',
  WEEKLY_DIGEST: 'Weekly digest email',
};

export const RECIPIENT_TYPE_LABELS = {
  OWNER: 'Rule owner', USER: 'User', ROLE: 'Everyone in a role', GROUP: 'Recipient group', EMAIL: 'External email',
};

export const LEVEL_LABELS = { ALL: 'All alerts', LOW: 'Low only', CRITICAL: 'Critical & out only' };

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function has(user, permission) {
  return !!user?.permissions?.includes(permission);
}

/** UTC ISO -> "2026-10-10 14:05" in the device's local time. */
export function when(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function qty(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : String(+n.toFixed(2));
}

export function SeverityPill({ severity }) {
  if (!severity) return null;
  const map = { LOW: ['LOW', 'warning'], CRITICAL: ['CRITICAL', 'danger'], OUT: ['OUT OF STOCK', 'danger'] };
  const [label, tone] = map[severity] || [severity, 'info'];
  return <Pill label={label} tone={tone} />;
}

export function StatusPill({ status }) {
  const tone = {
    OPEN: 'danger', ESCALATED: 'danger', ACKNOWLEDGED: 'warning', SUPPRESSED: 'muted', RESOLVED: 'success',
    ACTIVE: 'success', DRAFT: 'info', DISABLED: 'muted',
    PENDING: 'info', PROCESSING: 'info', ACCEPTED: 'success', TEMP_FAILED: 'warning', FAILED: 'danger', CANCELLED: 'muted',
  }[status] || 'info';
  return <Pill label={String(status || '').replace('_', ' ')} tone={tone} />;
}

export function recipientLabel(entry, options) {
  const v = entry.recipient_value;
  switch (entry.recipient_type) {
    case 'OWNER': return 'Rule owner';
    case 'USER': {
      const u = options?.users?.find((x) => String(x.id) === String(v));
      return u ? `${u.name} (${u.employee_id})` : `User #${v}`;
    }
    case 'ROLE': return `Role: ${ROLE_LABELS[v] || v}`;
    case 'GROUP': {
      const g = options?.groups?.find((x) => String(x.id) === String(v));
      return g ? `Group: ${g.name}` : `Group #${v}`;
    }
    default: return v;
  }
}

export function Toggle({ label, value, onChange, hint, disabled }) {
  return (
    <Pressable
      onPress={() => !disabled && onChange(!value)}
      style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 8, opacity: disabled ? 0.5 : 1 }}
      accessibilityRole="switch"
      accessibilityState={{ checked: !!value, disabled: !!disabled }}
      accessibilityLabel={label}
    >
      <View
        style={{
          width: 40, height: 22, borderRadius: 11, padding: 2, marginTop: 1,
          backgroundColor: value ? COLORS.primary : COLORS.border,
          alignItems: value ? 'flex-end' : 'flex-start',
        }}
      >
        <View style={{ width: 18, height: 18, borderRadius: 9, backgroundColor: '#FFFFFF' }} />
      </View>
      <View style={styles.flex1}>
        <Text style={{ fontSize: 14, color: COLORS.text, fontWeight: '600' }}>{label}</Text>
        {!!hint && <Text style={styles.mono}>{hint}</Text>}
      </View>
    </Pressable>
  );
}

export function BellIcon({ size = 20, color = '#FFFFFF' }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <Path
        d="M18 16v-5a6 6 0 1 0-12 0v5l-2 2h16l-2-2Z"
        stroke={color}
        strokeWidth={1.8}
        strokeLinejoin="round"
      />
      <Path d="M10 20a2 2 0 0 0 4 0" stroke={color} strokeWidth={1.8} strokeLinecap="round" />
    </Svg>
  );
}

export function Dot({ color = COLORS.danger, size = 8 }) {
  return (
    <Svg width={size} height={size}>
      <Circle cx={size / 2} cy={size / 2} r={size / 2} fill={color} />
    </Svg>
  );
}
