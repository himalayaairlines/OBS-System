import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { usePaged } from '../paging';
import { COLORS, MONTHS, npr, styles } from '../theme';
import {
  Banner, Button, Card, Chip, Field, Metric, Notice, OptionModal, PageHeader, Pagination, Pill, RemarkLine,
  SectionTitle, Select,
} from '../ui';

/*
 * The Sales page for Management, Inventory and the administrator (7.13).
 *
 * Not a till: every sector in the period with its total, then one sector in
 * full — what sold, on which iPad, by whom, in which currency — with the
 * sales themselves underneath, filterable, and correctable with remarks.
 * Every correction is kept in the sale's history and the audit log.
 */
const YEARS = Array.from({ length: 4 }, (_, i) => new Date().getFullYear() - i);
const route = (f) => `${f.origin}→${f.destination}`;
const when = (s) => String(s || '').replace('T', ' ').slice(0, 16);

export default function FlightSalesScreen({ user, online }) {
  const canEdit = user.permissions?.includes('sales_edit');
  const [filter, setFilter] = useState({ year: new Date().getFullYear(), month: '', day: '', destination: '', flight: '' });
  const [destinations, setDestinations] = useState([]);
  const [picker, setPicker] = useState(null);
  const [selected, setSelected] = useState(null);     // flight id opened in full
  const [message, setMessage] = useState(null);

  const query = useMemo(() => {
    const parts = [`year=${filter.year}`];
    if (filter.month) parts.push(`month=${filter.month}`);
    if (filter.day) parts.push(`day=${filter.day}`);
    if (filter.destination) parts.push(`destination=${filter.destination}`);
    if (filter.flight.trim()) parts.push(`flight_number=${encodeURIComponent(filter.flight.trim())}`);
    return parts.join('&');
  }, [filter]);
  const flights = usePaged(`/sales/flights?${query}`, { enabled: online && !selected, pageSize: 25 });

  useEffect(() => {
    if (!online) return;
    api(`/dashboard?year=${filter.year}`).then((d) => setDestinations(d.destinations || [])).catch(() => {});
  }, [online, filter.year]);

  const periodTotal = flights.items.reduce((a, f) => a + Number(f.revenue_npr || 0), 0);
  const periodLabel = [filter.day, filter.month ? MONTHS[filter.month - 1] : '', filter.year].filter(Boolean).join(' ');

  if (selected) {
    return (
      <FlightDetail
        flightId={selected}
        user={user}
        canEdit={canEdit}
        online={online}
        onBack={() => { setSelected(null); flights.reload(); }}
      />
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Sales"
        description="Every sector with its sales total. Open a sector to see what sold, on which iPad and by whom."
        right={<Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'warning'} />}
      />
      {message && <Notice message={message} />}

      <Card>
        <SectionTitle title="Period" description="Sectors are listed newest first; the total on the right is the sector's normal sales in NPR." />
        <View style={[styles.wrap, styles.gap8]}>
          <Select label="Year" valueLabel={String(filter.year)} onPress={() => setPicker('year')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Month" valueLabel={filter.month ? MONTHS[filter.month - 1] : 'All'} onPress={() => setPicker('month')} style={{ flex: 1, minWidth: 100, marginBottom: 0 }} />
          <Select label="Day" valueLabel={filter.day || 'All'} onPress={() => setPicker('day')} style={{ flex: 1, minWidth: 80, marginBottom: 0 }} />
          <Select label="Destination" valueLabel={filter.destination || 'All'} onPress={() => setPicker('destination')} style={{ flex: 1, minWidth: 110, marginBottom: 0 }} />
          <View style={{ flex: 1, minWidth: 120 }}>
            <Field label="Flight no." value={filter.flight} onChangeText={(v) => setFilter({ ...filter, flight: v })} placeholder="H9" autoCapitalize="characters" style={{ marginBottom: 0 }} />
          </View>
        </View>
        <View style={[styles.wrap, { marginTop: 12 }]}>
          <Metric label={`Sectors (${periodLabel})`} value={String(flights.total)} />
          <Metric label="Sales on this page" value={npr(periodTotal)} tone="primary" />
        </View>
      </Card>

      <Card>
        <SectionTitle title="Sectors" description={flights.loading ? 'Loading…' : `${flights.total} sector(s). A highlighted sector has something the two iPads disagreed on, or a figure that does not add up — open it to see what.`} />
        {!!flights.error && <Banner tone="danger" message={flights.error} />}
        {!online && <Banner tone="warning" message="Connect to load the sales." />}
        {flights.items.map((f) => (
          <Pressable key={f.id} onPress={() => setSelected(f.id)} accessibilityRole="button"
            style={[styles.listRow, { paddingVertical: 12 }, f.differences ? { backgroundColor: COLORS.warningSoft, borderRadius: 10, paddingHorizontal: 8, borderLeftWidth: 4, borderLeftColor: COLORS.warning } : null]}>
            <View style={styles.rowBetween}>
              <View style={{ flex: 1 }}>
                <View style={[styles.row, styles.gap8, { flexWrap: 'wrap' }]}>
                  <Text style={{ fontSize: 15, fontWeight: '700', color: f.differences ? COLORS.warning : COLORS.text }}>
                    {f.flight_number} · {route(f)}
                  </Text>
                  {!!f.differences && <Pill label={`${f.differences} DIFFERENCE(S)`} tone="warning" />}
                </View>
                <Text style={styles.mono}>
                  {f.flight_date} · {f.trip_type === 'ROUND_TRIP' ? `segment ${f.segment}` : 'one way'}
                  {f.aircraft_type ? ` · ${f.aircraft_type}` : ''}{f.passengers ? ` · PAX ${f.passengers}` : ''}
                  {f.devices?.length ? ` · ${f.devices.join(' + ')}` : ''}
                </Text>
                <Text style={[styles.mono, { marginTop: 2 }]}>
                  {f.transactions} transaction(s) · {f.units} unit(s)
                  {f.complementary ? ` · ${f.complementary} complementary` : ''}
                  {f.cancellations ? ` · ${f.cancellations} cancelled` : ''}
                  {f.edits ? ` · ${f.edits} correction(s)` : ''}
                </Text>
              </View>
              <View style={{ alignItems: 'flex-end', marginLeft: 10 }}>
                <Text style={{ fontSize: 16, fontWeight: '800', color: COLORS.primary }}>{npr(f.revenue_npr)}</Text>
                <Pill
                  label={f.trip_completed_at ? 'COMPLETED' : f.status}
                  tone={f.trip_completed_at ? 'success' : f.status === 'OPEN' ? 'info' : f.status === 'FLOWN' ? 'warning' : 'muted'}
                />
                <Text style={[styles.mono, { marginTop: 4, color: COLORS.primary }]}>Open ›</Text>
              </View>
            </View>
          </Pressable>
        ))}
        {!flights.items.length && !flights.loading && <Text style={styles.empty}>No sectors in this period.</Text>}
        <Pagination pager={flights} label="sectors" />
      </Card>

      <OptionModal
        visible={picker === 'year'} title="Year"
        options={YEARS.map((y) => ({ value: y, label: String(y) }))}
        selectedValue={filter.year}
        onSelect={(value) => { setFilter({ ...filter, year: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'month'} title="Month"
        options={[{ value: '', label: 'All months' }, ...MONTHS.map((m, i) => ({ value: i + 1, label: m }))]}
        selectedValue={filter.month}
        onSelect={(value) => { setFilter({ ...filter, month: value, day: '' }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'day'} title="Day"
        options={[{ value: '', label: 'All days' }, ...Array.from({ length: 31 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))]}
        selectedValue={filter.day}
        onSelect={(value) => { setFilter({ ...filter, day: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'destination'} title="Destination"
        options={[{ value: '', label: 'All destinations' }, ...destinations.map((d) => ({ value: d, label: d }))]}
        selectedValue={filter.destination}
        onSelect={(value) => { setFilter({ ...filter, destination: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
    </ScrollView>
  );
}

/* ------------------------------------------------------------------ */
/* One sector in full                                                  */
/* ------------------------------------------------------------------ */
function FlightDetail({ flightId, user, canEdit, online, onBack }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [products, setProducts] = useState([]);
  // Filters on the sales list.
  const [device, setDevice] = useState('');
  const [productId, setProductId] = useState('');
  const [saleType, setSaleType] = useState('');
  const [sellerTerm, setSellerTerm] = useState('');
  // Correction
  const [editing, setEditing] = useState(null);
  const [editProduct, setEditProduct] = useState(null);
  const [editQuantity, setEditQuantity] = useState('');
  const [editSeat, setEditSeat] = useState('');
  const [editRemarks, setEditRemarks] = useState('');
  const [editPicker, setEditPicker] = useState(false);
  const [history, setHistory] = useState({});

  const [diff, setDiff] = useState(null);
  const load = useCallback(async () => {
    try {
      setSummary(await api(`/sales/flights/${flightId}/summary`));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
    try { setDiff(await api(`/sales/flights/${flightId}/differences`)); } catch { setDiff(null); }
  }, [flightId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (canEdit) api('/products').then(setProducts).catch(() => {});
  }, [canEdit]);

  const listPath = useMemo(() => {
    const parts = [`flight_id=${flightId}`];
    if (device) parts.push(`device=${encodeURIComponent(device)}`);
    if (productId) parts.push(`product_id=${productId}`);
    if (saleType) parts.push(`sale_type=${saleType}`);
    if (sellerTerm.trim()) parts.push(`seller_name=${encodeURIComponent(sellerTerm.trim())}`);
    return `/sales?${parts.join('&')}`;
  }, [flightId, device, productId, saleType, sellerTerm]);
  const sales = usePaged(listPath, { enabled: online, pageSize: 25 });

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
      setMessage({ tone: 'success', text: 'Sale corrected. The correction is kept with the sale, in the audit log and in the Excel export.' });
      setEditing(null); setEditProduct(null); setEditQuantity(''); setEditRemarks(''); setEditSeat('');
      setHistory((h) => ({ ...h, [editing.id]: undefined }));
      sales.reload();
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function toggleHistory(saleId) {
    if (history[saleId]) { setHistory((h) => ({ ...h, [saleId]: undefined })); return; }
    try {
      const rows = await api(`/sales/${saleId}/history`);
      setHistory((h) => ({ ...h, [saleId]: rows }));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  const f = summary?.flight;
  const t = summary?.totals;
  const selectedProduct = summary?.by_product?.find((p) => String(p.product_id) === String(productId));
  const typeLabel = { NORMAL: 'Normal', COMPLEMENTARY: 'Complementary', CANCELLATION: 'Cancellation' };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title={f ? `${f.flight_number} · ${route(f)}` : 'Sector'}
        description={f ? `${f.flight_date}${f.trip_type === 'ROUND_TRIP' ? ` · segment ${f.segment}` : ''}${f.aircraft_type ? ` · ${f.aircraft_type}` : ''}${f.passengers ? ` · PAX ${f.passengers}` : ''}${f.duty_group_label ? ` · ${f.duty_group_label}` : ''}` : ''}
        right={<Button title="‹ All sectors" tone="secondary" small onPress={onBack} />}
      />
      {message && <Notice message={message} />}
      {!!error && <Banner tone="danger" message={error} />}

      {!!t && (
        <Card>
          <View style={styles.wrap}>
            <Metric label="Sales (normal)" value={npr(t.revenue_npr)} tone="primary" />
            <Metric label="Transactions" value={String(t.transactions)} />
            <Metric label="Units" value={String(t.units)} />
            <Metric label="Seats served" value={String(t.seats)} />
            <Metric label="Corrections" value={String(t.edits)} tone={t.edits ? 'warning' : 'default'} />
          </View>
          {!!summary.by_type?.length && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              {summary.by_type.map((x) => `${typeLabel[x.sale_type] || x.sale_type}: ${x.transactions} transaction(s), ${x.units} unit(s)`).join(' · ')}
            </Text>
          )}
        </Card>
      )}

      {!!diff && (diff.total > 0 || diff.conflicts.length > 0) && (
        <DifferencesCard diff={diff} canEdit={canEdit} onChanged={load} onMessage={setMessage} />
      )}

      {!!summary && (
        <Card>
          <SectionTitle title="By iPad login" description="Each iPad on the sector and what it recorded. Tap one to see only its sales below." />
          <View style={[styles.wrap, styles.gap8]}>
            <Chip label="All iPads" active={!device} onPress={() => setDevice('')} />
            {(summary.by_device || []).map((d) => (
              <Chip key={d.device} label={`${d.device} · ${npr(d.revenue_npr)}`} active={device === d.device} onPress={() => setDevice(device === d.device ? '' : d.device)} />
            ))}
          </View>
          {(summary.by_device || []).map((d) => (
            <Text key={d.device} style={[styles.mono, { marginTop: 6 }]}>
              {d.device}: {d.transactions} transaction(s), {d.units} unit(s), {npr(d.revenue_npr)} · first sale {when(d.first_sale)} · last sale {when(d.last_sale)}
            </Text>
          ))}
          {!!f?.devices?.length && (summary.by_device || []).length < f.devices.length && (
            <Text style={[styles.mono, { marginTop: 6 }]}>
              Assigned but no sales recorded: {f.devices.filter((d) => !(summary.by_device || []).some((x) => x.device === d)).join(', ')}
            </Text>
          )}
        </Card>
      )}

      {!!summary && (
        <Card>
          <SectionTitle title="What sold" description="Every product and combo sold on this sector, best seller first. Tap one to see only its sales below." />
          <View style={[styles.rowBetween, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border }]}>
            <Text style={[styles.mono, { flex: 2 }]}>Product</Text>
            <Text style={[styles.mono, { width: 56, textAlign: 'right' }]}>Units</Text>
            <Text style={[styles.mono, { width: 56, textAlign: 'right' }]}>Txns</Text>
            <Text style={[styles.mono, { width: 96, textAlign: 'right' }]}>NPR</Text>
          </View>
          {(summary.by_product || []).map((p) => {
            const on = String(productId) === String(p.product_id);
            return (
              <Pressable key={p.product_id} onPress={() => setProductId(on ? '' : String(p.product_id))}
                style={[styles.rowBetween, { paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt, backgroundColor: on ? COLORS.infoSoft : 'transparent' }]}>
                <View style={{ flex: 2 }}>
                  <Text style={{ fontSize: 13, fontWeight: on ? '700' : '500', color: COLORS.text }}>{p.product_name}</Text>
                  <Text style={styles.mono}>{p.is_combo ? 'Combo' : p.category}</Text>
                </View>
                <Text style={[styles.value, { width: 56, textAlign: 'right' }]}>{p.units}</Text>
                <Text style={[styles.value, { width: 56, textAlign: 'right' }]}>{p.transactions}</Text>
                <Text style={[styles.value, { width: 96, textAlign: 'right', fontWeight: '600' }]}>{npr(p.revenue_npr)}</Text>
              </Pressable>
            );
          })}
          {!summary.by_product?.length && <Text style={styles.empty}>Nothing sold on this sector.</Text>}
        </Card>
      )}

      {!!summary && (summary.by_seller?.length > 0 || summary.by_currency?.length > 0) && (
        <Card>
          <SectionTitle title="By crew member and currency" description="Combo sales name the crew member who sold them; normal sales are pooled across the duty group." />
          {(summary.by_seller || []).map((s) => (
            <Text key={`${s.seller_name}-${s.crew_member_id}`} style={[styles.mono, { marginTop: 4 }]}>
              {s.seller_name}: {s.transactions} transaction(s), {s.units} unit(s){s.combo_units ? ` (${s.combo_units} in combos)` : ''}, {npr(s.revenue_npr)}
            </Text>
          ))}
          {!!summary.by_currency?.length && (
            <Text style={[styles.mono, { marginTop: 8 }]}>
              Paid in: {summary.by_currency.map((c) => `${c.currency} ${Number(c.amount).toLocaleString()} (${c.transactions})`).join(' · ')}
            </Text>
          )}
        </Card>
      )}

      <Card>
        <SectionTitle
          title="Sales on this sector"
          description={canEdit
            ? 'Every sale as recorded. Correct a mis-keyed product, quantity or seat with remarks; every correction is recorded.'
            : 'Every sale as recorded.'}
        />
        <View style={[styles.wrap, styles.gap8, { marginBottom: 8 }]}>
          {['', 'NORMAL', 'COMPLEMENTARY', 'CANCELLATION'].map((k) => (
            <Chip key={k || 'all'} label={k ? typeLabel[k] : 'All types'} active={saleType === k} onPress={() => setSaleType(k)} />
          ))}
        </View>
        <Field label="Crew member (combo seller)" value={sellerTerm} onChangeText={setSellerTerm} placeholder="Name" />
        {(device || productId || saleType || sellerTerm) && (
          <View style={[styles.row, styles.gap8, { marginBottom: 8, flexWrap: 'wrap' }]}>
            <Text style={styles.mono}>
              Showing{device ? ` iPad ${device}` : ''}{selectedProduct ? ` · ${selectedProduct.product_name}` : ''}{saleType ? ` · ${typeLabel[saleType]}` : ''}{sellerTerm ? ` · "${sellerTerm}"` : ''} — {sales.total} line(s)
            </Text>
            <Button title="Clear filters" tone="ghost" small onPress={() => { setDevice(''); setProductId(''); setSaleType(''); setSellerTerm(''); }} />
          </View>
        )}
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
              {when(sale.transaction_time)} · seat {sale.seat_number} · iPad {sale.crew_id}
              {sale.seller_name ? ` · sold by ${sale.seller_name}` : ''} · {typeLabel[sale.sale_type] || sale.sale_type}
              {' · '}paid {sale.transaction_currency} {sale.transaction_currency_amount} ({sale.payment_method})
              {' · '}{sale.order_ref}
            </Text>
            {!!sale.remarks && sale.sale_type !== 'NORMAL' && <RemarkLine label={`${typeLabel[sale.sale_type]} remarks`} text={sale.remarks} />}
            {!!sale.edited_by && <RemarkLine label={`Last edited by ${sale.edited_by} ${when(sale.edited_at)}`} text={sale.edit_remarks} />}
            <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
              {!sale.bundle_id && canEdit && (
                <Button
                  title={editing?.id === sale.id ? 'Cancel' : 'Correct this sale'}
                  tone="ghost" small
                  onPress={() => {
                    if (editing?.id === sale.id) return setEditing(null);
                    setEditing(sale); setEditProduct(sale.product_id); setEditQuantity(String(sale.quantity));
                    setEditSeat(sale.seat_number || ''); setEditRemarks('');
                  }}
                />
              )}
              {!!sale.edit_count && (
                <Button title={history[sale.id] ? 'Hide corrections' : `${sale.edit_count} correction(s)`} tone="ghost" small onPress={() => toggleHistory(sale.id)} />
              )}
            </View>
            {!!history[sale.id] && (
              <View style={{ marginTop: 4 }}>
                {history[sale.id].map((h) => (
                  <View key={h.id}>
                    <Text style={styles.mono}>{when(h.changed_at)} · {h.changed_by} · {h.changes.join('; ') || 'no field changed'}</Text>
                    <RemarkLine text={h.remarks} style={{ marginTop: 2, marginBottom: 4 }} />
                  </View>
                ))}
              </View>
            )}
            {editing?.id === sale.id && (
              <View style={{ marginTop: 10, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
                <Select label="Correct product" valueLabel={products.find((p) => p.id === editProduct)?.product_name} onPress={() => setEditPicker(true)} />
                <View style={[styles.row, styles.gap8]}>
                  <View style={styles.flex1}><Field label="Quantity" value={editQuantity} onChangeText={setEditQuantity} keyboardType="number-pad" /></View>
                  <View style={styles.flex1}><Field label="Seat" value={editSeat} onChangeText={setEditSeat} autoCapitalize="characters" /></View>
                </View>
                <Field label="Remarks (mandatory)" value={editRemarks} onChangeText={setEditRemarks} multiline placeholder="Crew keyed water but sold a soft drink" />
                <Button title="Save correction" onPress={saveEdit} disabled={!editRemarks.trim()} />
              </View>
            )}
          </View>
        ))}
        {!sales.items.length && !sales.loading && <Text style={styles.empty}>No sales match.</Text>}
        <Pagination pager={sales} label="sales" />
      </Card>

      <OptionModal
        visible={editPicker} title="Product"
        options={products.map((p) => ({ value: p.id, label: p.product_name, sub: p.category }))}
        selectedValue={editProduct}
        onSelect={(value) => { setEditProduct(value); setEditPicker(false); }}
        onClose={() => setEditPicker(false)}
      />
    </ScrollView>
  );
}


/* ------------------------------------------------------------------ */
/* What the two iPads disagreed on, and what does not add up (7.15)    */
/* ------------------------------------------------------------------ */
function DifferencesCard({ diff, canEdit, onChanged, onMessage }) {
  const [resolving, setResolving] = useState(null);   // conflict id
  const [remarks, setRemarks] = useState('');
  const fieldLabel = { passengers: 'PAX load', aircraft: 'Aircraft' };
  const open = diff.conflicts.filter((c) => !c.resolved_at);
  const settled = diff.conflicts.filter((c) => c.resolved_at);

  async function resolve(c, apply) {
    if (!remarks.trim()) return onMessage({ tone: 'danger', text: 'Remarks are mandatory when settling a difference.' });
    try {
      await api(`/flight-conflicts/${c.id}/resolve`, { method: 'PUT', body: { apply_device_value: apply, remarks: remarks.trim() } });
      setResolving(null); setRemarks('');
      onMessage({ tone: 'success', text: apply ? `${fieldLabel[c.field] || c.field} set to iPad ${c.device}\u2019s value and recorded.` : `${fieldLabel[c.field] || c.field} kept as it was and recorded.` });
      onChanged?.();
    } catch (e) {
      onMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <Card style={{ borderColor: COLORS.warning, borderWidth: 1 }}>
      <SectionTitle
        title={`Differences (${diff.total})`}
        description="Found when the iPads uploaded: values the two iPads disagreed on, forms both filled in, and stock figures that do not add up."
        right={<Pill label={diff.total ? 'CHECK' : 'SETTLED'} tone={diff.total ? 'warning' : 'success'} />}
      />
      {open.map((c) => (
        <View key={c.id} style={[styles.listRow, { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 }]}>
          <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.warning }}>{fieldLabel[c.field] || c.field}</Text>
          <Text style={styles.mono}>Kept: {c.kept_value} (entered by iPad {c.kept_by}) \u00b7 iPad {c.device} sent: {c.device_value} \u00b7 {when(c.created_at)}</Text>
          {canEdit && resolving !== c.id && (
            <Button title="Settle this difference" tone="secondary" small style={{ marginTop: 6, alignSelf: 'flex-start' }} onPress={() => { setResolving(c.id); setRemarks(''); }} />
          )}
          {resolving === c.id && (
            <View style={{ marginTop: 8 }}>
              <Field label="Remarks (mandatory)" value={remarks} onChangeText={setRemarks} multiline placeholder="e.g. load sheet confirms 160" />
              <View style={[styles.row, styles.gap8]}>
                <Button title={`Keep ${c.kept_value}`} small style={styles.flex1} onPress={() => resolve(c, false)} disabled={!remarks.trim()} />
                <Button title={`Use ${c.device_value} (iPad ${c.device})`} tone="warning" small style={styles.flex1} onPress={() => resolve(c, true)} disabled={!remarks.trim()} />
                <Button title="Cancel" tone="ghost" small onPress={() => setResolving(null)} />
              </View>
            </View>
          )}
        </View>
      ))}
      {diff.merged_forms.map((m) => (
        <View key={m.id} style={[styles.listRow, { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 }]}>
          <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.warning }}>Form filled in on both iPads: {m.report_type.replace(/_/g, ' ')}</Text>
          <Text style={styles.mono}>First copy by iPad {m.submitted_by}; the copy from iPad {m.changed_by} is kept as revision {m.rev_no} ({when(m.changed_at)}). Compare them on the Reports page.</Text>
        </View>
      ))}
      {diff.sector_stock.length > 0 && (
        <View style={[styles.listRow, { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 }]}>
          <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.warning }}>Remaining stock after this sector does not add up</Text>
          {diff.sector_stock.map((l) => (
            <Text key={l.product_id} style={styles.mono}>
              {l.product_name}: system {l.expected_remaining} \u00b7 {Object.entries(l.by_device).map(([d, q]) => `iPad ${d} ${q}`).join(' + ')} = {l.declared_total} \u00b7 difference {l.variance > 0 ? '+' : ''}{l.variance}
            </Text>
          ))}
          {Object.entries(diff.sector_stock_remarks || {}).map(([d, r]) => <RemarkLine key={d} label={`iPad ${d} remarks`} text={r} />)}
        </View>
      )}
      {!!diff.stock_return && diff.stock_return.items.length > 0 && (
        <View style={[styles.listRow, { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 }]}>
          <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.warning }}>Final stock return ({diff.stock_return.status})</Text>
          {diff.stock_return.items.map((i) => (
            <View key={i.product_id}>
              <Text style={styles.mono}>
                {i.product_name}: system {i.expected_remaining} \u00b7 {Object.entries(i.by_device || {}).map(([d, q]) => `iPad ${d} ${q}`).join(' + ') || 'no iPad lines'} = {i.declared_remaining} \u00b7 difference {i.variance > 0 ? '+' : ''}{i.variance}
                {i.overridden ? ` \u00b7 corrected by ${i.override_by}` : ''}
              </Text>
              {i.overridden && <RemarkLine label="Correction remarks" text={i.override_remarks} />}
            </View>
          ))}
          {Object.entries(diff.stock_return.device_remarks || {}).filter(([, r]) => r).map(([d, r]) => <RemarkLine key={d} label={`iPad ${d} remarks`} text={r} />)}
          <Text style={[styles.mono, { marginTop: 4 }]}>The Inventory corrects a return line, with remarks, from Warehouse \u2192 Stock movements.</Text>
        </View>
      )}
      {settled.map((c) => (
        <View key={c.id} style={styles.listRow}>
          <Text style={{ fontSize: 13, fontWeight: '600', color: COLORS.text }}>{fieldLabel[c.field] || c.field} \u00b7 settled by {c.resolved_by} ({when(c.resolved_at)})</Text>
          <Text style={styles.mono}>{c.resolution === 'KEPT' ? `Kept ${c.kept_value}` : `Applied iPad ${c.device}\u2019s value ${c.device_value}`}; iPad {c.device} had sent {c.device_value}.</Text>
          <RemarkLine text={c.remarks} />
        </View>
      ))}
    </Card>
  );
}
