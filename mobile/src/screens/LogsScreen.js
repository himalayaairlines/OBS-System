import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport } from '../api';
import { BarChart, RankBarChart } from '../charts';
import { COLORS, MONTHS, compact, npr, styles } from '../theme';
import { usePaged, usePagedArray } from '../paging';
import {
  Banner, Button, Card, Chip, DataTable, Field, KeyValue, Metric, Notice, OptionModal, PageHeader,
  Pagination, Pill, SearchBar, SectionTitle, Select,
  RemarkLine,
} from '../ui';

const YEARS = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i);

export default function LogsScreen({ user }) {
  // Management oversee everything here. The inventory user reaches this
  // screen for the sales ledger alone, which they reconcile against what the
  // crew returned, and may correct a mis-keyed line with remarks.
  const perms = user.permissions || [];
  const canOversee = perms.includes('logs');
  const canSeeSales = perms.includes('sales_view');
  const canEdit = perms.includes('sales_edit');
  // 7.18: Finance sees the sales report only.
  const canReport = canOversee || perms.includes('sales_report');
  const [tab, setTab] = useState(canOversee ? 'ranking' : (canReport && !canSeeSales) ? 'report' : 'sales');
  const [filter, setFilter] = useState({ year: new Date().getFullYear(), month: '', day: '', destination: '' });
  const [picker, setPicker] = useState(null);
  const [message, setMessage] = useState(null);

  const [ranking, setRanking] = useState(null);
  const [top, setTop] = useState([]);
  const [modules, setModules] = useState([]);
  const [destinations, setDestinations] = useState([]);
  const [products, setProducts] = useState([]);

  const [editing, setEditing] = useState(null);
  const [editSeat, setEditSeat] = useState('');
  // Every correction of a sale, line by line (7.10), shown on request.
  const [saleHistory, setSaleHistory] = useState({});
  const [editProduct, setEditProduct] = useState(null);
  const [editQuantity, setEditQuantity] = useState('');
  const [editRemarks, setEditRemarks] = useState('');
  const [editPicker, setEditPicker] = useState(false);
  const [exporting, setExporting] = useState(false);
  // Crew ranking table: searchable, five a page, top to bottom.
  const [crewSearch, setCrewSearch] = useState('');
  const [historyFor, setHistoryFor] = useState(null);   // { id, name }
  const crewTerm = crewSearch.trim().toLowerCase();
  const rankingRows = (ranking?.ranking || []).filter((e) => !crewTerm
    || e.name.toLowerCase().includes(crewTerm) || String(e.crew_code || '').toLowerCase().includes(crewTerm));
  const rankingPager = usePagedArray(rankingRows, { pageSize: 5, resetKey: `${crewTerm}|${ranking ? ranking.ranking.length : 0}` });

  const query = useCallback(() => {
    const parts = [`year=${filter.year}`];
    if (filter.month) parts.push(`month=${filter.month}`);
    if (filter.day) parts.push(`day=${filter.day}`);
    if (filter.destination) parts.push(`destination=${filter.destination}`);
    return parts.join('&');
  }, [filter]);

  // Audit log and sales ledger are paged on the server; both grow without limit.
  const logs = usePaged(`/logs?${query().replace(/&?destination=[^&]*/, '')}`, { enabled: canOversee && tab === 'logs' });
  // 7.12: a trip is worked by two iPad logins; Management can look at one at a time.
  const [deviceFilter, setDeviceFilter] = useState('');
  const [devices, setDevices] = useState([]);
  useEffect(() => {
    if (!canSeeSales) return;
    api('/crew-accounts').then((rows) => setDevices((rows || []).map((r) => r.employee_id).filter(Boolean))).catch(() => {});
  }, [canSeeSales]);
  const sales = usePaged(`/sales${deviceFilter ? `?device=${encodeURIComponent(deviceFilter)}` : ''}`, { enabled: canSeeSales && tab === 'sales' });

  // Each tab exports exactly what it is showing, filters included.
  const EXPORTS = {
    ranking: { path: '/reports/crew-ranking.xlsx', label: 'crew ranking' },
    products: { path: '/reports/top-products.xlsx', label: 'top products' },
    logs: { path: '/reports/audit-log.xlsx', label: 'audit log' },
    sales: { path: '/reports/sales-detail.xlsx', label: 'sales' },
    report: { path: '/reports/sales-report.xlsx', label: 'sales report' },
  };
  // The standard onboard sales report (7.17).
  const [report, setReport] = useState(null);
  const [reportError, setReportError] = useState(null);
  useEffect(() => {
    if (tab !== 'report' || !canReport) return;
    api(`/reports/sales-report?${query()}`).then((r) => { setReport(r); setReportError(null); }).catch((e) => setReportError(e.message));
  }, [tab, canReport, query]);

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
    if (!canOversee) {
      if (canReport) api(`/dashboard?${query()}`).then((d) => setDestinations(d.destinations || [])).catch(() => {});
      return;
    }
    try {
      const [r, t, m, d] = await Promise.all([
        api(`/crew-ranking?${query()}&all_crew=true`),
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
  }, [query, canOversee, canReport]);

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
          seat_number: editSeat.trim() ? editSeat.trim().toUpperCase() : undefined,
          edit_remarks: editRemarks,
        },
      });
      setMessage({ tone: 'success', text: 'Sale corrected. The correction is recorded with your remarks and appears in the Excel export.' });
      setEditing(null); setEditProduct(null); setEditQuantity(''); setEditRemarks(''); setEditSeat('');
      setSaleHistory((h) => ({ ...h, [editing.id]: undefined }));
      sales.reload();
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function toggleSaleHistory(saleId) {
    if (saleHistory[saleId]) { setSaleHistory((h) => ({ ...h, [saleId]: undefined })); return; }
    try {
      const rows = await api(`/sales/${saleId}/history`);
      setSaleHistory((h) => ({ ...h, [saleId]: rows }));
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  const sectorLabel = (sale) => `${sale.flight_number} \u00b7 ${sale.origin}\u2192${sale.destination} \u00b7 ${sale.flight_date}`
    + (sale.trip_type === 'ROUND_TRIP' ? ` \u00b7 ${sale.segment === 2 ? 'second' : 'first'} segment (${String(sale.direction || '').toLowerCase()})` : '');

  const tabs = [
    ...(canReport ? [{ key: 'report', label: 'Sales report' }] : []),
    ...(canOversee ? [
      { key: 'ranking', label: 'Crew ranking' },
      { key: 'products', label: 'Top products' },
      { key: 'logs', label: 'Audit log' },
    ] : []),
    ...(canSeeSales ? [{ key: 'sales', label: canEdit ? 'Edit sales' : 'Sales' }] : []),
  ];

  if (historyFor) {
    return (
      <CrewHistory
        member={historyFor}
        filter={filter}
        query={query}
        onBack={() => setHistoryFor(null)}
      />
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title={canReport ? 'Performance & Sales Logs' : 'Onboard Sales'}
        description={canOversee
          ? 'Commission ranking, best sellers and the full activity trail.'
          : 'Every sale recorded on board. Correct a mis-keyed line with remarks.'}
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        {tabs.map((item) => (
          <Chip key={item.key} label={item.label} active={tab === item.key} onPress={() => setTab(item.key)} />
        ))}
      </View>
      {message && <Notice message={message} />}

      <Button
        title={exporting ? 'Preparing…' : `Download ${(EXPORTS[tab]?.label || '').toLowerCase()} (Excel)`}
        tone="secondary"
        onPress={exportTab}
        disabled={exporting}
        small
        style={{ marginBottom: 14, alignSelf: 'flex-end' }}
      />

      {canReport && tab !== 'sales' && (
      <Card tight>
        <View style={styles.wrap}>
          <Select label="Year" valueLabel={String(filter.year)} onPress={() => setPicker('year')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Month" valueLabel={filter.month ? MONTHS[filter.month - 1] : 'All'} onPress={() => setPicker('month')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Day" valueLabel={filter.day || 'All'} onPress={() => setPicker('day')} style={{ flex: 1, minWidth: 80, marginBottom: 0 }} />
          <Select label="Destination" valueLabel={filter.destination || 'All'} onPress={() => setPicker('destination')} style={{ flex: 1, minWidth: 110, marginBottom: 0 }} />
        </View>
      </Card>
      )}

      {tab === 'report' && (
        <SalesReportView report={report} error={reportError} />
      )}

      {tab === 'ranking' && ranking && (
        <>
          <Card>
            <SectionTitle
              title="Commission by crew member"
              description={`Each sector pays ${(ranking.commission_rate * 100).toFixed(0)}% of its product sales into a pool split equally between the crew on it (NPR 100 and 4 crew = NPR 25 each). Combo sales are ranked separately below.`}
            />
            <RankBarChart
              data={ranking.ranking.filter((e) => e.commission_npr > 0).slice(0, 10).map((entry) => ({
                label: entry.name, value: entry.commission_npr,
              }))}
              format={compact}
            />
          </Card>

          <Card>
            <SectionTitle
              title="Ranking table"
              description="Every crew member, highest commission first. Click a name to see their full sales history."
            />
            <SearchBar value={crewSearch} onChangeText={setCrewSearch} placeholder="Search crew name or code…" />
            <DataTable
              breakpoint={760}
              rows={rankingPager.items}
              rowKey={(e) => String(e.crew_member_id)}
              emptyText={crewTerm ? 'No crew member matches.' : 'No crew members yet.'}
              columns={[
                { key: 'rank', label: '#', flex: 0.4, render: (e) => String(e.rank) },
                {
                  key: 'name', label: 'Crew member', flex: 2.2, render: (e) => (
                    <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} />
                  ),
                },
                { key: 'sectors', label: 'Sectors', flex: 0.8, align: 'right', render: (e) => String(e.flights) },
                { key: 'tx', label: 'Transactions', flex: 1, align: 'right', render: (e) => String(e.transactions) },
                { key: 'sales', label: 'Share of sales', flex: 1.2, align: 'right', render: (e) => npr(e.attributed_sales_npr) },
                {
                  key: 'commission', label: 'Pool commission', flex: 1.2, align: 'right', render: (e) => (
                    <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>{npr(e.commission_npr)}</Text>
                  ),
                },
                { key: 'combo', label: 'Combo', flex: 1, align: 'right', render: (e) => npr(e.combo_commission_npr || 0) },
                { key: 'total', label: 'Total', flex: 1.1, align: 'right', render: (e) => <Text style={{ fontSize: 14, fontWeight: '700' }}>{npr(e.total_commission_npr ?? e.commission_npr)}</Text> },
              ]}
              renderStacked={(e) => (
                <>
                  <View style={styles.rowBetween}>
                    <View style={[styles.row, { gap: 6, flex: 1 }]}>
                      <Text style={{ fontSize: 14, fontWeight: '600' }}>{e.rank}.</Text>
                      <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} />
                    </View>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>{npr(e.commission_npr)}</Text>
                  </View>
                  <Text style={styles.mono}>
                    {e.flights} sector(s) · share of sales {npr(e.attributed_sales_npr)} · combo {npr(e.combo_commission_npr || 0)} · total {npr(e.total_commission_npr ?? e.commission_npr)}
                  </Text>
                </>
              )}
            />
            <Pagination pager={rankingPager} label="crew members" sizes={[5, 10, 25]} />
          </Card>

          <Card>
            <SectionTitle
              title="Combo sales ranking"
              description="Commission on combos is paid to the crew member who sold the combo, ranked here on its own."
            />
            {(ranking.combo_ranking || []).length ? (
              <DataTable
                breakpoint={760}
                rows={ranking.combo_ranking}
                rowKey={(e) => String(e.crew_member_id)}
                columns={[
                  { key: 'rank', label: '#', flex: 0.4, render: (e) => String(e.rank) },
                  { key: 'name', label: 'Crew member', flex: 2.2, render: (e) => <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} /> },
                  { key: 'sectors', label: 'Sectors', flex: 0.8, align: 'right', render: (e) => String(e.flights) },
                  { key: 'tx', label: 'Combos', flex: 0.9, align: 'right', render: (e) => String(e.transactions) },
                  { key: 'sales', label: 'Combo sales', flex: 1.2, align: 'right', render: (e) => npr(e.combo_sales_npr) },
                  { key: 'commission', label: 'Combo commission', flex: 1.3, align: 'right', render: (e) => <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>{npr(e.combo_commission_npr)}</Text> },
                ]}
                renderStacked={(e) => (
                  <View style={styles.rowBetween}>
                    <View style={[styles.row, { gap: 6, flex: 1 }]}>
                      <Text style={{ fontSize: 14, fontWeight: '600' }}>{e.rank}.</Text>
                      <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} />
                    </View>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>{npr(e.combo_commission_npr)}</Text>
                  </View>
                )}
              />
            ) : <Text style={styles.empty}>No combo sold in this period.</Text>}
          </Card>

          <Card>
            <SectionTitle
              title="Sales ranking"
              description="Who sold the most, by the value of the sales each crew member recorded on their own login (normal and combo)."
            />
            {(ranking.sales_ranking || []).length ? (
              <DataTable
                breakpoint={760}
                rows={ranking.sales_ranking.slice(0, 25)}
                rowKey={(e) => String(e.crew_member_id)}
                columns={[
                  { key: 'rank', label: '#', flex: 0.4, render: (e) => String(e.rank) },
                  { key: 'name', label: 'Crew member', flex: 2.2, render: (e) => <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} /> },
                  { key: 'sectors', label: 'Sectors', flex: 0.8, align: 'right', render: (e) => String(e.flights) },
                  { key: 'tx', label: 'Sales', flex: 0.8, align: 'right', render: (e) => String(e.transactions) },
                  { key: 'units', label: 'Units', flex: 0.8, align: 'right', render: (e) => String(e.units) },
                  { key: 'value', label: 'Sales value', flex: 1.3, align: 'right', render: (e) => <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>{npr(e.sales_npr)}</Text> },
                ]}
                renderStacked={(e) => (
                  <View style={styles.rowBetween}>
                    <View style={[styles.row, { gap: 6, flex: 1 }]}>
                      <Text style={{ fontSize: 14, fontWeight: '600' }}>{e.rank}.</Text>
                      <CrewLink entry={e} onPress={() => setHistoryFor({ id: e.crew_member_id, name: e.name })} />
                    </View>
                    <Text style={{ fontSize: 14, fontWeight: '600' }}>{npr(e.sales_npr)}</Text>
                  </View>
                )}
              />
            ) : <Text style={styles.empty}>No sale recorded under a crew login in this period.</Text>}
          </Card>

          <Card>
            <SectionTitle
              title="By sector"
              description="Each sector pays a pool on its product sales, shared equally across its duty group; combos are paid to their sellers."
            />
            {(ranking.sectors || []).map((sector) => (
              <View key={sector.flight_id} style={styles.listRow}>
                <Text style={{ fontSize: 14, fontWeight: '600' }}>{sector.duty_group_label}</Text>
                <KeyValue label="Product sales" value={npr(sector.product_revenue_npr ?? sector.revenue_npr)} />
                <KeyValue label={`Pool \u00f7 ${sector.member_count} crew`} value={`${npr(sector.commission_pool_npr)} = ${npr(sector.share_npr || 0)} each`} tone={COLORS.success} />
                {!!sector.combo_revenue_npr && <KeyValue label="Combo sales" value={`${npr(sector.combo_revenue_npr)} \u2192 ${npr(sector.combo_commission_npr)} commission`} />}
                {(sector.sellers || []).map((seller) => (
                  <View key={seller.crew_member_id} style={[styles.rowBetween, { paddingVertical: 2 }]}>
                    <Text style={{ fontSize: 13, flex: 1 }}>{seller.name} (combo)</Text>
                    <Text style={[styles.mono, { fontSize: 11 }]}>{npr(seller.combo_sales_npr)} → {npr(seller.combo_commission_npr)}</Text>
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
            title={canEdit ? 'Correct a sale' : 'Sales'}
            description={canEdit
              ? 'Fix a mis-keyed product, quantity or seat. Remarks are mandatory; every correction is recorded and carried into the Excel export.'
              : 'Every sale recorded on board.'}
          />
          <Select
            label="iPad login"
            valueLabel={deviceFilter || 'All iPads'}
            onPress={() => setPicker('device')}
            style={{ marginBottom: 10 }}
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
                {sectorLabel(sale)} · seat {sale.seat_number}
                {sale.crew_id ? ` · iPad ${sale.crew_id}` : ''}
                {' · '}
                {sale.sold_by_crew_member_id && canOversee ? (
                  <Text
                    style={{ color: COLORS.primary, fontWeight: '600' }}
                    onPress={() => setHistoryFor({ id: sale.sold_by_crew_member_id, name: sale.seller_name })}
                  >
                    {sale.seller_name}
                  </Text>
                ) : (sale.seller_name || 'unattributed')}
                {' · '}{sale.sale_type}
              </Text>
              {!!sale.remarks && sale.sale_type !== 'NORMAL' && (
                <RemarkLine label={`${sale.sale_type} remarks`} text={sale.remarks} />
              )}
              {!!sale.edited_by && (
                <RemarkLine label={`Last edited by ${sale.edited_by}`} text={sale.edit_remarks} />
              )}
              <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
                {!sale.bundle_id && canEdit && (
                  <Button
                    title={editing?.id === sale.id ? 'Cancel' : 'Correct this sale'}
                    tone="ghost"
                    small
                    onPress={() => {
                      if (editing?.id === sale.id) return setEditing(null);
                      setEditing(sale);
                      setEditProduct(sale.product_id);
                      setEditQuantity(String(sale.quantity));
                      setEditSeat(sale.seat_number || '');
                      setEditRemarks('');
                    }}
                  />
                )}
                {!!sale.edit_count && (
                  <Button
                    title={saleHistory[sale.id] ? 'Hide corrections' : `${sale.edit_count} correction(s)`}
                    tone="ghost"
                    small
                    onPress={() => toggleSaleHistory(sale.id)}
                  />
                )}
              </View>
              {!!saleHistory[sale.id] && (
                <View style={{ marginTop: 4 }}>
                  {saleHistory[sale.id].map((h) => (
                    <View key={h.id}>
                      <Text style={styles.mono}>
                        {String(h.changed_at || '').replace('T', ' ').slice(0, 16)} · {h.changed_by} · {h.changes.join('; ') || 'no field changed'}
                      </Text>
                      <RemarkLine text={h.remarks} style={{ marginTop: 2, marginBottom: 4 }} />
                    </View>
                  ))}
                </View>
              )}

              {editing?.id === sale.id && (
                <View style={{ marginTop: 10, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
                  <Text style={[styles.label, { marginBottom: 2 }]}>Sector</Text>
                  <Text style={[styles.mono, { marginBottom: 8 }]}>
                    {sectorLabel(sale)}
                    {sale.aircraft ? ` · ${sale.aircraft}` : ''}
                    {sale.duty_group_label ? ` · ${sale.duty_group_label}` : ''}
                    {'\n'}Recorded {String(sale.transaction_time || '').replace('T', ' ').slice(0, 16)} · sold by {sale.seller_name || 'unattributed'} · {sale.sale_type} · paid {sale.transaction_currency} {sale.transaction_currency_amount}
                  </Text>
                  <Select
                    label="Correct product"
                    valueLabel={products.find((p) => p.id === editProduct)?.product_name}
                    onPress={() => setEditPicker(true)}
                  />
                  <View style={[styles.row, styles.gap8]}>
                    <View style={styles.flex1}><Field label="Quantity" value={editQuantity} onChangeText={setEditQuantity} keyboardType="number-pad" /></View>
                    <View style={styles.flex1}><Field label="Seat" value={editSeat} onChangeText={setEditSeat} autoCapitalize="characters" /></View>
                  </View>
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
        visible={picker === 'device'}
        title="iPad login"
        options={[{ value: '', label: 'All iPads' }, ...[...new Set([...devices, ...sales.items.map((x) => x.crew_id).filter(Boolean)])].sort().map((d) => ({ value: d, label: d }))]}
        selectedValue={deviceFilter}
        onSelect={(value) => { setDeviceFilter(value); setPicker(null); }}
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

/* ------------------------------------------------------------------ */
/* A crew member's name that opens their history                       */
/* ------------------------------------------------------------------ */
function CrewLink({ entry, onPress }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="link" hitSlop={4}>
      {({ hovered }) => (
        <View>
          <Text style={{
            fontSize: 14, fontWeight: '600', color: COLORS.primary,
            textDecorationLine: hovered ? 'underline' : 'none',
          }}
          >
            {entry.name}
          </Text>
          <Text style={[styles.mono, { marginTop: 0 }]}>{entry.crew_code} · {entry.position}</Text>
        </View>
      )}
    </Pressable>
  );
}

/* ------------------------------------------------------------------ */
/* Everything one crew member sold                                     */
/* ------------------------------------------------------------------ */
function CrewHistory({ member, filter, query, onBack }) {
  const [scope, setScope] = useState('all');   // 'all' | 'period'
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [itemSearch, setItemSearch] = useState('');

  useEffect(() => {
    (async () => {
      try {
        setData(await api(`/crew-members/${member.id}/history${scope === 'period' ? `?${query()}` : ''}`));
        setError(null);
      } catch (e) {
        setError(e.message);
      }
    })();
  }, [member.id, scope, query]);

  const term = itemSearch.trim().toLowerCase();
  const lines = (data?.sales || []).filter((l) => !term
    || `${l.item_name} ${l.flight_number} ${l.seat_number} ${l.order_ref}`.toLowerCase().includes(term));
  const salesPager = usePagedArray(lines, { pageSize: 10, resetKey: `${term}|${scope}` });
  const sectorPager = usePagedArray(data?.sectors || [], { pageSize: 10, resetKey: scope });
  const periodLabel = [filter.day, filter.month ? MONTHS[filter.month - 1] : '', filter.year].filter(Boolean).join(' ');

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title={data?.crew_member?.name || member.name || 'Crew member'}
        description={data ? `${data.crew_member.crew_code} · ${data.crew_member.position}`
          + (data.crew_member.active ? '' : ' · inactive') : 'Loading…'}
        right={<Button title="Back to ranking" tone="ghost" small onPress={onBack} />}
      />
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        <Chip label="Whole history" active={scope === 'all'} onPress={() => setScope('all')} />
        <Chip label={`Selected period (${periodLabel || 'all'})`} active={scope === 'period'} onPress={() => setScope('period')} />
      </View>
      {!!error && <Banner tone="danger" message={error} />}

      {data && (
        <>
          <View style={[styles.wrap, { gap: 12, marginBottom: 12 }]}>
            <Metric label="Commission" value={npr(data.totals.commission_npr)} tone="success" />
            <Metric label="Own sales" value={npr(data.totals.sales_npr)} />
            <Metric label="Items sold" value={String(data.totals.items)} />
            <Metric label="Transactions" value={String(data.totals.transactions)} />
            <Metric label="Sectors worked" value={String(data.totals.sectors)} />
          </View>

          <Card>
            <SectionTitle
              title="Sectors worked"
              description={data.commission_model === 'POOLED'
                ? `Commission is ${(data.commission_rate * 100).toFixed(0)}% of each sector's sales, shared equally by its crew.`
                : `Commission is ${(data.commission_rate * 100).toFixed(0)}% of their own sales.`}
            />
            <DataTable
              rows={sectorPager.items}
              rowKey={(r) => String(r.flight_id)}
              emptyText="No sectors in this period."
              columns={[
                { key: 'date', label: 'Date', flex: 1, render: (r) => r.flight_date },
                { key: 'flight', label: 'Flight', flex: 1, render: (r) => r.flight_number },
                { key: 'sector', label: 'Sector', flex: 1, render: (r) => `${r.origin}–${r.destination}` },
                { key: 'group', label: 'Crew group', flex: 1.3, render: (r) => r.duty_group_label || '—' },
                { key: 'items', label: 'Items', flex: 0.6, align: 'right', render: (r) => String(r.items) },
                { key: 'sales', label: 'Own sales', flex: 1, align: 'right', render: (r) => npr(r.sales_npr) },
                { key: 'commission', label: 'Commission', flex: 1, align: 'right', render: (r) => npr(r.commission_npr) },
              ]}
              renderStacked={(r) => (
                <>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600' }}>{r.flight_number} · {r.origin}–{r.destination}</Text>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.success }}>{npr(r.commission_npr)}</Text>
                  </View>
                  <Text style={styles.mono}>{r.flight_date} · {r.items} item(s) · own sales {npr(r.sales_npr)}</Text>
                </>
              )}
            />
            <Pagination pager={sectorPager} label="sectors" />
          </Card>

          <Card>
            <SectionTitle title="Every sale" description="Newest first. Complementary and cancelled lines are shown but earn no commission." />
            <SearchBar value={itemSearch} onChangeText={setItemSearch} placeholder="Search item, flight or seat…" />
            <DataTable
              breakpoint={820}
              rows={salesPager.items}
              rowKey={(l) => String(l.id)}
              emptyText={term ? 'No sale matches.' : 'No sales in this period.'}
              columns={[
                { key: 'time', label: 'Date & time', flex: 1.3, render: (l) => String(l.transaction_time || '').replace('T', ' ').slice(0, 16) },
                { key: 'flight', label: 'Flight', flex: 1.1, render: (l) => `${l.flight_number} ${l.origin}–${l.destination}` },
                { key: 'seat', label: 'Seat', flex: 0.5, render: (l) => l.seat_number },
                { key: 'item', label: 'Item', flex: 1.6, render: (l) => l.item_name || '—' },
                { key: 'qty', label: 'Qty', flex: 0.4, align: 'right', render: (l) => String(l.quantity) },
                { key: 'amount', label: 'Amount', flex: 1, align: 'right', render: (l) => npr(l.npr_amount) },
                {
                  key: 'type', label: 'Type', flex: 0.9, render: (l) => (
                    <Pill label={l.sale_type} tone={l.sale_type === 'NORMAL' ? 'success' : 'warning'} />
                  ),
                },
              ]}
              renderStacked={(l) => (
                <>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>{l.item_name} × {l.quantity}</Text>
                    <Text style={{ fontSize: 13, fontWeight: '600' }}>{npr(l.npr_amount)}</Text>
                  </View>
                  <Text style={styles.mono}>
                    {String(l.transaction_time || '').replace('T', ' ').slice(0, 16)} · {l.flight_number}
                    {' '}{l.origin}–{l.destination} · seat {l.seat_number} · {l.sale_type}
                  </Text>
                  {!!l.remarks && l.sale_type !== 'NORMAL' && <RemarkLine label={`${l.sale_type} remarks`} text={l.remarks} />}
                  {!!l.edit_remarks && <RemarkLine label={`Edited by ${l.edited_by}`} text={l.edit_remarks} />}
                </>
              )}
            />
            <Pagination pager={salesPager} label="sales" />
          </Card>
        </>
      )}
    </ScrollView>
  );
}


/* ------------------------------------------------------------------ */
/* The standard onboard sales report, as the workbook lays it out (7.17) */
/* ------------------------------------------------------------------ */
function SalesReportView({ report, error }) {
  const [section, setSection] = useState('sectors');
  if (error) return <Card><Banner tone="danger" message={error} /></Card>;
  if (!report) return <Card><Text style={styles.empty}>Preparing the report\u2026</Text></Card>;
  const s = report.summary;
  const tot = s.revenue || 1;
  const Row = ({ cells, bold, widths }) => (
    <View style={[styles.rowBetween, { paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt, backgroundColor: bold ? COLORS.surfaceAlt : 'transparent' }]}>
      {cells.map((c, i) => (
        <Text key={i} style={[i === 0 ? { flex: 2, fontSize: 13 } : [styles.mono, { flex: 1, textAlign: 'right' }], bold ? { fontWeight: '700', color: COLORS.text } : null, widths?.[i] ? { flex: widths[i] } : null]} numberOfLines={2}>{c}</Text>
      ))}
    </View>
  );
  const Head = ({ cells, widths }) => (
    <View style={[styles.rowBetween, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border }]}>
      {cells.map((c, i) => <Text key={i} style={[styles.mono, i === 0 ? { flex: 2 } : { flex: 1, textAlign: 'right' }, widths?.[i] ? { flex: widths[i] } : null]}>{c}</Text>)}
    </View>
  );
  return (
    <>
      <Card>
        <SectionTitle
          title={report.title}
          description={`Period: ${report.period} \u00b7 ${report.destination} \u00b7 Currency NPR \u00b7 Generated ${report.generated_at}. The Excel download has the same sections, one per sheet.`}
        />
        <View style={styles.wrap}>
          <Metric label="Total onboard revenue" value={npr(s.revenue)} tone="primary" />
          <Metric label="Passengers" value={compact(s.passengers)} />
          <Metric label="Sectors" value={String(s.flights)} />
          <Metric label="Transactions" value={compact(s.transactions)} />
          <Metric label="Units" value={compact(s.units)} />
          <Metric label="Average basket" value={npr(s.basket)} />
          <Metric label="Revenue per passenger" value={npr(s.rpp)} tone={s.achievement >= 100 ? 'success' : 'warning'} sub={`target ${npr(s.target_rpp)} \u00b7 ${Number(s.achievement).toFixed(1)}%`} />
          <Metric label="Shortfall / surplus vs target" value={`${s.revenue - s.revenue_at_target >= 0 ? '+' : ''}${npr(s.revenue - s.revenue_at_target)}`} tone={s.revenue - s.revenue_at_target >= 0 ? 'success' : 'danger'} />
          <Metric label={`Commission pool (${Math.round(s.commission_rate * 100)}%)`} value={npr(s.commission_npr)} tone="success" />
          <Metric label="Sectors at / below target" value={`${s.sectors_at_target} / ${s.sectors_below_target}`} sub={`${s.sectors_no_sales} with no sales`} />
        </View>
        {!!report.by_type.length && (
          <Text style={[styles.mono, { marginTop: 8 }]}>
            {report.by_type.map((t) => `${t.sale_type.toLowerCase()}: ${t.transactions} txn, ${t.units} units`).join(' \u00b7 ')}
          </Text>
        )}
      </Card>

      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {[['sectors', 'By sector'], ['routes', 'By route'], ['products', 'By product'], ['categories', 'By category'], ['devices', 'By iPad'], ['currency', 'Currency & daily']].map(([k, l]) => (
          <Chip key={k} label={l} active={section === k} onPress={() => setSection(k)} />
        ))}
      </View>

      {section === 'sectors' && (
        <Card>
          <SectionTitle title="Sector-wise sales" description="Every leg in the period with PAX, revenue and revenue per passenger against the target." />
          <Head cells={['Date \u00b7 Flight \u00b7 Route', 'PAX', 'Txns', 'Units', 'Revenue', 'RPP', 'vs target']} widths={[2.4]} />
          {report.sectors.map((x) => (
            <View key={x.id}>
              <Row widths={[2.4]} cells={[`${x.flight_date} ${x.flight_number} ${x.route}${x.trip_type === 'ROUND_TRIP' ? ` (${x.direction.toLowerCase()})` : ''}`, String(x.passengers || 0), String(x.transactions), String(x.units), compact(x.revenue), compact(x.rpp), `${x.vs_target >= 0 ? '+' : ''}${compact(x.vs_target)}`]} />
              <Text style={[styles.mono, { marginTop: -2, marginBottom: 4 }]}>
                {x.aircraft_type || 'aircraft n/a'} \u00b7 {x.duty_group_label || 'crew not entered'}{x.crew_count ? ` (${x.crew_count})` : ''} \u00b7 iPads {x.devices || '\u2014'}
                {x.complementary ? ` \u00b7 ${x.complementary} complementary` : ''}{x.cancellations ? ` \u00b7 ${x.cancellations} cancelled` : ''} \u00b7 stock return {x.stock_return || 'not submitted'}
              </Text>
            </View>
          ))}
          <Row bold widths={[2.4]} cells={['Total', compact(s.passengers), String(s.transactions), String(s.units), compact(s.revenue), compact(s.rpp), `${s.gap >= 0 ? '+' : ''}${compact(s.gap)}`]} />
          {!report.sectors.length && <Text style={styles.empty}>No sectors in this period.</Text>}
        </Card>
      )}
      {section === 'routes' && (
        <Card>
          <SectionTitle title="Route performance" />
          <Head cells={['Route', 'Sectors', 'PAX', 'Revenue', 'RPP', 'Share']} />
          {report.routes.map((x) => <Row key={x.route} cells={[x.route, String(x.flights), compact(x.passengers), compact(x.revenue), compact(x.rpp), `${(x.revenue / tot * 100).toFixed(1)}%`]} />)}
          <Row bold cells={['Total', String(s.flights), compact(s.passengers), compact(s.revenue), compact(s.rpp), '100%']} />
        </Card>
      )}
      {section === 'products' && (
        <Card>
          <SectionTitle title="Product performance" description="Best seller first, with gross margin." />
          <Head cells={['Product', 'Units', 'Revenue', 'Margin', 'Share']} widths={[2.4]} />
          {report.products.filter((p) => p.revenue > 0 || p.units > 0).map((p, i) => (
            <Row key={p.product_id} widths={[2.4]} cells={[`${i + 1}. ${p.product_name}`, String(p.units), compact(p.revenue), compact(p.margin), `${(p.revenue / tot * 100).toFixed(1)}%`]} />
          ))}
        </Card>
      )}
      {section === 'categories' && (
        <Card>
          <SectionTitle title="Revenue by category" />
          <Head cells={['Category', 'Units', 'Revenue', 'Margin']} />
          {report.categories.map((c) => <Row key={c.category} cells={[c.category, String(c.units), compact(c.revenue), compact(c.margin)]} />)}
          <Row bold cells={['Total', String(report.categories.reduce((a, c) => a + c.units, 0)), compact(report.categories.reduce((a, c) => a + c.revenue, 0)), compact(report.categories.reduce((a, c) => a + c.margin, 0))]} />
        </Card>
      )}
      {section === 'devices' && (
        <Card>
          <SectionTitle title="Sales recorded per OBS iPad login" />
          <Head cells={['iPad login', 'Sectors', 'Txns', 'Units', 'Revenue', 'Share']} />
          {report.devices.map((d) => <Row key={d.device} cells={[d.device, String(d.sectors), String(d.transactions), String(d.units), compact(d.revenue), `${(d.revenue / tot * 100).toFixed(1)}%`]} />)}
          {!report.devices.length && <Text style={styles.empty}>No sales in this period.</Text>}
        </Card>
      )}
      {section === 'currency' && (
        <Card>
          <SectionTitle title="Payment currency mix" description="Converted to NPR at the rate in force when each sale was made." />
          <Head cells={['Currency', 'Lines', 'Revenue in NPR']} />
          {report.currencies.map((c) => <Row key={c.currency} cells={[c.currency, String(c.lines), compact(c.revenue)]} />)}
          <SectionTitle title="Daily revenue" />
          <BarChart data={report.daily.map((d) => ({ label: d.day, value: d.revenue }))} format={compact} />
        </Card>
      )}
    </>
  );
}
