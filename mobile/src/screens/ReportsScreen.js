import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, cached, downloadReport, readCache, writeCache } from '../api';
import {
  applyJournalToFlights, applyJournalToSector, buildLocalSector, journalLeg, readJournal, tripAction,
} from '../tripLocal';
import { usePaged } from '../paging';
import { confirm, notify } from '../dialog';
import { COLORS, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Chip, Field, Notice, OptionModal, PageHeader, Pagination, Pill,
  RemarkLine, SectionTitle, Select,
} from '../ui';
import { SectorStockList } from './StockCards';

/*
 * The cabin reports.
 *
 * The forms are not written into this screen. The server serves the schema of
 * each controlled form — its sections, headings and fields — and everything
 * below renders from that, so a change to a form in the manual is a change to
 * one definition rather than to the application.
 */

const STATUS_TONE = {
  SUBMITTED: 'success',
  DRAFT: 'warning',
  NOT_STARTED: 'danger',
  PENDING: 'danger',
  AWAITING_OTHER: 'info',
};

const STATUS_LABEL = {
  SUBMITTED: 'SUBMITTED',
  DRAFT: 'SAVED · NOT SUBMITTED',
  NOT_STARTED: 'NOT FILLED',
  PENDING: 'PENDING',
  AWAITING_OTHER: 'COLLEAGUE WILL FILL',
};

