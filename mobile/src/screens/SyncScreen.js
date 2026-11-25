import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import {
  api, editQueuedSale, readCache, readQueue, readReconQueue, readRejected, withQueueLock, writeCache,
  writeQueue, writeRejected,
} from '../api';
import {
  applyJournalToFlights, clearFailed, journalTrip, legFinished, readFailed, readJournal, readOutbox, rootOf, tripAction,
} from '../tripLocal';
import { confirm, notify } from '../dialog';
import { usePaged } from '../paging';
import { COLORS, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, KeyValue, Metric, Notice, PageHeader, Pagination,
  Pill, SectionTitle, Select,
  RemarkLine,
} from '../ui';

export default function SyncScreen({ user, online, onQueueChange, goToReports, goTo }) {
  const isCrew = user.role === 'crew_supervisor';
  // Inventory roles have no stake in commission; what they need from this page
  // is what the crew declared as left over.
  const isInventory = user.role === 'inventory' || user.role === 'inventory_supervisor';
  const canApproveReturns = user.permissions?.includes('reconcile_approve');

  const [queue, setQueue] = useState([]);
  const [rejected, setRejected] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sales, setSales] = useState([]);
  const [date, setDate] = useState(todayString());
  const [calendar, setCalendar] = useState(false);
  const [message, setMessage] = useState(null);

  // Reconciliation
  const [pending, setPending] = useState([]);
  const [reconQueue, setReconQueue] = useState([]);
  const [outbox, setOutbox] = useState([]);      // trip steps waiting to upload (7.12)
  const [failedSteps, setFailedSteps] = useState([]);
  // Reference data on the iPad, so queued sales show names and totals offline (7.14).
  const [products, setProducts] = useState([]);
  const [bundles, setBundles] = useState([]);
  const [flights, setFlights] = useState([]);
  const [groups, setGroups] = useState({});
  // Correcting a queued sale.
  const [editingRef, setEditingRef] = useState(null);
  const [editLines, setEditLines] = useState({});
  const [editSeat, setEditSeat] = useState('');
  const [editRemarks, setEditRemarks] = useState('');
  // What the crew physically counted, per trip and product, before submitting.
  const [counts, setCounts] = useState({});
  const [countRemarks, setCountRemarks] = useState({});
  const [adjusting, setAdjusting] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  // Every return ever submitted: paged on the server, newest first.
  const returns = usePaged('/reconciliations', { enabled: isInventory && online });
  const reloadReturns = returns.reload;

  const refreshLocal = useCallback(async () => {
    setQueue(await readQueue());
    setRejected(await readRejected());
    setReconQueue(await readReconQueue());
    setOutbox(await readOutbox());
    setFailedSteps(await readFailed());
    if (isCrew) {
      const [p, b, f] = await Promise.all([readCache('products', []), readCache('bundles', []), readCache('flights', [])]);
      setProducts(p || []); setBundles(b || []); setFlights(f || []);
      const g = {};
      for (const flight of f || []) {
        const group = await readCache(`dutyGroup.${flight.id}`, null);
        if (group) g[flight.id] = group;
      }
      setGroups(g);
    }
  }, [isCrew]);

  const refreshRemote = useCallback(async () => {
    if (isInventory) {
      reloadReturns();
      return;
    }
    try {
      const [s, list] = await Promise.all([
        api(`/my-sales-summary?date=${date}`),
        api(`/my-sales?date=${date}`),
      ]);
      setSummary(s);
      setSales(list);
      if (isCrew) await writeCache(`mySalesSummary.${date}`, s);
    } catch (error) {
      // With no signal the crew still see the last uploaded totals plus what
      // is on this iPad (below); nothing to warn about then.
      if (isCrew) {
        const last = await readCache(`mySalesSummary.${date}`, null);
        if (last) setSummary({ ...last, cached: true });
      }
      if (online) setMessage({ tone: 'warning', inline: true, text: `Live totals unavailable: ${error.message}` });
    }
  }, [date, isInventory, isCrew, online, reloadReturns]);

  async function saveAdjustment() {
    try {
      await api(`/reconciliations/${adjusting.reconId}/adjust`, {
        method: 'PUT',
        body: {
          product_id: adjusting.item.product_id,
          declared_remaining: Number(adjusting.quantity || 0),
          remarks: adjusting.remarks,
        },
      });
      setMessage({ tone: 'success', text: 'Figure corrected. The change and its reason are logged.' });
      setAdjusting(null);
      refreshRemote();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function reviewReturn(entry, approve) {
    try {
      await api(`/reconciliations/${entry.id}/review`, {
        method: 'PUT',
        body: {
          approve,
          review_remarks: approve ? 'Counted and accepted' : 'Recount required',
        },
      });
      setMessage({
        tone: approve ? 'success' : 'info',
        text: approve
          ? 'Approved — the leftover stock has returned to main inventory and the flight is closed.'
          : 'Sent back to the crew for a recount.',
      });
      refreshRemote();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  // With no signal the return is built from what this iPad knows: its trip,
  // its load and its own sales (7.12). The server adds the other iPad on upload.
  const localPending = useCallback(async () => {
    const journal = await readJournal();
    const flights = applyJournalToFlights((await readCache('flights', [])) || [], journal);
    const roots = [...new Set(flights.map(rootOf))];
    const out = [];
    for (const rootId of roots) {
      const legs = flights.filter((f) => rootOf(f) === rootId).sort((a, b) => (a.direction === 'RETURN') - (b.direction === 'RETURN'));
      if (!legs.length || !legs.every(legFinished)) continue;
      const trip = journal[rootId] || {};
      // The trolley as last seen while selling: the latest leg's cache is the one this iPad reduced.
      const inventory = (await readCache(`inventory.${legs[legs.length - 1].id}`, null)) || (await readCache(`inventory.${rootId}`, null)) || [];
      const mine = trip.stock_return_lines || {};
      out.push({
        flight_id: rootId, flight_number: legs.map((l) => l.flight_number).filter((v, i, a) => a.indexOf(v) === i).join(' / '),
        flight_date: legs[0].flight_date, round_trip: legs.length > 1, legs,
        status: trip.stock_return_at ? 'WAITING' : 'NOT_SUBMITTED', offline_built: true,
        devices: String(legs[0].crew_account || '').split(',').filter(Boolean),
        submitted_devices: trip.stock_return_at ? [user.employee_id] : [], waiting_for: [],
        my_submitted: !!trip.stock_return_at, submitted: false,
        items: inventory.map((row) => ({
          product_id: row.product_id, product_name: row.product_name, loaded_quantity: row.loaded_quantity,
          sold_quantity: Math.max(0, (row.loaded_quantity || 0) - (row.remaining_quantity || 0)),
          expected_remaining: Math.max(0, row.remaining_quantity || 0),
          declared_remaining: mine[row.product_id],
          by_device: trip.stock_return_at ? { [user.employee_id]: mine[row.product_id] ?? Math.max(0, row.remaining_quantity || 0) } : {},
        })),
      });
    }
    return out;
  }, [user.employee_id]);

  const loadPending = useCallback(async () => {
    if (!isCrew) return;
    try {
      if (!online) throw new Error('offline');
      const list = await api('/reconciliations/pending');
      // A return submitted on this iPad but not uploaded yet still shows as submitted.
      const journal = await readJournal();
      for (const t of list) {
        // Sent back for a recount: this iPad's earlier count no longer stands.
        if (t.status === 'REJECTED' && journal[t.flight_id]?.stock_return_at) {
          await journalTrip(t.flight_id, { stock_return_at: null, stock_return_lines: null });
          delete journal[t.flight_id].stock_return_at;
        }
      }
      setPending(list.map((t) => (journal[t.flight_id]?.stock_return_at && !t.my_submitted
        ? { ...t, my_submitted: true, local: true, submitted_devices: [...(t.submitted_devices || []), user.employee_id] }
        : t)));
      setPendingError(null);
    } catch (error) {
      try {
        setPending(await localPending());
      } catch { /* nothing cached yet */ }
      // Offline is normal here: the iPad has no Wi-Fi until after the flight.
      setPendingError(online ? error.message : null);
    }
  }, [isCrew, online, localPending, user.employee_id]);

  useEffect(() => { refreshLocal(); }, [refreshLocal]);
  useEffect(() => { loadPending(); }, [loadPending]);
  useEffect(() => { refreshRemote(); }, [refreshRemote]);

  // The shell uploads the queue on a timer and on reconnect, so this page only
  // needs to keep what it shows up to date.
  useEffect(() => {
    const timer = setInterval(() => {
      refreshLocal();
      if (online) {
        refreshRemote();
        // Retries by itself, so a backend that was down recovers without the
        // crew having to do anything.
        loadPending();
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [online, refreshLocal, refreshRemote, loadPending]);

  async function requeue(item) {
    // Through the lock: a background sync may be rewriting the queue right now.
    await withQueueLock(async () => {
      await writeQueue([...(await readQueue()), { ...item, attempts: 0 }]);
    });
    await writeRejected((await readRejected()).filter((r) => r.payload.order_ref !== item.payload.order_ref));
    await refreshLocal();
    onQueueChange?.();
    setMessage({ tone: 'info', text: 'Moved back into the upload queue.' });
  }

  async function discard(item) {
    const sure = await confirm({
      title: 'Discard transaction',
      message: 'This permanently removes the record from this device. The sale will never '
        + 'reach the server.',
      confirmText: 'Discard',
      cancelText: 'Keep',
      tone: 'danger',
    });
    if (!sure) return;
    await writeRejected((await readRejected()).filter((r) => r.payload.order_ref !== item.payload.order_ref));
    await refreshLocal();
    onQueueChange?.();
  }

  /* --------------------------- stock return ----------------------------- */
  // No flight picker: a trip becomes due once every sector is finished. The
  // system shows what it expects to be left; the crew type what is actually
  // in the trolley, and the difference is the variance the supervisor reviews.

  // What this iPad is expected to hold: the system figure less what the other
  // iPad has already declared (7.11, two devices share the trolley).
  function othersDeclared(trip, item) {
    return Object.entries(item.by_device || {})
      .filter(([device]) => device !== user.employee_id)
      .reduce((sum, [, qty]) => sum + Number(qty || 0), 0);
  }
  function myDefault(trip, item) {
    const others = othersDeclared(trip, item);
    return (trip.submitted_devices || []).some((d) => d !== user.employee_id)
      ? Math.max(0, item.expected_remaining - others)
      : item.expected_remaining;
  }

  /* ----------------------- queued sales (7.14) ------------------------ */
  const nameOf = (line) => (line.product_id
    ? (products.find((p) => p.id === line.product_id)?.product_name || `Product #${line.product_id}`)
    : (bundles.find((b) => b.id === line.bundle_id)?.bundle_name || `Combo #${line.bundle_id}`));
  const priceOf = (line) => (line.product_id
    ? Number(products.find((p) => p.id === line.product_id)?.selling_price || 0)
    : Number(bundles.find((b) => b.id === line.bundle_id)?.bundle_price || 0));
  const lineTotal = (line) => priceOf(line) * Number(line.quantity || 0);
  const saleTotal = (payload) => (payload.sale_type === 'NORMAL' ? payload.lines.reduce((a, l) => a + lineTotal(l), 0) : 0);
  const flightOf = (id) => flights.find((f) => f.id === id);
  const sectorLabel = (id) => { const f = flightOf(id); return f ? `${f.flight_number} ${f.origin}\u2192${f.destination}` : `Flight #${id}`; };

  // What is on this iPad and not yet uploaded, sector by sector, with the
  // pooled commission it would earn: live, with or without a signal.
  const local = React.useMemo(() => {
    const bySector = {};
    let transactions = 0; let units = 0; let revenue = 0;
    queue.forEach((item) => {
      const p = item.payload;
      const t = saleTotal(p);
      const u = p.sale_type === 'CANCELLATION' ? 0 : p.lines.reduce((a, l) => a + Number(l.quantity || 0), 0);
      const row = bySector[p.flight_id] || (bySector[p.flight_id] = { flight_id: p.flight_id, transactions: 0, units: 0, revenue_npr: 0 });
      row.transactions += 1; row.units += u; row.revenue_npr += t;
      transactions += 1; units += u; revenue += t;
    });
    const rate = summary?.commission_rate ?? 0.10;
    const sectors = Object.values(bySector).map((row) => {
      const members = (groups[row.flight_id]?.members || []).length;
      const pool = row.revenue_npr * rate;
      return { ...row, label: sectorLabel(row.flight_id), member_count: members, pool_npr: pool, share_npr: members ? pool / members : null };
    });
    return { transactions, units, revenue_npr: revenue, pool_npr: revenue * rate, rate, sectors };
  }, [queue, products, bundles, flights, groups, summary]);

  function startEdit(item) {
    setEditingRef(item.payload.order_ref);
    setEditSeat(item.payload.seat_number || '');
    setEditLines(Object.fromEntries(item.payload.lines.map((l, i) => [i, String(l.quantity)])));
    setEditRemarks('');
  }

  async function saveQueuedEdit(item) {
    if (!editRemarks.trim()) {
      return notify({ title: 'Remarks required', message: 'Say why the sale is being changed. The remarks are kept with the sale and shown to Management.' });
    }
    const seat = editSeat.trim().toUpperCase();
    if (!seat) return notify({ title: 'Seat required', message: 'Enter the seat number.' });
    const newLines = item.payload.lines
      .map((l, i) => ({ ...l, quantity: Number(editLines[i] === '' || editLines[i] === undefined ? l.quantity : editLines[i]) }))
      .filter((l) => l.quantity > 0);
    if (!newLines.length) return notify({ title: 'Nothing left in the sale', message: 'A sale needs at least one item. To cancel it entirely, record a cancellation on the Sales page.' });
    if (newLines.some((l) => Number.isNaN(l.quantity))) return notify({ title: 'Check the quantities', message: 'Quantities must be whole numbers.' });
    // Stock on the iPad follows the change, so the trolley figure stays right.
    const delta = {};
    const addDelta = (line, sign) => {
      if (line.product_id) delta[line.product_id] = (delta[line.product_id] || 0) + sign * line.quantity;
      else (bundles.find((b) => b.id === line.bundle_id)?.items || []).forEach((it) => { delta[it.product_id] = (delta[it.product_id] || 0) + sign * it.quantity * line.quantity; });
    };
    if (item.payload.sale_type !== 'CANCELLATION') {
      item.payload.lines.forEach((l) => addDelta(l, +1));   // given back
      newLines.forEach((l) => addDelta(l, -1));             // taken again
    }
    const ok = await editQueuedSale(item.payload.order_ref, (p) => ({ ...p, seat_number: seat, lines: newLines }), editRemarks.trim());
    if (!ok) {
      setEditingRef(null);
      return setMessage({ tone: 'info', text: 'This sale has already uploaded. Ask Management to correct it.' });
    }
    const flight = flightOf(item.payload.flight_id);
    const root = flight ? rootOf(flight) : item.payload.flight_id;
    for (const leg of flights.filter((f) => rootOf(f) === root)) {
      const inv = await readCache(`inventory.${leg.id}`, null);
      if (inv) await writeCache(`inventory.${leg.id}`, inv.map((r) => (delta[r.product_id] ? { ...r, remaining_quantity: Math.max(0, r.remaining_quantity + delta[r.product_id]) } : r)));
    }
    setEditingRef(null);
    await refreshLocal();
    onQueueChange?.();
    setMessage({ tone: 'success', text: 'Sale changed on this iPad. The change and your remarks upload with it and stay in its history.' });
  }

  async function confirmReturn(trip) {
    const sure = await confirm({
      title: 'Submit the stock return?',
      message: `The counted stock for ${trip.flight_number} goes to the Inventory. `
        + 'Once submitted it cannot be changed here unless it is sent back for a recount.',
      confirmText: 'Confirm submit',
    });
    if (!sure) return;
    const typed = counts[trip.flight_id] || {};
    const lines = trip.items.map((item) => ({
      product_id: item.product_id,
      declared_remaining: typed[item.product_id] === undefined || typed[item.product_id] === ''
        ? myDefault(trip, item)
        : Number(typed[item.product_id]),
    }));
    if (lines.some((l) => Number.isNaN(l.declared_remaining) || l.declared_remaining < 0)) {
      return setMessage({ tone: 'danger', text: 'Counts must be whole numbers, zero or more.' });
    }
    const payload = { flight_id: trip.flight_id, lines, remarks: countRemarks[trip.flight_id] || null };

    try {
      // Goes in the ordered outbox with the other trip steps when there is no
      // signal (7.12), after this iPad's sales and before "Finish whole flight".
      const { result, queued } = await tripAction({ endpoint: '/reconciliations', body: payload, label: `Final stock return ${trip.flight_number}`, flightId: trip.flight_id });
      await journalTrip(trip.flight_id, {
        stock_return_at: new Date().toISOString(),
        stock_return_lines: Object.fromEntries(lines.map((l) => [l.product_id, l.declared_remaining])),
      });
      await refreshLocal();
      onQueueChange?.();
      const waiting = result ? (result.waiting_for || []).length : 0;
      setMessage({
        tone: queued || waiting ? 'info' : result.has_variance ? 'warning' : 'success',
        text: queued
          ? 'Saved on this iPad. It uploads with your sales as soon as you reach Wi-Fi.'
          : waiting
            ? `Your count is in. ${result.waiting_for.join(', ')} declares its own part when it uploads; the two counts are added together.`
            : result.has_variance
              ? `Submitted with a difference of ${result.total_variance} unit(s) against the system `
                + 'figure. The Inventory will review it.'
              : `Submitted. ${result.total_declared} unit(s) go back to main inventory once the `
                + 'Inventory approves.',
      });
      loadPending();

      // Next: the commission and the forms on the Reports page, then Finish whole flight.
      if (goTo && isCrew) {
        const now = await confirm({
          title: 'Stock return submitted',
          message: 'Next: see this sector\u2019s commission and fill in the forms on the Reports page, '
            + 'then press \u201cFinish whole flight\u201d on the Sales page.',
          confirmText: 'Go to Reports',
          cancelText: 'Later',
        });
        if (now) goTo('reports');
      }
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }



  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Sync & Commission"
        description={isInventory
          ? 'Stock returned by crew after each flight.'
          : isCrew
            ? 'Your group\'s sales for the selected day, plus the upload queue.'
            : 'All groups, sales and commission for the selected day.'}
        right={<Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'warning'} />}
      />

      {message && <Notice message={message} />}

      {!isInventory && (
      <Card>
        <SectionTitle
          title="Upload queue"
          description="Sales taken without a connection upload automatically once the device reconnects. Nothing to press."
        />
        <View style={[styles.wrap, { marginBottom: 12 }]}>
          <Metric label="Sales waiting" value={String(queue.length)} tone={queue.length ? 'warning' : 'success'} />
          <Metric label="Trip steps waiting" value={String(outbox.length + reconQueue.length)} tone={outbox.length + reconQueue.length ? 'warning' : 'success'} />
          <Metric label="Needs review" value={String(rejected.length + failedSteps.length)} tone={rejected.length + failedSteps.length ? 'danger' : 'default'} />
        </View>
        {outbox.slice(0, 8).map((item) => (
          <View key={item.ref} style={styles.listRow}>
            <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.text }}>{item.label || item.endpoint}</Text>
            <Text style={styles.mono}>Trip step \u00b7 queued {new Date(item.queuedAt).toLocaleString()}{item.attempts ? ` \u00b7 ${item.attempts} attempt(s)` : ''}</Text>
          </View>
        ))}
        {!online && !!queue.length && (
          <Banner
            tone="warning"
            message="No connection. These will upload by themselves as soon as the device is back online."
          />
        )}
        {queue.map((item) => {
          const p = item.payload;
          const editing = editingRef === p.order_ref;
          return (
          <View key={p.order_ref} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                {sectorLabel(p.flight_id)} · seat {p.seat_number}{p.sale_type !== 'NORMAL' ? ` · ${p.sale_type}` : ''}
              </Text>
              <Text style={{ fontSize: 13, fontWeight: '700', color: COLORS.primary }}>{npr(saleTotal(p))}</Text>
            </View>
            {p.lines.map((l, i) => (
              <Text key={i} style={[styles.mono, { marginTop: 2 }]}>
                {nameOf(l)} × {l.quantity}{p.sale_type === 'NORMAL' ? ` · ${npr(lineTotal(l))}` : ''}
              </Text>
            ))}
            <Text style={styles.mono}>
              Queued {new Date(item.queuedAt).toLocaleString()}
              {item.attempts ? ` · ${item.attempts} attempt(s)` : ''}
              {(p.device_edits || []).length ? ` · ${p.device_edits.length} change(s) on this iPad` : ''}
            </Text>
            {(p.device_edits || []).map((e) => (
              <RemarkLine key={e.at} label={`Changed ${new Date(e.at).toLocaleTimeString()}`} text={e.remarks} />
            ))}
            {!!item.lastError && (
              <Text style={{ fontSize: 11, color: COLORS.warning, marginTop: 2 }}>{item.lastError}</Text>
            )}
            {isCrew && (
              <Button title={editing ? 'Cancel' : 'Correct this sale'} tone="ghost" small style={{ marginTop: 6, alignSelf: 'flex-start' }}
                onPress={() => (editing ? setEditingRef(null) : startEdit(item))} />
            )}
            {editing && (
              <View style={{ marginTop: 8, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
                <Text style={[styles.mono, { marginBottom: 8 }]}>
                  Change the quantities (0 removes the item) or the seat. The sale has not uploaded yet, so the change goes up with it; your remarks are kept in its history.
                </Text>
                {p.lines.map((l, i) => (
                  <View key={i} style={[styles.rowBetween, { paddingVertical: 4 }]}>
                    <Text style={{ flex: 1, fontSize: 13 }}>{nameOf(l)} · {npr(priceOf(l))} each</Text>
                    <TextInput
                      value={editLines[i] ?? ''}
                      onChangeText={(v) => setEditLines((c) => ({ ...c, [i]: v.replace(/[^0-9]/g, '') }))}
                      keyboardType="number-pad"
                      style={[styles.input, { width: 64, textAlign: 'center', paddingVertical: 4, marginBottom: 0 }]}
                    />
                  </View>
                ))}
                <Field label="Seat" value={editSeat} onChangeText={setEditSeat} autoCapitalize="characters" />
                <Field label="Remarks (mandatory)" value={editRemarks} onChangeText={setEditRemarks} multiline placeholder="e.g. passenger took one can, not two" />
                <Button title="Save change" onPress={() => saveQueuedEdit(item)} disabled={!editRemarks.trim()} />
              </View>
            )}
          </View>
          );
        })}
        {!queue.length && !outbox.length && !reconQueue.length && <Text style={styles.empty}>Nothing waiting to upload.</Text>}
      </Card>
      )}

      {!isInventory && !!failedSteps.length && (
        <Card style={{ borderColor: COLORS.danger }}>
          <SectionTitle
            title="Trip steps the server refused"
            description="These were taken on the iPad but the server would not accept them on upload. Tell Management; the trip itself carries on."
          />
          {failedSteps.map((item) => (
            <View key={item.ref} style={styles.listRow}>
              <Text style={{ fontSize: 13, fontWeight: '600' }}>{item.label || item.endpoint}</Text>
              <Text style={{ fontSize: 12, color: COLORS.danger, marginTop: 2 }}>{item.reason}</Text>
            </View>
          ))}
          <Button title="Clear this list" tone="ghost" small style={{ marginTop: 8 }} onPress={async () => { await clearFailed(); refreshLocal(); }} />
        </Card>
      )}

      {!isInventory && !!rejected.length && (
        <Card style={{ borderColor: COLORS.danger }}>
          <SectionTitle
            title="Rejected — needs a decision"
            description="The server refused these permanently, so they are held here instead of blocking the queue."
          />
          {rejected.map((item) => (
            <View key={item.payload.order_ref} style={styles.listRow}>
              <Text style={{ fontSize: 13, fontWeight: '600' }}>
                Seat {item.payload.seat_number} · {item.payload.lines.length} item(s)
              </Text>
              <Text style={{ fontSize: 12, color: COLORS.danger, marginTop: 2 }}>{item.reason}</Text>
              <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
                <Button title="Retry" tone="secondary" small style={styles.flex1} onPress={() => requeue(item)} />
                <Button title="Discard" tone="danger" small style={styles.flex1} onPress={() => discard(item)} />
              </View>
            </View>
          ))}
        </Card>
      )}

      {isCrew && (
        <Card>
          <SectionTitle
            title="Live sales & commission on this iPad"
            description="Updates as sales are recorded, with or without a signal: what has uploaded plus what is still on this iPad."
            right={<Pill label={online ? 'LIVE' : 'OFFLINE · LIVE'} tone={online ? 'success' : 'warning'} />}
          />
          <View style={[styles.wrap, { marginBottom: 10 }]}>
            <Metric label="Uploaded (server)" value={npr(summary?.total_sales_npr || 0)} sub={summary?.cached ? 'last known' : undefined} />
            <Metric label="On this iPad, not uploaded" value={npr(local.revenue_npr)} tone={local.transactions ? 'warning' : 'default'} sub={`${local.transactions} transaction(s) · ${local.units} unit(s)`} />
            <Metric label="Total so far" value={npr((summary?.total_sales_npr || 0) + local.revenue_npr)} tone="primary" />
            <Metric label={`Pool commission (${Math.round(local.rate * 100)}%)`} value={npr((summary?.total_commission_npr || 0) + local.pool_npr)} tone="success" />
          </View>
          {local.sectors.map((sec) => (
            <View key={sec.flight_id} style={[styles.listRow, { paddingVertical: 8 }]}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>{sec.label}</Text>
                {!!sec.member_count && <Pill label={`${sec.member_count} crew`} tone="muted" />}
              </View>
              <KeyValue label="Not yet uploaded" value={`${npr(sec.revenue_npr)} · ${sec.transactions} transaction(s)`} />
              <KeyValue
                label={sec.member_count ? `Pool ÷ ${sec.member_count} crew` : 'Pool (crew not entered yet)'}
                value={sec.member_count ? `${npr(sec.pool_npr)} = ${npr(sec.share_npr)} each` : npr(sec.pool_npr)}
                tone={COLORS.success}
              />
            </View>
          ))}
          <Text style={[styles.mono, { marginTop: 6 }]}>
            Combo sales pay their commission to the crew member who sold them; the server works that out on upload. The figures here are the pooled estimate.
          </Text>
        </Card>
      )}

      {isInventory ? (
        <Card>
          <SectionTitle
            title="Crew stock returns"
            description="What each crew declared as remaining at the end of the flight, newest first."
          />
          {!!returns.error && <Banner tone="warning" message={`Stock returns unavailable: ${returns.error}`} />}
          {returns.items.map((entry) => (
            <View key={entry.id} style={[styles.listRow, { paddingVertical: 12 }]}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>
                  {entry.flight_number} · {entry.origin}→{entry.destination}
                </Text>
                <Pill
                  label={entry.status}
                  tone={entry.status === 'APPROVED' ? 'success'
                    : entry.status === 'REJECTED' ? 'danger' : 'warning'}
                />
              </View>
              <Text style={styles.mono}>
                {entry.flight_date} · declared by {(entry.submitted_devices || []).join(' + ') || entry.submitted_by || '—'}
                {entry.has_variance ? ` · ${entry.total_variance} unit difference` : ' · matches the system'}
                {(entry.waiting_for || []).length ? ` · waiting for ${entry.waiting_for.join(', ')}` : ''}
              </Text>
              {(entry.submitted_devices || []).length > 1 && (
                <Text style={styles.mono}>The Recorded column is the two iPads added together; each iPad's part is shown under the product.</Text>
              )}

              <View style={[styles.rowBetween, { marginTop: 8, marginBottom: 2 }]}>
                <Text style={[styles.label, { flex: 2, marginBottom: 0 }]}>Product</Text>
                <Text style={[styles.label, { width: 52, textAlign: 'right', marginBottom: 0 }]}>Loaded</Text>
                <Text style={[styles.label, { width: 44, textAlign: 'right', marginBottom: 0 }]}>Sold</Text>
                <Text style={[styles.label, { width: 52, textAlign: 'right', marginBottom: 0 }]}>System</Text>
                <Text style={[styles.label, { width: 52, textAlign: 'right', marginBottom: 0 }]}>Recorded</Text>
              </View>
              {entry.items.map((item) => (
                <View key={item.product_id}>
                  <View style={[styles.rowBetween, { paddingVertical: 3 }]}>
                    <Text style={{ flex: 2, fontSize: 13 }} numberOfLines={1}>{item.product_name}</Text>
                    <Text style={{ width: 52, fontSize: 13, textAlign: 'right' }}>{item.loaded_quantity}</Text>
                    <Text style={{ width: 44, fontSize: 13, textAlign: 'right' }}>{item.sold_quantity}</Text>
                    <Text style={{ width: 52, fontSize: 13, textAlign: 'right' }}>
                      {item.expected_remaining}
                    </Text>
                    <Text style={{
                      width: 52, fontSize: 13, textAlign: 'right', fontWeight: '600',
                      color: item.overridden ? COLORS.warning : COLORS.text,
                    }}>
                      {item.declared_remaining}
                    </Text>
                  </View>
                  {Object.keys(item.by_device || {}).length > 1 && (
                    <Text style={[styles.mono, { fontSize: 11 }]}>
                      {Object.entries(item.by_device).map(([d, q]) => `${d}: ${q}`).join(' · ')}
                    </Text>
                  )}
                  {!!item.overridden && (
                    <RemarkLine label={`Corrected by ${item.override_by}`} text={item.override_remarks} style={{ marginTop: 2 }} />
                  )}
                  {canApproveReturns && entry.status === 'PENDING' && (
                    <Button
                      title={adjusting?.key === `${entry.id}:${item.product_id}` ? 'Cancel' : 'Correct this figure'}
                      tone="ghost"
                      small
                      style={{ alignSelf: 'flex-start' }}
                      onPress={() => {
                        const key = `${entry.id}:${item.product_id}`;
                        if (adjusting?.key === key) return setAdjusting(null);
                        setAdjusting({
                          key, reconId: entry.id, item,
                          quantity: String(item.declared_remaining), remarks: '',
                        });
                      }}
                    />
                  )}
                  {adjusting?.key === `${entry.id}:${item.product_id}` && (
                    <View style={{ backgroundColor: COLORS.surfaceAlt, padding: 10, borderRadius: 8, marginTop: 4 }}>
                      <Field
                        label={`Actual count (system says ${item.expected_remaining})`}
                        value={adjusting.quantity}
                        onChangeText={(v) => setAdjusting({ ...adjusting, quantity: v.replace(/[^0-9]/g, '') })}
                        keyboardType="number-pad"
                      />
                      <Field
                        label="Reason (mandatory)"
                        value={adjusting.remarks}
                        onChangeText={(v) => setAdjusting({ ...adjusting, remarks: v })}
                        multiline
                        placeholder="One bottle damaged in the galley"
                      />
                      <Button title="Save correction" small onPress={saveAdjustment}
                        disabled={!adjusting.remarks.trim()} />
                    </View>
                  )}
                </View>
              ))}

              {!!entry.review_remarks && <RemarkLine label="Review" text={entry.review_remarks} />}
              {entry.has_variance && entry.status === 'PENDING' && (
                <Banner
                  tone="warning"
                  message={'The crew counted something different from the system figure. Check the '
                    + 'Counted column before approving; approving accepts their count.'}
                />
              )}
              {canApproveReturns && entry.status === 'PENDING' && (
                <View style={[styles.row, styles.gap8, { marginTop: 10 }]}>
                  <Button title="Approve" tone="success" small style={styles.flex1}
                    onPress={() => reviewReturn(entry, true)} />
                  <Button title="Send back" tone="danger" small style={styles.flex1}
                    onPress={() => reviewReturn(entry, false)} />
                </View>
              )}
            </View>
          ))}
          {!returns.items.length && !returns.loading && <Text style={styles.empty}>No stock returns submitted yet.</Text>}
          <Pagination pager={returns} label="returns" />
        </Card>
      ) : (
      <Card>
        <SectionTitle title={isCrew ? 'Uploaded sales & commission' : 'Sales & commission'} description={isCrew && summary?.cached ? 'Last totals received from the server; refreshes on Wi-Fi.' : undefined} />
        <Select label="Date" valueLabel={date} onPress={() => setCalendar(true)} />
        {summary ? (
          <>
            <View style={[styles.wrap, { marginBottom: 10 }]}>
              <Metric label="Sales" value={npr(summary.total_sales_npr)} tone="primary" />
              <Metric
                label={`Commission (${(summary.commission_rate * 100).toFixed(0)}%)`}
                value={npr(summary.total_commission_npr)}
                tone="success"
              />
              <Metric label="Transactions" value={String(summary.transactions)} />
            </View>
            <SectionTitle
              title="By crew member"
              description={summary.commission_model === 'POOLED'
                ? 'The sector total is shared equally across its duty group.'
                : 'Each crew member earns on what they personally sold.'}
            />
            {(summary.crew || []).map((member) => (
              <View key={member.crew_member_id} style={[styles.rowBetween, { paddingVertical: 7 }]}>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>
                    {member.name}
                  </Text>
                  <Text style={styles.mono}>
                    {member.crew_code} · {member.transactions} transaction(s)
                  </Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={{ fontSize: 13, color: COLORS.text }}>{npr(member.sales_npr)}</Text>
                  <Text style={[styles.mono, { color: COLORS.success }]}>
                    {npr(member.commission_npr)}
                  </Text>
                </View>
              </View>
            ))}
            {!(summary.crew || []).length && (
              <Text style={styles.empty}>{isCrew ? 'No trip in progress on this iPad.' : 'No sales recorded on this date.'}</Text>
            )}

            {!!(summary.sectors || []).length && (
              <>
                <View style={styles.divider} />
                <SectionTitle title="By sector" />
                {summary.sectors.map((sector) => (
                  <View key={sector.flight_id} style={[styles.listRow, { paddingVertical: 10 }]}>
                    <View style={styles.rowBetween}>
                      <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>
                        {sector.duty_group_label}
                      </Text>
                      <Pill label={`${sector.member_count} crew`} tone="muted" />
                    </View>
                    <KeyValue label="Sector sales" value={npr(sector.revenue_npr)} />
                    <KeyValue
                      label={`Pool \u00f7 ${sector.member_count} crew`}
                      value={`${npr(sector.commission_pool_npr)} = ${npr(sector.share_npr || 0)} each`}
                      tone={COLORS.success}
                    />
                    {!!sector.combo_commission_npr && (
                      <KeyValue label="Combo commission" value={npr(sector.combo_commission_npr)} />
                    )}
                    {sector.sellers.map((seller) => (
                      <View key={seller.crew_member_id} style={[styles.rowBetween, { paddingVertical: 2 }]}>
                        <Text style={{ fontSize: 13, flex: 1 }}>{seller.name} (combo)</Text>
                        <Text style={[styles.mono, { fontSize: 11 }]}>{npr(seller.combo_sales_npr ?? seller.sales_npr)}</Text>
                      </View>
                    ))}
                  </View>
                ))}
              </>
            )}
          </>
        ) : (
          <Text style={styles.empty}>{isCrew ? 'No uploaded sales for this date yet. The live figures above include what is on this iPad.' : 'Connect to load sales totals.'}</Text>
        )}
      </Card>
      )}

      {isCrew && (
        pending.length ? pending.map((trip) => {
          // This iPad has declared its part: read-only until sent back.
          const locked = (trip.my_submitted || trip.submitted) && trip.status !== 'REJECTED';
          const otherDevices = (trip.devices || []).filter((d) => d !== user.employee_id);
          return (
          <Card key={trip.flight_id}>
            <SectionTitle
              title="End-of-flight stock return"
              description={trip.round_trip
                ? 'The trip was loaded once for both legs, so the leftovers cover the whole trip.'
                : 'What is left on board, worked out from the load and everything sold.'}
              right={<Pill label={trip.round_trip ? 'ROUND TRIP' : 'ONE WAY'} tone="muted" />}
            />

            <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>
              {trip.flight_number}
            </Text>
            <Text style={[styles.mono, { marginBottom: 10 }]}>
              {trip.flight_date} · {(trip.legs || []).map((l) => `${l.origin}→${l.destination}`).join('  |  ')}
            </Text>

            {trip.status === 'REJECTED' && (
              <Banner tone="danger" message={`Sent back: ${trip.review_remarks || 'recount required'}`} />
            )}
            {otherDevices.length > 0 && (
              <Banner
                tone="info"
                message={`Two iPads share this trolley. Type what is left on THIS iPad; ${otherDevices.join(', ')} declares its own part and the two are added together. `
                  + ((trip.submitted_devices || []).some((d) => d !== user.employee_id) ? 'The other iPad has already declared; the Expected column shows what should be left on this one.' : '')}
              />
            )}

            <View style={[styles.rowBetween, { marginBottom: 4 }]}>
              <Text style={[styles.label, { flex: 2, marginBottom: 0 }]}>Product</Text>
              <Text style={[styles.label, { width: 48, textAlign: 'right', marginBottom: 0 }]}>Sold</Text>
              <Text style={[styles.label, { width: 58, textAlign: 'right', marginBottom: 0 }]}>Expected</Text>
              <Text style={[styles.label, { width: 72, textAlign: 'right', marginBottom: 0 }]}>Counted</Text>
            </View>
            {trip.items.map((item) => {
              const typed = (counts[trip.flight_id] || {})[item.product_id];
              const mine = (item.by_device || {})[user.employee_id];
              const expectedHere = myDefault(trip, item);
              const value = locked
                ? String(mine ?? item.declared_remaining ?? item.expected_remaining)
                : typed === undefined ? String(expectedHere) : typed;
              const diff = Number(value || 0) - expectedHere;
              return (
                <View key={item.product_id} style={[styles.rowBetween, { paddingVertical: 4 }]}>
                  <Text style={{ flex: 2, fontSize: 13 }} numberOfLines={1}>{item.product_name}</Text>
                  <Text style={{ width: 48, fontSize: 13, textAlign: 'right' }}>{item.sold_quantity}</Text>
                  <Text style={{ width: 58, fontSize: 13, textAlign: 'right' }}>{otherDevices.length ? expectedHere : item.expected_remaining}</Text>
                  <TextInput
                    value={value}
                    onChangeText={(text) => setCounts((current) => ({
                      ...current,
                      [trip.flight_id]: {
                        ...(current[trip.flight_id] || {}),
                        [item.product_id]: text.replace(/[^0-9]/g, ''),
                      },
                    }))}
                    keyboardType="number-pad"
                    editable={!locked}
                    style={[styles.input, {
                      width: 72,
                      paddingVertical: 4,
                      textAlign: 'right',
                      marginBottom: 0,
                      borderColor: diff === 0 ? COLORS.border : COLORS.warning,
                    }]}
                  />
                </View>
              );
            })}
            <View style={styles.divider} />

            <Field
              label="Remarks (only if something does not match)"
              value={countRemarks[trip.flight_id] || ''}
              onChangeText={(text) => setCountRemarks((c) => ({ ...c, [trip.flight_id]: text }))}
              multiline
              placeholder="e.g. two bottles broken in the trolley"
            />

            {locked ? (
              <Banner
                tone="success"
                message={(trip.local || trip.offline_built
                  ? 'Confirmed on this iPad; it uploads with the sales on Wi-Fi. '
                  : (trip.waiting_for || []).length
                    ? 'Your count is in; the other iPad adds its part when it uploads. '
                    : 'Submitted. The count is with the Inventory. ')
                  + (isCrew ? 'Next: the commission and the forms on the Reports page, then \u201cFinish whole flight\u201d on the Sales page.' : '')}
              />
            ) : (
              <Button
                title={online ? 'Submit stock return' : 'SUBMIT (will upload on Wi-Fi)'}
                tone={online ? 'primary' : 'warning'}
                style={{ marginTop: 10 }}
                onPress={() => confirmReturn(trip)}
              />
            )}
            <Text style={[styles.mono, { marginTop: 6 }]}>
              The Expected column is what the system works out from the load and every sale on the
              trip. Type what is actually in the trolley. Anything that differs is flagged for the
              Inventory.
            </Text>
          </Card>
          );
        }) : (
          <Card>
            <SectionTitle title="End-of-flight stock return" />
            {pendingError
              ? (
                <>
                  <Banner tone="danger" message={pendingError} />
                  <Button title="Try again" tone="secondary" onPress={loadPending} />
                  <Text style={[styles.mono, { marginTop: 8 }]}>
                    This retries by itself every few seconds.
                  </Text>
                </>
              )
              : (
                <Text style={styles.empty}>
                  {online
                    ? 'Nothing to confirm. A trip appears here once every leg has been finished on this iPad.'
                    : 'Nothing to confirm yet. The return appears here once every leg has been finished on this iPad \u2014 no signal needed.'}
                </Text>
              )}
          </Card>
        )
      )}

      <CalendarModal
        visible={calendar}
        title="Select date"
        value={date}
        onSelect={(value) => setDate(value || todayString())}
        onClose={() => setCalendar(false)}
      />
    </ScrollView>
  );
}
