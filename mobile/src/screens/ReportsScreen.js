import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, cached, downloadReport, writeCache } from '../api';
import { usePaged } from '../paging';
import { confirm, notify } from '../dialog';
import { COLORS, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, Notice, OptionModal, PageHeader, Pagination, Pill,
  RemarkLine, SectionTitle, Select,
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
  AWAITING_OTHER: 'info',
};

const STATUS_LABEL = {
  SUBMITTED: 'SAVED',
  DRAFT: 'DRAFT',
  NOT_STARTED: 'NOT FILLED',
  AWAITING_OTHER: 'OTHER iPAD',
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
// A sector the iPad is working: selling now, or finished but not yet locked.
const crewSector = (f) => !f.trip_completed_at && !f.reports_locked_at
  && (f.status === 'FLOWN' || (f.status === 'OPEN' && (f.direction !== 'RETURN' || !!f.started_at)));

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
      // The crew care about the sector they are selling on or have just finished.
      const open = list.filter((f) => crewSector(f));
      if (!canReview) return (open.find((f) => f.status === 'FLOWN') || open[0])?.id ?? null;
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
        const roundFirst = fresh.trip_type === 'ROUND_TRIP' && fresh.segment === 1;
        const go = await confirm({
          title: 'All forms handled',
          message: `All forms for ${fresh.sector} are saved and sent to Management.\n\n`
            + (roundFirst
              ? 'Next: go to the Sales page and press \u201cStart second flight\u201d (both iPads must have confirmed the remaining stock).'
              : 'Next: press \u201cFinish whole flight\u201d on the Sales page (both iPads must have submitted the final stock return).'),
          confirmText: 'Go to Sales',
          cancelText: 'Later',
        });
        if (go) goTo?.('crew');
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

  /** "Submitted by the other iPad" (7.11): this device leaves the form to the other one. */
  async function deferForm(item) {
    const sure = await confirm({
      title: 'Submitted by the other iPad?',
      message: `${item.title} will be left to ${item.other_device || 'the other iPad'}. It counts as handled for this trip; `
        + 'it shows as SAVED once that iPad uploads it, and Management sees it as awaiting the other iPad until then.',
      confirmText: 'Yes, the other iPad has it',
    });
    if (!sure) return;
    try {
      await api('/flight-reports/defer', { method: 'POST', body: { flight_id: sector.flight_id, report_type: item.report_type } });
      await writeCache(`sectorReports.${sector.flight_id}`, null);
      await loadSector(sector.flight_id);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function undeferForm(reportType) {
    try {
      await api(`/flight-reports/defer?flight_id=${sector.flight_id}&report_type=${encodeURIComponent(reportType)}`, { method: 'DELETE' });
      await writeCache(`sectorReports.${sector.flight_id}`, null);
      await loadSector(sector.flight_id);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
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

  /** Moved here from the Sales page (7.9): the sector is finished right above its forms. */
  async function finishFlight() {
    if (!sector) return;
    const roundFirst = sector.trip_type === 'ROUND_TRIP' && sector.segment === 1;
    const sure = await confirm({
      title: 'Finish this flight?',
      message: `${sector.flight_number} ${sector.sector} will be finished.\n\n`
        + 'No further sale can be recorded on this sector and it cannot be reopened. '
        + 'The sales already recorded are kept.'
        + (roundFirst ? '\n\nThe returning crew will enter their own names before selling.' : ''),
      confirmText: 'Finish this flight',
      tone: 'danger',
    });
    if (!sure) return;
    setBusy(true);
    try {
      const result = await api(`/flights/${sector.flight_id}/finish`, { method: 'PUT' });
      await writeCache(`sectorReports.${sector.flight_id}`, null);
      await loadFlights();
      await loadSector(sector.flight_id);
      const left = result.reports_outstanding ?? 0;
      await notify({
        title: 'Sector finished',
        message: `${sector.flight_number}: ${result.transactions} transaction(s), ${npr(result.revenue_npr)}.\n\n`
          + 'Next, in order: 1) confirm the remaining stock on this iPad, 2) see the commission, 3) the forms'
          + (left ? ` (${left} of ${result.reports_total} still to handle on this sector)` : ' (all already saved)')
          + ', then the final step on the Sales page.',
      });
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
            <RemarkLine label={`Last correction by ${report.edited_by}`} text={report.edit_remarks} style={{ marginTop: 8 }} />
          )}
          {!!report?.revisions?.length && (
            <View style={{ marginTop: 8 }}>
              {report.revisions.map((rev, i) => (
                <View key={i}>
                  <Text style={styles.mono}>
                    Rev {String(rev.rev_no).padStart(2, '0')} · {rev.action.toLowerCase()} by{' '}
                    {rev.changed_by} · {String(rev.changed_at || '').replace('T', ' ').slice(0, 16)}
                  </Text>
                  {!!rev.remarks && <RemarkLine text={rev.remarks} style={{ marginTop: 2, marginBottom: 4 }} />}
                </View>
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
    : flights.filter(crewSector);
  const segmentTitle = !sector ? 'Forms'
    : sector.trip_type === 'ROUND_TRIP'
      ? `${sector.flight_status === 'OPEN' ? '' : '3. '}Forms (${sector.segment === 2 ? 'second' : 'first'} segment) · ${sector.sector}`
      : `${sector.flight_status === 'OPEN' ? '' : '3. '}Forms · ${sector.sector}`;

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
                tone="info"
                message="This sector is still selling. The forms can be filled in now or after you finish it; anything still open is reminded at finish."
              />
            )}
          </>
        )}
      </Card>

      )}

      {!canReview && !!sector && sector.flight_status === 'OPEN' && (
        <Card>
          <SectionTitle
            title="Finish this flight"
            description="Press this when the sector is over. Selling stops on this sector and the forms below open for filling in."
          />
          <Button
            title={busy ? 'Finishing\u2026' : `Finish this flight \u00b7 ${sector.flight_number} ${sector.sector}`}
            tone="warning"
            onPress={finishFlight}
            disabled={busy || !online}
          />
          {!online && <Text style={[styles.mono, { marginTop: 6 }]}>Needs a connection.</Text>}
        </Card>
      )}

      {!canReview && !!sector && sector.flight_status !== 'OPEN' && (
        sector.trip_type === 'ROUND_TRIP' && sector.segment === 1 ? (
          <SectorStockCard flightId={sector.flight_id} online={online} locked={locked} goTo={goTo} user={user} />
        ) : (
          <FinalReturnCard flightId={sector.flight_id} online={online} goTo={goTo} user={user} />
        )
      )}

      {!canReview && !!sector && sector.flight_status !== 'OPEN' && (
        <CommissionCard flightId={sector.flight_id} online={online} user={user} />
      )}

      {!canReview && !!sector && !!sectorReports.length && (
        <Card>
          <SectionTitle
            title={segmentTitle}
            description={sector.locked
              ? 'Locked: the next flight was started or the whole flight was finished.'
              : 'Tap a form to fill it in, before or after the sector is finished. Saved forms go to Management straight away.'}
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
                  {item.status === 'AWAITING_OTHER' ? ` · marked as submitted by ${item.other_device || 'the other iPad'}` : ''}
                </Text>
                {!sector.locked && item.status !== 'SUBMITTED' && sector.flight_status !== 'OPEN' && (
                  <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
                    {item.status === 'AWAITING_OTHER' && item.deferred_by_me ? (
                      <Button title="Take it back on this iPad" tone="ghost" small onPress={() => undeferForm(item.report_type)} />
                    ) : item.status !== 'AWAITING_OTHER' ? (
                      <>
                        <Button title="Fill in on this iPad" tone="secondary" small onPress={() => openForm(item.report_type)} />
                        <Button title={`Submitted by ${item.other_device || 'the other iPad'}`} tone="ghost" small onPress={() => deferForm(item)} />
                      </>
                    ) : null}
                  </View>
                )}
              </Pressable>
            );
          })}
          {!!(sector.awaiting_other || []).length && (
            <Banner tone="info" message={`${sector.awaiting_other.length} form(s) are marked as submitted by the other iPad. They turn to SAVED when that iPad uploads them.`} />
          )}
          {!outstanding.length && sector.flight_status !== 'OPEN' && !sector.locked && (
            <Banner
              tone="success"
              message={sector.trip_type === 'ROUND_TRIP' && sector.segment === 1
                ? 'All forms handled. Next: go to Sales and press \u201cStart second flight\u201d once both iPads have confirmed the remaining stock.'
                : 'All forms handled. Next: press \u201cFinish whole flight\u201d on the Sales page once both iPads have submitted the final stock return.'}
            />
          )}
        </Card>
      )}

      {canReview && <SectorStockList />}

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
              {!!r.edit_remarks && <RemarkLine label={`Corrected by ${r.edited_by}`} text={r.edit_remarks} />}
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


