import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport } from '../api';
import { BarChart, RankBarChart } from '../charts';
import { COLORS, MONTHS, compact, npr, styles } from '../theme';
import { usePaged } from '../paging';
import { Banner, Button, Card, Chip, Field, KeyValue, OptionModal, PageHeader, Pagination, Pill, SectionTitle, Select } from '../ui';

const YEARS = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i);

export default function LogsScreen({ user }) {
  // Management oversee everything here. The inventory supervisor reaches this
  // screen for the sales ledger alone, which they reconcile against what the
  // crew returned, and may correct a mis-keyed line with remarks.
  const perms = user.permissions || [];
  const canOversee = perms.includes('logs');
  const canSeeSales = perms.includes('sales_view');
  const canEdit = perms.includes('sales_edit');
  const [tab, setTab] = useState(canOversee ? 'ranking' : 'sales');
  const [filter, setFilter] = useState({ year: new Date().getFullYear(), month: '', day: '', destination: '' });
  const [picker, setPicker] = useState(null);
  const [message, setMessage] = useState(null);

  const [ranking, setRanking] = useState(null);
  const [top, setTop] = useState([]);
  const [modules, setModules] = useState([]);
  const [destinations, setDestinations] = useState([]);
  const [products, setProducts] = useState([]);

  const [editing, setEditing] = useState(null);
  const [editProduct, setEditProduct] = useState(null);
  const [editQuantity, setEditQuantity] = useState('');
  const [editRemarks, setEditRemarks] = useState('');
  const [editPicker, setEditPicker] = useState(false);
  const [exporting, setExporting] = useState(false);

  const query = useCallback(() => {
    const parts = [`year=${filter.year}`];
    if (filter.month) parts.push(`month=${filter.month}`);
    if (filter.day) parts.push(`day=${filter.day}`);
    if (filter.destination) parts.push(`destination=${filter.destination}`);
    return parts.join('&');
  }, [filter]);

  // Audit log and sales ledger are paged on the server; both grow without limit.
  const logs = usePaged(`/logs?${query().replace(/&?destination=[^&]*/, '')}`, { enabled: canOversee && tab === 'logs' });
  const sales = usePaged('/sales', { enabled: canSeeSales && tab === 'sales' });

  // Each tab exports exactly what it is showing, filters included.
  const EXPORTS = {
    ranking: { path: '/reports/crew-ranking.xlsx', label: 'crew ranking' },
    products: { path: '/reports/top-products.xlsx', label: 'top products' },
    logs: { path: '/reports/audit-log.xlsx', label: 'audit log' },
    sales: { path: '/reports/sales-detail.xlsx', label: 'sales' },
  };

  async function exportTab() {
    const target = EXPORTS[tab];
    if (!target) return;
    setExporting(true);
    try {
      // The audit log is filtered by date only; destination does not apply.
      const params = tab === 'logs'
        ? query().replace(/&?destination=[^&]*/, '')
        : tab === 'sales' ? '' : query();
      await downloadReport(params, FileSystem, Sharing, target.path);
      setMessage({ tone: 'success', text: `Exported the ${target.label}.` });
    } catch (error) {
      setMessage({ tone: 'danger', text: `Export failed: ${error.message}` });
    }
    setExporting(false);
  }

  const load = useCallback(async () => {
    if (!canOversee) return;
    try {
      const [r, t, m, d] = await Promise.all([
        api(`/crew-ranking?${query()}`),
        api(`/top-products?${query()}&limit=10`),
        api('/logs/modules'),
        api(`/dashboard?${query()}`),
      ]);
      setRanking(r); setTop(t); setModules(m);
      setDestinations(d.destinations || []);
      setMessage(null);
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [query, canOversee]);

  const loadSales = useCallback(async () => {
    if (!canSeeSales) return;
    try {
      setProducts(await api('/products'));
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [canSeeSales]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (tab === 'sales') loadSales(); }, [tab, loadSales]);

  async function saveEdit() {
    try {
      await api(`/sales/${editing.id}`, {
        method: 'PUT',
        body: {
          product_id: editProduct || editing.product_id,
          quantity: Number(editQuantity || editing.quantity),
          edit_remarks: editRemarks,
        },
      });
      setMessage({ tone: 'success', text: 'Sale corrected. The remarks will appear in the Excel export.' });
      setEditing(null); setEditProduct(null); setEditQuantity(''); setEditRemarks('');
      sales.reload();
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  const tabs = [
    ...(canOversee ? [
      { key: 'ranking', label: 'Crew ranking' },
      { key: 'products', label: 'Top products' },
      { key: 'logs', label: 'Audit log' },
    ] : []),
    ...(canSeeSales ? [{ key: 'sales', label: canEdit ? 'Edit sales' : 'Sales' }] : []),
  ];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title={canOversee ? 'Performance & Logs' : 'Onboard Sales'}
        description={canOversee
          ? 'Commission ranking, best sellers and the full activity trail.'
          : 'Every sale recorded on board. Correct a mis-keyed line with remarks.'}
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        {tabs.map((item) => (
          <Chip key={item.key} label={item.label} active={tab === item.key} onPress={() => setTab(item.key)} />
        ))}
      </View>
      {message && <Banner tone={message.tone} message={message.text} />}

      <Button
        title={exporting ? 'Preparing…' : `Download ${(EXPORTS[tab]?.label || '').toLowerCase()} (Excel)`}
        tone="secondary"
        onPress={exportTab}
        disabled={exporting}
        small
        style={{ marginBottom: 14, alignSelf: 'flex-end' }}
      />

      {canOversee && tab !== 'sales' && (
      <Card tight>
        <View style={styles.wrap}>
          <Select label="Year" valueLabel={String(filter.year)} onPress={() => setPicker('year')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Month" valueLabel={filter.month ? MONTHS[filter.month - 1] : 'All'} onPress={() => setPicker('month')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Day" valueLabel={filter.day || 'All'} onPress={() => setPicker('day')} style={{ flex: 1, minWidth: 80, marginBottom: 0 }} />
          <Select label="Destination" valueLabel={filter.destination || 'All'} onPress={() => setPicker('destination')} style={{ flex: 1, minWidth: 110, marginBottom: 0 }} />
        </View>
      </Card>
      )}

      {tab === 'ranking' && ranking && (
        <>
          <Card>
            <SectionTitle
              title="Commission by crew member"
              description={`Each flight pays ${(ranking.commission_rate * 100).toFixed(0)}% of its sales into the group pool, split equally between the group's members.`}
            />
            <RankBarChart
              data={ranking.ranking.slice(0, 10).map((entry) => ({
                label: entry.name, value: entry.commission_npr,
              }))}
              format={compact}
            />
          </Card>

          <Card>
            <SectionTitle title="Ranking table" />
            {ranking.ranking.map((entry) => (
              <View key={entry.crew_member_id} style={styles.listRow}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>
                    {entry.rank}. {entry.name}
                  </Text>
                  <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>
                    {npr(entry.commission_npr)}
                  </Text>
                </View>
                <Text style={styles.mono}>
                  {entry.crew_code} · {entry.position} · {entry.flights} sector(s)
                  {' · '}{entry.transactions} transaction(s)
                  {' · '}own sales {npr(entry.attributed_sales_npr)}
                </Text>
              </View>
            ))}
            {!ranking.ranking.length && <Text style={styles.empty}>No commission earned in this period.</Text>}
          </Card>

          <Card>
            <SectionTitle
              title="By sector"
              description={ranking.commission_model === 'POOLED'
                ? 'Each sector pays a pool, shared equally across its duty group.'
                : 'Each crew member earns on what they personally sold.'}
            />
            {(ranking.sectors || []).map((sector) => (
              <View key={sector.flight_id} style={styles.listRow}>
                <Text style={{ fontSize: 14, fontWeight: '600' }}>{sector.duty_group_label}</Text>
                <KeyValue label="Sales" value={npr(sector.revenue_npr)} />
                <KeyValue label="Commission" value={npr(sector.commission_pool_npr)} tone={COLORS.success} />
                {(sector.sellers || []).map((seller) => (
                  <View key={seller.crew_member_id} style={[styles.rowBetween, { paddingVertical: 2 }]}>
                    <Text style={{ fontSize: 13, flex: 1 }}>{seller.name}</Text>
                    <Text style={[styles.mono, { fontSize: 11 }]}>{npr(seller.sales_npr)}</Text>
                  </View>
                ))}
              </View>
            ))}
          </Card>
        </>
      )}

      {tab === 'products' && (
        <>
          <Card>
            <SectionTitle
              title="Top selling products"
              description={filter.destination ? `Filtered to ${filter.destination}.` : 'All destinations.'}
            />
            <BarChart
              data={top.slice(0, 8).map((p) => ({ label: p.product_name.split(' ')[0], value: p.revenue }))}
              format={compact}
            />
          </Card>
          <Card>
            {top.map((product, index) => (
              <View key={product.product_id} style={styles.listRow}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>
                    {index + 1}. {product.product_name}
                  </Text>
                  <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.primary }}>
                    {npr(product.revenue)}
                  </Text>
                </View>
                <Text style={styles.mono}>
                  {product.units} units · {product.flights} flight(s) · margin {npr(product.margin)} · {product.category}
                </Text>
              </View>
            ))}
            {!top.length && <Text style={styles.empty}>No sales in this period.</Text>}
          </Card>
        </>
      )}

      {tab === 'logs' && (
        <Card>
          <SectionTitle title="Activity log" description={`${logs.total.toLocaleString()} entries, newest first`} />
          {!!logs.error && <Banner tone="danger" message={logs.error} />}
          {logs.items.map((entry) => (
            <View key={entry.id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 13, fontWeight: '600', flex: 1 }}>{entry.module}</Text>
                <Pill
                  label={entry.status}
                  tone={entry.status === 'SUCCESS' ? 'success' : entry.status === 'FAILED' ? 'danger' : 'warning'}
                />
              </View>
              <Text style={{ fontSize: 13, color: COLORS.text, marginTop: 3 }}>{entry.description}</Text>
              <Text style={styles.mono}>
                {entry.employee_id || 'system'} · {entry.role || '—'} · {String(entry.timestamp).replace('T', ' ')}
              </Text>
            </View>
          ))}
          {!logs.items.length && !logs.loading && <Text style={styles.empty}>No log entries for this period.</Text>}
          <Pagination pager={logs} label="entries" />
        </Card>
      )}

      {tab === 'sales' && canSeeSales && (
        <Card>
          <SectionTitle
            title="Correct a sale"
            description="Fix a mis-keyed product or quantity. Remarks are mandatory and are carried into the Excel export."
          />
          {!!sales.error && <Banner tone="danger" message={sales.error} />}
          {sales.items.map((sale) => (
            <View key={sale.id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>
                  {sale.product_name || sale.bundle_name} × {sale.quantity}
                </Text>
                <Text style={{ fontSize: 13, fontWeight: '600' }}>{npr(sale.npr_amount)}</Text>
              </View>
              <Text style={styles.mono}>
                {sale.flight_number} · {sale.flight_date} · seat {sale.seat_number}
                {' · '}{sale.seller_name || 'unattributed'} · {sale.sale_type}
              </Text>
              {!!sale.edited_by && (
                <Text style={{ fontSize: 12, color: COLORS.warning, marginTop: 2 }}>
                  Edited by {sale.edited_by}: {sale.edit_remarks}
                </Text>
              )}
              {!sale.bundle_id && canEdit && (
                <Button
                  title={editing?.id === sale.id ? 'Cancel' : 'Correct this sale'}
                  tone="ghost"
                  small
                  style={{ marginTop: 6, alignSelf: 'flex-start' }}
                  onPress={() => {
                    if (editing?.id === sale.id) return setEditing(null);
                    setEditing(sale);
                    setEditProduct(sale.product_id);
                    setEditQuantity(String(sale.quantity));
                    setEditRemarks('');
                  }}
                />
              )}

              {editing?.id === sale.id && (
                <View style={{ marginTop: 10, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
                  <Select
                    label="Correct product"
                    valueLabel={products.find((p) => p.id === editProduct)?.product_name}
                    onPress={() => setEditPicker(true)}
                  />
                  <Field label="Quantity" value={editQuantity} onChangeText={setEditQuantity} keyboardType="number-pad" />
                  <Field
                    label="Remarks (mandatory)"
                    value={editRemarks}
                    onChangeText={setEditRemarks}
                    multiline
                    placeholder="Crew keyed water but sold a soft drink"
                  />
                  <Button title="Save correction" onPress={saveEdit} disabled={!editRemarks.trim()} />
                </View>
              )}
            </View>
          ))}
          {!sales.items.length && !sales.loading && <Text style={styles.empty}>No sales to show.</Text>}
          <Pagination pager={sales} label="sale lines" />
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
        options={[{ value: '', label: 'All months' }, ...MONTHS.map((m, i) => ({ value: i + 1, label: m }))]}
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
        options={[{ value: '', label: 'All destinations' }, ...destinations.map((d) => ({ value: d, label: d }))]}
        selectedValue={filter.destination}
        onSelect={(value) => { setFilter({ ...filter, destination: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={editPicker}
        title="Select the correct product"
        options={products.map((p) => ({ value: p.id, label: p.product_name, sub: npr(p.selling_price) }))}
        selectedValue={editProduct}
        onSelect={(value) => { setEditProduct(value); setEditPicker(false); }}
        onClose={() => setEditPicker(false)}
      />
    </ScrollView>
  );
}
