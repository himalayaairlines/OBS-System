/**
 * Dialogs that work on web as well as on a device.
 *
 * React Native's `Alert.alert` ignores the button array on react-native-web:
 * the dialog either does not appear or appears without its buttons, so the
 * `onPress` callback never runs and the action silently does nothing. Since the
 * app is used in a browser as well as on iPads, confirmations go through this
 * service instead, which renders a normal React modal on every platform.
 *
 *   if (await confirm({ title: 'Are you sure?', message: '…' })) { … }
 *   await notify({ title: 'Done', message: '…' });
 */

let handler = null;

export function registerDialogHandler(fn) {
  handler = fn;
}

function fallback(options) {
  // Only reached if the host component has not mounted yet.
  if (typeof window !== 'undefined' && window.confirm) {
    const text = [options.title, options.message].filter(Boolean).join('\n\n');
    return Promise.resolve(options.single ? (window.alert(text), true) : window.confirm(text));
  }
  return Promise.resolve(true);
}

export function confirm({
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  tone = 'primary',
}) {
  if (!handler) return fallback({ title, message });
  return new Promise((resolve) => {
    handler({ title, message, confirmText, cancelText, tone, single: false, resolve });
  });
}

export function notify({ title, message, confirmText = 'OK' }) {
  if (!handler) return fallback({ title, message, single: true });
  return new Promise((resolve) => {
    handler({ title, message, confirmText, single: true, resolve });
  });
}
