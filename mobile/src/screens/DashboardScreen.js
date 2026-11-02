import React, { useCallback, useEffect, useState } from 'react';
import { Platform, ScrollView, View } from 'react-native';
import { Text } from '../text';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport } from '../api';
import { BarChart, DonutChart, LineChartView, ProgressBar } from '../charts';
import { COLORS, MONTHS, compact, npr, styles } from '../theme';
import {
  Banner, Button, Card, Chip, KeyValue, Metric, Notice, OptionModal, PageHeader, Pill,
  SectionTitle, Select,
} from '../ui';

const YEARS = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i);

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
