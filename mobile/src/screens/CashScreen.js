import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Platform, Pressable, ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, apiText } from '../api';
import { usePaged } from '../paging';
import { confirm, notify } from '../dialog';
import { COLORS, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Chip, Field, Notice, PageHeader, Pagination, Pill, RemarkLine,
  SectionTitle, Select,
} from '../ui';

/*
 * The cash sheet of a sector (7.18), laid out like the paper form:
 *
 *   Floating cash balance per crew  — opening and closing, by note
 *   Cash sales collection per crew  — NPR by note + foreign currency lines
 *   Totals per crew                 — collected, commission, net cash
 *   Grand total
 *
 * Inventory hands out the floating cash; each crew member fills their own
 * block. A saved part is closed: only the administrator changes it, with
 * remarks. Once every crew member's phone has synced the sector, the sheet is
 * compared with the sales and any difference is shown at the top for
 * everyone. Inventory (or Management) approves; Finance reads.
 */

const DENOMINATIONS = [1000, 500, 100, 50, 20, 10, 5];
const SECTION_TITLES = {
  float_open: 'Opening floating cash',
  float_close: 'Closing floating cash',
  collection: 'Cash sales collection',
};
const STATUS_TONE = { NONE: 'muted', OPEN: 'warning', APPROVED: 'success' };
const STATUS_LABEL = { NONE: 'NOT STARTED', OPEN: 'IN PROGRESS', APPROVED: 'APPROVED' };

const money = (v) => Number(v || 0).toLocaleString('en-NP', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const denoTotal = (values) => DENOMINATIONS.reduce((sum, d) => sum + d * (parseInt(values?.[d] ?? values?.[String(d)], 10) || 0), 0);
const when = (iso) => String(iso || '').replace('T', ' ').slice(0, 16);

export default function CashScreen({ user, online, focusFlightId, onFocused, onAlertsChanged }) {
  const [flightId, setFlightId] = useState(focusFlightId || null);
  const [message, setMessage] = useState(null);

  // Filters
  const [filterDate, setFilterDate] = useState('');
  const [filterFlight, setFilterFlight] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [calendar, setCalendar] = useState(false);

  useEffect(() => {
    if (focusFlightId) {
      setFlightId(focusFlightId);
      onFocused?.();
    }
  }, [focusFlightId, onFocused]);

  const parts = [];
  if (filterDate) parts.push(`date=${filterDate}`);
  if (filterFlight.trim()) parts.push(`flight_number=${encodeURIComponent(filterFlight.trim())}`);
  if (filterStatus) parts.push(`status=${filterStatus}`);
  const pager = usePaged(`/cash/sheets${parts.length ? `?${parts.join('&')}` : ''}`, { enabled: !flightId && online !== false });

  if (flightId) {
    return (
      <CashSheet
        flightId={flightId}
        user={user}
        online={online}
        onBack={() => { setFlightId(null); pager.reload(); }}
        onAlertsChanged={onAlertsChanged}
      />
    );
  }

  const rows = pager.items || [];
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader title="Cash" description="Floating cash and cash sales collection, sector by sector." />
      {message && <Notice message={message} />}
      {online === false && <Banner tone="warning" message="The cash sheets need a connection." />}

      <Card>
        <View style={[styles.row, styles.gap8]}>
          <View style={styles.flex1}>
            <Select label="Date" valueLabel={filterDate} placeholder="Any date" onPress={() => setCalendar(true)} />
          </View>
          <View style={styles.flex1}>
            <Field label="Flight number" value={filterFlight} onChangeText={setFilterFlight} autoCapitalize="characters" placeholder="Any flight" />
          </View>
        </View>
        <View style={[styles.wrap, { marginBottom: 6 }]}>
          {[['', 'All'], ['OPEN', 'In progress'], ['MISMATCH', 'With differences'], ['APPROVED', 'Approved'], ['NONE', 'Not started']].map(([value, label]) => (
            <Chip key={label} label={label} active={filterStatus === value} onPress={() => setFilterStatus(value)} />
          ))}
        </View>
        {(!!filterDate || !!filterFlight || !!filterStatus) && (
          <Button title="Clear filters" tone="ghost" small onPress={() => { setFilterDate(''); setFilterFlight(''); setFilterStatus(''); }} />
        )}

        {rows.map((r) => (
          <Pressable key={r.id} onPress={() => setFlightId(r.id)} style={[styles.listRow, { paddingVertical: 12 }]}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                {r.flight_number} · {r.origin}→{r.destination} · {r.flight_date}
              </Text>
              {!!r.open_alerts && <Pill label={`${r.open_alerts} difference(s)`} tone="danger" />}
              <View style={{ width: 6 }} />
              <Pill label={STATUS_LABEL[r.sheet_status] || r.sheet_status} tone={STATUS_TONE[r.sheet_status] || 'muted'} />
            </View>
            <Text style={styles.mono}>
              {r.direction} · sales {npr(r.sales_npr)} · {r.collections}/{r.blocks} collection(s) saved
              {r.approved_by ? ` · approved by ${r.approved_by}` : ''}
            </Text>
          </Pressable>
        ))}
        {!!pager.error && <Banner tone="danger" message={pager.error} />}
        {!rows.length && !pager.loading && <Text style={styles.empty}>No sector matches.</Text>}
        <Pagination pager={pager} label="sectors" />
      </Card>

      <CalendarModal
        visible={calendar}
        title="Filter by date"
        value={filterDate || todayString()}
        allowClear
        onSelect={(value) => { setFilterDate(value || ''); setCalendar(false); }}
        onClose={() => setCalendar(false)}
      />
    </ScrollView>
  );
}

