import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

export const API_BASE =
  process.env.EXPO_PUBLIC_API_BASE || 'http://192.168.88.99:8000';

// The oldest backend this app can talk to. Bump both this and API_VERSION in
// backend/main.py whenever endpoints are added.
export const REQUIRED_API_VERSION = '7.2';

export const KEYS = {
  session: 'obs.session',
  queue: 'obs.queue',
  reconQueue: 'obs.reconQueue',
  dutyQueue: 'obs.dutyQueue',
  rejected: 'obs.rejected',
  lastSync: 'obs.lastSync',
  cache: 'obs.cache.',
};

/*
 * Every read-modify-write of a queue goes through this.
 *
 * Without it, a sale recorded while a sync was in flight was read by neither
 * side and was erased when the flush wrote the queue back. Cash sales
 * disappeared. Network calls are deliberately NOT held inside the lock: the
 * flush takes it to read, releases it for the upload, then takes it again to
 * remove only the records the server actually confirmed.
 */
let storageLock = Promise.resolve();

export function withQueueLock(fn) {
  const run = storageLock.then(fn, fn);
  storageLock = run.then(() => undefined, () => undefined);
  return run;
}

let sessionToken = null;
export function setToken(token) {
  sessionToken = token;
}
export function getToken() {
  return sessionToken;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
    // 4xx means the request itself is wrong, so retrying will not help.
    this.permanent = status >= 400 && status < 500;
  }
}

const inFlight = new Map();

export async function api(endpoint, { method = 'GET', body, auth = true, timeout = 15000 } = {}) {
  const verb = method.toUpperCase();
  const key = `${verb} ${endpoint}`;

  // Collapse duplicate concurrent GETs without caching stale data.
  if (verb === 'GET' && inFlight.has(key)) return inFlight.get(key);

  const run = (async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const headers = {};
      if (body) headers['Content-Type'] = 'application/json';
      if (auth && sessionToken) headers.Authorization = `Bearer ${sessionToken}`;

      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: verb,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        // A 404 on a path the app knows about almost always means the server is
        // an older build rather than a genuine missing record.
        const detail = payload.detail || (response.status === 404
          ? `The server does not have ${endpoint}. It is probably running an older build — restart uvicorn from the current backend folder.`
          : `Request failed (${response.status})`);
        throw new ApiError(detail, response.status);
      }
      return payload;
    } catch (error) {
      if (error.name === 'AbortError') {
        throw new ApiError('The server did not respond. Check that the API is running and reachable.', 0);
      }
      if (error instanceof ApiError) throw error;
      // "Failed to fetch" on its own tells the user nothing, so name the address
      // that could not be reached.
      throw new ApiError(
        `Could not reach the API at ${API_BASE}. Check that uvicorn is running and that `
        + 'EXPO_PUBLIC_API_BASE points at it.',
        0,
      );
    } finally {
      clearTimeout(timer);
      if (verb === 'GET') inFlight.delete(key);
    }
  })();

  if (verb === 'GET') inFlight.set(key, run);
  return run;
}

/* ------------------------------------------------------------------ */
/* Offline cache                                                       */
/* ------------------------------------------------------------------ */

/**
 * Fetch and cache. If the network fails, fall back to the last good copy so
 * crew can still work from a cold start in the air.
 */
export async function cached(name, endpoint, fallback = []) {
  try {
    const data = await api(endpoint);
    await AsyncStorage.setItem(KEYS.cache + name, JSON.stringify({ at: Date.now(), data }));
    return { data, stale: false };
  } catch (error) {
    const raw = await AsyncStorage.getItem(KEYS.cache + name);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { data: parsed.data, stale: true, cachedAt: parsed.at, error };
    }
    return { data: fallback, stale: true, error };
  }
}

export async function readCache(name, fallback = null) {
  const raw = await AsyncStorage.getItem(KEYS.cache + name);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw).data;
  } catch {
    return fallback;
  }
}

export async function writeCache(name, data) {
  await AsyncStorage.setItem(KEYS.cache + name, JSON.stringify({ at: Date.now(), data }));
}

/* ------------------------------------------------------------------ */
/* Offline sale queue                                                  */
/* ------------------------------------------------------------------ */
export async function readQueue() {
  const raw = await AsyncStorage.getItem(KEYS.queue);
  return raw ? JSON.parse(raw) : [];
}

export async function writeQueue(items) {
  await AsyncStorage.setItem(KEYS.queue, JSON.stringify(items));
}

export async function readRejected() {
  const raw = await AsyncStorage.getItem(KEYS.rejected);
  return raw ? JSON.parse(raw) : [];
}

export async function writeRejected(items) {
  await AsyncStorage.setItem(KEYS.rejected, JSON.stringify(items));
}

export async function readReconQueue() {
  const raw = await AsyncStorage.getItem(KEYS.reconQueue);
  return raw ? JSON.parse(raw) : [];
}

