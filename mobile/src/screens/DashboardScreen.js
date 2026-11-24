import React, { useCallback, useEffect, useState } from 'react';
import { Platform, ScrollView, View } from 'react-native';
import { Text } from '../text';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport, uploadFile } from '../api';
import { BarChart, DonutChart, LineChartView, ProgressBar } from '../charts';
import { COLORS, MONTHS, compact, npr, styles } from '../theme';
import {
  Banner, Button, Card, Chip, KeyValue, Metric, Notice, OptionModal, PageHeader, Pill,
  SectionTitle, Select,
} from '../ui';

const BASE_YEARS = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i);

export default function DashboardScreen({ user }) {
  const [filter, setFilter] = useState({
    year: new Date().getFullYear(), month: '', day: '', destination: '',
  });
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [picker, setPicker] = useState(null);
  const [message, setMessage] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [productView, setProductView] = useState('chart');
  // Uploaded sales history for years before OBS (7.16).
  const [history, setHistory] = useState(null);
  const [uploads, setUploads] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [historyView, setHistoryView] = useState('months');
  const fileInput = React.useRef(null);
  const isAdmin = user.permissions?.includes('users');

  const loadHistory = useCallback(async () => {
    try {
      const parts = [`year=${filter.year}`];
      if (filter.month) parts.push(`month=${filter.month}`);
      setHistory(await api(`/sales-history/summary?${parts.join('&')}`));
      if (isAdmin) setUploads(await api('/sales-history/uploads'));
    } catch { setHistory(null); }
  }, [filter.year, filter.month, isAdmin]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  async function uploadHistory(file) {
    if (!file) return;
    setUploading(true);
    try {
      const result = await uploadFile('/sales-history/upload', file);
      setMessage({ tone: 'success', text: `Uploaded ${file.name}: ${result.months.length} month(s) (${result.months[0]} to ${result.months[result.months.length - 1]}), ${result.rows} product line(s), ${npr(result.revenue)}. Choose the year to see it.` });
      loadHistory();
    } catch (error) {
      setMessage({ tone: 'danger', text: `Upload failed: ${error.message}` });
    }
    setUploading(false);
  }

  async function removeUpload(u) {
    try {
      await api(`/sales-history/uploads/${u.id}`, { method: 'DELETE' });
      setMessage({ tone: 'info', text: `${u.filename} removed from the dashboard.` });
      loadHistory();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  const query = useCallback(() => {
    const parts = [`year=${filter.year}`];
    if (filter.month) parts.push(`month=${filter.month}`);
    if (filter.day) parts.push(`day=${filter.day}`);
    if (filter.destination) parts.push(`destination=${filter.destination}`);
    return parts.join('&');
  }, [filter]);

  const load = useCallback(async () => {
    try {
      setData(await api(`/dashboard?${query()}`));
      setFailed(false);
      setMessage(null);
    } catch (error) {
      // Without this the screen sat on "Loading…" for ever after one bad
      // response, with no way back except restarting the app.
      setFailed(true);
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [query]);

  useEffect(() => { load(); }, [load]);

  async function exportExcel() {
    setExporting(true);
    try {
      const result = await downloadReport(query(), FileSystem, Sharing);
      setMessage({
        tone: 'success',
        text: result.platform === 'web'
          ? 'The workbook has been downloaded by your browser.'
          : 'The workbook is ready — choose where to save or share it.',
      });
    } catch (error) {
      setMessage({ tone: 'danger', text: `Export failed: ${error.message}` });
    }
    setExporting(false);
  }

  if (!data) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
        <PageHeader title="Revenue Dashboard" />
        {message && <Notice message={message} />}
        {failed ? (
          <>
            <Text style={styles.empty}>The dashboard could not be loaded.</Text>
            <Button title="Try again" tone="secondary" onPress={load} />
          </>
        ) : (
          <Text style={styles.empty}>Loading…</Text>
        )}
      </ScrollView>
    );
  }

  const YEARS = [...new Set([...BASE_YEARS, ...((history && history.years) || [])])].sort((a, b) => b - a);
  const achievement = Number(data.achievement || 0);
  const flown = data.flights.filter((f) => f.passengers > 0);
  const atTarget = flown.filter((f) => f.rpp >= data.target_rpp).length;
  const belowTarget = flown.filter((f) => f.rpp > 0 && f.rpp < data.target_rpp).length;
  const noSales = data.flights.filter((f) => f.revenue === 0).length;
  const ranked = [...flown].filter((f) => f.revenue > 0).sort((a, b) => b.rpp - a.rpp);
  const best = ranked[0];
  const worst = ranked[ranked.length - 1];
  const periodLabel = [
    filter.day || null,
    filter.month ? MONTHS[Number(filter.month) - 1] : null,
    filter.year,
  ].filter(Boolean).reverse().join(' ');

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Revenue Dashboard"
        description={`${periodLabel}${filter.destination ? ` · ${filter.destination}` : ' · all destinations'}`}
        right={(
          <View style={[styles.row, styles.gap8]}>
            <Pill label={`${data.flight_count} flights`} tone="info" />
            <Button
              title={exporting ? 'Preparing…' : 'Export to Excel'}
              small
              onPress={exportExcel}
              disabled={exporting}
              accessibilityLabel="Export to Excel: summary, sales, flights, products, commission and reconciliation"
            />
          </View>
        )}
      />
      {message && <Notice message={message} />}

      <Card tight>
        <View style={styles.wrap}>
          <Select label="Year" valueLabel={String(filter.year)} onPress={() => setPicker('year')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Month" valueLabel={filter.month ? MONTHS[filter.month - 1] : 'All'} onPress={() => setPicker('month')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Day" valueLabel={filter.day || 'All'} onPress={() => setPicker('day')} style={{ flex: 1, minWidth: 80, marginBottom: 0 }} />
          <Select label="Destination" valueLabel={filter.destination || 'All'} onPress={() => setPicker('destination')} style={{ flex: 1, minWidth: 110, marginBottom: 0 }} />
        </View>
      </Card>

      <Card>
        <SectionTitle title="Revenue per passenger" description={`Target is ${npr(data.target_rpp)} per passenger.`} />
        <View style={[styles.rowBetween, { marginBottom: 8 }]}>
          <Text style={{ fontSize: 30, fontWeight: '700', color: achievement >= 100 ? COLORS.success : COLORS.primary }}>
            {npr(data.rpp)}
          </Text>
          <Pill
            label={`${achievement.toFixed(1)}% of target`}
            tone={achievement >= 100 ? 'success' : achievement >= 70 ? 'warning' : 'danger'}
          />
        </View>
        <ProgressBar value={data.rpp} max={data.target_rpp} />
        <Text style={[styles.mono, { marginTop: 8 }]}>
          {data.gap >= 0
            ? `Ahead of target by ${npr(data.gap)} per passenger.`
            : `Short of target by ${npr(Math.abs(data.gap))} per passenger.`}
        </Text>
      </Card>

      <Card>
        <SectionTitle title="Headline numbers" />
        <View style={styles.wrap}>
          <Metric label="Revenue" value={npr(data.revenue)} tone="primary" />
          <Metric label="Passengers carried" value={compact(data.passengers)} />
          <Metric label="Flights" value={String(data.flight_count)} />
          <Metric label="Units sold" value={compact(data.units)} />
          <Metric label="Transactions" value={compact(data.transactions)} />
          <Metric label="Average basket" value={npr(data.basket)} />
        </View>
      </Card>

      <Card>
        <SectionTitle
          title="Against target"
          description="Revenue per passenger is the measure the business is run on."
        />
        <View style={styles.wrap}>
          <Metric label="Actual RPP" value={npr(data.rpp)} tone={achievement >= 100 ? 'success' : 'primary'} />
          <Metric label="Target RPP" value={npr(data.target_rpp)} />
          <Metric
            label="Difference per passenger"
            value={`${data.gap >= 0 ? '+' : ''}${npr(data.gap)}`}
            tone={data.gap >= 0 ? 'success' : 'danger'}
          />
          <Metric
            label="Achievement"
            value={`${achievement.toFixed(1)}%`}
            tone={achievement >= 100 ? 'success' : achievement >= 70 ? 'warning' : 'danger'}
          />
          <Metric
            label="Revenue at target"
            value={npr(data.passengers * data.target_rpp)}
            sub="What the period should have produced"
          />
          <Metric
            label="Shortfall / surplus"
            value={`${data.revenue - data.passengers * data.target_rpp >= 0 ? '+' : ''}${npr(data.revenue - data.passengers * data.target_rpp)}`}
            tone={data.revenue - data.passengers * data.target_rpp >= 0 ? 'success' : 'danger'}
            sub="Total, not per passenger"
          />
        </View>
      </Card>

      <Card>
        <SectionTitle
          title="Flights against target"
          description="How many legs cleared the RPP target in this period."
        />
        <View style={styles.wrap}>
          <Metric
            label="At or above target"
            value={String(atTarget)}
            tone="success"
            sub={data.flight_count ? `${((atTarget / data.flight_count) * 100).toFixed(0)}% of flights` : undefined}
          />
          <Metric label="Below target" value={String(belowTarget)} tone="warning" />
          <Metric label="No sales recorded" value={String(noSales)} tone={noSales ? 'danger' : 'default'} />
          <Metric label="Best leg" value={best ? npr(best.rpp) : '—'} sub={best?.flight_number} />
          <Metric label="Weakest leg" value={worst ? npr(worst.rpp) : '—'} sub={worst?.flight_number} />
          <Metric
            label="Commission"
            value={npr(data.commission_npr ?? 0)}
            tone="success"
            sub={`${((data.commission_rate ?? 0.1) * 100).toFixed(0)}% of qualifying sales`}
          />
        </View>
      </Card>

      <Card>
        <SectionTitle title="Daily revenue trend" description="The dashed line is the revenue-per-passenger target scaled to the day." />
        <LineChartView
          data={data.trend.map((point) => ({ label: point.day, value: point.revenue }))}
          format={compact}
        />
      </Card>

      <Card>
        <SectionTitle
          title="Revenue by product"
          right={(
            <View style={[styles.row, styles.gap8]}>
              <Chip label="Chart" active={productView === 'chart'} onPress={() => setProductView('chart')} />
              <Chip label="Top list" active={productView === 'list'} onPress={() => setProductView('list')} />
            </View>
          )}
        />
        {productView === 'chart' ? (
          <DonutChart
            data={data.products.filter((p) => p.revenue > 0).slice(0, 8)
              .map((p) => ({ label: p.product_name, value: p.revenue }))}
            format={compact}
          />
        ) : (
          <>
            {data.products
              .filter((p) => p.revenue > 0)
              .slice(0, 15)
              .map((product, index) => (
                <View key={product.product_id} style={styles.listRow}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }} numberOfLines={1}>
                      {index + 1}. {product.product_name}
                    </Text>
                    <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary }}>
                      {npr(product.revenue)}
                    </Text>
                  </View>
                  <Text style={styles.mono}>
                    {product.units} units · margin {npr(product.margin)} · {product.category}
                  </Text>
                </View>
              ))}
            {!data.products.some((p) => p.revenue > 0) && (
              <Text style={styles.empty}>No product sales in this period.</Text>
            )}
          </>
        )}
      </Card>

      <Card>
        <SectionTitle title="Revenue by route" />
        <BarChart
          data={data.routes.slice(0, 8).map((r) => ({ label: r.route, value: r.revenue }))}
          format={compact}
        />
        {data.routes.map((route) => (
          <View key={route.route} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600' }}>{route.route}</Text>
              <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary }}>{npr(route.revenue)}</Text>
            </View>
            <Text style={styles.mono}>
              {route.flights} flight(s) · {route.passengers} pax · RPP {npr(route.rpp)}
            </Text>
          </View>
        ))}
      </Card>

      {!!data.currencies.length && (
        <Card>
          <SectionTitle title="Payment currency mix" description="Converted to NPR at the rate effective when each sale was made." />
          {data.currencies.map((entry) => (
            <KeyValue key={entry.currency} label={`${entry.currency} · ${entry.lines} lines`} value={npr(entry.revenue)} />
          ))}
        </Card>
      )}

      <Card>
        <SectionTitle title="Flight performance" />
        {data.flights.slice(0, 20).map((flight) => (
          <View key={flight.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600' }}>
                {flight.flight_number} · {flight.origin}→{flight.destination}
              </Text>
              <Pill
                label={npr(flight.rpp)}
                tone={flight.rpp >= data.target_rpp ? 'success' : flight.rpp > 0 ? 'warning' : 'muted'}
              />
            </View>
            <Text style={styles.mono}>
              {flight.flight_date} · {flight.duty_group_label || 'crew not entered'} · {flight.passengers} pax · {npr(flight.revenue)}
            </Text>
          </View>
        ))}
        {!data.flights.length && <Text style={styles.empty}>No flights in this period.</Text>}
      </Card>


      {(history?.available || isAdmin) && (
        <Card style={history?.available ? { borderColor: COLORS.info, borderWidth: 1 } : null}>
          <SectionTitle
            title={`Uploaded sales history \u00b7 ${periodLabel}`}
            description={history?.available
              ? `From the monthly consumption report uploaded by the administrator (${history.periods.length} period(s)). Live OBS figures above; this is the record from before OBS, or the paper report for the same period.`
              : 'No uploaded report covers this period. The administrator can upload the monthly OBS consumption report workbook (one sheet per month) below.'}
            right={history?.available ? <Pill label="UPLOADED REPORT" tone="info" /> : null}
          />
          {history?.available && (
            <>
              <View style={[styles.wrap, { marginBottom: 10 }]}>
                <Metric label="Sales (report)" value={npr(history.revenue)} tone="primary" />
                <Metric label="Cost of goods" value={npr(history.cost)} />
                <Metric label="Margin" value={npr(history.margin)} tone="success" sub={`${history.margin_pct}% of sales`} />
                <Metric label="Units sold" value={compact(history.units)} />
              </View>
              <View style={[styles.row, styles.gap8, { marginBottom: 8 }]}>
                <Chip label="By month" active={historyView === 'months'} onPress={() => setHistoryView('months')} />
                <Chip label="By product" active={historyView === 'products'} onPress={() => setHistoryView('products')} />
                <Chip label="Periods" active={historyView === 'periods'} onPress={() => setHistoryView('periods')} />
              </View>
              {historyView === 'months' && (
                <>
                  <BarChart data={history.months.map((m) => ({ label: MONTHS[m.month - 1].slice(0, 3), value: m.revenue }))} format={compact} />
                  {history.months.map((m) => (
                    <View key={m.month} style={[styles.rowBetween, { paddingVertical: 5 }]}>
                      <Text style={{ fontSize: 13, flex: 1 }}>{MONTHS[m.month - 1]}{m.last_day && m.last_day.slice(8) < '28' && m.periods === 1 ? ` (to ${m.last_day.slice(8)})` : ''}</Text>
                      <Text style={[styles.mono, { width: 70, textAlign: 'right' }]}>{compact(m.units)} u</Text>
                      <Text style={[styles.mono, { width: 90, textAlign: 'right' }]}>cost {compact(m.cost)}</Text>
                      <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary, width: 110, textAlign: 'right' }}>{npr(m.revenue)}</Text>
                    </View>
                  ))}
                </>
              )}
              {historyView === 'products' && history.products.map((p, index) => (
                <View key={p.product_name} style={styles.listRow}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }} numberOfLines={1}>{index + 1}. {p.product_name}</Text>
                    <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary }}>{npr(p.revenue)}</Text>
                  </View>
                  <Text style={styles.mono}>{p.units} units \u00b7 cost {npr(p.cost)} \u00b7 margin {npr(p.margin)} \u00b7 sells at {npr(p.sales_price)}</Text>
                </View>
              ))}
              {historyView === 'periods' && history.periods.map((pd) => (
                <View key={`${pd.period_start}-${pd.period_end}`} style={[styles.rowBetween, { paddingVertical: 5 }]}>
                  <Text style={{ fontSize: 13, flex: 1 }}>{pd.period_start} to {pd.period_end} <Text style={styles.mono}>(sheet {pd.sheet})</Text></Text>
                  <Text style={styles.mono}>{pd.lines} products \u00b7 {compact(pd.units)} u</Text>
                  <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary, width: 110, textAlign: 'right' }}>{npr(pd.revenue)}</Text>
                </View>
              ))}
            </>
          )}
          {isAdmin && (
            <View style={{ marginTop: 10 }}>
              <Button
                title={uploading ? 'Uploading\u2026' : 'Upload consumption report (Excel)'}
                tone="secondary"
                onPress={() => {
                  if (Platform.OS === 'web') fileInput.current?.click();
                  else setMessage({ tone: 'info', text: 'Open OBS in a browser to upload a workbook.' });
                }}
                disabled={uploading}
              />
              {Platform.OS === 'web' && (
                <input
                  ref={fileInput}
                  type="file"
                  accept=".xlsx,.xlsm"
                  style={{ display: 'none' }}
                  onChange={(event) => { const f = event.target.files?.[0]; event.target.value = ''; uploadHistory(f); }}
                />
              )}
              <Text style={[styles.mono, { marginTop: 6 }]}>
                Layout: one sheet per month, blocks headed \u201cSales Report \u2013 Mon dd-dd, yyyy\u201d with SL.No, product, cost price, quantity, total cost, sales price. A month uploaded again replaces the earlier figures.
              </Text>
              {uploads.map((u) => (
                <View key={u.id} style={[styles.rowBetween, { paddingVertical: 6 }]}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontSize: 13, fontWeight: '600' }}>{u.filename}</Text>
                    <Text style={styles.mono}>{u.first_period} to {u.last_period} \u00b7 {u.rows} lines \u00b7 {npr(u.revenue)} \u00b7 by {u.uploaded_by} {String(u.uploaded_at).replace('T', ' ').slice(0, 16)}</Text>
                  </View>
                  <Button title="Remove" tone="ghost" small onPress={() => removeUpload(u)} />
                </View>
              ))}
            </View>
          )}
        </Card>
      )}

      <OptionModal
        visible={picker === 'year'}
        title="Year"
        options={YEARS.map((y) => ({ value: y, label: String(y) }))}
        selectedValue={filter.year}
        onSelect={(value) => { setFilter({ ...filter, year: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'month'}
        title="Month"
        options={[{ value: '', label: 'All months' },
          ...MONTHS.map((m, i) => ({ value: i + 1, label: m }))]}
        selectedValue={filter.month}
        onSelect={(value) => { setFilter({ ...filter, month: value, day: '' }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'day'}
        title="Day"
        options={[{ value: '', label: 'All days' },
          ...Array.from({ length: 31 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))]}
        selectedValue={filter.day}
        onSelect={(value) => { setFilter({ ...filter, day: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'destination'}
        title="Destination"
        options={[{ value: '', label: 'All destinations' },
          ...data.destinations.map((d) => ({ value: d, label: d }))]}
        selectedValue={filter.destination}
        onSelect={(value) => { setFilter({ ...filter, destination: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
    </ScrollView>
  );
}
