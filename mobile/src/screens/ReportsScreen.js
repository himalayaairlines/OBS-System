import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, cached, downloadReport } from '../api';
import { usePaged } from '../paging';
import { confirm, notify } from '../dialog';
import { COLORS, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, Notice, OptionModal, PageHeader, Pagination, Pill,
  SectionTitle, Select,
} from '../ui';

/*
 * The cabin reports filed after a sector.
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
};

const STATUS_LABEL = {
  SUBMITTED: 'SAVED',
  DRAFT: 'DRAFT',
  NOT_STARTED: 'NOT FILLED',
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
export default function ReportsScreen({ user, online, initialFlightId, onHandled, goTo }) {
  const canReview = user.permissions?.includes('reports_review');

  const [forms, setForms] = useState([]);
  const [flights, setFlights] = useState([]);
  const [flightId, setFlightId] = useState(initialFlightId || null);
  const [sector, setSector] = useState(null);       // reports_for_flight payload
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  // The form being filled or read.
  const [openType, setOpenType] = useState(null);
  const [draft, setDraft] = useState({});
  const [editRemarks, setEditRemarks] = useState('');
  const [datePicker, setDatePicker] = useState(null);
  // A report opened from Management's "Filed reports" list.
  const [filedReport, setFiledReport] = useState(null);

  // Management filters
  const [filterDate, setFilterDate] = useState('');
  const [filterFlight, setFilterFlight] = useState('');
  const [filterCalendar, setFilterCalendar] = useState(false);
  const [flightPicker, setFlightPicker] = useState(false);

  const form = forms.find((f) => f.type === openType) || null;
  const sectorReports = sector?.reports || [];
  const outstanding = sector?.outstanding || [];
  const entry = sectorReports.find((r) => r.report_type === openType) || null;
  const report = (filedReport && filedReport.report_type === openType ? filedReport : null)
    || entry?.report || null;
  // Something was changed on screen compared with the saved version.
  const dirty = !!report && JSON.stringify(draft || {}) !== JSON.stringify(report.payload || {});

  /* ------------------------------ data ------------------------------ */
  const loadForms = useCallback(async () => {
    const result = await cached('reportForms', '/report-forms');
    setForms(result.data || []);
  }, []);

  const loadFlights = useCallback(async () => {
    const result = await cached('flights', '/flights');
    const list = result.data || [];
    setFlights(list);
    setFlightId((current) => {
      if (current && list.some((f) => f.id === current)) return current;
      // The crew care about the sector they have just finished and can still fill in.
      const open = list.filter((f) => f.status === 'FLOWN' && !f.reports_locked_at && !f.trip_completed_at);
      if (!canReview) return open[0]?.id ?? null;
      const finished = list.filter((f) => f.status === 'FLOWN');
      return finished[0]?.id ?? list[0]?.id ?? null;
    });
  }, []);

  const loadSector = useCallback(async (id) => {
    if (!id) return setSector(null);
    try {
      const result = await cached(`sectorReports.${id}`, `/flights/${id}/reports`, null);
      setSector(result.data || null);
      return result.data || null;
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, []);

  // Every report ever filed, so the management list is paged on the server.
  const filedParts = [];
  if (filterDate) filedParts.push(`date=${filterDate}`);
  if (filterFlight) filedParts.push(`flight_number=${encodeURIComponent(filterFlight)}`);
  const filedPager = usePaged(`/flight-reports${filedParts.length ? `?${filedParts.join('&')}` : ''}`, { enabled: canReview });
  const filed = filedPager.items;
  const loadFiled = filedPager.reload;

  useEffect(() => { loadForms(); loadFlights(); }, [loadForms, loadFlights]);
  useEffect(() => { loadSector(flightId); }, [flightId, loadSector]);

  /* --------------------------- open a form -------------------------- */
  function openForm(reportType) {
    const definition = forms.find((f) => f.type === reportType);
    if (!definition) return;
    const existing = sector?.reports?.find((r) => r.report_type === reportType)?.report;

    if (existing) {
      setDraft(existing.payload || {});
    } else {
      // Fill in what the system already knows so the crew do not key it twice.
      const known = {
        flight_number: sector?.flight_number,
        flight_date: sector?.flight_date,
        sector: sector?.sector,
        origin: sector?.origin,
        destination: sector?.destination,
        aircraft: sector?.aircraft,
        aircraft_type: sector?.aircraft_type,
      };
      const seeded = {};
      definition.sections.forEach((section) => section.fields.forEach((field) => {
        if (field.prefill && known[field.prefill]) seeded[field.key] = known[field.prefill];
      }));
      setDraft(seeded);
    }
    setEditRemarks('');
    setOpenType(reportType);
  }

  // The crew may fill in and change their sector's forms until they lock
  // (second flight started, or whole flight finished). Changing a saved form
  // needs remarks. Management may always correct, with remarks.
  const locked = !!sector?.locked;
  const crewEditingSaved = !canReview && report?.status === 'SUBMITTED';
  const readOnly = useMemo(() => {
    if (canReview) return false;
    return locked;
  }, [canReview, locked]);

  function setValue(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  /* --------------------------- submitting --------------------------- */
  async function submitForm() {
    const sure = await confirm({
      title: 'Save this form?',
      message: `${form.title} for ${sector.flight_number} ${sector.sector} is saved and sent to `
        + 'Management.\n\nYou can still change it until the next flight is started or the whole '
        + 'flight is finished; each change needs remarks.',
      confirmText: 'Save',
    });
    if (!sure) return;

    setBusy(true);
    try {
      const saved = await api('/flight-reports', {
        method: 'POST',
        body: {
          flight_id: sector.flight_id,
          report_type: openType,
          payload: draft,
          submit: true,
          client_ref: `RPT-${sector.flight_id}-${openType}-${Date.now().toString(36)}`,
        },
      });
      setOpenType(null);
      const fresh = await loadSector(sector.flight_id);
      await loadFiled();
      onHandled?.();
      const left = (fresh?.outstanding || []).length;
      if (!canReview && fresh && left === 0) {
        const next = fresh.trip_type === 'ROUND_TRIP' && fresh.segment === 1
          ? 'Go to the Sales page and press \u201cStart second flight\u201d.'
          : 'Next: submit the stock return on the Sync page, then press \u201cFinish whole flight\u201d on the Sales page.';
        const go = await confirm({
          title: 'All forms saved',
          message: `All forms for ${fresh.sector} are saved and sent to Management.\n\n${next}`,
          confirmText: fresh.trip_type === 'ROUND_TRIP' && fresh.segment === 1 ? 'Go to Sales' : 'Go to Sync',
          cancelText: 'Later',
        });
        if (go) goTo?.(fresh.trip_type === 'ROUND_TRIP' && fresh.segment === 1 ? 'crew' : 'sync');
      } else {
        setMessage({
          tone: 'success',
          text: `${form.title} saved and sent to Management — ${saved.issue_label}, ${saved.rev_label}.`
            + (left ? ` ${left} form(s) still to fill in for this sector.` : ''),
        });
      }
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function unsubmitForm() {
    const sure = await confirm({
      title: 'Pull this report back?',
      message: 'You can do this once. After you submit it again the report is closed to '
        + 'the iPad and only Management can change it.',
      confirmText: 'Pull back',
      tone: 'danger',
    });
    if (!sure) return;
    setBusy(true);
    try {
      await api(`/flight-reports/${report.id}/unsubmit`, { method: 'POST' });
      await loadSector(sector.flight_id);
      setMessage({ tone: 'info', text: `${form.title} is editable again. This was the only pull-back.` });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function saveCorrection(target) {
    if (!editRemarks.trim()) {
      return notify({
        title: 'Remarks required',
        message: 'Say why the report is being corrected. The remarks are kept with it.',
      });
    }
    setBusy(true);
    try {
      const saved = await api(`/flight-reports/${target.id}`, {
        method: 'PUT',
        body: { payload: draft, remarks: editRemarks.trim() },
      });
      setMessage({ tone: 'success', text: canReview
        ? `Correction saved — now ${saved.rev_label}.`
        : `Changes saved — now ${saved.rev_label}. Management can see them.` });
      setOpenType(null);
      setFiledReport(null);
      setEditRemarks('');
      await loadSector(flightId);
      await loadFiled();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  function openFiled(r) {
    setFiledReport(r);
    setFlightId(r.flight_id);
    setDraft(r.payload || {});
    setEditRemarks('');
    setOpenType(r.report_type);
  }

  function closeForm() {
    setOpenType(null);
    setFiledReport(null);
    setEditRemarks('');
  }

  async function downloadPdf() {
    try {
      await downloadReport('', FileSystem, Sharing, `/flight-reports/${report.id}/pdf`);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  /* ------------------------------ view ------------------------------ */
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
              {!!report && report.status === 'SUBMITTED' && (
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
            {!!report && (
              <Pill label={STATUS_LABEL[report.status] || report.status}
                    tone={STATUS_TONE[report.status] || 'muted'} />
            )}
          </View>
          {!!form.statement && (
            <Text style={[styles.mono, { marginTop: 8 }]}>{form.statement}</Text>
          )}
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
          <SectionTitle
            title="Form control"
            description="Issue and revision numbers are allocated by the system. The date is the day the form is filled."
          />
          <View style={styles.wrap}>
            <Pill label={report ? report.issue_label : 'Issue: allocated on submit'} tone="muted" />
            <Pill label={report ? report.rev_label : 'Rev. 00'} tone="muted" />
            <Pill label={`Date: ${footerDate}`} tone="muted" />
          </View>
          {!!report?.edit_remarks && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              Last correction by {report.edited_by}: {report.edit_remarks}
            </Text>
          )}
          {!!report?.revisions?.length && (
            <View style={{ marginTop: 8 }}>
              {report.revisions.map((rev, i) => (
                <Text key={i} style={styles.mono}>
                  Rev {String(rev.rev_no).padStart(2, '0')} · {rev.action.toLowerCase()} by{' '}
                  {rev.changed_by} · {String(rev.changed_at || '').replace('T', ' ').slice(0, 16)}
                  {rev.remarks ? ` · ${rev.remarks}` : ''}
                </Text>
              ))}
            </View>
          )}
        </Card>

        {report && report.status === 'SUBMITTED' && (canReview ? dirty : !locked) && (
          <Card>
            <SectionTitle
              title={canReview ? 'Correct this report' : 'Change this saved form'}
              description="The saved version is kept. Remarks are mandatory and stay with the report."
            />
            <Field
              label="Remarks (mandatory)"
              value={editRemarks}
              onChangeText={setEditRemarks}
              multiline
              placeholder="Why is this being corrected?"
            />
            {!canReview && (
              <Button
                title={busy ? 'Saving…' : 'Save changes'}
                onPress={() => saveCorrection(report)}
                disabled={busy || !online || !dirty}
              />
            )}
          </Card>
        )}

        <Card>
          <View style={[styles.row, styles.gap8]}>
            {canReview && report?.status === 'SUBMITTED' ? (
              dirty ? (
                <>
                  <Button
                    title="Discard changes"
                    tone="ghost"
                    style={styles.flex1}
                    onPress={() => { setDraft(report.payload || {}); setEditRemarks(''); }}
                  />
                  <Button
                    title={busy ? 'Saving…' : 'Save correction'}
                    tone="primary"
                    style={styles.flex1}
                    onPress={() => saveCorrection(report)}
                    disabled={busy || !online || !editRemarks.trim()}
                  />
                </>
              ) : (
                <Button title="Close" tone="ghost" style={styles.flex1} onPress={closeForm} />
              )
            ) : (
              <Button
                title={dirty ? 'Discard changes' : 'Cancel'}
                tone="ghost"
                style={styles.flex1}
                onPress={closeForm}
              />
            )}
            {!readOnly && !crewEditingSaved && !(canReview && report?.status === 'SUBMITTED') && (
              <Button
                title={busy ? 'Saving…' : 'Save'}
                tone="primary"
                style={styles.flex1}
                onPress={submitForm}
                disabled={busy || !online}
              />
            )}

          </View>
          {!online && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              Reports need a connection. Fill this in once the iPad is back on Wi-Fi.
            </Text>
          )}
          {locked && !canReview && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              These forms are locked because the next flight was started or the whole flight
              was finished. Ask Management if something still needs changing.
            </Text>
          )}
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

  // Management see every finished sector; the crew only the ones still open to them.
  const sellable = canReview
    ? flights.filter((f) => f.status !== 'OPEN')
    : flights.filter((f) => f.status === 'FLOWN' && !f.reports_locked_at && !f.trip_completed_at);
  const segmentTitle = !sector ? 'Forms'
    : sector.trip_type === 'ROUND_TRIP'
      ? `Forms (${sector.segment === 2 ? 'second' : 'first'} segment) · ${sector.sector}`
      : `Forms · ${sector.sector}`;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Reports"
        description={canReview
          ? 'Cabin reports filed by the crew after each sector.'
          : 'Complete these after finishing a sector. Each crew files its own set.'}
      />
      {message && <Notice message={message} />}

      {!canReview && (
      <Card>
        <SectionTitle
          title="Sector"
          description="Reports belong to the crew that worked the sector, so each sector of a round trip has its own set."
        />
        <Select
          label="Flight"
          valueLabel={sector ? `${sector.flight_number} · ${sector.sector} · ${sector.flight_date}` : ''}
          placeholder={sellable.length ? 'Select a finished sector'
            : canReview ? 'No finished sector yet' : 'No forms to fill in right now'}
          onPress={() => setFlightPicker(true)}
        />
        {!!sector && (
          <>
            <View style={[styles.wrap, { marginBottom: 6 }]}>
              <Pill label={sector.direction} tone="info" />
              {!!sector.duty_group_label && <Pill label={sector.duty_group_label} tone="muted" />}
              {!!sector.aircraft_type && <Pill label={sector.aircraft_type} tone="muted" />}
              <Pill
                label={outstanding.length
                  ? `${outstanding.length} still to fill in`
                  : 'All forms saved'}
                tone={outstanding.length ? 'warning' : 'success'}
              />
            </View>
            {sector.flight_status === 'OPEN' && (
              <Banner
                tone="warning"
                message="This sector is still selling. Finish it before filing its reports."
              />
            )}
          </>
        )}
      </Card>

      )}

      {!canReview && !!sector && !!sectorReports.length && (
        <Card>
          <SectionTitle
            title={segmentTitle}
            description={sector.locked
              ? 'Locked: the next flight was started or the whole flight was finished.'
              : 'Tap a form to fill it in. Saved forms go to Management straight away.'}
          />
          {sectorReports.map((item) => {
            const r = item.report;
            return (
              <Pressable
                key={item.report_type}
                onPress={() => openForm(item.report_type)}
                style={[styles.listRow, { paddingVertical: 12 }]}
              >
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                    {item.title}
                  </Text>
                  <Pill
                    label={STATUS_LABEL[item.status] || item.status}
                    tone={STATUS_TONE[item.status] || 'muted'}
                  />
                </View>
                <Text style={styles.mono}>
                  {item.form_code} · {item.manual}
                  {r ? ` · ${r.issue_label} · ${r.rev_label} · ${r.report_date}` : ''}
                </Text>
              </Pressable>
            );
          })}
        </Card>
      )}

      {canReview && (
        <Card>
          <SectionTitle
            title="Filed reports"
            description="Filter by date and flight. Open a report to read it or correct it."
          />
          <View style={[styles.row, styles.gap8]}>
            <View style={styles.flex1}>
              <Select
                label="Date"
                valueLabel={filterDate}
                placeholder="Any date"
                onPress={() => setFilterCalendar(true)}
              />
            </View>
            <View style={styles.flex1}>
              <Field
                label="Flight number"
                value={filterFlight}
                onChangeText={setFilterFlight}
                autoCapitalize="characters"
                placeholder="Any flight"
              />
            </View>
          </View>
          {(!!filterDate || !!filterFlight) && (
            <Button
              title="Clear filters"
              tone="ghost"
              small
              onPress={() => { setFilterDate(''); setFilterFlight(''); }}
            />
          )}

          {filed.map((r) => (
            <Pressable
              key={r.id}
              onPress={() => openFiled(r)}
              style={[styles.listRow, { paddingVertical: 12 }]}
            >
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                  {r.title}
                </Text>
                <Pill label={`${r.issue_label} · ${r.rev_label}`} tone="muted" />
              </View>
              <Text style={styles.mono}>
                {r.flight_number} · {r.origin}→{r.destination} · {r.flight_date}
                {r.duty_group_label ? ` · ${r.duty_group_label}` : ''}
                {' · filed by '}{r.submitted_by || r.created_by}
                {r.edited_by ? ` · corrected by ${r.edited_by}` : ''}
              </Text>
            </Pressable>
          ))}
          {!!filedPager.error && <Banner tone="danger" message={filedPager.error} />}
          {!filed.length && !filedPager.loading && (
            <Text style={styles.empty}>
              No reports match. Reports appear here as soon as a crew files them.
            </Text>
          )}
          <Pagination pager={filedPager} label="reports" />
        </Card>
      )}

      <OptionModal
        visible={flightPicker}
        title="Select sector"
        options={sellable.map((f) => ({
          value: f.id,
          label: `${f.flight_number} · ${f.origin}→${f.destination}`,
          sub: `${f.flight_date} · ${f.direction} · ${f.status}`,
        }))}
        selectedValue={flightId}
        onSelect={(value) => { setFlightId(value); setFlightPicker(false); }}
        onClose={() => setFlightPicker(false)}
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