/* ------------------------------------------------------------------ */
/* Remaining stock after a sector (7.9)                                */
/* ------------------------------------------------------------------ */
function SectorStockCard({ flightId, online, locked, goTo, user }) {
  const me = user?.employee_id;
  const [position, setPosition] = useState(null);
  const [declared, setDeclared] = useState({});
  const [remarks, setRemarks] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // What THIS iPad should hold: the system figure less what the other iPad declared.
  const mineDefault = (p, i) => {
    const others = Object.entries(i.by_device || {}).filter(([d]) => d !== me).reduce((a, [, q]) => a + Number(q || 0), 0);
    const otherIn = (p.submitted_devices || []).some((d) => d !== me);
    return otherIn ? Math.max(0, i.expected_remaining - others) : i.expected_remaining;
  };

  const load = useCallback(async () => {
    try {
      const p = await api(`/flights/${flightId}/sector-stock`);
      setPosition(p);
      setDeclared(Object.fromEntries(p.items.map((i) => [i.product_id,
        String((i.by_device || {})[me] ?? mineDefault(p, i))])));
      setRemarks((p.device_remarks || {})[me] || '');
      setEditing(!p.my_submitted);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [flightId, me]);
  useEffect(() => { if (online) load(); }, [load, online]);

  async function submit() {
    const lines = position.items.map((i) => ({ product_id: i.product_id, declared_remaining: Number(declared[i.product_id] || 0) }));
    if (lines.some((l) => Number.isNaN(l.declared_remaining) || l.declared_remaining < 0)) {
      return notify({ title: 'Check the quantities', message: 'Every remaining quantity must be a whole number, 0 or more.' });
    }
    const changed = position.items.filter((i) => Number(declared[i.product_id]) !== mineDefault(position, i));
    if (changed.length && !remarks.trim()) {
      return notify({ title: 'Remarks required', message: `${changed.length} line(s) differ from the system figure. Say why.` });
    }
    const others = (position.devices || []).filter((d) => d !== me);
    const sure = await confirm({
      title: 'Confirm the remaining stock on this iPad?',
      message: `${position.flight_number} ${position.origin}\u2192${position.destination}: `
        + `${lines.reduce((a, l) => a + l.declared_remaining, 0)} unit(s) left on this iPad after this sector.`
        + (others.length ? ` ${others.join(', ')} declares its own part; the two are added together.` : '')
        + ' Management and Inventory can see this at once.',
      confirmText: 'Confirm',
    });
    if (!sure) return;
    setBusy(true);
    try {
      const result = await api(`/flights/${flightId}/sector-stock`, { method: 'POST', body: { lines, remarks: remarks.trim() || null } });
      await load();
      const waiting = (result.waiting_for || []).filter((d) => d !== me);
      await notify({
        title: 'Remaining stock confirmed',
        message: (waiting.length ? `Waiting for ${waiting.join(', ')} to confirm its part. ` : 'Both iPads have confirmed. ')
          + 'Next: see the commission below, then the forms, then \u201cStart second flight\u201d on the Sales page.',
      });
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  if (!online) {
    return <Card><SectionTitle title="Remaining stock after this sector" description="Needs a connection." /></Card>;
  }
  if (error) return <Card><Banner tone="danger" message={error} /></Card>;
  if (!position) return null;
  const readOnly = locked || !editing;
  const others = (position.devices || []).filter((d) => d !== me);
  const waiting = (position.waiting_for || []).filter((d) => d !== me);

  return (
    <Card>
      <SectionTitle
        title="1. Remaining stock after this sector"
        description={position.my_submitted
          ? (position.submitted
            ? `Both iPads have confirmed. ${position.total_declared} unit(s) left in total.`
            : `Your part is in. Waiting for ${waiting.join(', ')} to confirm its part.`)
          : 'Count what is left on THIS iPad. The system figure is calculated from the load and every sale on both iPads; change a line only if the trolley says otherwise.'}
        right={<Pill label={position.submitted ? 'Confirmed' : position.my_submitted ? 'Waiting for other iPad' : 'To confirm'}
                     tone={position.submitted ? 'success' : position.my_submitted ? 'info' : 'warning'} />}
      />
      <View style={[styles.row, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border }]}>
        <Text style={[styles.mono, { flex: 2 }]}>Product</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>Loaded</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>Sold</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>{others.length ? 'Should be here' : 'Should be left'}</Text>
        <Text style={[styles.mono, { flex: 1.2, textAlign: 'right' }]}>{others.length ? 'On this iPad' : 'Left'}</Text>
      </View>
      {position.items.map((i) => {
        const expectedHere = mineDefault(position, i);
        const diff = Number(declared[i.product_id] || 0) - expectedHere;
        const otherQty = Object.entries(i.by_device || {}).filter(([d]) => d !== me);
        return (
          <View key={i.product_id} style={[styles.row, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt }]}>
            <View style={{ flex: 2 }}>
              <Text style={{ fontSize: 13, color: COLORS.text }} numberOfLines={2}>{i.product_name}</Text>
              {diff !== 0 && <Text style={[styles.mono, { color: COLORS.warning }]}>{diff > 0 ? '+' : ''}{diff} vs system</Text>}
              {otherQty.length > 0 && <Text style={styles.mono}>{otherQty.map(([d, q]) => `${d}: ${q}`).join(' · ')}</Text>}
            </View>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{i.loaded_quantity}</Text>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{i.sold_before + i.sold_quantity}</Text>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{expectedHere}</Text>
            <View style={{ flex: 1.2, alignItems: 'flex-end' }}>
              {readOnly ? (
                <Text style={[styles.value, { fontWeight: '600' }]}>{(i.by_device || {})[me] ?? i.declared_remaining}</Text>
              ) : (
                <TextInput
                  value={declared[i.product_id] ?? ''}
                  onChangeText={(v) => setDeclared((d) => ({ ...d, [i.product_id]: v.replace(/[^0-9]/g, '') }))}
                  keyboardType="number-pad"
                  style={[styles.input, { width: 64, textAlign: 'center', paddingVertical: 6 }]}
                />
              )}
            </View>
          </View>
        );
      })}
      <View style={[styles.rowBetween, { paddingVertical: 8 }]}>
        <Text style={styles.label}>{others.length ? 'Total on this iPad' : 'Total left'}</Text>
        <Text style={[styles.value, { fontWeight: '700' }]}>
          {position.items.reduce((a, i) => a + Number(declared[i.product_id] || 0), 0)} unit(s)
        </Text>
      </View>
      {!readOnly ? (
        <>
          <Field label="Remarks (required if a figure differs from the system)" value={remarks} onChangeText={setRemarks} multiline placeholder="e.g. one can damaged" />
          <Button title={busy ? 'Saving\u2026' : 'Confirm remaining stock on this iPad'} onPress={submit} disabled={busy} />
          {position.my_submitted && (
            <Button title="Cancel" tone="ghost" small style={{ marginTop: 8 }} onPress={() => { setEditing(false); load(); }} />
          )}
        </>
      ) : (
        <>
          {!!position.remarks && <Text style={[styles.mono, { marginTop: 4 }]}>Remarks: {position.remarks}</Text>}
          {!locked && (
            <Button title="Correct this iPad's count" tone="ghost" small style={{ marginTop: 8 }} onPress={() => setEditing(true)} />
          )}
        </>
      )}
    </Card>
  );
}

/** The last sector: the final stock return is on the Sync page (per iPad). */
function FinalReturnCard({ flightId, online, goTo, user }) {
  const [trip, setTrip] = useState(null);
  useEffect(() => {
    if (!online) return;
    api(`/flights/${flightId}/trip-progress`).then(setTrip).catch(() => setTrip(null));
  }, [flightId, online]);
  const done = trip && ['PENDING', 'APPROVED'].includes(trip.stock_return);
  const mine = trip?.my_stock_return_done;
  const waiting = (trip?.stock_return_waiting_for || []).filter((d) => d !== user?.employee_id);
  return (
    <Card>
      <SectionTitle
        title="1. Final stock return"
        description={done
          ? 'Both iPads have submitted the final stock return. It is with the Inventory Supervisor.'
          : mine
            ? `Your part is in. Waiting for ${waiting.join(', ')} to submit its part.`
            : 'Each iPad submits what is left on it; the two counts are added together and go to Inventory.'}
        right={<Pill label={done ? 'Submitted' : mine ? 'Waiting for other iPad' : 'To submit'} tone={done ? 'success' : mine ? 'info' : 'warning'} />}
      />
      {!done && !mine && <Button title="Open the final stock return" tone="secondary" onPress={() => goTo?.('sync')} />}
    </Card>
  );
}

/** What this sector earned and how it is shared (7.11). */
function CommissionCard({ flightId, online, user }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!online) return;
    api(`/flights/${flightId}/commission`).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [flightId, online]);
  if (!online) return null;
  if (error) return <Card><Banner tone="danger" message={error} /></Card>;
  if (!data) return null;
  const s = data.sector || {};
  return (
    <Card>
      <SectionTitle
        title="2. Commission on this sector"
        description={`${Math.round((data.commission_rate || 0.1) * 100)}% of product sales is shared equally by the ${s.member_count || 0} crew on this sector; `
          + 'combo commission goes to whoever sold the combo.'}
        right={<Pill label={npr(data.total_commission_npr)} tone="success" />}
      />
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        <Pill label={`Product sales ${npr(s.product_revenue_npr || 0)}`} tone="muted" />
        <Pill label={`Pool ${npr(s.commission_pool_npr || 0)} \u00f7 ${s.member_count || 0} = ${npr(s.share_npr || 0)} each`} tone="info" />
        {!!s.combo_revenue_npr && <Pill label={`Combo sales ${npr(s.combo_revenue_npr)} \u2192 ${npr(s.combo_commission_npr)} commission`} tone="warning" />}
      </View>
      {data.members.map((m) => (
        <View key={m.crew_member_id} style={[styles.rowBetween, { paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 13, color: COLORS.text }}>{m.name}</Text>
            <Text style={styles.mono}>
              share {npr(m.share_npr)}{m.combo_commission_npr ? ` + combo ${npr(m.combo_commission_npr)}` : ''}
            </Text>
          </View>
          <Text style={[styles.value, { fontWeight: '700' }]}>{npr(m.total_npr)}</Text>
        </View>
      ))}
      {!data.members.length && <Text style={styles.empty}>No crew recorded on this sector.</Text>}
      <Text style={[styles.mono, { marginTop: 6 }]}>Recorded for Management and the administrator. Next: the forms below.</Text>
    </Card>
  );
}

/** Management, Inventory and the administrator: every confirmed sector count. */
function SectorStockList() {
  const pager = usePaged('/sector-stock-counts');
  const [open, setOpen] = useState(null);      // flight id expanded
  const [detail, setDetail] = useState(null);

  async function toggle(flightId) {
    if (open === flightId) { setOpen(null); setDetail(null); return; }
    setOpen(flightId);
    setDetail(null);
    try { setDetail(await api(`/flights/${flightId}/sector-stock`)); } catch (e) { setDetail({ error: e.message }); }
  }

  return (
    <Card>
      <SectionTitle
        title="Remaining stock after each sector"
        description="Confirmed by the crew after the first sector of a round trip, before the second flight starts. The final stock return is on the Sync page."
      />
      {pager.items.map((c) => (
        <View key={c.id}>
          <Pressable onPress={() => toggle(c.flight_id)} style={[styles.listRow, { paddingVertical: 12 }]}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                {c.flight_number} · {c.origin}→{c.destination} · {c.flight_date}
              </Text>
              <Pill label={c.total_variance ? `${c.total_variance > 0 ? '+' : ''}${c.total_variance} vs system` : 'Matches system'}
                    tone={c.total_variance ? 'warning' : 'success'} />
            </View>
            <Text style={styles.mono}>
              {c.total_declared} unit(s) left · confirmed by {c.submitted_by} · {String(c.submitted_at || '').replace('T', ' ').slice(0, 16)}
            </Text>
            {!!c.remarks && <RemarkLine label="Crew remarks" text={c.remarks} />}
          </Pressable>
          {open === c.flight_id && (
            <View style={{ paddingHorizontal: 8, paddingBottom: 10 }}>
              {!detail && <Text style={styles.mono}>Loading…</Text>}
              {!!detail?.error && <Banner tone="danger" message={detail.error} />}
              {!!detail?.items && detail.items.map((i) => (
                <View key={i.product_id} style={[styles.rowBetween, { paddingVertical: 4 }]}>
                  <Text style={{ fontSize: 13, color: COLORS.text, flex: 2 }}>{i.product_name}</Text>
                  <Text style={[styles.mono, { flex: 2, textAlign: 'right' }]}>
                    loaded {i.loaded_quantity} · sold {i.sold_before + i.sold_quantity} · should be {i.expected_remaining} · left {i.declared_remaining}
                    {i.variance ? ` (${i.variance > 0 ? '+' : ''}${i.variance})` : ''}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </View>
      ))}
      {!!pager.error && <Banner tone="danger" message={pager.error} />}
      {!pager.items.length && !pager.loading && <Text style={styles.empty}>No sector count yet.</Text>}
      <Pagination pager={pager} label="counts" />
    </Card>
  );
}
