import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { confirm } from '../dialog';
import { COLORS, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, KeyValue, Notice, OptionModal, PageHeader, Pill,
  SectionTitle, Select,
} from '../ui';

export default function FinanceScreen({ user }) {
  const readOnly = !(user.permissions || []).includes('rates_edit');

  const [currencies, setCurrencies] = useState([]);
  const [history, setHistory] = useState([]);
  const [active, setActive] = useState([]);
  const [form, setForm] = useState({
    currency_code: 'USD', unit: '1', rate_to_npr: '',
    effective_from: todayString(), effective_to: null,
  });
  const [picker, setPicker] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [c, h, a] = await Promise.all([
        api('/exchange-rates/currencies'),
        api('/exchange-rates'),
        api('/exchange-rates/active'),
      ]);
      setCurrencies(c); setHistory(h); setActive(a);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      await api('/exchange-rates', {
        method: 'POST',
        body: {
          currency_code: form.currency_code,
          unit: Number(form.unit || 1),
          rate_to_npr: Number(form.rate_to_npr),
          effective_from: form.effective_from,
          effective_to: form.effective_to,
        },
      });
      setMessage({
        tone: 'success',
        text: form.effective_from > todayString()
          ? `Scheduled: ${form.currency_code} becomes NPR ${form.rate_to_npr} from ${form.effective_from}.`
          : `${form.currency_code} is now NPR ${form.rate_to_npr}.`,
      });
      setForm({ ...form, rate_to_npr: '' });
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function retire(rate) {
    try {
      await api(`/exchange-rates/${rate.id}`, { method: 'DELETE' });
      setMessage({ tone: 'info', text: 'Rate band retired.' });
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  const grouped = history.reduce((acc, item) => {
    (acc[item.currency_code] = acc[item.currency_code] || []).push(item);
    return acc;
  }, {});

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Exchange Rates"
        description="Rates are set per month. A sale is charged at the month's rate, rounded up to the next whole unit of the currency."
      />
      {message && <Notice message={message} />}

      <MonthlyRates readOnly={readOnly} onChanged={load} setMessage={setMessage} />

      <Card>
        <SectionTitle title="Rates in force today" />
        {active.filter((r) => r.currency_code !== 'NPR').map((rate) => (
          <KeyValue
            key={rate.currency_code}
            label={`${rate.unit > 1 ? `${rate.unit} ` : ''}${rate.currency_code} — ${rate.currency_name}`}
            value={`NPR ${rate.rate_to_npr}`}
          />
        ))}
      </Card>

      {!readOnly && (
        <Card>
          <SectionTitle
            title="Set a rate"
            description="Leave the end date empty to keep the rate open-ended. An overlapping earlier band is closed automatically."
          />
          <Select label="Currency" valueLabel={form.currency_code} onPress={() => setPicker('currency')} />
          <View style={[styles.row, styles.gap8]}>
            <Field
              label="Unit"
              value={form.unit}
              onChangeText={(v) => setForm({ ...form, unit: v.replace(/[^0-9.]/g, '') })}
              keyboardType="decimal-pad"
              placeholder="1"
              style={{ flex: 1 }}
            />
            <Field
              label="Rate (NPR)"
              value={form.rate_to_npr}
              onChangeText={(v) => setForm({ ...form, rate_to_npr: v.replace(/[^0-9.]/g, '') })}
              keyboardType="decimal-pad"
              placeholder="151.00"
              style={{ flex: 2 }}
            />
          </View>
          <Text style={[styles.mono, { marginTop: -4, marginBottom: 12 }]}>
            {`${form.unit || 1} ${form.currency_code} = NPR ${form.rate_to_npr || '—'}`}
            {form.rate_to_npr && Number(form.unit) > 1
              ? `  (1 ${form.currency_code} = NPR ${(Number(form.rate_to_npr) / Number(form.unit)).toFixed(4)})`
              : ''}
          </Text>
          <Select label="Effective from" valueLabel={form.effective_from} onPress={() => setCalendar('effective_from')} />
          <Select
            label="Effective to"
            valueLabel={form.effective_to || 'Open-ended'}
            onPress={() => setCalendar('effective_to')}
          />
          <Button
            title="Save rate"
            onPress={save}
            disabled={!form.rate_to_npr || !form.effective_from}
          />
        </Card>
      )}

      <Card>
        <SectionTitle title="Rate history" description="Scheduled bands are highlighted so you can confirm future pricing." />
        {Object.entries(grouped).map(([code, bands]) => (
          <View key={code} style={{ marginBottom: 14 }}>
            <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, marginBottom: 4 }}>{code}</Text>
            {bands.map((band) => (
              <View key={band.id} style={styles.listRow}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600' }}>
                    {band.unit > 1 ? `${band.unit} ${code} = ` : ''}NPR {band.rate_to_npr}
                  </Text>
                  <Pill
                    label={band.state}
                    tone={band.state === 'CURRENT' ? 'success'
                      : band.state === 'SCHEDULED' ? 'info'
                        : band.state === 'EXPIRED' ? 'muted' : 'warning'}
                  />
                </View>
                <Text style={styles.mono}>
                  {band.effective_from} → {band.effective_to || 'open-ended'} · set by {band.updated_by}
                </Text>
                {!readOnly && band.state === 'SCHEDULED' && (
                  <Button
                    title="Cancel this scheduled rate"
                    tone="ghost"
                    small
                    style={{ marginTop: 6, alignSelf: 'flex-start' }}
                    onPress={() => retire(band)}
                  />
                )}
              </View>
            ))}
          </View>
        ))}
      </Card>

      <OptionModal
        visible={picker === 'currency'}
        title="Currency"
        options={currencies.filter((c) => c.code !== 'NPR').map((c) => ({
          value: c.code,
          label: `${c.code} — ${c.name}`,
          sub: c.unit > 1 ? `Normally quoted per ${c.unit}` : 'Quoted per 1',
        }))}
        selectedValue={form.currency_code}
        onSelect={(value) => {
          const picked = currencies.find((c) => c.code === value);
          setForm({ ...form, currency_code: value, unit: String(picked?.unit || 1) });
          setPicker(null);
        }}
        onClose={() => setPicker(null)}
      />
      <CalendarModal
        visible={!!calendar}
        title={calendar === 'effective_from' ? 'Effective from' : 'Effective to'}
        value={form[calendar]}
        allowClear={calendar === 'effective_to'}
        minDate={calendar === 'effective_to' ? form.effective_from : undefined}
        onSelect={(value) => setForm({ ...form, [calendar]: value })}
        onClose={() => setCalendar(null)}
      />
    </ScrollView>
  );
}