/* ------------------------------------------------------------------ */
/* One sector's sheet                                                  */
/* ------------------------------------------------------------------ */
function CashSheet({ flightId, user, online, onBack, onAlertsChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [approveRemarks, setApproveRemarks] = useState('');
  const [fixRemarks, setFixRemarks] = useState({});

  const load = useCallback(async () => {
    try {
      setData(await api(`/cash/flights/${flightId}`));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [flightId]);
  useEffect(() => { load(); }, [load]);

  const isCrew = ['crew', 'crew_supervisor'].includes(user.role);
  // Rostered crew without a block yet: an empty block to start from. Built
  // once per load so the editors below keep what is typed into them.
  const blocks = useMemo(() => {
    if (!data) return [];
    const approvedSheet = data.status === 'APPROVED';
    const pendingBlocks = (data.pending_crew || []).map((p) => {
      const sold = (data.unblocked_sales || []).find((u) => u.crew_account === p.crew_account);
      const mine = !isCrew || p.crew_account === user.employee_id;
      const can = data.may_fill && mine && (!approvedSheet || data.is_admin);
      return {
        ...p, id: `new-${p.crew_account}`, isNew: true,
        float_open: {}, float_close: {}, npr_collect: {}, fcy_collect: [],
        float_open_total: 0, float_close_total: 0, npr_total: 0, fcy_npr_total: 0, total_collected: 0,
        commission: 0, net_cash: 0, differences: [],
        saved: { float_open: false, float_close: false, collection: false },
        editable: { float_open: can, float_close: can, collection: can },
        system: sold || { currencies: {}, npr_value: 0, transactions: 0 },
        system_suggestion: {
          fcy: Object.entries(sold?.currencies || {}).filter(([c]) => c !== 'NPR').map(([c, v]) => ({ currency: c, amount: v.amount, rate: v.rate })),
          npr_total: sold?.currencies?.NPR?.amount || 0,
        },
      };
    });
    return [...data.blocks, ...pendingBlocks];
  }, [data, isCrew, user.employee_id]);

  async function saveBlock(block, section, denominations, fcy, remarks) {
    setBusy(true);
    try {
      const result = await api(`/cash/flights/${flightId}/blocks`, {
        method: 'PUT',
        body: {
          crew_account: block.crew_account, crew_name: block.crew_name, staff_no: block.staff_no, section,
          denominations, fcy: section === 'collection' ? fcy : [], remarks: remarks || null,
        },
      });
      setData(result);
      setMessage({ tone: 'success', text: `${SECTION_TITLES[section]} of ${block.crew_name} saved.` });
      onAlertsChanged?.();
      setBusy(false);
      return true;
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
      setBusy(false);
      return false;
    }
  }

  async function approve() {
    const open = (data.alerts || []).filter((a) => !a.closed_at).length;
    if (open && !approveRemarks.trim()) {
      return notify({ title: 'Remarks required', message: 'The cash does not match the sales. Explain the difference in the remarks to approve.' });
    }
    const sure = await confirm({
      title: 'Approve this cash sheet?',
      message: `${data.flight.flight_number} ${data.flight.origin}-${data.flight.destination} ${data.flight.flight_date}: `
        + `net cash ${npr(data.totals.net_cash)}. After approval only the administrator can change it.`,
      confirmText: 'Approve',
    });
    if (!sure) return;
    setBusy(true);
    try {
      setData(await api(`/cash/flights/${flightId}/approve`, { method: 'POST', body: { remarks: approveRemarks.trim() || null } }));
      setApproveRemarks('');
      setMessage({ tone: 'success', text: 'Cash sheet approved.' });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  async function fixAlert(alert) {
    const remarks = (fixRemarks[alert.id] || '').trim();
    if (remarks.length < 2) {
      return notify({ title: 'Remarks required', message: 'Say why this difference is being cleared. The notice stays in the history.' });
    }
    setBusy(true);
    try {
      await api(`/cash/alerts/${alert.id}/fix`, { method: 'POST', body: { remarks } });
      setFixRemarks((r) => ({ ...r, [alert.id]: '' }));
      await load();
      onAlertsChanged?.();
      setMessage({ tone: 'success', text: 'Notice cleared. It is kept in the history below.' });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  async function print() {
    try {
      if (Platform.OS === 'web') {
        // Opened first, so the browser does not treat it as an unrequested pop-up.
        const win = window.open('', '_blank');
        const html = await apiText(`/cash/flights/${flightId}/print`);
        if (!win) throw new Error('The browser blocked the print window. Allow pop-ups for this site and try again.');
        win.document.open();
        win.document.write(html);
        win.document.close();
        win.focus();
        setTimeout(() => win.print(), 300);
      } else {
        const html = await apiText(`/cash/flights/${flightId}/print`);
        const uri = `${FileSystem.cacheDirectory}cash-sheet-${flightId}-${Date.now()}.html`;
        await FileSystem.writeAsStringAsync(uri, html);
        if (await Sharing.isAvailableAsync()) await Sharing.shareAsync(uri, { mimeType: 'text/html' });
      }
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (!data) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
        <PageHeader title="Cash sheet" right={<Button title="Back" tone="ghost" small onPress={onBack} />} />
        {error ? <Banner tone="danger" message={error} /> : <Text style={styles.empty}>Loading…</Text>}
      </ScrollView>
    );
  }

  const f = data.flight;
  const approved = data.status === 'APPROVED';
  const openAlerts = (data.alerts || []).filter((a) => !a.closed_at);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title={`Cash · ${f.flight_number} ${f.origin}-${f.destination}`}
        description={`${f.flight_date} · ${f.direction}${data.sn ? ` · S.N. ${data.sn}` : ''}`}
        right={(
          <View style={[styles.row, styles.gap8]}>
            <Button title="Print" tone="secondary" small onPress={print} disabled={!data.sheet_id} />
            <Button title="Back" tone="ghost" small onPress={onBack} />
          </View>
        )}
      />
      {message && <Notice message={message} />}

      <Card>
        <View style={styles.wrap}>
          <Pill label={STATUS_LABEL[data.status] || data.status} tone={STATUS_TONE[data.status] || 'muted'} />
          <Pill label={`Commission ${(data.commission_rate * 100).toFixed(0)}%`} tone="muted" />
          {data.all_uploaded
            ? <Pill label="All phones synced" tone="success" />
            : <Pill label={`Waiting for ${data.waiting_for.length} phone(s)`} tone="info" />}
        </View>
        {!data.all_uploaded && (
          <Text style={[styles.mono, { marginTop: 6 }]}>
            The sheet is compared with the sales once every crew member on the sector has synced
            {data.waiting_for.length ? ` (still: ${data.waiting_for.join(', ')})` : ''}.
          </Text>
        )}
        {approved && (
          <Text style={[styles.mono, { marginTop: 6 }]}>
            Approved by {data.approved_by} · {when(data.approved_at)}. Only the administrator can change it now.
          </Text>
        )}
        {!!data.approval_remarks && <RemarkLine label="Approval remarks" text={data.approval_remarks} />}
        {!data.may_fill && <Text style={[styles.mono, { marginTop: 6 }]}>Read only.</Text>}
      </Card>

      {openAlerts.map((alert) => (
        <Card key={alert.id} style={{ borderColor: COLORS.danger, borderWidth: 1 }}>
          <Banner tone="danger" message={alert.message} />
          {data.is_admin && (
            <View style={{ marginTop: 6 }}>
              <Field label="Remarks to clear this notice" value={fixRemarks[alert.id] || ''}
                onChangeText={(v) => setFixRemarks((r) => ({ ...r, [alert.id]: v }))} placeholder="What was wrong?" />
              <Button title="Fix" tone="secondary" small onPress={() => fixAlert(alert)} disabled={busy} style={{ alignSelf: 'flex-start' }} />
            </View>
          )}
        </Card>
      ))}

      {blocks.map((block, index) => (
        <CrewBlock key={block.id} index={index + 1} block={block} busy={busy} isAdmin={data.is_admin}
          allUploaded={data.all_uploaded} onSave={saveBlock} />
      ))}
      {!blocks.length && (
        <Card><Text style={styles.empty}>No crew is assigned to this sector yet. Inventory assigns the crew first.</Text></Card>
      )}

      <Card>
        <SectionTitle title="Grand total" />
        <TotalRow label="Opening floating cash" value={data.totals.float_open} />
        <TotalRow label="Closing floating cash" value={data.totals.float_close} />
        <TotalRow label="Total cash collected (NPR)" value={data.totals.collected} strong />
        <TotalRow label="Crew commission" value={data.totals.commission} />
        <TotalRow label="Net cash" value={data.totals.net_cash} strong />
        <TotalRow label="Sales recorded by the phones (NPR value)" value={data.totals.system_npr_value} />
      </Card>

      {data.may_approve && (
        <Card>
          <SectionTitle title="Approve" description="Every crew block needs a saved collection. With a difference open, remarks are required." />
          <Field label={openAlerts.length ? 'Remarks (required)' : 'Remarks (optional)'} value={approveRemarks}
            onChangeText={setApproveRemarks} multiline />
          <Button title={busy ? 'Approving…' : 'Approve cash sheet'} onPress={approve} disabled={busy || !data.sheet_id} />
        </Card>
      )}

      {!!data.events?.length && (
        <Card>
          <SectionTitle title="History" description="Every save, correction, difference and approval, newest first." />
          {data.events.map((ev) => (
            <View key={ev.id} style={{ marginBottom: 6 }}>
              <Text style={styles.mono}>
                {when(ev.at)} · {ev.action.toLowerCase()} by {ev.by}{ev.crew_account ? ` · ${ev.crew_account}` : ''}
                {(() => { try { const d = JSON.parse(ev.detail || '{}'); return d.section ? ` · ${SECTION_TITLES[d.section] || d.section}` : ''; } catch (e) { return ''; } })()}
              </Text>
              {!!ev.remarks && <RemarkLine text={ev.remarks} style={{ marginTop: 2 }} />}
            </View>
          ))}
          {(data.alerts || []).filter((a) => a.closed_at).map((a) => (
            <Text key={`a${a.id}`} style={styles.mono}>
              {when(a.closed_at)} · {a.close_reason === 'FIXED' ? `cleared by ${a.closed_by}` : 'matched'}: {a.message}
              {a.close_remarks ? ` (${a.close_remarks})` : ''}
            </Text>
          ))}
        </Card>
      )}
    </ScrollView>
  );
}

function TotalRow({ label, value, strong }) {
  return (
    <View style={[styles.rowBetween, { paddingVertical: 4 }]}>
      <Text style={{ fontSize: 14, color: COLORS.text, fontWeight: strong ? '700' : '400' }}>{label}</Text>
      <Text style={{ fontSize: 14, color: COLORS.text, fontWeight: strong ? '700' : '500' }}>{money(value)}</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ */
/* One crew member's block                                             */
/* ------------------------------------------------------------------ */
function CrewBlock({ index, block, busy, isAdmin, allUploaded, onSave }) {
  return (
    <Card>
      <SectionTitle
        title={`Crew ${index}: ${block.crew_name}`}
        description={`Staff No. ${block.staff_no || block.crew_account}${block.isNew ? ' · nothing entered yet' : ''}`}
      />
      <DenoSection block={block} section="float_open" values={block.float_open} busy={busy} isAdmin={isAdmin} onSave={onSave} />
      <DenoSection block={block} section="float_close" values={block.float_close} busy={busy} isAdmin={isAdmin} onSave={onSave} />
      <CollectionSection block={block} busy={busy} isAdmin={isAdmin} allUploaded={allUploaded} onSave={onSave} />

      <View style={{ marginTop: 8, borderTopWidth: 1, borderTopColor: COLORS.border, paddingTop: 8 }}>
        <TotalRow label="Total cash collected (NPR)" value={block.total_collected} strong />
        <TotalRow label="Crew commission" value={block.commission} />
        <TotalRow label="Net cash" value={block.net_cash} strong />
        {!!block.system?.transactions && (
          <Text style={styles.mono}>Phones recorded {block.system.transactions} sale(s) worth {npr(block.system.npr_value)} under this login.</Text>
        )}
      </View>
      {!!block.differences?.length && (
        <View style={{ marginTop: 6 }}>
          {block.differences.map((d) => (
            <Banner key={d.currency} tone="danger"
              message={`${d.currency}: entered ${money(d.entered)}, phones recorded ${money(d.system)} (difference ${d.difference > 0 ? '+' : ''}${money(d.difference)}).`} />
          ))}
        </View>
      )}
      {!!block.remarks && <RemarkLine label="Remarks" text={block.remarks} style={{ marginTop: 6 }} />}
    </Card>
  );
}

function SavedLine({ by, at }) {
  if (!at) return null;
  return <Text style={styles.mono}>Saved by {by} · {when(at)}</Text>;
}

function DenoGrid({ values, onChange, editable }) {
  return (
    <View>
      <View style={[styles.row, { paddingVertical: 2 }]}>
        <Text style={[styles.mono, { flex: 1 }]}>Note</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'center' }]}>Qty</Text>
        <Text style={[styles.mono, { flex: 1.4, textAlign: 'right' }]}>Amount</Text>
      </View>
      {DENOMINATIONS.map((d) => {
        const qty = values[d] ?? values[String(d)] ?? '';
        return (
          <View key={d} style={[styles.row, { alignItems: 'center', paddingVertical: 2 }]}>
            <Text style={{ flex: 1, fontSize: 14, color: COLORS.text }}>{d}</Text>
            {editable ? (
              <TextInput
                value={String(qty)}
                onChangeText={(v) => onChange({ ...values, [d]: v.replace(/[^0-9]/g, '').slice(0, 6) })}
                keyboardType="number-pad"
                accessibilityLabel={`Number of ${d} notes`}
                style={[styles.input, { flex: 1, textAlign: 'center', paddingVertical: 4, marginHorizontal: 4 }]}
              />
            ) : (
              <Text style={{ flex: 1, textAlign: 'center', fontSize: 14, color: COLORS.text }}>{qty || '–'}</Text>
            )}
            <Text style={{ flex: 1.4, textAlign: 'right', fontSize: 14, color: COLORS.text }}>
              {qty ? money(d * (parseInt(qty, 10) || 0)) : ''}
            </Text>
          </View>
        );
      })}
      <View style={[styles.rowBetween, { paddingTop: 4 }]}>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>Total</Text>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>{money(denoTotal(values))}</Text>
      </View>
    </View>
  );
}

const asCounts = (values) => {
  const out = {};
  DENOMINATIONS.forEach((d) => {
    const n = parseInt(values?.[d] ?? values?.[String(d)], 10);
    if (n > 0) out[String(d)] = n;
  });
  return out;
};

/** Editing a saved part: administrator only, with remarks. */
function useSectionEdit(block, section, isAdmin) {
  const saved = !!block.saved?.[section];
  const editable = !!block.editable?.[section];
  const [editing, setEditing] = useState(!saved && editable);
  const [remarks, setRemarks] = useState('');
  useEffect(() => { setEditing(!saved && editable); setRemarks(''); }, [saved, editable, block.id]);
  return { saved, editable, editing, setEditing, remarks, setRemarks, correcting: saved && isAdmin };
}

function DenoSection({ block, section, values, busy, isAdmin, onSave }) {
  const edit = useSectionEdit(block, section, isAdmin);
  const [draft, setDraft] = useState(values || {});
  useEffect(() => { setDraft(values || {}); }, [values]);

  async function save() {
    if (edit.correcting && !edit.remarks.trim()) {
      return notify({ title: 'Remarks required', message: 'Say why the saved figures are being changed.' });
    }
    const sure = await confirm({
      title: `Save the ${SECTION_TITLES[section].toLowerCase()}?`,
      message: `${block.crew_name}: ${npr(denoTotal(draft))}.${edit.correcting ? '' : ' After saving only the administrator can change it.'}`,
      confirmText: 'Save',
    });
    if (!sure) return;
    if (await onSave(block, section, asCounts(draft), [], edit.remarks.trim())) edit.setEditing(false);
  }

  return (
    <View style={{ marginBottom: 10 }}>
      <View style={styles.rowBetween}>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>{SECTION_TITLES[section]}</Text>
        {edit.saved ? <Pill label="SAVED" tone="success" /> : <Pill label="NOT SAVED" tone="muted" />}
      </View>
      <SavedLine by={block[`${section}_by`]} at={block[`${section}_at`]} />
      <DenoGrid values={draft} onChange={setDraft} editable={edit.editing} />
      {edit.editing ? (
        <>
          {edit.correcting && <Field label="Remarks (mandatory)" value={edit.remarks} onChangeText={edit.setRemarks} multiline />}
          <View style={[styles.row, styles.gap8, { marginTop: 4 }]}>
            {edit.saved && <Button title="Cancel" tone="ghost" small onPress={() => { setDraft(values || {}); edit.setEditing(false); }} />}
            <Button title={busy ? 'Saving…' : 'Save'} small onPress={save} disabled={busy} />
          </View>
        </>
      ) : (edit.saved && isAdmin && edit.editable) ? (
        <Button title="Correct (admin)" tone="ghost" small onPress={() => edit.setEditing(true)} style={{ alignSelf: 'flex-start' }} />
      ) : null}
    </View>
  );
}

function CollectionSection({ block, busy, isAdmin, allUploaded, onSave }) {
  const edit = useSectionEdit(block, 'collection', isAdmin);
  const suggestion = block.system_suggestion || { fcy: [], npr_total: 0 };
  const initialFcy = () => (block.saved?.collection ? block.fcy_collect : suggestion.fcy)
    .map((x) => ({ currency: x.currency, amount: String(x.amount ?? ''), rate: String(x.rate ?? '') }));
  const [notes, setNotes] = useState(block.npr_collect || {});
  const [fcy, setFcy] = useState(initialFcy);
  useEffect(() => { setNotes(block.npr_collect || {}); setFcy(initialFcy()); }, [block]); // eslint-disable-line react-hooks/exhaustive-deps

  const fcyNpr = fcy.reduce((s, x) => s + (Number(x.amount) || 0) * (Number(x.rate) || 0), 0);
  const nprTotal = denoTotal(notes);
  const prefilled = !block.saved?.collection && !!suggestion.fcy.length;
  const changedFromSystem = !block.saved?.collection && allUploaded
    && (Math.abs(nprTotal - (suggestion.npr_total || 0)) > 0.5
      || suggestion.fcy.some((s) => {
        const line = fcy.find((x) => x.currency === s.currency);
        return !line || Math.abs((Number(line.amount) || 0) - s.amount) > 0.5;
      }));

  function setLine(i, key, value) {
    setFcy((lines) => lines.map((l, j) => (j === i ? {
      ...l, [key]: key === 'currency' ? value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3) : value.replace(/[^0-9.]/g, ''),
    } : l)));
  }

  async function save() {
    const bad = fcy.find((x) => (x.currency || x.amount) && (x.currency.length !== 3 || x.currency === 'NPR' || !(Number(x.rate) > 0)));
    if (bad) return notify({ title: 'Check the currency lines', message: 'Each line needs a 3-letter foreign currency, an amount and an exchange rate above 0.' });
    const needRemarks = edit.correcting || changedFromSystem;
    if (needRemarks && !edit.remarks.trim()) {
      return notify({ title: 'Remarks required', message: edit.correcting
        ? 'Say why the saved collection is being changed.'
        : 'The figures differ from what the phones recorded. Explain the difference in the remarks.' });
    }
    const sure = await confirm({
      title: 'Save the cash sales collection?',
      message: `${block.crew_name}: NPR notes ${money(nprTotal)} + foreign currency ${money(fcyNpr)} = ${npr(nprTotal + fcyNpr)}.`
        + (edit.correcting ? '' : ' After saving only the administrator can change it.'),
      confirmText: 'Save',
    });
    if (!sure) return;
    const lines = fcy.filter((x) => Number(x.amount) > 0).map((x) => ({ currency: x.currency, amount: Number(x.amount), rate: Number(x.rate) }));
    if (await onSave(block, 'collection', asCounts(notes), lines, edit.remarks.trim())) edit.setEditing(false);
  }

  return (
    <View style={{ marginBottom: 6 }}>
      <View style={styles.rowBetween}>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>{SECTION_TITLES.collection}</Text>
        {edit.saved ? <Pill label="SAVED" tone="success" /> : <Pill label="NOT SAVED" tone="muted" />}
      </View>
      <SavedLine by={block.collection_by} at={block.collection_at} />
      {edit.editing && !edit.saved && (
        <Text style={styles.mono}>
          Phones recorded: NPR {money(suggestion.npr_total)}
          {suggestion.fcy.map((s) => ` · ${s.currency} ${money(s.amount)}`).join('')}
          {prefilled ? ' (foreign currency filled in from the sales; change it if the cash differs)' : ''}
        </Text>
      )}
      <Text style={[styles.mono, { marginTop: 4 }]}>NPR notes</Text>
      <DenoGrid values={notes} onChange={setNotes} editable={edit.editing} />

      <Text style={[styles.mono, { marginTop: 8 }]}>Foreign currency (FCY)</Text>
      <View style={[styles.row, { paddingVertical: 2 }]}>
        <Text style={[styles.mono, { flex: 1 }]}>Currency</Text>
        <Text style={[styles.mono, { flex: 1.2, textAlign: 'right' }]}>Amount</Text>
        <Text style={[styles.mono, { flex: 1.2, textAlign: 'right' }]}>Rate</Text>
        <Text style={[styles.mono, { flex: 1.4, textAlign: 'right' }]}>NPR</Text>
      </View>
      {fcy.map((line, i) => (
        <View key={i} style={[styles.row, { alignItems: 'center', paddingVertical: 2 }]}>
          {edit.editing ? (
            <>
              <TextInput value={line.currency} onChangeText={(v) => setLine(i, 'currency', v)} autoCapitalize="characters"
                accessibilityLabel="Currency" style={[styles.input, { flex: 1, paddingVertical: 4, marginRight: 4 }]} />
              <TextInput value={line.amount} onChangeText={(v) => setLine(i, 'amount', v)} keyboardType="decimal-pad"
                accessibilityLabel="Amount" style={[styles.input, { flex: 1.2, textAlign: 'right', paddingVertical: 4, marginRight: 4 }]} />
              <TextInput value={line.rate} onChangeText={(v) => setLine(i, 'rate', v)} keyboardType="decimal-pad"
                accessibilityLabel="Exchange rate" style={[styles.input, { flex: 1.2, textAlign: 'right', paddingVertical: 4 }]} />
            </>
          ) : (
            <>
              <Text style={{ flex: 1, fontSize: 14, color: COLORS.text }}>{line.currency}</Text>
              <Text style={{ flex: 1.2, textAlign: 'right', fontSize: 14, color: COLORS.text }}>{money(line.amount)}</Text>
              <Text style={{ flex: 1.2, textAlign: 'right', fontSize: 14, color: COLORS.text }}>{line.rate}</Text>
            </>
          )}
          <Text style={{ flex: 1.4, textAlign: 'right', fontSize: 14, color: COLORS.text }}>
            {money((Number(line.amount) || 0) * (Number(line.rate) || 0))}
          </Text>
          {edit.editing && (
            <Pressable onPress={() => setFcy((lines) => lines.filter((_, j) => j !== i))} accessibilityLabel="Remove line" hitSlop={6}>
              <Text style={{ color: COLORS.danger, marginLeft: 6, fontSize: 16 }}>×</Text>
            </Pressable>
          )}
        </View>
      ))}
      {!fcy.length && <Text style={styles.mono}>No foreign currency.</Text>}
      {edit.editing && (
        <Button title="Add currency line" tone="ghost" small style={{ alignSelf: 'flex-start' }}
          onPress={() => setFcy((lines) => [...lines, { currency: '', amount: '', rate: '' }])} />
      )}
      <View style={[styles.rowBetween, { paddingTop: 4 }]}>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>FCY total (NPR)</Text>
        <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>{money(fcyNpr)}</Text>
      </View>

      {edit.editing ? (
        <>
          {(edit.correcting || changedFromSystem) && (
            <Field label="Remarks (mandatory)" value={edit.remarks} onChangeText={edit.setRemarks} multiline
              placeholder={edit.correcting ? 'Why is this being changed?' : 'Why does the cash differ from the sales?'} />
          )}
          <View style={[styles.row, styles.gap8, { marginTop: 4 }]}>
            {edit.saved && <Button title="Cancel" tone="ghost" small onPress={() => { setNotes(block.npr_collect || {}); setFcy(initialFcy()); edit.setEditing(false); }} />}
            <Button title={busy ? 'Saving…' : 'Save collection'} small onPress={save} disabled={busy} />
          </View>
        </>
      ) : (edit.saved && isAdmin && edit.editable) ? (
        <Button title="Correct (admin)" tone="ghost" small onPress={() => edit.setEditing(true)} style={{ alignSelf: 'flex-start' }} />
      ) : null}
    </View>
  );
}
