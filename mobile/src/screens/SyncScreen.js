import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';

import {
  api, readQueue, readReconQueue, readRejected, withQueueLock,
  writeQueue, writeReconQueue, writeRejected,
} from '../api';
import { confirm } from '../dialog';
import { COLORS, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, KeyValue, Metric,
  PageHeader, Pill, SectionTitle, Select,
} from '../ui';

export default function SyncScreen({ user, online, onQueueChange, goToReports }) {
  const isCrew = user.role === 'crew_supervisor';
  // Inventory roles have no stake in commission; what they need from this page
  // is what the crew declared as left over.
  const isInventory = user.role === 'inventory' || user.role === 'inventory_supervisor';
  const canApproveReturns = user.role === 'inventory_supervisor' || user.role === 'management';

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
  // What the crew physically counted, per trip and product, before submitting.
  const [counts, setCounts] = useState({});
  const [countRemarks, setCountRemarks] = useState({});
  const [adjusting, setAdjusting] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  const [returns, setReturns] = useState([]);

  const refreshLocal = useCallback(async () => {
    setQueue(await readQueue());
    setRejected(await readRejected());
    setReconQueue(await readReconQueue());
  }, []);

  const refreshRemote = useCallback(async () => {
    if (isInventory) {
      try {
        setReturns(await api('/reconciliations'));
      } catch (error) {
        setMessage({ tone: 'warning', text: `Stock returns unavailable: ${error.message}` });
      }
      return;
    }
    try {
      const [s, list] = await Promise.all([
        api(`/my-sales-summary?date=${date}`),
        api(`/my-sales?date=${date}`),
      ]);
      setSummary(s);
      setSales(list);
    } catch (error) {
      setMessage({ tone: 'warning', text: `Live totals unavailable: ${error.message}` });
    }
  }, [date, isInventory]);

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

  const loadPending = useCallback(async () => {
    if (!isCrew) return;
    try {
      setPending(await api('/reconciliations/pending'));
      setPendingError(null);
    } catch (error) {
      // Offline is normal here: the iPad has no Wi-Fi until after the flight.
      setPendingError(online ? error.message : null);
    }
  }, [isCrew, online]);

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

  async function confirmReturn(trip) {
    const typed = counts[trip.flight_id] || {};
    const lines = trip.items.map((item) => ({
      product_id: item.product_id,
      declared_remaining: typed[item.product_id] === undefined || typed[item.product_id] === ''
        ? item.expected_remaining
        : Number(typed[item.product_id]),
    }));
    if (lines.some((l) => Number.isNaN(l.declared_remaining) || l.declared_remaining < 0)) {
      return setMessage({ tone: 'danger', text: 'Counts must be whole numbers, zero or more.' });
    }
    const payload = { flight_id: trip.flight_id, lines, remarks: countRemarks[trip.flight_id] || null };

    if (!online) {
      const queued = (await readReconQueue()).filter((e) => e.payload.flight_id !== trip.flight_id);
      await writeReconQueue([...queued, { payload, queuedAt: new Date().toISOString() }]);
      await refreshLocal();
      onQueueChange?.();
      return setMessage({
        tone: 'info',
        text: 'Saved on this device. It uploads with your sales as soon as you reach Wi-Fi.',
      });
    }

    try {
      const result = await api('/reconciliations', { method: 'POST', body: payload });
      setMessage({
        tone: result.has_variance ? 'warning' : 'success',
        text: result.has_variance
          ? `Submitted with a difference of ${result.total_variance} unit(s) against the system `
            + 'figure. The Inventory Supervisor will review it.'
          : `Submitted. ${result.total_declared} unit(s) go back to main inventory once the `
            + 'Inventory Supervisor approves.',
      });
      loadPending();

      // The stock is dealt with; the sector's reports are what remain.
      if (goToReports) {
        const lastLeg = (trip.legs || [])[(trip.legs || []).length - 1];
        const sectorId = lastLeg?.id || trip.flight_id;
        const now = await confirm({
          title: 'File this sector\u2019s reports?',
          message: 'The stock return is in. The four cabin reports for this sector still '
            + 'need to be filled in.',
          confirmText: 'Go to Reports',
          cancelText: 'Later',
        });
        if (now) goToReports(sectorId);
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

      {message && <Banner tone={message.tone} message={message.text} />}

      {!isInventory && (
      <Card>
        <SectionTitle
          title="Upload queue"
          description="Sales taken without a connection upload automatically once the device reconnects. Nothing to press."
        />
        <View style={[styles.wrap, { marginBottom: 12 }]}>
          <Metric label="Waiting" value={String(queue.length)} tone={queue.length ? 'warning' : 'success'} />
          <Metric label="Needs review" value={String(rejected.length)} tone={rejected.length ? 'danger' : 'default'} />
        </View>
        {!online && !!queue.length && (
          <Banner
            tone="warning"
            message="No connection. These will upload by themselves as soon as the device is back online."
          />
        )}
        {queue.slice(0, 8).map((item) => (
          <View key={item.payload.order_ref} style={styles.listRow}>
            <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.text }}>
              Seat {item.payload.seat_number} · {item.payload.lines.length} item(s)
            </Text>
            <Text style={styles.mono}>
              Queued {new Date(item.queuedAt).toLocaleString()}
              {item.attempts ? ` · ${item.attempts} attempt(s)` : ''}
            </Text>
            {!!item.lastError && (
              <Text style={{ fontSize: 11, color: COLORS.warning, marginTop: 2 }}>{item.lastError}</Text>
            )}
          </View>
        ))}
        {queue.length > 8 && (
          <Text style={[styles.mono, { marginTop: 8 }]}>…and {queue.length - 8} more.</Text>
        )}
        {!queue.length && <Text style={styles.empty}>Nothing waiting to upload.</Text>}
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

      {isInventory ? (
        <Card>
          <SectionTitle
            title="Crew stock returns"
            description="What each crew declared as remaining at the end of the flight, newest first."
          />
          {returns.map((entry) => (
            <View key={entry.id} style={[styles.listRow, { paddingVertical: 12 }]}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '700', flex: 1 }}>
                  {entry.flight_number} · {entry.origin}→{entry.destination}
                </Text>
                <Pill
                  label={entry.status}
                  tone={entry.status === 'APPROVED' ? 'success'
                    : entry.status === 'REJECTED' ? 'danger' : 'warning'}
                />
              </View>
              <Text style={styles.mono}>
                {entry.flight_date} · declared by {entry.submitted_by || '—'}
                {entry.has_variance ? ` · ${entry.total_variance} unit difference` : ' · matches the system'}
              </Text>

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
                    <Text style={{ flex: 2, fontSize: 12.5 }} numberOfLines={1}>{item.product_name}</Text>
                    <Text style={{ width: 52, fontSize: 12.5, textAlign: 'right' }}>{item.loaded_quantity}</Text>
                    <Text style={{ width: 44, fontSize: 12.5, textAlign: 'right' }}>{item.sold_quantity}</Text>
                    <Text style={{ width: 52, fontSize: 12.5, textAlign: 'right' }}>
                      {item.expected_remaining}
                    </Text>
                    <Text style={{
                      width: 52, fontSize: 12.5, textAlign: 'right', fontWeight: '700',
                      color: item.overridden ? COLORS.warning : COLORS.text,
                    }}>
                      {item.declared_remaining}
                    </Text>
                  </View>
                  {item.overridden && (
                    <Text style={{ fontSize: 11, color: COLORS.warning }}>
                      Corrected by {item.override_by}: {item.override_remarks}
                    </Text>
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
                      <Button title="SAVE CORRECTION" small onPress={saveAdjustment}
                        disabled={!adjusting.remarks.trim()} />
                    </View>
                  )}
                </View>
              ))}

              {!!entry.review_remarks && (
                <Text style={[styles.mono, { marginTop: 4 }]}>Review: {entry.review_remarks}</Text>
              )}
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
          {!returns.length && <Text style={styles.empty}>No stock returns submitted yet.</Text>}
        </Card>
      ) : (
      <Card>
        <SectionTitle title="Sales & commission" />
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
                  <Text style={{ fontSize: 13.5, fontWeight: '600', color: COLORS.text }}>
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
              <Text style={styles.empty}>No sales recorded on this date.</Text>
            )}

            {!!(summary.sectors || []).length && (
              <>
                <View style={styles.divider} />
                <SectionTitle title="By sector" />
                {summary.sectors.map((sector) => (
                  <View key={sector.flight_id} style={[styles.listRow, { paddingVertical: 10 }]}>
                    <View style={styles.rowBetween}>
                      <Text style={{ fontSize: 13.5, fontWeight: '700', color: COLORS.text }}>
                        {sector.duty_group_label}
                      </Text>
                      <Pill label={`${sector.member_count} crew`} tone="muted" />
                    </View>
                    <KeyValue label="Sector sales" value={npr(sector.revenue_npr)} />
                    <KeyValue
                      label="Commission earned"
                      value={npr(sector.commission_pool_npr)}
                      tone={COLORS.success}
                    />
                    {sector.sellers.map((seller) => (
                      <View key={seller.crew_member_id} style={[styles.rowBetween, { paddingVertical: 2 }]}>
                        <Text style={{ fontSize: 12.5, flex: 1 }}>{seller.name}</Text>
                        <Text style={[styles.mono, { fontSize: 11 }]}>{npr(seller.sales_npr)}</Text>
                      </View>
                    ))}
                  </View>
                ))}
              </>
            )}
          </>
        ) : (
          <Text style={styles.empty}>Connect to load sales totals.</Text>
        )}
      </Card>
      )}

      {isCrew && (
        pending.length ? pending.map((trip) => (
          <Card key={trip.flight_id}>
            <SectionTitle
              title="End-of-flight stock return"
              description={trip.round_trip
                ? 'The trip was loaded once for both legs, so the leftovers cover the whole trip.'
                : 'What is left on board, worked out from the load and everything sold.'}
              right={<Pill label={trip.round_trip ? 'ROUND TRIP' : 'ONE WAY'} tone="muted" />}
            />

            <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>
              {trip.flight_number}
            </Text>
            <Text style={[styles.mono, { marginBottom: 10 }]}>
              {trip.flight_date} · {(trip.legs || []).map((l) => `${l.origin}→${l.destination}`).join('  |  ')}
            </Text>

            {trip.status === 'REJECTED' && (
              <Banner tone="danger" message={`Sent back: ${trip.review_remarks || 'recount required'}`} />
            )}

            <View style={[styles.rowBetween, { marginBottom: 4 }]}>
              <Text style={[styles.label, { flex: 2, marginBottom: 0 }]}>Product</Text>
              <Text style={[styles.label, { width: 48, textAlign: 'right', marginBottom: 0 }]}>Sold</Text>
              <Text style={[styles.label, { width: 58, textAlign: 'right', marginBottom: 0 }]}>Expected</Text>
              <Text style={[styles.label, { width: 72, textAlign: 'right', marginBottom: 0 }]}>Counted</Text>
            </View>
            {trip.items.map((item) => {
              const typed = (counts[trip.flight_id] || {})[item.product_id];
              const value = typed === undefined ? String(item.expected_remaining) : typed;
              const diff = Number(value || 0) - item.expected_remaining;
              return (
                <View key={item.product_id} style={[styles.rowBetween, { paddingVertical: 4 }]}>
                  <Text style={{ flex: 2, fontSize: 12.5 }} numberOfLines={1}>{item.product_name}</Text>
                  <Text style={{ width: 48, fontSize: 12.5, textAlign: 'right' }}>{item.sold_quantity}</Text>
                  <Text style={{ width: 58, fontSize: 12.5, textAlign: 'right' }}>{item.expected_remaining}</Text>
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

            {trip.submitted ? (
              <Banner
                tone="info"
                message={'This count has been sent to the Inventory Supervisor. Submitting again '
                  + 'replaces it until they approve.'}
              />
            ) : null}

            <Button
              title={online ? 'SUBMIT STOCK RETURN' : 'SUBMIT (will upload on Wi-Fi)'}
              tone={online ? 'primary' : 'warning'}
              style={{ marginTop: 10 }}
              onPress={() => confirmReturn(trip)}
            />
            <Text style={[styles.mono, { marginTop: 6 }]}>
              The Expected column is what the system works out from the load and every sale on the
              trip. Type what is actually in the trolley. Anything that differs is flagged for the
              Inventory Supervisor.
            </Text>
          </Card>
        )) : (
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
                    ? 'Nothing to confirm. A trip appears here once every leg has been finished.'
                    : 'Offline. This will load once the iPad reaches Wi-Fi.'}
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