/* ------------------------------------------------------------------ */
/* Monthly rates (7.18)                                               */
/* ------------------------------------------------------------------ */
const thisMonth = () => todayString().slice(0, 7);
function shiftMonth(month, delta) {
  const [y, m] = month.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const monthLabel = (month) => `${MONTH_NAMES[Number(month.slice(5, 7)) - 1]} ${month.slice(0, 4)}`;

function MonthlyRates({ readOnly, onChanged, setMessage }) {
  const [month, setMonth] = useState(thisMonth());
  const [rows, setRows] = useState([]);
  const [edits, setEdits] = useState({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api(`/exchange-rates/month?month=${month}`);
      setRows(result.rates || []);
      setEdits({});
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [month, setMessage]);
  useEffect(() => { load(); }, [load]);

  const valueOf = (row, key) => (edits[row.currency_code]?.[key] ?? (row[key] == null ? '' : String(row[key])));
  const setValue = (row, key, value) => setEdits((e) => ({
    ...e, [row.currency_code]: { ...(e[row.currency_code] || {}), [key]: value.replace(/[^0-9.]/g, '') },
  }));
  const changed = Object.keys(edits).filter((code) => {
    const row = rows.find((r) => r.currency_code === code);
    return row && (valueOf(row, 'rate_to_npr') !== String(row.rate_to_npr ?? '') || valueOf(row, 'unit') !== String(row.unit ?? ''));
  });
  const invalid = changed.some((code) => {
    const row = rows.find((r) => r.currency_code === code);
    return !(Number(valueOf(row, 'rate_to_npr')) > 0) || !(Number(valueOf(row, 'unit')) > 0);
  });

  async function save() {
    setBusy(true);
    try {
      const rates = changed.map((code) => {
        const row = rows.find((r) => r.currency_code === code);
        return { currency_code: code, unit: Number(valueOf(row, 'unit')), rate_to_npr: Number(valueOf(row, 'rate_to_npr')) };
      });
      const result = await api('/exchange-rates/month', { method: 'POST', body: { month, rates } });
      setRows(result.rates || []);
      setEdits({});
      setMessage({ tone: 'success', text: `${rates.length} rate(s) saved for ${monthLabel(month)}.` });
      onChanged?.();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  async function copyPrevious(overwrite = false) {
    setBusy(true);
    try {
      const result = await api('/exchange-rates/month/copy', { method: 'POST', body: { month, overwrite } });
      setRows(result.rates || []);
      setEdits({});
      if (!result.saved?.length && !overwrite) {
        setBusy(false);
        const again = await confirm({
          title: 'Already set',
          message: `Every currency already has a rate for ${monthLabel(month)}. Replace them with ${monthLabel(shiftMonth(month, -1))}'s rates?`,
          confirmText: 'Replace',
          tone: 'danger',
        });
        if (again) return copyPrevious(true);
        return;
      }
      setMessage({ tone: 'success', text: `${result.saved.length} rate(s) copied from ${monthLabel(shiftMonth(month, -1))}.` });
      onChanged?.();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  return (
    <Card>
      <SectionTitle title="Monthly rates" description="Each rate applies from the 1st to the last day of the month." />
      <View style={[styles.row, styles.gap8, { alignItems: 'center', marginBottom: 8 }]}>
        <Button title="\u2039 Previous" tone="ghost" small onPress={() => setMonth(shiftMonth(month, -1))} />
        <Text style={{ fontSize: 15, fontWeight: '700', color: COLORS.text, flex: 1, textAlign: 'center' }}>{monthLabel(month)}</Text>
        <Button title="Next \u203a" tone="ghost" small onPress={() => setMonth(shiftMonth(month, 1))} />
      </View>
      {month !== thisMonth() && (
        <Button title="This month" tone="ghost" small onPress={() => setMonth(thisMonth())} style={{ alignSelf: 'center', marginBottom: 6 }} />
      )}
      {rows.map((row) => (
        <View key={row.currency_code} style={[styles.listRow, { paddingVertical: 8 }]}>
          <View style={styles.rowBetween}>
            <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
              {row.currency_code} — {row.currency_name}
            </Text>
            <Pill label={row.set_for_month ? 'SET FOR MONTH' : row.rate_to_npr ? 'CARRIED OVER' : 'NOT SET'}
              tone={row.set_for_month ? 'success' : row.rate_to_npr ? 'info' : 'danger'} />
          </View>
          {readOnly ? (
            <Text style={styles.mono}>
              {row.rate_to_npr ? `${row.unit} ${row.currency_code} = NPR ${row.rate_to_npr}` : 'No rate'}
              {row.updated_by ? ` · set by ${row.updated_by}` : ''}
            </Text>
          ) : (
            <View style={[styles.row, styles.gap8]}>
              <Field label="Unit" value={valueOf(row, 'unit')} onChangeText={(v) => setValue(row, 'unit', v)}
                keyboardType="decimal-pad" style={{ flex: 1 }} />
              <Field label="Rate (NPR)" value={valueOf(row, 'rate_to_npr')} onChangeText={(v) => setValue(row, 'rate_to_npr', v)}
                keyboardType="decimal-pad" placeholder="—" style={{ flex: 2 }} />
            </View>
          )}
        </View>
      ))}
      {!readOnly && (
        <View style={[styles.row, styles.gap8, { marginTop: 10 }]}>
          <Button title="Copy previous month" tone="secondary" style={styles.flex1} onPress={() => copyPrevious(false)} disabled={busy} />
          <Button title={busy ? 'Saving…' : changed.length ? `Save ${changed.length} change(s)` : 'Save'} style={styles.flex1}
            onPress={save} disabled={busy || !changed.length || invalid} />
        </View>
      )}
      {invalid && <Text style={[styles.mono, { color: COLORS.danger, marginTop: 4 }]}>Every changed rate and unit must be more than 0.</Text>}
    </Card>
  );
}
