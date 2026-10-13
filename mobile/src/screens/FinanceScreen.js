import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { COLORS, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Field, KeyValue, Notice, OptionModal, PageHeader, Pill,
  SectionTitle, Select,
} from '../ui';

export default function FinanceScreen({ user }) {
  const readOnly = !['finance', 'management'].includes(user.role);

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
        description="Each rate applies over a date range. Future rates can be set in advance and take effect automatically."
      />
      {message && <Notice message={message} />}

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
