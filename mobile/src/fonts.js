/**
 * Work Sans for the whole app.
 *
 * Web: the Google Fonts stylesheet is linked into <head> (preconnect + css2,
 * exactly as Google publishes it) and a global rule makes every element use it.
 *
 * iOS / Android: there is no <head>, so the font files are loaded with
 * expo-font. Native text cannot pick weights out of one family, so each weight
 * is registered as its own family (WorkSans-400, WorkSans-600, ...). If the
 * device is offline or the download fails, text falls back to the system font
 * and nothing breaks. To ship the font inside the app instead, see README.
 */
import { Platform } from 'react-native';
import * as Font from 'expo-font';

export const WEB_STACK = "'Work Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
const WEIGHTS = [400, 500, 600, 700, 800];
const NATIVE_SOURCE = (w) => `https://cdn.jsdelivr.net/fontsource/fonts/work-sans@latest/latin-${w}-normal.ttf`;

let nativeLoaded = false;

function injectWebFonts() {
  if (typeof document === 'undefined' || document.getElementById('obs-work-sans')) return;
  const head = document.head;
  const add = (attrs) => {
    const link = document.createElement('link');
    Object.entries(attrs).forEach(([k, v]) => { if (v === true) link.setAttribute(k, ''); else link.setAttribute(k, v); });
    head.appendChild(link);
  };
  add({ rel: 'preconnect', href: 'https://fonts.googleapis.com' });
  add({ rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true });
  add({
    id: 'obs-work-sans',
    rel: 'stylesheet',
    href: 'https://fonts.googleapis.com/css2?family=Work+Sans:ital,wght@0,100..900;1,100..900&display=swap',
  });
  const style = document.createElement('style');
  style.textContent = [
    `html, body, #root, * { font-family: ${WEB_STACK} !important; }`,
    // Visible keyboard focus on every control, without rings on mouse clicks.
    `:focus-visible { outline: 2px solid #2F6FED !important; outline-offset: 2px; border-radius: 6px; }`,
    `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }`,
  ].join('\n');
  head.appendChild(style);
}

/** Load the font. Resolves within `timeoutMs` whatever happens. */
export async function loadFonts(timeoutMs = 4000) {
  if (Platform.OS === 'web') {
    injectWebFonts();
    return true;
  }
  const map = Object.fromEntries(WEIGHTS.map((w) => [`WorkSans-${w}`, NATIVE_SOURCE(w)]));
  const timeout = new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs));
  const load = Font.loadAsync(map).then(() => { nativeLoaded = true; return true; }).catch(() => false);
  return Promise.race([load, timeout]);
}

/** Text style for a Work Sans weight: `{ ...ws(600) }`. */
export function ws(weight = 400) {
  const w = WEIGHTS.includes(weight) ? weight : 400;
  if (Platform.OS === 'web') return { fontFamily: WEB_STACK, fontWeight: String(w) };
  if (nativeLoaded && Font.isLoaded(`WorkSans-${w}`)) return { fontFamily: `WorkSans-${w}` };
  return { fontWeight: String(w) };
}
