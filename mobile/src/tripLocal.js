/*
 * The iPad's own trip journal (7.12).
 *
 * Everything on board happens with no signal, so the iPad cannot ask the
 * server where it stands. Instead every step this iPad takes — finish the
 * sector, confirm the remaining stock, save or hand over a form, start the
 * second flight, submit the final return, finish the whole flight — is
 * written to a journal here first and sent to the server when there is one.
 * If there is no signal the request joins an ordered outbox that uploads
 * with the sales as soon as the iPad reaches Wi-Fi.
 *
 * The screens work from the journal laid over the last server copy, so the
 * Sales and Reports pages move through the steps exactly as they would
 * online, and the other iPad never comes into it.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { api, readCache, withQueueLock, writeCache } from './api';

export const TRIP_KEYS = {
  journal: 'obs.tripJournal',
  outbox: 'obs.tripOutbox',
  failed: 'obs.tripFailed',
};

export const DEFAULT_REPORTS_TOTAL = 4;

/* ------------------------------------------------------------------ */
/* Journal                                                            */
/* ------------------------------------------------------------------ */
export async function readJournal() {
  try {
    const raw = await AsyncStorage.getItem(TRIP_KEYS.journal);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

async function writeJournal(journal) {
  await AsyncStorage.setItem(TRIP_KEYS.journal, JSON.stringify(journal));
}

export function rootOf(flight) {
  if (!flight) return null;
  return flight.direction === 'RETURN' && flight.linked_flight_id ? flight.linked_flight_id : flight.id;
}

function emptyTrip() {
  return { legs: {}, stock_return_at: null, completed_at: null };
}

/** Change one leg's local record. `patch` may be an object or a function. */
export async function journalLeg(rootId, legId, patch) {
  return withQueueLock(async () => {
    const journal = await readJournal();
    const trip = journal[rootId] || emptyTrip();
    const leg = trip.legs[legId] || { forms: {} };
    const next = typeof patch === 'function' ? patch(leg) : { ...leg, ...patch };
    trip.legs[legId] = next;
    journal[rootId] = trip;
    await writeJournal(journal);
    return journal;
  });
}

/** Change the trip-level record (final return, whole flight finished). */
export async function journalTrip(rootId, patch) {
  return withQueueLock(async () => {
    const journal = await readJournal();
    journal[rootId] = { ...(journal[rootId] || emptyTrip()), ...patch };
    await writeJournal(journal);
    return journal;
  });
}

/** Forget trips the server has completed too, so the journal does not grow for ever. */
export async function pruneJournal(flights) {
  const live = new Set((flights || []).map(rootOf));
  return withQueueLock(async () => {
    const journal = await readJournal();
    let changed = false;
    Object.keys(journal).forEach((rootId) => {
      // Keep a trip while the server still lists it or while its outbox is unsent;
      // a completed trip the server no longer sends has been merged.
      if (!live.has(Number(rootId)) && journal[rootId].completed_at && journal[rootId].synced) {
        delete journal[rootId];
        changed = true;
      }
    });
    if (changed) await writeJournal(journal);
    return journal;
  });
}

/* ------------------------------------------------------------------ */
/* Overlays: the journal laid over the server's copy                   */
/* ------------------------------------------------------------------ */
const later = (a, b) => (a && b ? (a > b ? a : b) : a || b || null);

/** Flights as this iPad sees them: its own flags, local aircraft/PAX, and a
 *  trip it has finished removed altogether. */
export function applyJournalToFlights(flights, journal) {
  return (flights || []).map((f) => {
    const trip = journal[rootOf(f)];
    if (!trip) return f;
    const leg = trip.legs[f.id] || {};
    const out = { ...f };
    out.my_finished_at = later(f.my_finished_at, leg.finished_at);
    if (f.direction === 'RETURN') out.my_started_at = later(f.my_started_at, leg.started_at);
    else if (out.my_started_at === undefined) out.my_started_at = f.created_at || 'yes';
    out.my_forms_locked_at = later(f.my_forms_locked_at, leg.forms_locked_at);
    out.my_completed_at = later(f.my_completed_at, trip.completed_at);
    if (leg.aircraft_type && !f.aircraft_type) { out.aircraft_type = leg.aircraft_type; out.aircraft = leg.aircraft || null; }
    if (leg.passengers && !f.passengers) out.passengers = leg.passengers;
    return out;
  }).filter((f) => !f.my_completed_at);
}

/** Is this leg finished on THIS iPad? Works for crew rows (my_*) and for
 *  Management rows (status) alike. */
export const legFinished = (f) => !!f && (f.status === 'FLOWN' || f.status === 'CLOSED' || !!f.my_finished_at);
export const legStarted = (f) => !!f && (f.direction !== 'RETURN'
  || (f.my_started_at !== undefined ? !!f.my_started_at : !!f.started_at));
export const legSellable = (f) => !!f && f.status === 'OPEN' && legStarted(f) && !f.my_finished_at;

/** The next step, from the same rules the server uses. */
export function stepOf(p) {
  const first = p.legs[0];
  const last = p.legs[p.legs.length - 1];
  if (p.completed) return 'DONE';
  if (!first.finished) return 'SELL_FIRST';
  if (p.round_trip && !last.started) {
    if (!first.stock_count_done) return 'STOCK_FIRST';
    if (!first.reports_done) return 'FORMS_FIRST';
    return 'START_SECOND';
  }
  if (p.round_trip && !last.finished) return 'SELL_SECOND';
  if (!p.my_stock_return_done) return 'STOCK_RETURN';
  if (!last.reports_done) return 'FORMS_LAST';
  return 'COMPLETE';
}

function finish(p) {
  const first = p.legs[0];
  const last = p.legs[p.legs.length - 1];
  p.step = stepOf(p);
  p.can_start_second = p.round_trip && first.finished && first.reports_done && first.stock_count_done
    && !last.started && !p.completed;
  p.can_complete = p.legs.every((l) => l.finished && l.reports_done && (!l.stock_count_needed || l.stock_count_done))
    && p.my_stock_return_done && !p.completed;
  p.local = true;
  return p;
}

/** Server progress (this iPad's view) with the journal laid over it. */
export function applyJournalToProgress(progress, journal) {
  if (!progress) return null;
  const trip = journal[progress.root_id];
  if (!trip) return progress;
  const p = { ...progress, legs: progress.legs.map((l) => ({ ...l })) };
  p.legs.forEach((leg) => {
    const local = trip.legs[leg.id] || {};
    const forms = local.forms || {};
    if (local.finished_at) leg.finished = true;
    if (local.started_at) leg.started = true;
    if (local.stock_count_at) { leg.stock_count_done = true; leg.my_stock_count_done = true; }
    if (local.forms_locked_at) leg.reports_locked = true;
    const handled = new Set(leg.reports_handled_types || []);
    const filed = new Set(leg.reports_filed_types || []);
    Object.entries(forms).forEach(([type, mark]) => { handled.add(type); if (mark === 'mine') filed.add(type); });
    leg.reports_handled = handled.size;
    leg.reports_filed = filed.size;
    leg.reports_handled_types = [...handled];
    leg.reports_filed_types = [...filed];
    leg.reports_awaiting_other = [...handled].filter((t) => !filed.has(t));
    if (handled.size >= leg.reports_total) leg.reports_done = true;
  });
  if (trip.stock_return_at) p.my_stock_return_done = true;
  if (trip.completed_at) p.completed = true;
  return finish(p);
}

/** No server copy at all (offline from the start): build the progress from
 *  the cached flight list and the journal. */
export function buildLocalProgress(flights, flightId, journal, reportsTotal = DEFAULT_REPORTS_TOTAL) {
  const flight = (flights || []).find((f) => f.id === flightId);
  if (!flight) return null;
  const rootId = rootOf(flight);
  const legs = (flights || [])
    .filter((f) => f.id === rootId || (f.linked_flight_id === rootId && f.direction === 'RETURN'))
    .sort((a, b) => (a.direction === 'RETURN') - (b.direction === 'RETURN'));
  if (!legs.length) return null;
  const trip = journal[rootId] || emptyTrip();
  const p = {
    root_id: rootId,
    round_trip: legs.length > 1,
    legs: legs.map((f, index) => {
      const local = trip.legs[f.id] || {};
      const forms = local.forms || {};
      return {
        id: f.id, flight_number: f.flight_number, flight_date: f.flight_date, origin: f.origin,
        destination: f.destination, direction: f.direction, status: f.status, segment: index + 1,
        started: legStarted(f) || !!local.started_at,
        finished: legFinished(f) || !!local.finished_at,
        reports_filed: Object.values(forms).filter((v) => v === 'mine').length,
        reports_total: reportsTotal,
        reports_handled: Object.keys(forms).length,
        reports_done: Object.keys(forms).length >= reportsTotal,
        reports_awaiting_other: Object.keys(forms).filter((t) => forms[t] === 'other'),
        reports_locked: !!local.forms_locked_at || !!f.my_forms_locked_at,
        devices: String(f.crew_account || '').split(',').filter(Boolean),
        stock_count_needed: index < legs.length - 1,
        stock_count_done: !!local.stock_count_at,
        my_stock_count_done: !!local.stock_count_at,
        stock_count_waiting_for: [],
      };
    }),
    stock_return: trip.stock_return_at ? 'WAITING' : 'NOT_SUBMITTED',
    my_stock_return_done: !!trip.stock_return_at,
    completed: !!trip.completed_at,
    offline_built: true,
  };
  return finish(p);
}

/** The sector's form list with the journal laid over it. */
export function applyJournalToSector(sector, journal, employeeId) {
  if (!sector) return sector;
  const leg = findLeg(journal, sector.flight_id);
  if (!leg) return sector;
  const forms = leg.forms || {};
  const reports = (sector.reports || []).map((entry) => {
    const mark = forms[entry.report_type];
    if (!mark || entry.status === 'SUBMITTED') return entry;
    if (mark === 'mine') {
      return {
        ...entry,
        status: 'SUBMITTED',
        report: entry.report || {
          status: 'SUBMITTED', local: true, payload: (leg.payloads || {})[entry.report_type] || {},
          submitted_by: employeeId, issue_label: 'Saved on this iPad', rev_label: 'uploads on Wi-Fi',
          report_date: new Date().toISOString().slice(0, 10),
        },
      };
    }
    return { ...entry, status: 'AWAITING_OTHER', deferred_by_me: true, deferred_by: [...(entry.deferred_by || []), employeeId] };
  });
  return {
    ...sector,
    reports,
    locked: !!sector.locked || !!(leg && leg.forms_locked_at),
    flight_status: leg && leg.finished_at && sector.flight_status === 'OPEN' ? 'FLOWN' : sector.flight_status,
    my_finished: !!(leg && leg.finished_at) || sector.flight_status !== 'OPEN',
    outstanding: reports.filter((e) => e.status !== 'SUBMITTED' && e.status !== 'AWAITING_OTHER').map((e) => e.report_type),
    awaiting_other: reports.filter((e) => e.status === 'AWAITING_OTHER').map((e) => e.report_type),
  };
}

export function findLeg(journal, legId) {
  for (const trip of Object.values(journal || {})) {
    if (trip.legs && trip.legs[legId]) return trip.legs[legId];
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* Outbox                                                             */
/* ------------------------------------------------------------------ */
export async function readOutbox() {
  try {
    const raw = await AsyncStorage.getItem(TRIP_KEYS.outbox);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

async function writeOutbox(items) {
  await AsyncStorage.setItem(TRIP_KEYS.outbox, JSON.stringify(items));
}

export async function readFailed() {
  try {
    const raw = await AsyncStorage.getItem(TRIP_KEYS.failed);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export async function clearFailed() {
  await AsyncStorage.setItem(TRIP_KEYS.failed, '[]');
}

let seq = 0;
function ref() {
  seq += 1;
  return `TA-${Date.now().toString(36)}-${seq}`;
}

/**
 * Do one step of the trip. The server is tried first; with no signal the
 * request is queued in order and the step counts as taken on this iPad.
 * A 4xx answer means the server will never take it, so that is raised.
 *
 * Returns { result, queued }.
 */
export async function tripAction({ method = 'POST', endpoint, body, label, flightId }) {
  try {
    const result = await api(endpoint, { method, body });
    return { result, queued: false };
  } catch (error) {
    if (error.permanent) throw error;
    await withQueueLock(async () => {
      const outbox = await readOutbox();
      outbox.push({ ref: ref(), method, endpoint, body, label, flightId, queuedAt: new Date().toISOString(), attempts: 0 });
      await writeOutbox(outbox);
    });
    return { result: null, queued: true };
  }
}

/**
 * Upload the outbox in order. Stops at the first transient failure so the
 * order is kept; a request the server refuses for good is set aside (and
 * shown on the Sync page) so it does not block what follows.
 */
export async function flushTripActions() {
  const outbox = await withQueueLock(() => readOutbox());
  if (!outbox.length) return { accepted: 0, remaining: 0, failed: 0 };
  const done = new Set();
  const failed = [];
  let accepted = 0;
  for (const entry of outbox) {
    try {
      await api(entry.endpoint, { method: entry.method, body: entry.body });
      done.add(entry.ref);
      accepted += 1;
      if (entry.endpoint.endsWith('/complete-trip') && entry.flightId) {
        await journalTrip(entry.flightId, { synced: true });
      }
    } catch (error) {
      if (error.permanent) {
        done.add(entry.ref);
        failed.push({ ...entry, reason: error.message, failedAt: new Date().toISOString() });
        continue;
      }
      break; // no signal (or the server is down): keep the rest for the next try
    }
  }
  const remaining = await withQueueLock(async () => {
    const current = await readOutbox();
    const left = current.filter((e) => !done.has(e.ref)).map((e) => ({ ...e, attempts: (e.attempts || 0) + (done.has(e.ref) ? 0 : 1) }));
    await writeOutbox(left);
    if (failed.length) {
      const previous = await readFailed();
      await AsyncStorage.setItem(TRIP_KEYS.failed, JSON.stringify([...previous, ...failed]));
    }
    return left.length;
  });
  if (accepted && !remaining) {
    // Everything this iPad did is on the server now: fresh copies next time.
    await writeCache('tripOutboxFlushedAt', Date.now());
  }
  return { accepted, remaining, failed: failed.length };
}

export async function outboxCount() {
  return (await readOutbox()).length;
}

export async function cachedProgress(flightId) {
  return readCache(`tripProgress.${flightId}`, null);
}

/**
 * Where the trip stands for THIS iPad, with or without a signal: the server's
 * answer when there is one (cached for later), otherwise the last cached
 * answer, otherwise the cached flight list — always with the journal on top.
 */
export async function loadProgress(flightId, flights, online, reportsTotal = DEFAULT_REPORTS_TOTAL) {
  if (!flightId) return null;
  const journal = await readJournal();
  let server = null;
  if (online) {
    try {
      server = await api(`/flights/${flightId}/trip-progress`);
      await writeCache(`tripProgress.${flightId}`, server);
    } catch { /* fall back below */ }
  }
  if (!server) server = await cachedProgress(flightId);
  const flight = (flights || []).find((f) => f.id === flightId);
  if (server && flight && server.root_id !== rootOf(flight)) server = null;
  return server ? applyJournalToProgress(server, journal) : buildLocalProgress(flights, flightId, journal, reportsTotal);
}

/** The remaining-stock position for a sector when the server cannot be asked:
 *  built from the trolley stock this iPad knows (cached and reduced by its own sales). */
export function buildLocalPosition(flight, inventory, me) {
  if (!flight) return null;
  const items = (inventory || []).map((row) => ({
    product_id: row.product_id,
    product_name: row.product_name,
    loaded_quantity: row.loaded_quantity,
    sold_before: 0,
    sold_quantity: Math.max(0, (row.loaded_quantity || 0) - (row.remaining_quantity || 0) - (row.returned_quantity || 0) - (row.wasted_quantity || 0)),
    wasted_quantity: row.wasted_quantity || 0,
    expected_remaining: Math.max(0, row.remaining_quantity || 0),
    by_device: {},
  }));
  return {
    flight_id: flight.id, flight_number: flight.flight_number, origin: flight.origin, destination: flight.destination,
    items, devices: [me], submitted_devices: [], waiting_for: [], device_remarks: {},
    my_submitted: false, submitted: false, total_declared: 0, offline_built: true,
  };
}

/** This iPad's own confirmation (from the journal) laid over a position. */
export function applyJournalToPosition(position, leg, me) {
  if (!position || !leg || !leg.stock_count_at || position.my_submitted) return position;
  const lines = leg.stock_lines || {};
  return {
    ...position,
    my_submitted: true,
    local: true,
    submitted_devices: [...new Set([...(position.submitted_devices || []), me])],
    device_remarks: { ...(position.device_remarks || {}), [me]: leg.stock_remarks || null },
    items: (position.items || []).map((i) => ({
      ...i, by_device: { ...(i.by_device || {}), [me]: lines[i.product_id] ?? i.expected_remaining },
    })),
  };
}

/** A sector's form list when the server cannot be asked: every form, not started. */
export function buildLocalSector(flight, forms, me) {
  if (!flight) return null;
  const devices = String(flight.crew_account || '').split(',').filter(Boolean);
  return {
    flight_id: flight.id, flight_number: flight.flight_number, flight_date: flight.flight_date,
    origin: flight.origin, destination: flight.destination, direction: flight.direction,
    sector: `${flight.origin}-${flight.destination}`, aircraft: flight.aircraft, aircraft_type: flight.aircraft_type,
    flight_status: legFinished(flight) ? 'FLOWN' : flight.status, merged_status: flight.status,
    trip_type: flight.trip_type, segment: flight.direction === 'RETURN' ? 2 : 1,
    locked: !!flight.my_forms_locked_at, devices, offline_built: true,
    reports: (forms || []).map((form) => ({
      report_type: form.type, title: form.title, form_code: form.form_code, manual: form.manual, appendix: form.appendix,
      report: null, status: 'NOT_STARTED', deferred_by: [], deferred_by_me: false,
      other_device: devices.find((d) => d !== me) || null,
    })),
    outstanding: (forms || []).map((f) => f.type),
    awaiting_other: [],
  };
}

/** Change a queued form before it has uploaded. Returns true when it was still in the outbox. */
export async function amendQueuedReport(flightId, reportType, payload) {
  return withQueueLock(async () => {
    const outbox = await readOutbox();
    let found = false;
    const next = outbox.map((e) => {
      if (e.endpoint === '/flight-reports' && e.body && e.body.flight_id === flightId && e.body.report_type === reportType) {
        found = true;
        return { ...e, body: { ...e.body, payload } };
      }
      return e;
    });
    if (found) await writeOutbox(next);
    return found;
  });
}