/* ------------------------------------------------------------------ */
/* One field of a form                                                 */
/* ------------------------------------------------------------------ */
function FormField({ field, value, onChange, readOnly, onPickDate }) {
  if (field.type === 'note') {
    return (
      <Text style={[styles.mono, { marginBottom: 6, fontStyle: 'italic' }]}>{field.text}</Text>
    );
  }

  if (field.type === 'textarea') {
    return (
      <Field
        label={field.label}
        value={value || ''}
        onChangeText={onChange}
        editable={!readOnly}
        multiline
        numberOfLines={field.lines || 4}
        style={{ minHeight: Math.min(26 * (field.lines || 4), 190) }}
      />
    );
  }

  if (field.type === 'date') {
    return (
      <Select
        label={field.label}
        valueLabel={value || ''}
        placeholder={readOnly ? '—' : 'Choose date'}
        onPress={() => !readOnly && onPickDate(field.key, value)}
      />
    );
  }

  if (field.type === 'time') {
    return (
      <Field
        label={`${field.label} (hh:mm)`}
        value={value || ''}
        onChangeText={(text) => onChange(text.replace(/[^0-9:]/g, '').slice(0, 5))}
        editable={!readOnly}
        placeholder="07:35"
        keyboardType="numbers-and-punctuation"
      />
    );
  }

  if (field.type === 'checks' || field.type === 'choice') {
    const selected = field.type === 'checks' ? (value || []) : value;
    const isOn = (option) => (field.type === 'checks'
      ? (selected || []).includes(option)
      : selected === option);
    const toggle = (option) => {
      if (readOnly) return;
      if (field.type === 'choice') return onChange(selected === option ? '' : option);
      const current = value || [];
      onChange(current.includes(option)
        ? current.filter((o) => o !== option)
        : [...current, option]);
    };
    return (
      <View style={{ marginBottom: 12 }}>
        <Text style={styles.label}>{field.label}</Text>
        <View style={styles.wrap}>
          {field.options.map((option) => (
            <Pressable
              key={option}
              onPress={() => toggle(option)}
              style={{
                paddingVertical: 8,
                paddingHorizontal: 12,
                borderRadius: 8,
                borderWidth: 1,
                borderColor: isOn(option) ? COLORS.primary : COLORS.border,
                backgroundColor: isOn(option) ? COLORS.primarySoft : COLORS.surface,
                opacity: readOnly && !isOn(option) ? 0.5 : 1,
              }}
            >
              <Text style={{
                fontSize: 13,
                color: isOn(option) ? COLORS.primary : COLORS.text,
                fontWeight: isOn(option) ? '700' : '400',
              }}
              >
                {isOn(option) ? '☑  ' : '☐  '}{option}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>
    );
  }

  if (field.type === 'checklist') {
    const ticked = value || [];
    const toggle = (item) => {
      if (readOnly) return;
      onChange(ticked.includes(item) ? ticked.filter((i) => i !== item) : [...ticked, item]);
    };
    const total = field.groups.reduce((n, g) => n + g.items.length, 0);
    return (
      <View style={{ marginBottom: 12 }}>
        <View style={styles.rowBetween}>
          <Text style={styles.label}>{field.label}</Text>
          <Pill
            label={`${ticked.length} of ${total} checked`}
            tone={ticked.length === total ? 'success' : 'warning'}
          />
        </View>
        {field.groups.map((group) => (
          <View key={group.title} style={{ marginTop: 10 }}>
            <Text style={{
              fontSize: 13, fontWeight: '600', color: COLORS.text,
              backgroundColor: COLORS.surfaceAlt, paddingVertical: 5, paddingHorizontal: 8,
            }}
            >
              {group.title}
            </Text>
            {group.items.map((item) => {
              const on = ticked.includes(item);
              return (
                <Pressable
                  key={item}
                  onPress={() => toggle(item)}
                  style={{
                    flexDirection: 'row',
                    alignItems: 'flex-start',
                    paddingVertical: 9,
                    paddingHorizontal: 8,
                    borderBottomWidth: 1,
                    borderBottomColor: COLORS.surfaceAlt,
                  }}
                >
                  <Text style={{ fontSize: 15, color: on ? COLORS.primary : COLORS.faint, width: 26 }}>
                    {on ? '☑' : '☐'}
                  </Text>
                  <Text style={{ flex: 1, fontSize: 13, color: COLORS.text, lineHeight: 18 }}>
                    {item}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        ))}
      </View>
    );
  }

  if (field.type === 'rows') {
    const rows = value || [];
    const setCell = (index, columnKey, text) => {
      if (readOnly) return;
      const next = Array.from({ length: field.count }, (_, i) => ({ ...(rows[i] || {}) }));
      next[index] = { ...next[index], [columnKey]: text };
      onChange(next);
    };
    return (
      <View style={{ marginBottom: 12 }}>
        <Text style={styles.label}>{field.label}</Text>
        {Array.from({ length: field.count }, (_, index) => (
          <View key={index} style={[styles.row, { marginBottom: 8 }]}>
            <Text style={{ width: 28, fontSize: 13, color: COLORS.muted }}>{index + 1}.</Text>
            {field.columns.map((column) => (
              <TextInput
                key={column.key}
                value={(rows[index] || {})[column.key] || ''}
                onChangeText={(text) => setCell(index, column.key, text)}
                editable={!readOnly}
                placeholder={column.label}
                placeholderTextColor={COLORS.faint}
                style={[styles.input, { flex: 1, marginBottom: 0 }]}
              />
            ))}
          </View>
        ))}
      </View>
    );
  }

  // text
  return (
    <Field
      label={field.label}
      value={value || ''}
      onChangeText={onChange}
      editable={!readOnly}
    />
  );
}


/* ------------------------------------------------------------------ */
/* The screen                                                          */
/* ------------------------------------------------------------------ */
/*
 * 7.18: the forms are outside the flight flow. A crew member picks any date
 * and sector they flew, at any time (also after logging out and in again).
 * Only one crew member needs to fill the forms: they save each form (a draft
 * they can still change) and press "Final submit" once; the others press
 * "Another crew member will fill these forms". After the final submission
 * the forms are read-only for the crew; who submitted is in the audit log.
 *
 * Management / Admin / Inventory see the forms trip by trip
 * ("KTM-DXB-KTM (date)") with every leg and form, and "Pending" where a form
 * is still missing. Inventory reads only; Admin alone switches forms off.
 */
const sectorLabel = (s) => `${s.flight_number} · ${s.origin}-${s.destination} · ${s.flight_date}`;

export default function ReportsScreen({ user, online, initialFlightId, onHandled }) {
  const perms = user.permissions || [];
  const canReview = perms.includes('reports_review');
  const isReader = !canReview && !perms.includes('flight_reports') && perms.includes('reports_view');
  const isCrew = !canReview && !isReader;

  const [forms, setForms] = useState([]);
  const [sectors, setSectors] = useState([]);        // crew: every sector they flew
  const [flightId, setFlightId] = useState(initialFlightId || null);
  const [sector, setSector] = useState(null);       // reports_for_flight payload
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  // The form being filled or read.
  const [openType, setOpenType] = useState(null);
  const [draft, setDraft] = useState({});
  const [editRemarks, setEditRemarks] = useState('');
  const [datePicker, setDatePicker] = useState(null);
  const [filedReport, setFiledReport] = useState(null);   // opened from the trips list

  // Filters
  const [filterDate, setFilterDate] = useState('');
  const [filterFlight, setFilterFlight] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterCalendar, setFilterCalendar] = useState(false);
  const [sectorPicker, setSectorPicker] = useState(false);
  const [expanded, setExpanded] = useState({});

  const form = forms.find((f) => f.type === openType) || null;
  const sectorReports = sector?.reports || [];
  const entry = sectorReports.find((r) => r.report_type === openType) || null;
  const report = (filedReport && filedReport.report_type === openType ? filedReport : null) || entry?.report || null;
  const dirty = JSON.stringify(draft || {}) !== JSON.stringify(report?.payload || {});
  const rootId = sector ? (sector.direction === 'RETURN' && sector.linked_flight_id ? sector.linked_flight_id : sector.flight_id) : null;

  /* ------------------------------ data ------------------------------ */
  const loadForms = useCallback(async () => {
    try {
      const result = await cached('reportForms', '/report-forms');
      setForms(result.data || []);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, []);

  // Crew: the sectors they were rostered on, cached so the list also shows offline.
  const loadSectors = useCallback(async () => {
    if (!isCrew) return;
    try {
      const result = await cached('reportSectors', '/flight-reports/my-sectors', []);
      let list = result.data || [];
      // Sectors assigned since the last time online are in the flights cache.
      const flights = applyJournalToFlights((await readCache('flights', [])) || [], await readJournal());
      const known = new Set(list.map((s) => s.id));
      flights.filter((f) => !known.has(f.id)).forEach((f) => list.push({
        id: f.id, flight_number: f.flight_number, flight_date: f.flight_date, origin: f.origin,
        destination: f.destination, direction: f.direction, trip_type: f.trip_type, linked_flight_id: f.linked_flight_id,
        status: f.status, sector: `${f.origin}-${f.destination}`, offline: true,
      }));
      list = list.sort((a, b) => String(b.flight_date).localeCompare(String(a.flight_date)) || a.id - b.id);
      setSectors(list);
      setFlightId((current) => (current && list.some((s) => s.id === current) ? current
        : (list.find((s) => s.flight_date === todayString()) || list[0])?.id ?? null));
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [isCrew]);

  // The server copy when there is one, otherwise the cached copy, otherwise a
  // list built on the phone; this phone's own saves on top.
  const loadSector = useCallback(async (id) => {
    if (!id || !isCrew) return setSector(null);
    try {
      const result = await cached(`sectorReports.${id}`, `/flights/${id}/reports`, null);
      let data = result.data || null;
      const journal = await readJournal();
      if (!data) {
        const flight = ((await readCache('flights', [])) || []).find((f) => f.id === id)
          || sectors.find((s) => s.id === id);
        const defs = forms.length ? forms : ((await readCache('reportForms', [])) || []);
        data = flight ? buildLocalSector(flight, defs, user.employee_id) : null;
      }
      data = applyJournalToSector(data, journal, user.employee_id);
      if (data) {
        const known = sectors.find((s) => s.id === id);
        if (known?.linked_flight_id) data = { ...data, linked_flight_id: known.linked_flight_id };
      }
      setSector(data);
      return data;
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [isCrew, forms, sectors, user.employee_id]);

  // Management / Inventory / Admin: trip by trip, paged on the server.
  const tripParts = [];
  if (filterDate) tripParts.push(`date=${filterDate}`);
  if (filterFlight.trim()) tripParts.push(`flight_number=${encodeURIComponent(filterFlight.trim())}`);
  if (filterStatus) tripParts.push(`status=${filterStatus}`);
  const tripPager = usePaged(`/flight-reports/trips${tripParts.length ? `?${tripParts.join('&')}` : ''}`, { enabled: !isCrew });

  useEffect(() => { loadForms(); loadSectors(); }, [loadForms, loadSectors]);
  useEffect(() => { if (isCrew) loadSector(flightId); }, [isCrew, flightId, loadSector]);

  /* --------------------------- open a form -------------------------- */
  function seedFor(definition, info) {
    const known = {
      flight_number: info?.flight_number, flight_date: info?.flight_date, sector: info?.sector
        || (info?.origin ? `${info.origin}-${info.destination}` : undefined),
      origin: info?.origin, destination: info?.destination, aircraft: info?.aircraft, aircraft_type: info?.aircraft_type,
    };
    const seeded = {};
    definition.sections.forEach((section) => section.fields.forEach((field) => {
      if (field.prefill && known[field.prefill]) seeded[field.key] = known[field.prefill];
    }));
    return seeded;
  }

  function openForm(reportType) {
    const definition = forms.find((f) => f.type === reportType);
    if (!definition) return;
    const existing = sectorReports.find((r) => r.report_type === reportType)?.report;
    setDraft(existing ? (existing.payload || {}) : seedFor(definition, sector));
    setEditRemarks('');
    setFiledReport(null);
    setOpenType(reportType);
  }

  async function openFiled(leg, item) {
    if (!item.report_id) {
      return notify({ title: `${item.title}: pending`, message: item.status === 'AWAITING_OTHER'
        ? `Left to another crew member by ${item.deferred_by.join(', ')}. It has not been filled yet.`
        : 'Nobody has filled this form for the sector yet.' });
    }
    try {
      const r = await api(`/flight-reports/${item.report_id}`);
      setSector({ flight_id: leg.id, flight_number: leg.flight_number, flight_date: leg.flight_date, origin: leg.origin,
        destination: leg.destination, sector: leg.sector, duty_group_label: r.duty_group_label });
      setFiledReport(r);
      setDraft(r.payload || {});
      setEditRemarks('');
      setOpenType(r.report_type);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  function closeForm() {
    setOpenType(null);
    setFiledReport(null);
    setEditRemarks('');
  }

  // Crew: editable until the final submission. Management: corrections with
  // remarks. Inventory: read only.
  const readOnly = isReader || (isCrew && (report?.status === 'SUBMITTED' || sector?.read_only));

  function setValue(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  /* ----------------------------- crew ------------------------------- */
  async function refreshAfter(result) {
    if (result && result.flight_id) await writeCache(`sectorReports.${result.flight_id}`, result);
    await loadSector(sector.flight_id);
    if (online) await loadSectors();
    onHandled?.();
  }

  /** Save one form as a draft. It can still be changed until the final submit. */
  async function saveDraft() {
    setBusy(true);
    try {
      const body = {
        flight_id: sector.flight_id, report_type: openType, payload: draft, submit: false,
        client_ref: `RPT-${sector.flight_id}-${openType}-${Date.now().toString(36)}`,
      };
      // Saved on the phone first; it uploads on Wi-Fi if there is no signal.
      const { queued } = await tripAction({ endpoint: '/flight-reports', body, label: `${form.title} (saved)`, flightId: sector.flight_id });
      await journalLeg(rootId, sector.flight_id, (leg) => ({
        ...leg,
        forms: { ...(leg.forms || {}), [openType]: 'draft' },
        payloads: { ...(leg.payloads || {}), [openType]: draft },
      }));
      setOpenType(null);
      await loadSector(sector.flight_id);
      setMessage({ tone: 'success', text: `${form.title} saved${queued ? ' on this phone (uploads on Wi-Fi)' : ''}. `
        + 'You can still change it. Press “Final submit” when all forms are done.' });
      onHandled?.();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function finalSubmit() {
    const drafts = sectorReports.filter((r) => r.status === 'DRAFT');
    const missing = sectorReports.filter((r) => r.status !== 'SUBMITTED' && r.status !== 'DRAFT');
    const sure = await confirm({
      title: 'Final submit?',
      message: `${drafts.map((d) => d.title).join(', ')} for ${sectorLabel(sector)} will be submitted to Management.\n\n`
        + 'After this they can no longer be changed by the crew. Your name is recorded as the submitter.'
        + (missing.length ? `\n\nNot filled yet (stay pending): ${missing.map((m) => m.title).join(', ')}.` : ''),
      confirmText: 'Final submit',
      tone: 'danger',
    });
    if (!sure) return;
    setBusy(true);
    try {
      const { result, queued } = await tripAction({ endpoint: '/flight-reports/final-submit', body: { flight_id: sector.flight_id },
        label: `Final submit ${sector.flight_number} ${sector.sector}`, flightId: sector.flight_id });
      await journalLeg(rootId, sector.flight_id, (leg) => {
        const marks = { ...(leg.forms || {}) };
        drafts.forEach((d) => { marks[d.report_type] = 'mine'; });
        return { ...leg, forms: marks };
      });
      await refreshAfter(result);
      setMessage({ tone: 'success', text: queued
        ? 'Final submission saved on this phone. It uploads on Wi-Fi.'
        : 'Forms submitted to Management. They are now read-only.' });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function leaveToColleague() {
    const sure = await confirm({
      title: 'Another crew member will fill these forms?',
      message: `You will not fill the forms of ${sectorLabel(sector)}. They stay pending until the colleague submits them.`,
      confirmText: 'Yes, another crew member will',
    });
    if (!sure) return;
    setBusy(true);
    try {
      const { result } = await tripAction({ endpoint: '/flight-reports/defer-all', body: { flight_id: sector.flight_id },
        label: `Forms left to a colleague · ${sector.flight_number}`, flightId: sector.flight_id });
      await journalLeg(rootId, sector.flight_id, { defer_all_at: new Date().toISOString() });
      await refreshAfter(result);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function takeBack() {
    setBusy(true);
    try {
      const { result } = await tripAction({ method: 'DELETE', endpoint: `/flight-reports/defer-all?flight_id=${sector.flight_id}`,
        label: `Take the forms back · ${sector.flight_number}`, flightId: sector.flight_id });
      await journalLeg(rootId, sector.flight_id, (leg) => {
        const marks = { ...(leg.forms || {}) };
        Object.keys(marks).forEach((k) => { if (marks[k] === 'other') delete marks[k]; });
        return { ...leg, forms: marks, defer_all_at: null };
      });
      if (!result) await writeCache(`sectorReports.${sector.flight_id}`, null);
      await refreshAfter(result);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  /* -------------------------- management ---------------------------- */
  async function saveCorrection() {
    if (!editRemarks.trim()) {
      return notify({ title: 'Remarks required', message: 'Say why the report is being corrected. The remarks are kept with it.' });
    }
    setBusy(true);
    try {
      const saved = await api(`/flight-reports/${report.id}`, { method: 'PUT', body: { payload: draft, remarks: editRemarks.trim() } });
      setMessage({ tone: 'success', text: `Correction saved — now ${saved.rev_label}.` });
      closeForm();
      await tripPager.reload();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function downloadPdf() {
    try {
      await downloadReport('', FileSystem, Sharing, `/flight-reports/${report.id}/pdf`);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  /* --------------------------- form view ---------------------------- */
  if (openType && form) {
    const footerDate = report?.report_date || todayString();
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
        <PageHeader
          title={form.title}
          description={`${form.manual} · ${form.appendix}`}
          right={(
            <View style={[styles.row, styles.gap8]}>
              <Pill label={form.form_code} tone="muted" />
              {!!report && report.status === 'SUBMITTED' && !report.local && (
                <Button title="Download PDF" tone="secondary" small onPress={downloadPdf} disabled={!online} />
              )}
            </View>
          )}
        />
        {message && <Notice message={message} />}

        <Card>
          <View style={styles.wrap}>
            <Pill label={sector?.flight_number || ''} tone="info" />
            <Pill label={sector?.sector || ''} tone="muted" />
            <Pill label={sector?.flight_date || ''} tone="muted" />
            {!!sector?.duty_group_label && <Pill label={sector.duty_group_label} tone="muted" />}
            {!!report && <Pill label={STATUS_LABEL[report.status] || report.status} tone={STATUS_TONE[report.status] || 'muted'} />}
          </View>
          {!!form.statement && <Text style={[styles.mono, { marginTop: 8 }]}>{form.statement}</Text>}
          {isCrew && report?.status === 'SUBMITTED' && (
            <Banner tone="info" message={`Final submitted${report.submitted_by ? ` by ${report.submitted_by}` : ''}. Read only; ask Management if something needs correcting.`} />
          )}
          {isReader && <Banner tone="info" message="Read only. Management corrects forms; Admin switches forms on or off." />}
        </Card>

        {form.sections.map((section) => (
          <Card key={section.title}>
            <SectionTitle title={section.title} description={section.description} />
            {section.fields.map((field, index) => (
              <FormField
                key={field.key || `note-${index}`}
                field={field}
                value={draft[field.key]}
                onChange={(value) => setValue(field.key, value)}
                readOnly={readOnly}
                onPickDate={(key, current) => setDatePicker({ key, value: current })}
              />
            ))}
          </Card>
        ))}

        <Card>
          <SectionTitle title="Form control" description="Issue and revision numbers are allocated by the system. The date is the day the form is filled." />
          <View style={styles.wrap}>
            <Pill label={report?.issue_label || 'Issue: allocated on save'} tone="muted" />
            <Pill label={report?.rev_label || 'Rev. 00'} tone="muted" />
            <Pill label={`Date: ${footerDate}`} tone="muted" />
          </View>
          {!!report?.submitted_by && report.status === 'SUBMITTED' && (
            <Text style={[styles.mono, { marginTop: 6 }]}>
              Submitted by {report.submitted_by}{report.submitted_at ? ` · ${String(report.submitted_at).replace('T', ' ').slice(0, 16)}` : ''}
            </Text>
          )}
          {!!report?.edit_remarks && (
            <RemarkLine label={`Last correction by ${report.edited_by}`} text={report.edit_remarks} style={{ marginTop: 8 }} />
          )}
          {!!report?.revisions?.length && (
            <View style={{ marginTop: 8 }}>
              {report.revisions.map((rev, i) => (
                <View key={i}>
                  <Text style={styles.mono}>
                    Rev {String(rev.rev_no).padStart(2, '0')} · {String(rev.action || '').toLowerCase()} by{' '}
                    {rev.changed_by} · {String(rev.changed_at || '').replace('T', ' ').slice(0, 16)}
                  </Text>
                  {!!rev.remarks && <RemarkLine text={rev.remarks} style={{ marginTop: 2, marginBottom: 4 }} />}
                </View>
              ))}
            </View>
          )}
        </Card>

        {canReview && !!report && dirty && (
          <Card>
            <SectionTitle title="Correct this report" description="The saved version is kept. Remarks are mandatory and stay with the report." />
            <Field label="Remarks (mandatory)" value={editRemarks} onChangeText={setEditRemarks} multiline placeholder="Why is this being corrected?" />
          </Card>
        )}

        <Card>
          <View style={[styles.row, styles.gap8]}>
            {canReview ? (
              dirty ? (
                <>
                  <Button title="Discard changes" tone="ghost" style={styles.flex1}
                    onPress={() => { setDraft(report?.payload || {}); setEditRemarks(''); }} />
                  <Button title={busy ? 'Saving…' : 'Save correction'} tone="primary" style={styles.flex1}
                    onPress={saveCorrection} disabled={busy || !online || !editRemarks.trim() || !report} />
                </>
              ) : (
                <Button title="Close" tone="ghost" style={styles.flex1} onPress={closeForm} />
              )
            ) : (
              <>
                <Button title={!readOnly && dirty ? 'Discard changes' : 'Close'} tone="ghost" style={styles.flex1} onPress={closeForm} />
                {!readOnly && (
                  <Button title={busy ? 'Saving…' : 'Save'} tone="primary" style={styles.flex1} onPress={saveDraft} disabled={busy} />
                )}
              </>
            )}
          </View>
          {isCrew && !readOnly && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              {online ? 'Saving keeps this form editable. Press “Final submit” on the list when every form is done.'
                : 'No signal: the form is saved on this phone and uploads on Wi-Fi.'}
            </Text>
          )}
          {canReview && !online && <Text style={[styles.mono, { marginTop: 8 }]}>Corrections need a connection.</Text>}
        </Card>

        <CalendarModal
          visible={!!datePicker}
          title="Select date"
          value={datePicker?.value || todayString()}
          onSelect={(value) => { setValue(datePicker.key, value); setDatePicker(null); }}
          onClose={() => setDatePicker(null)}
        />
      </ScrollView>
    );
  }

  /* ---------------------------- crew view --------------------------- */
  if (isCrew) {
    const shown = filterDate ? sectors.filter((s) => s.flight_date === filterDate) : sectors;
    const drafts = sectorReports.filter((r) => r.status === 'DRAFT');
    const open = sectorReports.filter((r) => r.status !== 'SUBMITTED');
    const allDone = !!sectorReports.length && !open.length;
    const leftByMe = !!sector?.left_to_others || sectorReports.some((r) => r.deferred_by_me);
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
        <PageHeader title="Reports" description="Fill the cabin forms any time: choose the date and sector. Only one crew member needs to fill them." />
        {message && <Notice message={message} />}

        <Card>
          <SectionTitle title="Date and sector" description="Every sector you were rostered on is listed, also after the flight is over." />
          <View style={[styles.row, styles.gap8]}>
            <View style={styles.flex1}>
              <Select label="Date" valueLabel={filterDate} placeholder="Any date" onPress={() => setFilterCalendar(true)} />
            </View>
            <View style={styles.flex1}>
              <Select label="Sector" valueLabel={sector ? sectorLabel(sector) : ''}
                placeholder={shown.length ? 'Select a sector' : 'No sector on this date'} onPress={() => setSectorPicker(true)} />
            </View>
          </View>
          {!!filterDate && <Button title="Any date" tone="ghost" small onPress={() => setFilterDate('')} />}
          {!!sector && (
            <View style={[styles.wrap, { marginTop: 6 }]}>
              <Pill label={sector.direction || ''} tone="info" />
              {!!sector.duty_group_label && <Pill label={sector.duty_group_label} tone="muted" />}
              <Pill label={allDone ? 'All forms submitted' : `${open.length} not submitted`} tone={allDone ? 'success' : 'warning'} />
              {!!drafts.length && <Pill label={`${drafts.length} saved, not submitted`} tone="warning" />}
            </View>
          )}
        </Card>

        {!!sector && !!sectorReports.length && (
          <Card>
            <SectionTitle title={`Forms · ${sector.sector}`}
              description="Tap a form, fill it in and save it. Saved forms can still be changed until the final submit." />
            {leftByMe && !allDone && (
              <Banner tone="info" message="You left these forms to another crew member. They stay pending until that colleague submits them." />
            )}
            {sectorReports.map((item) => {
              const r = item.report;
              const status = item.status === 'NOT_STARTED' ? 'NOT_STARTED' : item.status;
              return (
                <Pressable key={item.report_type} onPress={() => openForm(item.report_type)} style={[styles.listRow, { paddingVertical: 12 }]}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>{item.title}</Text>
                    <Pill label={STATUS_LABEL[status] || status} tone={STATUS_TONE[status] || 'muted'} />
                  </View>
                  <Text style={styles.mono}>
                    {item.form_code}
                    {r && r.status === 'SUBMITTED' && r.submitted_by ? ` · submitted by ${r.submitted_by}` : ''}
                    {r && r.status === 'DRAFT' ? ' · saved, can still be changed' : ''}
                    {item.status === 'AWAITING_OTHER' ? ' · another crew member will fill it' : ''}
                  </Text>
                </Pressable>
              );
            })}
            {!allDone && (
              <View style={{ marginTop: 12 }}>
                <Button
                  title={busy ? 'Submitting…' : drafts.length ? `Final submit (${drafts.length} saved form${drafts.length > 1 ? 's' : ''})` : 'Final submit'}
                  tone="primary" onPress={finalSubmit} disabled={busy || !drafts.length} />
                {!drafts.length && <Text style={[styles.mono, { marginTop: 4 }]}>Save at least one form first.</Text>}
                {leftByMe ? (
                  <Button title="I will fill them after all" tone="ghost" style={{ marginTop: 8 }} onPress={takeBack} disabled={busy} />
                ) : (
                  <Button title="Another crew member will fill these forms" tone="secondary" style={{ marginTop: 8 }}
                    onPress={leaveToColleague} disabled={busy} />
                )}
              </View>
            )}
            {!online && <Text style={[styles.mono, { marginTop: 8 }]}>No signal: everything is saved on this phone and uploads on Wi-Fi.</Text>}
          </Card>
        )}
        {!sector && <Text style={styles.empty}>{sectors.length ? 'Select a sector.' : 'No sector has been assigned to you yet.'}</Text>}

        <OptionModal
          visible={sectorPicker}
          title="Select sector"
          options={shown.map((s) => ({
            value: s.id,
            label: `${s.flight_number} · ${s.origin}→${s.destination}`,
            sub: `${s.flight_date} · ${s.direction}${s.total ? ` · ${s.submitted}/${s.total} submitted` : ''}${s.left_to_others ? ' · left to colleague' : ''}`,
          }))}
          selectedValue={flightId}
          onSelect={(value) => { setFlightId(value); setSectorPicker(false); }}
          onClose={() => setSectorPicker(false)}
        />
        <CalendarModal
          visible={filterCalendar}
          title="Filter by date"
          value={filterDate || todayString()}
          allowClear
          onSelect={(value) => { setFilterDate(value || ''); setFilterCalendar(false); }}
          onClose={() => setFilterCalendar(false)}
        />
      </ScrollView>
    );
  }

  /* ----------------------- management / inventory ------------------- */
  const trips = tripPager.items || [];
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader title="Reports" description={isReader
        ? 'Cabin forms trip by trip. Read only.'
        : 'Cabin forms trip by trip. Open a form to read or correct it.'} />
      {message && <Notice message={message} />}

      {perms.includes('users') && <FormsVisibilityCard />}

      <Card>
        <SectionTitle title="Forms by trip" description="Each trip with its legs and forms. Pending = not submitted yet." />
        <View style={[styles.row, styles.gap8]}>
          <View style={styles.flex1}>
            <Select label="Date" valueLabel={filterDate} placeholder="Any date" onPress={() => setFilterCalendar(true)} />
          </View>
          <View style={styles.flex1}>
            <Field label="Flight number" value={filterFlight} onChangeText={setFilterFlight} autoCapitalize="characters" placeholder="Any flight" />
          </View>
        </View>
        <View style={[styles.wrap, { marginBottom: 6 }]}>
          {[['', 'All'], ['PENDING', 'With pending forms'], ['COMPLETE', 'Complete']].map(([value, label]) => (
            <Chip key={label} label={label} active={filterStatus === value} onPress={() => setFilterStatus(value)} />
          ))}
        </View>
        {(!!filterDate || !!filterFlight || !!filterStatus) && (
          <Button title="Clear filters" tone="ghost" small onPress={() => { setFilterDate(''); setFilterFlight(''); setFilterStatus(''); }} />
        )}

        {trips.map((trip) => {
          const isOpen = !!expanded[trip.root_id];
          return (
            <View key={trip.root_id} style={[styles.listRow, { paddingVertical: 10 }]}>
              <Pressable onPress={() => setExpanded((e) => ({ ...e, [trip.root_id]: !e[trip.root_id] }))}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text, flex: 1 }}>
                    {isOpen ? '▾ ' : '▸ '}{trip.label}
                  </Text>
                  <Pill label={trip.pending ? `${trip.pending} pending` : 'Complete'} tone={trip.pending ? 'warning' : 'success'} />
                </View>
                <Text style={styles.mono}>{trip.flight_numbers}</Text>
              </Pressable>
              {isOpen && trip.legs.map((leg) => (
                <View key={leg.id} style={{ marginTop: 8, marginLeft: 12 }}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                      {leg.flight_number} · {leg.sector} · {leg.flight_date}
                    </Text>
                    <Pill label={leg.pending ? `${leg.pending} pending` : 'All submitted'} tone={leg.pending ? 'warning' : 'success'} />
                  </View>
                  {!!leg.original_destination && (
                    <Text style={[styles.mono, { color: COLORS.warning }]}>Diverted: planned to {leg.original_destination}</Text>
                  )}
                  {leg.forms.map((item) => (
                    <Pressable key={item.report_type} onPress={() => openFiled(leg, item)} style={[styles.rowBetween, { paddingVertical: 6 }]}>
                      <Text style={{ fontSize: 13, color: COLORS.text, flex: 1 }}>
                        {item.title}{item.switched_off ? ' (switched off)' : ''}
                      </Text>
                      <Pill
                        label={item.status === 'PENDING' ? 'PENDING' : (STATUS_LABEL[item.status] || item.status)}
                        tone={item.status === 'PENDING' ? 'danger' : (STATUS_TONE[item.status] || 'muted')} />
                    </Pressable>
                  ))}
                  {leg.forms.some((f) => f.submitted_by) && (
                    <Text style={styles.mono}>
                      Submitted by {Array.from(new Set(leg.forms.filter((f) => f.submitted_by).map((f) => f.submitted_by))).join(', ')}
                    </Text>
                  )}
                </View>
              ))}
            </View>
          );
        })}
        {!!tripPager.error && <Banner tone="danger" message={tripPager.error} />}
        {!trips.length && !tripPager.loading && <Text style={styles.empty}>No trip matches.</Text>}
        <Pagination pager={tripPager} label="trips" />
      </Card>

      <SectorStockList />

      <CalendarModal
        visible={filterCalendar}
        title="Filter by date"
        value={filterDate || todayString()}
        allowClear
        onSelect={(value) => { setFilterDate(value || ''); setFilterCalendar(false); }}
        onClose={() => setFilterCalendar(false)}
      />
    </ScrollView>
  );
}


/* ------------------------------------------------------------------ */
/* Administrator: which forms the crew see (7.13)                      */
/* ------------------------------------------------------------------ */
function FormsVisibilityCard() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api('/report-forms/visibility').then((r) => { setRows(r); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function toggle(type) {
    const next = rows.map((r) => (r.type === type ? { ...r, enabled: !r.enabled } : r));
    const enabled = next.filter((r) => r.enabled).map((r) => r.type);
    const turningOff = rows.find((r) => r.type === type)?.enabled;
    if (turningOff) {
      const sure = await confirm({
        title: 'Switch this form off for the crew?',
        message: `${rows.find((r) => r.type === type)?.title} disappears from every crew iPad and no longer counts towards \u201call forms handled\u201d. Forms already filed stay with Management.`,
        confirmText: 'Switch off',
        tone: 'danger',
      });
      if (!sure) return;
    }
    setBusy(true);
    try {
      setRows(await api('/report-forms/visibility', { method: 'PUT', body: { enabled } }));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <Card>
      <SectionTitle
        title="Forms shown to the crew"
        description="A form switched on is on every crew iPad; switched off, it is on none. The change is recorded in the audit log."
      />
      {!!error && <Banner tone="danger" message={error} />}
      {rows.map((r) => (
        <View key={r.type} style={[styles.listRow, styles.rowBetween, { paddingVertical: 10 }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>{r.title}</Text>
            <Text style={styles.mono}>{r.form_code}</Text>
          </View>
          <Pill label={r.enabled ? 'ON FOR CREW' : 'OFF'} tone={r.enabled ? 'success' : 'muted'} />
          <Button title={r.enabled ? 'Switch off' : 'Switch on'} tone={r.enabled ? 'ghost' : 'secondary'} small style={{ marginLeft: 8 }} disabled={busy} onPress={() => toggle(r.type)} />
        </View>
      ))}
      {!rows.length && !error && <Text style={styles.empty}>Loading\u2026</Text>}
    </Card>
  );
}