export async function writeReconQueue(items) {
  await AsyncStorage.setItem(KEYS.reconQueue, JSON.stringify(items));
}

/** Add a sale to the outbox. Safe to call while a sync is running. */
export async function enqueueSale(payload) {
  return withQueueLock(async () => {
    const queue = await readQueue();
    queue.push({ payload, queuedAt: new Date().toISOString(), attempts: 0 });
    await writeQueue(queue);
    return queue.length;
  });
}

/**
 * Push the queue to the server.
 *
 * The server answers per transaction. Anything marked `permanent` is moved to
 * a rejected list for supervisor review instead of being retried forever —
 * that is what used to jam the whole queue behind one bad record.
 *
 * The queue is re-read after the upload so that anything sold while the
 * request was in the air survives. Only the refs the server answered for are
 * removed.
 */
export async function flushQueue() {
  const queue = await withQueueLock(() => readQueue());
  if (!queue.length) return { accepted: 0, rejected: 0, remaining: 0 };

  const batch = queue.slice(0, 50);
  const response = await api('/sales/sync', {
    method: 'POST',
    body: { transactions: batch.map((item) => item.payload) },
  });

  const byRef = new Map(response.results.map((r) => [r.order_ref, r]));
  const newlyRejected = [];

  const remaining = await withQueueLock(async () => {
    // Re-read: the array captured above may be out of date by now.
    const current = await readQueue();
    const stillQueued = [];
    for (const item of current) {
      const result = byRef.get(item.payload.order_ref);
      if (!result) {
        stillQueued.push(item);
        continue;
      }
      if (result.status === 'success' || result.status === 'already_recorded') continue;
      if (result.permanent) {
        newlyRejected.push({ ...item, reason: result.detail, rejectedAt: new Date().toISOString() });
      } else {
        stillQueued.push({ ...item, attempts: (item.attempts || 0) + 1, lastError: result.detail });
      }
    }
    await writeQueue(stillQueued);
    if (newlyRejected.length) {
      await writeRejected([...(await readRejected()), ...newlyRejected]);
    }
    return stillQueued.length;
  });

  await AsyncStorage.setItem(KEYS.lastSync, new Date().toISOString());

  return {
    accepted: response.accepted,
    rejected: newlyRejected.length,
    remaining,
  };
}

/* ------------------------------------------------------------------ */
/* Duty group — formed on board, so it queues like a sale              */
/* ------------------------------------------------------------------ */
export async function readDutyQueue() {
  const raw = await AsyncStorage.getItem(KEYS.dutyQueue);
  return raw ? JSON.parse(raw) : [];
}

export async function writeDutyQueue(items) {
  await AsyncStorage.setItem(KEYS.dutyQueue, JSON.stringify(items));
}

/**
 * Record the crew working this sector.
 *
 * Tries the server first so the crew get an immediate confirmation, and falls
 * back to the outbox when there is no signal. `client_ref` makes the server
 * side idempotent, so a replay after a timeout cannot create a second group.
 */
export async function saveDutyGroup(flightId, memberIds) {
  const clientRef = `DG-${flightId}-${Date.now().toString(36)}`;
  const payload = { member_ids: memberIds, client_ref: clientRef };
  try {
    const group = await api(`/flights/${flightId}/duty-group`, { method: 'POST', body: payload });
    await writeCache(`dutyGroup.${flightId}`, group);
    return { group, queued: false };
  } catch (error) {
    if (error.permanent) throw error;
    await withQueueLock(async () => {
      const queue = await readDutyQueue();
      queue.push({ flightId, payload, queuedAt: new Date().toISOString() });
      await writeDutyQueue(queue);
    });
    // Stand in for the server copy so selling can start straight away.
    const pending = {
      flight_id: flightId,
      client_ref: clientRef,
      pending: true,
      members: memberIds.map((id) => ({ id })),
    };
    await writeCache(`dutyGroup.${flightId}`, pending);
    return { group: pending, queued: true };
  }
}

export async function flushDutyGroups() {
  const queue = await withQueueLock(() => readDutyQueue());
  if (!queue.length) return { accepted: 0, remaining: 0 };

  const done = new Set();
  let accepted = 0;
  for (const entry of queue) {
    try {
      const group = await api(`/flights/${entry.flightId}/duty-group`, {
        method: 'POST', body: entry.payload,
      });
      await writeCache(`dutyGroup.${entry.flightId}`, group);
      done.add(entry.payload.client_ref);
      accepted += 1;
    } catch (error) {
      if (error.permanent) done.add(entry.payload.client_ref);
    }
  }

  const remaining = await withQueueLock(async () => {
    const current = await readDutyQueue();
    const left = current.filter((e) => !done.has(e.payload.client_ref));
    await writeDutyQueue(left);
    return left.length;
  });
  return { accepted, remaining };
}

