/**
 * Laptop-and-up layout helpers. Phones and tablets keep the existing layout;
 * from DESKTOP_MIN_WIDTH the app switches to the sidebar shell.
 */
import React from 'react';
import { Platform, useWindowDimensions, View } from 'react-native';

export const DESKTOP_MIN_WIDTH = 1024;

// OBS iPads (and other tablets) keep the tab layout even when wide: the shared
// cabin device accounts, the native apps, and any touch tablet browser
// (iPadOS Safari reports itself as a Mac, so check for touch points too).
export const DEVICE_ACCOUNT_ROLES = ['crew', 'crew_supervisor'];

function isTabletBrowser() {
  if (Platform.OS !== 'web') return true;
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  return /iPad|Android|Tablet/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}
const TABLET = isTabletBrowser();

/** Sidebar layout: laptop or larger, a desktop browser, not an OBS device account. */
export function useSidebarLayout(role) {
  const wide = useIsDesktop();
  return wide && !TABLET && !DEVICE_ACCOUNT_ROLES.includes(role);
}

export function useIsDesktop() {
  const { width } = useWindowDimensions();
  return width >= DESKTOP_MIN_WIDTH;
}

/**
 * Side-by-side columns on wide screens, stacked below `breakpoint`.
 * `flex` gives each child's share, e.g. [2, 1].
 */
export function Columns({ children, flex = [], gap = 16, breakpoint = 900 }) {
  const { width } = useWindowDimensions();
  const items = React.Children.toArray(children).filter(Boolean);
  if (width < breakpoint) return <>{items}</>;
  return (
    <View style={{ flexDirection: 'row', gap, alignItems: 'flex-start' }}>
      {items.map((child, i) => (
        <View key={i} style={{ flex: flex[i] || 1, minWidth: 0 }}>{child}</View>
      ))}
    </View>
  );
}

/** "3 min ago" style text for recent times; dates for anything older than a week. */
export function ago(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} d ago`;
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Plain-language names for audit events. Unknown events fall back to the code. */
export const EVENT_LABELS = {
  LOGIN_SUCCESS: 'Signed in',
  LOGIN: 'Signed in',
  LOGIN_FAILED: 'Sign-in failed',
  LOGIN_BLOCKED: 'Sign-in blocked',
  LOGIN_PASSWORD_OK: 'Password accepted, code sent',
  ACCOUNT_LOCKED: 'Account locked',
  OTP_SENT: 'Verification code sent',
  OTP_FAILED: 'Wrong verification code',
  PASSWORD_RESET_REQUESTED: 'Reset link requested',
  PASSWORD_RESET: 'Password reset',
  PASSWORD_CHANGED: 'Password changed',
  PASSWORD_CHANGE_FAILED: 'Password change failed',
  INVITE_ACCEPTED: 'Invitation accepted',
  LOGOUT: 'Signed out',
  SESSION_REVOKED: 'Device signed out',
  SESSIONS_REVOKED: 'Sessions signed out',
  PERMISSION_DENIED: 'Access denied',
  INVITE_USER: 'User invited',
  RESEND_INVITE: 'Invitation re-sent',
  SEND_RESET_LINK: 'Reset link sent',
  CREATE_USER: 'Account created',
  UPDATE_USER: 'Account updated',
  DEACTIVATE_USER: 'Account deactivated',
  LOCK_USER: 'Account locked by admin',
  UNLOCK_USER: 'Account unlocked',
  TWO_FACTOR_ENABLED: 'Two-factor enabled',
  TWO_FACTOR_DISABLED: 'Two-factor disabled',
  UPDATE_SECURITY_SETTINGS: 'Sign-in settings changed',
  LOGIN_THROTTLED: 'Sign-in throttled (IP)',
  RECOVERY_PASSWORD_RESET: 'Admin recovery used',
  RECOVERY_FAILED: 'Admin recovery failed',
  RECOVERY_REFUSED: 'Admin recovery refused',
  UPDATE_SMTP_SETTINGS: 'SMTP settings changed',
  TEST_SMTP_CONNECTION: 'SMTP connection tested',
  SEND_TEST_EMAIL: 'Test e-mail sent',
};

export const eventLabel = (code) => EVENT_LABELS[code] || String(code || '').replace(/_/g, ' ').toLowerCase()
  .replace(/^./, (c) => c.toUpperCase());