/**
 * Upload any queued leftover counts.
 *
 * Always runs AFTER the sales queue. The server works out the expected
 * remainder from recorded sales, so sending a count while sales are still
 * queued on this device would produce a variance that is an artefact of sync
 * order rather than a real discrepancy.
 */
export async function flushReconciliations() {
  const queue = await readReconQueue();
  if (!queue.length) return { accepted: 0, remaining: 0 };

  const remaining = [];
  let accepted = 0;
  for (const entry of queue) {
    try {
      await api('/reconciliations', { method: 'POST', body: entry.payload });
      accepted += 1;
    } catch (error) {
      if (error.permanent) {
        // Nothing the device can do; keep it visible rather than silently lost.
        remaining.push({ ...entry, lastError: error.message, permanent: true });
      } else {
        remaining.push({ ...entry, attempts: (entry.attempts || 0) + 1 });
      }
    }
  }
  await writeReconQueue(remaining);
  return { accepted, remaining: remaining.length };
}

/**
 * Duty groups, then sales, then leftover counts. The order matters: a sale
 * cannot be accepted until the group it names exists on the server, and a
 * leftover count is only meaningful once every sale is in.
 */
export async function flushAll() {
  const duty = await flushDutyGroups();
  const sales = await flushQueue();
  const recon = await flushReconciliations();
  return {
    ...sales,
    dutyGroups: duty.accepted,
    dutyRemaining: duty.remaining,
    reconciliations: recon.accepted,
    reconRemaining: recon.remaining,
  };
}

/* ------------------------------------------------------------------ */
/* Excel download — works on web and on native                         */
/* ------------------------------------------------------------------ */
export async function downloadReport(query, FileSystem, Sharing, path = '/reports/sales.xlsx') {
  const url = `${API_BASE}${path}?${query}`;

  if (Platform.OS === 'web') {
    // A plain <a href> cannot carry the Authorization header, and a
    // cross-origin download attribute is ignored, so fetch to a blob instead.
    const response = await fetch(url, { headers: { Authorization: `Bearer ${sessionToken}` } });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new ApiError(detail.detail || `Export failed (${response.status})`, response.status);
    }
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = filenameFrom(response) || path.split('/').pop();
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 4000);
    return { platform: 'web' };
  }

  const target = `${FileSystem.cacheDirectory}${path.split('/').pop().replace('.xlsx', '')}-${Date.now()}.xlsx`;
  const result = await FileSystem.downloadAsync(url, target, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (result.status !== 200) throw new ApiError(`Export failed (${result.status})`, result.status);
  if (await Sharing.isAvailableAsync()) await Sharing.shareAsync(result.uri);
  return { platform: 'native', uri: result.uri };
}

function filenameFrom(response) {
  const header = response.headers.get('content-disposition') || '';
  const match = header.match(/filename="?([^"]+)"?/);
  return match ? match[1] : null;
}

/**
 * Upload a spreadsheet. On web this takes a File from an <input type="file">;
 * on native it takes the object expo-document-picker returns.
 */
export async function uploadFile(endpoint, file, fields = {}) {
  const form = new FormData();
  if (Platform.OS === 'web') {
    form.append('file', file, file.name);
  } else {
    form.append('file', {
      uri: file.uri,
      name: file.name || 'upload.xlsx',
      type: file.mimeType || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
  }
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== null && value !== undefined) form.append(key, String(value));
  });

  const response = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {},
    body: form,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(payload.detail || `Upload failed (${response.status})`, response.status);
  }
  return payload;
}

export function orderRef() {
  const random = Math.random().toString(36).slice(2, 10).toUpperCase();
  return `ORD-${Date.now().toString(36).toUpperCase()}-${random}`;
}

/**
 * Compare the running backend against what this app expects. Returns null when
 * they match, otherwise a message to show the user.
 */
export async function checkBackendVersion() {
  try {
    const health = await api('/health', { auth: false, timeout: 8000 });
    const running = health.version ? String(health.version) : null;

    // No version field at all means a build from before the handshake existed.
    if (!running) {
      return `The API at ${API_BASE} is an older build — it does not report a version. `
        + `This app needs ${REQUIRED_API_VERSION}. Stop uvicorn (Ctrl-C) and start it again `
        + 'from the backend folder you just extracted, then reload this page.';
    }

    if (running === REQUIRED_API_VERSION) return null;
    const [runMajor, runMinor = 0] = running.split('.').map(Number);
    const [needMajor, needMinor = 0] = REQUIRED_API_VERSION.split('.').map(Number);
    if (runMajor > needMajor || (runMajor === needMajor && runMinor >= needMinor)) return null;
    return `The API at ${API_BASE} is running version ${running} but this app needs `
      + `${REQUIRED_API_VERSION}. Stop uvicorn (Ctrl-C) and start it again from the backend `
      + 'folder you just extracted, then reload this page.';
  } catch {
    return null; // offline is handled separately
  }
}
