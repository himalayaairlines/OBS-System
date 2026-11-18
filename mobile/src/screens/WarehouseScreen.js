import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text, TextInput } from '../text';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport } from '../api';
import { usePaged } from '../paging';
import { BarChart } from '../charts';
import { COLORS, compact, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Chip, DataTable, Field, FrozenTable, Metric, Notice,
  OptionModal, PageHeader, Pagination, Pill, SearchBar, SectionTitle, Select, StockPill,
  RemarkLine,
} from '../ui';

export default function WarehouseScreen({ user }) {
  const canApprove = ['inventory_supervisor', 'management'].includes(user.role);
  const [tab, setTab] = useState('stock');
  const [dashboard, setDashboard] = useState(null);
  const [orders, setOrders] = useState([]);
  const [recons, setRecons] = useState([]);
  const [message, setMessage] = useState(null);

  const [request, setRequest] = useState({});
  const [remarks, setRemarks] = useState('');
  const [productPicker, setProductPicker] = useState(false);
  const [adjust, setAdjust] = useState({ product_id: null, quantity: '', remarks: '' });
  const [adjustPicker, setAdjustPicker] = useState(false);
  const [receipt, setReceipt] = useState({ product_id: null, quantity: '', remarks: '' });
  const [receiptPicker, setReceiptPicker] = useState(false);
  const [orderMode, setOrderMode] = useState('existing');
  const [stockSearch, setStockSearch] = useState('');
  const [orderForm, setOrderForm] = useState({
    po_code: 'ANC', supply_type: 'OBS',
    order_placement_date: todayString(), delivery_date: '',
  });
  const [orderCalendar, setOrderCalendar] = useState(null);
  const [newLines, setNewLines] = useState([]);
  const [newProduct, setNewProduct] = useState({
    new_product_code: '', new_product_name: '', new_product_category: 'Beverage',
    new_product_unit: 'pcs', new_selling_price: '', unit_cost: '', quantity: '',
    new_reorder_level: '20',
  });
  const [poFilter, setPoFilter] = useState({
    status: '', supply_type: '', delivery_from: '', delivery_to: '',
  });
  const [poCalendar, setPoCalendar] = useState(null);
  const [poPicker, setPoPicker] = useState(null);
  const [receiveFor, setReceiveFor] = useState(null);
  const [receiveLines, setReceiveLines] = useState({});
  const [receiveMeta, setReceiveMeta] = useState({ received_date: todayString(), received_location: 'SGG' });
  const [receiveCalendar, setReceiveCalendar] = useState(false);
  const [exporting, setExporting] = useState(false);

  // The PO register and the movement ledger grow for ever, so they are paged
  // on the server with every filter applied there, not on the device.
  const poQuery = ['status', 'supply_type', 'delivery_from', 'delivery_to']
    .filter((key) => poFilter[key]).map((key) => `${key}=${encodeURIComponent(poFilter[key])}`).join('&');
  const po = usePaged(`/orders${poQuery ? `?${poQuery}` : ''}`, { enabled: tab === 'po' });
  const moves = usePaged('/main-inventory/movements', { enabled: tab === 'ledger' });
  // A post-flight return opened from the ledger (7.15).
  const [openReturn, setOpenReturn] = useState(null);

  const load = useCallback(async () => {
    try {
      const [d, o] = await Promise.all([
        api('/warehouse/dashboard'),
        // Only pending requests are acted on in Approvals; that list stays short.
        api('/orders?status=PENDING'),
      ]);
      setDashboard(d);
      setOrders(o);
      if (canApprove) setRecons(await api('/reconciliations?status=PENDING'));
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }, [canApprove]);

  useEffect(() => { load(); }, [load]);

  // After any change: summary, pending lists and whichever paged list is open.
  const refresh = () => { load(); po.reload(); moves.reload(); };

  async function addStock() {
    try {
      await api('/main-inventory/receive', {
        method: 'POST',
        body: {
          product_id: receipt.product_id,
          quantity: Number(receipt.quantity),
          remarks: receipt.remarks,
        },
      });
      setMessage({ tone: 'success', text: 'Stock added to main inventory.' });
      setReceipt({ product_id: null, quantity: '', remarks: '' });
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function exportPoRegister() {
    setExporting(true);
    try {
      const parts = [];
      if (poFilter.status) parts.push(`status=${poFilter.status}`);
      if (poFilter.supply_type) parts.push(`supply_type=${poFilter.supply_type}`);
      if (poFilter.delivery_from) parts.push(`delivery_from=${poFilter.delivery_from}`);
      if (poFilter.delivery_to) parts.push(`delivery_to=${poFilter.delivery_to}`);
      await downloadReport(parts.join('&'), FileSystem, Sharing, '/reports/po-register.xlsx');
      setMessage({ tone: 'success', text: 'PO register exported.' });
    } catch (error) {
      setMessage({ tone: 'danger', text: `Export failed: ${error.message}` });
    }
    setExporting(false);
  }

  async function submitReceipt() {
    try {
      const updated = await api(`/orders/${receiveFor.id}/receive`, {
        method: 'PUT',
        body: {
          items: receiveFor.items.map((item) => ({
            product_id: item.product_id,
            received_quantity: Number(receiveLines[item.product_id] ?? item.received_quantity),
          })),
          received_date: receiveMeta.received_date,
          received_location: receiveMeta.received_location,
        },
      });
      setMessage({
        tone: 'success',
        text: `${updated.order_no} marked ${updated.status.toLowerCase()}. Received pieces have been added to the warehouse.`,
      });
      setReceiveFor(null);
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  function addNewLine() {
    const line = {
      quantity: Number(newProduct.quantity || 0),
      unit_cost: Number(newProduct.unit_cost || 0),
      new_product_code: newProduct.new_product_code.trim().toUpperCase(),
      new_product_name: newProduct.new_product_name.trim(),
      new_product_category: newProduct.new_product_category.trim() || 'General',
      new_product_unit: newProduct.new_product_unit.trim() || 'pcs',
      new_selling_price: Number(newProduct.new_selling_price || 0),
      new_reorder_level: Number(newProduct.new_reorder_level || 20),
    };
    if (!line.new_product_code || !line.new_product_name || line.quantity <= 0) {
      return setMessage({ tone: 'warning', text: 'A new product needs a code, a name and a quantity.' });
    }
    setNewLines((previous) => [...previous, line]);
    setNewProduct({
      new_product_code: '', new_product_name: '', new_product_category: 'Beverage',
      new_product_unit: 'pcs', new_selling_price: '', unit_cost: '', quantity: '',
      new_reorder_level: '20',
    });
    setMessage(null);
  }

  async function submitOrder() {
    const items = [
      ...Object.entries(request)
        .filter(([, quantity]) => Number(quantity) > 0)
        .map(([id, quantity]) => ({ product_id: Number(id), quantity: Number(quantity) })),
      ...newLines,
    ];
    if (!items.length) return setMessage({ tone: 'warning', text: 'Add at least one product to the order.' });
    try {
      const order = await api('/orders', {
        method: 'POST',
        body: {
          items,
          remarks,
          po_code: orderForm.po_code,
          supply_type: orderForm.supply_type,
          order_placement_date: orderForm.order_placement_date,
          expected_delivery_date: orderForm.delivery_date || null,
        },
      });
      setMessage({
        tone: 'success',
        text: `Order ${order.order_no} raised and sent for approval.`
          + (order.has_new_products
            ? ' The new product(s) will be created once the Inventory Supervisor approves it.'
            : ''),
      });
      setRequest({}); setRemarks(''); setNewLines([]);
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function reviewOrder(order, approve) {
    try {
      await api(`/orders/${order.id}/review`, {
        method: 'PUT',
        body: { approve, review_remarks: approve ? 'Approved' : 'Rejected on review' },
      });
      setMessage({
        tone: approve ? 'success' : 'info',
        text: approve
          ? `Order ${order.order_no} approved and added to main inventory.`
          : `Order ${order.order_no} rejected.`,
      });
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function reviewRecon(recon, approve) {
    try {
      await api(`/reconciliations/${recon.id}/review`, {
        method: 'PUT',
        body: { approve, review_remarks: approve ? 'Counted and accepted' : 'Recount required' },
      });
      setMessage({
        tone: approve ? 'success' : 'info',
        text: approve
          ? 'Approved — leftover stock has returned to main inventory and the flight is closed.'
          : 'Sent back to the crew for a recount.',
      });
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function submitAdjust() {
    try {
      await api('/main-inventory/adjust', {
        method: 'POST',
        body: {
          product_id: adjust.product_id,
          quantity: Number(adjust.quantity),
          remarks: adjust.remarks,
        },
      });
      setMessage({ tone: 'success', text: 'Stock adjusted.' });
      setAdjust({ product_id: null, quantity: '', remarks: '' });
      refresh();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  if (!dashboard) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
        <PageHeader title="Main Inventory" />
        {message && <Notice message={message} />}
        <Text style={styles.empty}>Loading warehouse data…</Text>
      </ScrollView>
    );
  }

  const filteredOrders = po.items;

  const tabs = [
    { key: 'stock', label: 'Stock' },
    ...(canApprove ? [{ key: 'add', label: 'Add stock' }] : []),
    { key: 'order', label: 'Request order' },
    { key: 'po', label: 'PO register' },
    { key: 'approvals', label: `Approvals${(dashboard.pending_orders + dashboard.pending_reconciliations) ? ` (${dashboard.pending_orders + dashboard.pending_reconciliations})` : ''}` },
    { key: 'ledger', label: 'Movements' },
  ];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Main Inventory"
        description="Central warehouse feeding the OBS trolleys. Stock moves automatically on load and return."
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        {tabs.map((item) => (
          <Chip key={item.key} label={item.label} active={tab === item.key} onPress={() => setTab(item.key)} />
        ))}
      </View>
      {message && <Notice message={message} />}

      {!!dashboard.low_stock_count && (
        <Banner
          tone={dashboard.out_of_stock_count ? 'danger' : 'warning'}
          message={`${dashboard.low_stock_count} product(s) at or below the reorder level`
            + (dashboard.out_of_stock_count ? `, and ${dashboard.out_of_stock_count} completely out of stock.` : '.')
            + ' Raise an order request from the tab above.'}
        />
      )}

      {tab === 'stock' && (
        <>
          <Card>
            <View style={styles.wrap}>
              <Metric label="Products" value={String(dashboard.total_products)} />
              <Metric label="Units in warehouse" value={compact(dashboard.total_units)} tone="primary" />
              <Metric label="Units on aircraft" value={compact(dashboard.units_on_aircraft)} />
              <Metric label="Stock value" value={npr(dashboard.stock_value)} />
              <Metric
                label="Low stock"
                value={String(dashboard.low_stock_count)}
                tone={dashboard.low_stock_count ? 'danger' : 'success'}
              />
              <Metric label="Pending approvals" value={String(dashboard.pending_orders + dashboard.pending_reconciliations)} />
            </View>
          </Card>

          <Card>
            <SectionTitle title="Stock on hand" />
            <BarChart
              data={dashboard.items.slice(0, 8).map((i) => ({
                label: i.product_name.split(' ')[0],
                value: i.quantity,
                color: i.low_stock ? COLORS.danger : COLORS.primary,
              }))}
              format={compact}
            />
            <SearchBar value={stockSearch} onChangeText={setStockSearch} placeholder="Search product…" />
            <DataTable
              rows={dashboard.items.filter((i) => i.product_name.toLowerCase().includes(stockSearch.trim().toLowerCase()))}
              rowKey={(item) => item.product_id}
              emptyText={stockSearch ? `No product matches “${stockSearch}”.` : 'No products yet.'}
              columns={[
                { key: 'product_name', label: 'Product', flex: 2.4 },
                { key: 'quantity', label: 'In warehouse', align: 'right', render: (i) => `${i.quantity} ${i.unit}` },
                { key: 'on_aircraft', label: 'On aircraft', align: 'right', render: (i) => String(i.on_aircraft) },
                { key: 'reorder_level', label: 'Reorder at', align: 'right', render: (i) => String(i.reorder_level) },
                { key: 'status', label: 'Status', flex: 1.2, align: 'right', render: (i) => <StockPill quantity={i.quantity} low={i.low_stock} /> },
              ]}
              renderStacked={(item) => (
                <>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>{item.product_name}</Text>
                    <StockPill quantity={item.quantity} low={item.low_stock} />
                  </View>
                  <Text style={[styles.mono, styles.numeric]}>
                    {item.quantity} {item.unit} in warehouse · {item.on_aircraft} on aircraft · reorder at {item.reorder_level}
                  </Text>
                </>
              )}
            />
          </Card>

          {canApprove && (
            <Card>
              <SectionTitle title="Manual stock adjustment" description="Remarks are mandatory and recorded in the audit log." />
              <Button
                title={adjust.product_id
                  ? dashboard.items.find((i) => i.product_id === adjust.product_id)?.product_name
                  : 'Choose product'}
                tone="secondary"
                onPress={() => setAdjustPicker(true)}
              />
              <Field
                label="Quantity change (use a minus sign to reduce)"
                value={adjust.quantity}
                onChangeText={(v) => setAdjust({ ...adjust, quantity: v.replace(/[^0-9-]/g, '') })}
                keyboardType="numbers-and-punctuation"
                placeholder="-12"
                style={{ marginTop: 12 }}
              />
              <Field
                label="Remarks"
                value={adjust.remarks}
                onChangeText={(v) => setAdjust({ ...adjust, remarks: v })}
                multiline
                placeholder="Damaged in transit"
              />
              <Button
                title="Apply adjustment"
                onPress={submitAdjust}
                disabled={!adjust.product_id || !adjust.quantity || !adjust.remarks}
              />
            </Card>
          )}
        </>
      )}

      {tab === 'add' && canApprove && (
        <Card>
          <SectionTitle
            title="Add stock to main inventory"
            description="Use this for stock arriving outside the PO process. Everything is recorded in the movement ledger."
          />
          <Select
            label="Product"
            valueLabel={dashboard.items.find((i) => i.product_id === receipt.product_id)?.product_name}
            placeholder="Choose product"
            onPress={() => setReceiptPicker(true)}
          />
          <Field
            label="Quantity (pieces)"
            value={receipt.quantity}
            onChangeText={(v) => setReceipt({ ...receipt, quantity: v.replace(/[^0-9]/g, '') })}
            keyboardType="number-pad"
            placeholder="250"
            hint={receipt.product_id
              ? `Currently ${dashboard.items.find((i) => i.product_id === receipt.product_id)?.quantity} in stock`
              : 'Quantities are always in pieces so they post directly to stock.'}
          />
          <Field
            label="Remarks"
            value={receipt.remarks}
            onChangeText={(v) => setReceipt({ ...receipt, remarks: v })}
            multiline
            placeholder="Delivery received at SGG"
          />
          <Button
            title="Add to warehouse"
            onPress={addStock}
            disabled={!receipt.product_id || !receipt.quantity || !receipt.remarks.trim()}
          />
        </Card>
      )}

      {tab === 'order' && (
        <Card>
          <SectionTitle
            title="Request a purchase order"
            description="Goes to the Inventory Supervisor. Quantities are in pieces and post to stock when received."
          />
          <View style={[styles.row, styles.gap8]}>
            <Field
              label="PO code"
              value={orderForm.po_code}
              onChangeText={(v) => setOrderForm({ ...orderForm, po_code: v })}
              autoCapitalize="characters"
              style={{ flex: 1 }}
            />
            <View style={{ flex: 1.4 }}>
              <Text style={styles.label}>Inflight / OBS</Text>
              <View style={[styles.wrap, { marginBottom: 12 }]}>
                <Chip label="OBS" active={orderForm.supply_type === 'OBS'}
                  onPress={() => setOrderForm({ ...orderForm, supply_type: 'OBS' })} />
                <Chip label="Inflight" active={orderForm.supply_type === 'Inflight'}
                  onPress={() => setOrderForm({ ...orderForm, supply_type: 'Inflight' })} />
              </View>
            </View>
          </View>
          <View style={[styles.row, styles.gap8]}>
            <Select
              label="Order placement date"
              valueLabel={orderForm.order_placement_date}
              onPress={() => setOrderCalendar('order_placement_date')}
              style={{ flex: 1 }}
            />
            <Select
              label="Delivery date"
              valueLabel={orderForm.delivery_date || 'Not set'}
              onPress={() => setOrderCalendar('delivery_date')}
              style={{ flex: 1 }}
            />
          </View>
          <Text style={styles.label}>What are you ordering?</Text>
          <View style={[styles.wrap, { marginBottom: 12 }]}>
            <Chip label="Existing product" active={orderMode === 'existing'} onPress={() => setOrderMode('existing')} />
            <Chip label="New product" active={orderMode === 'new'} onPress={() => setOrderMode('new')} />
          </View>

          {orderMode === 'existing' ? (
            <Button title="Choose products from the warehouse" tone="secondary" onPress={() => setProductPicker(true)} />
          ) : (
            <View style={{ backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
              <Text style={[styles.mono, { marginBottom: 10 }]}>
                The product is created only once the Inventory Supervisor approves this order.
              </Text>
              <View style={[styles.row, styles.gap8]}>
                <Field
                  label="Code"
                  value={newProduct.new_product_code}
                  onChangeText={(v) => setNewProduct({ ...newProduct, new_product_code: v })}
                  autoCapitalize="characters"
                  placeholder="P024"
                  style={{ flex: 1 }}
                />
                <Field
                  label="Product name"
                  value={newProduct.new_product_name}
                  onChangeText={(v) => setNewProduct({ ...newProduct, new_product_name: v })}
                  placeholder="Himalayan Herbal Tea"
                  style={{ flex: 2 }}
                />
              </View>
              <View style={[styles.row, styles.gap8]}>
                <Field
                  label="Category"
                  value={newProduct.new_product_category}
                  onChangeText={(v) => setNewProduct({ ...newProduct, new_product_category: v })}
                  style={{ flex: 1 }}
                />
                <Field
                  label="Unit"
                  value={newProduct.new_product_unit}
                  onChangeText={(v) => setNewProduct({ ...newProduct, new_product_unit: v })}
                  style={{ flex: 1 }}
                />
              </View>
              <View style={[styles.row, styles.gap8]}>
                <Field
                  label="Quantity (pcs)"
                  value={newProduct.quantity}
                  onChangeText={(v) => setNewProduct({ ...newProduct, quantity: v.replace(/[^0-9]/g, '') })}
                  keyboardType="number-pad"
                  style={{ flex: 1 }}
                />
                <Field
                  label="Cost each"
                  value={newProduct.unit_cost}
                  onChangeText={(v) => setNewProduct({ ...newProduct, unit_cost: v.replace(/[^0-9.]/g, '') })}
                  keyboardType="decimal-pad"
                  style={{ flex: 1 }}
                />
                <Field
                  label="Selling price"
                  value={newProduct.new_selling_price}
                  onChangeText={(v) => setNewProduct({ ...newProduct, new_selling_price: v.replace(/[^0-9.]/g, '') })}
                  keyboardType="decimal-pad"
                  style={{ flex: 1 }}
                />
              </View>
              <Button title="Add this line" tone="secondary" small onPress={addNewLine} />
            </View>
          )}
          <View style={{ height: 12 }} />

          {newLines.map((line, index) => (
            <View key={`${line.new_product_code}-${index}`} style={[styles.rowBetween, styles.listRow]}>
              <View style={styles.flex1}>
                <View style={[styles.row, styles.gap8]}>
                  <Text style={{ fontSize: 14, fontWeight: '600' }}>{line.new_product_name}</Text>
                  <Pill label="NEW" tone="warning" />
                </View>
                <Text style={styles.mono}>
                  {line.new_product_code} · {line.quantity} pcs · cost {npr(line.unit_cost)} · sells {npr(line.new_selling_price)}
                </Text>
              </View>
              <Button
                title="Remove"
                tone="ghost"
                small
                onPress={() => setNewLines((previous) => previous.filter((_, i) => i !== index))}
              />
            </View>
          ))}
          {Object.entries(request).filter(([, q]) => Number(q) > 0).map(([id, quantity]) => {
            const item = dashboard.items.find((i) => i.product_id === Number(id));
            return (
              <View key={id} style={[styles.rowBetween, styles.listRow]}>
                <View style={styles.flex1}>
                  <Text style={{ fontSize: 14, fontWeight: '600' }}>{item?.product_name}</Text>
                  <Text style={styles.mono}>Currently {item?.quantity} in warehouse</Text>
                </View>
                <TextInput
                  value={String(quantity)}
                  onChangeText={(text) =>
                    setRequest((previous) => ({ ...previous, [id]: text.replace(/[^0-9]/g, '') }))}
                  keyboardType="number-pad"
                  style={[styles.input, { width: 78, textAlign: 'center', paddingVertical: 7 }]}
                />
              </View>
            );
          })}
          <Field
            label="Remarks"
            value={remarks}
            onChangeText={setRemarks}
            multiline
            placeholder="Stock running low before the festival season"
            style={{ marginTop: 12 }}
          />
          <Button title="Submit order request" onPress={submitOrder} />
        </Card>
      )}

      {tab === 'po' && (
        <>
          <Card>
            <SectionTitle
              title="PO register"
              description="Every purchase order, in the same columns as the register workbook. Received pieces post to the warehouse."
            />
            <View style={[styles.row, styles.gap8]}>
              <Select
                label="Status"
                valueLabel={poFilter.status || 'All'}
                onPress={() => setPoPicker('status')}
                style={{ flex: 1 }}
              />
              <Select
                label="Inflight / OBS"
                valueLabel={poFilter.supply_type || 'All'}
                onPress={() => setPoPicker('supply_type')}
                style={{ flex: 1 }}
              />
            </View>
            <View style={[styles.row, styles.gap8]}>
              <Select
                label="Delivery from"
                valueLabel={poFilter.delivery_from || 'Any'}
                onPress={() => setPoCalendar('delivery_from')}
                style={{ flex: 1 }}
              />
              <Select
                label="Delivery to"
                valueLabel={poFilter.delivery_to || 'Any'}
                onPress={() => setPoCalendar('delivery_to')}
                style={{ flex: 1 }}
              />
            </View>
            <Button
              title={exporting ? 'Preparing…' : 'Download PO register (Excel)'}
              tone="secondary"
              onPress={exportPoRegister}
              disabled={exporting}
            />
          </Card>

          <Card tight>
            {filteredOrders.length ? (
              <>
                <FrozenTable
                  frozen={{
                    width: 96,
                    header: ['PO No.'],
                    cells: filteredOrders.map((order) => order.order_no),
                  }}
                  columns={[
                    { label: 'PO code', width: 64 },
                    { label: 'PO Number', width: 72 },
                    { label: 'FY', width: 54 },
                    { label: 'Purchase Items', width: 170 },
                    { label: 'QTY', width: 110, align: 'right' },
                    { label: 'ORDER PLACEMENT DATE', width: 92 },
                    { label: 'Delivery Date (expected)', width: 92 },
                    { label: 'Delivery Date (actual)', width: 92 },
                    { label: 'Order Received QTY', width: 110, align: 'right' },
                    { label: 'Order Received Date', width: 92 },
                    { label: 'Order Received Location', width: 100 },
                    { label: 'Inflight/OBS', width: 80 },
                    { label: 'Status', width: 80 },
                    { label: 'Remarks', width: 150 },
                  ]}
                  rows={filteredOrders.map((order) => ({
                    key: String(order.id),
                    cells: [
                      { text: order.po_code },
                      { text: order.po_number },
                      { text: order.fiscal_year || '' },
                      { text: order.purchase_items },
                      { text: order.qty_text, align: 'right' },
                      { text: order.order_placement_date || '' },
                      { text: order.expected_delivery_date || '', faint: true },
                      { text: order.delivery_date || '', bold: !!order.delivery_date },
                      {
                        text: order.received_qty_text,
                        tone: order.outstanding ? COLORS.warning : COLORS.success,
                      },
                      { text: order.received_date || '' },
                      { text: order.received_location || '' },
                      { text: order.supply_type },
                      {
                        text: order.status,
                        bold: true,
                        tone: order.status === 'RECEIVED' ? COLORS.success
                          : order.status === 'REJECTED' ? COLORS.danger
                            : order.status === 'PARTIAL' ? COLORS.warning : COLORS.info,
                      },
                      { text: order.remarks || '', faint: true },
                    ],
                  }))}
                />
                <Text style={[styles.mono, { marginTop: 8 }]}>
                  Scroll sideways for the remaining columns. Quantities are in pieces so they post to stock.
                </Text>
                <Pagination pager={po} label="purchase orders" />
              </>
            ) : (
              <Text style={styles.empty}>
                {po.loading ? 'Loading…' : po.error || 'No purchase orders match that filter.'}
              </Text>
            )}
          </Card>

          {filteredOrders.map((order) => (
            <Card key={order.id}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 15, fontWeight: '700', color: COLORS.primary }}>
                  {order.order_no}
                </Text>
                <View style={[styles.row, styles.gap8]}>
                  <Pill label={order.supply_type} tone="muted" />
                  <Pill
                    label={order.status}
                    tone={order.status === 'RECEIVED' ? 'success'
                      : order.status === 'PARTIAL' ? 'warning'
                        : order.status === 'REJECTED' ? 'danger' : 'info'}
                  />
                </View>
              </View>

              {order.items.map((item) => (
                <View key={item.id} style={[styles.rowBetween, { paddingVertical: 4 }]}>
                  <Text style={{ flex: 1, fontSize: 13 }} numberOfLines={1}>{item.product_name}</Text>
                  <Text style={{ width: 84, fontSize: 13, textAlign: 'right' }}>
                    {item.quantity} pcs
                  </Text>
                  <Text style={{
                    width: 84, fontSize: 13, textAlign: 'right', fontWeight: '600',
                    color: item.received_quantity >= item.quantity ? COLORS.success : COLORS.warning,
                  }}>
                    {item.received_quantity} recd
                  </Text>
                </View>
              ))}
              {!!order.outstanding && (
                <Text style={{ fontSize: 12, color: COLORS.warning, marginTop: 4 }}>
                  {order.outstanding} pcs still outstanding.
                </Text>
              )}
              {!!order.edited_by && (
                <RemarkLine label={`Edited by ${order.edited_by}`} text={order.edit_remarks} />
              )}

              {canApprove && order.status === 'PENDING' && (
                <View style={[styles.row, styles.gap8, { marginTop: 10 }]}>
                  <Button title="Approve" tone="success" small style={styles.flex1}
                    onPress={() => reviewOrder(order, true)} />
                  <Button title="Reject" tone="danger" small style={styles.flex1}
                    onPress={() => reviewOrder(order, false)} />
                </View>
              )}

              {canApprove && ['APPROVED', 'PARTIAL'].includes(order.status) && (
                <Button
                  title={receiveFor?.id === order.id ? 'Cancel' : 'Record what arrived'}
                  tone="secondary"
                  small
                  style={{ marginTop: 10, alignSelf: 'flex-start' }}
                  onPress={() => {
                    if (receiveFor?.id === order.id) return setReceiveFor(null);
                    setReceiveFor(order);
                    const lines = {};
                    order.items.forEach((item) => { lines[item.product_id] = String(item.received_quantity); });
                    setReceiveLines(lines);
                  }}
                />
              )}

              {receiveFor?.id === order.id && (
                <View style={{ marginTop: 10, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
                  {order.items.map((item) => (
                    <View key={item.id} style={[styles.rowBetween, { paddingVertical: 5 }]}>
                      <View style={styles.flex1}>
                        <Text style={{ fontSize: 13, fontWeight: '600' }}>{item.product_name}</Text>
                        <Text style={styles.mono}>Ordered {item.quantity} pcs</Text>
                      </View>
                      <TextInput
                        value={String(receiveLines[item.product_id] ?? item.received_quantity)}
                        onChangeText={(text) => setReceiveLines((previous) => ({
                          ...previous, [item.product_id]: text.replace(/[^0-9]/g, ''),
                        }))}
                        keyboardType="number-pad"
                        style={[styles.input, { width: 82, textAlign: 'center', paddingVertical: 7 }]}
                      />
                    </View>
                  ))}
                  <Select
                    label="Order received date"
                    valueLabel={receiveMeta.received_date}
                    onPress={() => setReceiveCalendar(true)}
                    style={{ marginTop: 10 }}
                  />
                  <Field
                    label="Order received location"
                    value={receiveMeta.received_location}
                    onChangeText={(v) => setReceiveMeta({ ...receiveMeta, received_location: v })}
                    placeholder="SGG"
                  />
                  <Button title="Post receipt to warehouse" onPress={submitReceipt} />
                </View>
              )}
            </Card>
          ))}
        </>
      )}

      {tab === 'approvals' && (
        <>
          <Card>
            <SectionTitle title="Order requests" />
            {orders.filter((o) => o.status === 'PENDING').map((order) => (
              <View key={order.id} style={styles.listRow}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600' }}>{order.order_no}</Text>
                  <Pill label={order.status} tone="warning" />
                </View>
                <Text style={styles.mono}>
                  Raised by {order.requested_by} · {order.total_units} units · {npr(order.total_cost)}
                </Text>
                {order.items.map((item) => (
                  <Text key={item.id} style={styles.mono}>  {item.quantity}× {item.product_name}</Text>
                ))}
                {!!order.remarks && <RemarkLine label="Request remarks" text={order.remarks} />}
                {canApprove && (
                  <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
                    <Button title="Approve" tone="success" small style={styles.flex1} onPress={() => reviewOrder(order, true)} />
                    <Button title="Reject" tone="danger" small style={styles.flex1} onPress={() => reviewOrder(order, false)} />
                  </View>
                )}
              </View>
            ))}
            {!orders.some((o) => o.status === 'PENDING') && <Text style={styles.empty}>No pending orders.</Text>}
          </Card>

          <Card>
            <SectionTitle
              title="Stock returns from crew"
              description="Approving returns the declared leftovers to main inventory and closes the flight."
            />
            {recons.map((recon) => (
              <View key={recon.id} style={styles.listRow}>
                <View style={styles.rowBetween}>
                  <Text style={{ fontSize: 14, fontWeight: '600' }}>
                    {recon.flight_number} · {recon.flight_date}
                  </Text>
                  <Pill
                    label={recon.has_variance ? `VARIANCE ${recon.total_variance}` : 'MATCHES'}
                    tone={recon.has_variance ? 'danger' : 'success'}
                  />
                </View>
                <Text style={styles.mono}>Submitted by {recon.submitted_by}</Text>
                {recon.items.map((item) => (
                  <Text key={item.id} style={styles.mono}>
                    {'  '}{item.product_name}: declared {item.declared_remaining}, expected {item.expected_remaining}
                    {item.variance !== 0 ? `  (${item.variance > 0 ? '+' : ''}${item.variance})` : ''}
                  </Text>
                ))}
                {canApprove && (
                  <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
                    <Button title="Approve" tone="success" small style={styles.flex1} onPress={() => reviewRecon(recon, true)} />
                    <Button title="Send back" tone="danger" small style={styles.flex1} onPress={() => reviewRecon(recon, false)} />
                  </View>
                )}
              </View>
            ))}
            {!recons.length && <Text style={styles.empty}>No stock returns waiting.</Text>}
          </Card>
        </>
      )}

      {tab === 'ledger' && (
        <Card>
          <SectionTitle title="Stock movements" description="Every automatic and manual change to main inventory, newest first." />
          {!!moves.error && <Banner tone="danger" message={moves.error} />}
          {moves.items.map((move) => (
            <View key={move.id} style={[styles.listRow, move.line_variance ? { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 } : null]}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 14, fontWeight: '600', flex: 1 }}>{move.product_name}</Text>
                {!!move.line_variance && <Pill label={`DIFFERENCE ${move.line_variance > 0 ? '+' : ''}${move.line_variance}`} tone="warning" />}
                <Text style={{
                  fontWeight: '600',
                  color: move.change_qty > 0 ? COLORS.success : COLORS.danger,
                }}>
                  {move.change_qty > 0 ? '+' : ''}{move.change_qty}
                </Text>
              </View>
              <Text style={styles.mono}>
                {move.reason} · balance {move.balance_after} · {move.created_by} · {move.created_at?.slice(0, 16).replace('T', ' ')}
              </Text>
              {!!move.reference && <Text style={styles.mono}>{move.reference}</Text>}
              {!!move.reconciliation_id && (
                <Button
                  title={openReturn?.id === move.reconciliation_id ? 'Hide the return' : `Open the stock return${move.variance_lines ? ` (${move.variance_lines} line(s) differ)` : ''}`}
                  tone="ghost" small style={{ marginTop: 4, alignSelf: 'flex-start' }}
                  onPress={async () => {
                    if (openReturn?.id === move.reconciliation_id) return setOpenReturn(null);
                    try { setOpenReturn(await api(`/reconciliations/${move.reconciliation_id}`)); } catch (e) { setMessage({ tone: 'danger', text: e.message }); }
                  }}
                />
              )}
              {openReturn?.id === move.reconciliation_id && (
                <ReturnDetail recon={openReturn} canAdjust={canApprove} onChanged={async () => { setOpenReturn(await api(`/reconciliations/${move.reconciliation_id}`)); moves.reload(); }} onMessage={setMessage} />
              )}
            </View>
          ))}
          {!moves.items.length && !moves.loading && <Text style={styles.empty}>No movements recorded.</Text>}
          <Pagination pager={moves} label="movements" />
        </Card>
      )}

      <OptionModal
        visible={productPicker}
        multi
        title="Select products to order"
        options={dashboard.items.map((i) => ({
          value: i.product_id,
          label: i.product_name,
          sub: `${i.quantity} in stock · reorder at ${i.reorder_level}`,
        }))}
        selectedValue={Object.keys(request).filter((k) => Number(request[k]) > 0).map(Number)}
        onSelect={(value) => setRequest((previous) => {
          const next = { ...previous };
          if (Number(next[value] || 0) > 0) delete next[value];
          else next[value] = '100';
          return next;
        })}
        onClose={() => setProductPicker(false)}
      />
      <OptionModal
        visible={receiptPicker}
        title="Select product"
        options={dashboard.items.map((i) => ({
          value: i.product_id, label: i.product_name, sub: `${i.quantity} ${i.unit} in stock`,
        }))}
        selectedValue={receipt.product_id}
        onSelect={(value) => { setReceipt({ ...receipt, product_id: value }); setReceiptPicker(false); }}
        onClose={() => setReceiptPicker(false)}
      />
      <OptionModal
        visible={poPicker === 'status'}
        title="Status"
        options={[{ value: '', label: 'All' },
          ...['PENDING', 'APPROVED', 'PARTIAL', 'RECEIVED', 'REJECTED'].map((v) => ({ value: v, label: v }))]}
        selectedValue={poFilter.status}
        onSelect={(value) => { setPoFilter({ ...poFilter, status: value }); setPoPicker(null); }}
        onClose={() => setPoPicker(null)}
      />
      <OptionModal
        visible={poPicker === 'supply_type'}
        title="Inflight / OBS"
        options={[{ value: '', label: 'All' }, { value: 'OBS', label: 'OBS' }, { value: 'Inflight', label: 'Inflight' }]}
        selectedValue={poFilter.supply_type}
        onSelect={(value) => { setPoFilter({ ...poFilter, supply_type: value }); setPoPicker(null); }}
        onClose={() => setPoPicker(null)}
      />
      <CalendarModal
        visible={!!orderCalendar}
        title={orderCalendar === 'delivery_date' ? 'Delivery date' : 'Order placement date'}
        value={orderForm[orderCalendar] || todayString()}
        allowClear={orderCalendar === 'delivery_date'}
        onSelect={(value) => setOrderForm({ ...orderForm, [orderCalendar]: value || '' })}
        onClose={() => setOrderCalendar(null)}
      />
      <CalendarModal
        visible={!!poCalendar}
        title={poCalendar === 'delivery_to' ? 'Delivery to' : 'Delivery from'}
        value={poFilter[poCalendar] || todayString()}
        allowClear
        onSelect={(value) => setPoFilter({ ...poFilter, [poCalendar]: value || '' })}
        onClose={() => setPoCalendar(null)}
      />
      <CalendarModal
        visible={receiveCalendar}
        title="Order received date"
        value={receiveMeta.received_date}
        onSelect={(value) => setReceiveMeta({ ...receiveMeta, received_date: value || todayString() })}
        onClose={() => setReceiveCalendar(false)}
      />
      <OptionModal
        visible={adjustPicker}
        title="Select product"
        options={dashboard.items.map((i) => ({ value: i.product_id, label: i.product_name, sub: `${i.quantity} in stock` }))}
        selectedValue={adjust.product_id}
        onSelect={(value) => { setAdjust({ ...adjust, product_id: value }); setAdjustPicker(false); }}
        onClose={() => setAdjustPicker(false)}
      />
    </ScrollView>
  );
}


/* ------------------------------------------------------------------ */
/* A post-flight stock return opened from the ledger (7.15)            */
/* ------------------------------------------------------------------ */
function ReturnDetail({ recon, canAdjust, onChanged, onMessage }) {
  const [editing, setEditing] = useState(null);     // product_id
  const [value, setValue] = useState('');
  const [remarks, setRemarks] = useState('');
  const devices = recon.devices || [];

  async function save(item) {
    if (!remarks.trim()) return onMessage({ tone: 'danger', text: 'Remarks are mandatory when correcting a return.' });
    try {
      await api(`/reconciliations/${recon.id}/adjust`, { method: 'PUT', body: { product_id: item.product_id, declared_remaining: Number(value || 0), remarks: remarks.trim() } });
      setEditing(null); setRemarks(''); setValue('');
      onMessage({ tone: 'success', text: recon.status === 'APPROVED'
        ? 'Return corrected. The difference has been moved in main inventory and recorded with your remarks.'
        : 'Return corrected and recorded with your remarks.' });
      onChanged?.();
    } catch (e) {
      onMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <View style={{ marginTop: 8, backgroundColor: COLORS.surfaceAlt, padding: 12, borderRadius: 10 }}>
      <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>
        Stock return \u00b7 {recon.flight_number} \u00b7 {recon.flight_date} \u00b7 {recon.status}
      </Text>
      <Text style={[styles.mono, { marginTop: 2 }]}>
        {(recon.legs || []).map((l) => `${l.flight_number} ${l.origin}\u2192${l.destination}`).join('  |  ')}
      </Text>
      {Object.entries(recon.device_sectors || {}).map(([device, sectors]) => (
        <Text key={device} style={styles.mono}>
          iPad {device}: {sectors.join(', ')}{(recon.submitted_devices || []).includes(device) ? ' \u00b7 declared' : ' \u00b7 not declared'}
          {recon.device_remarks?.[device] ? ` \u00b7 remarks: ${recon.device_remarks[device]}` : ''}
        </Text>
      ))}
      <View style={[styles.rowBetween, { paddingVertical: 6, marginTop: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border }]}>
        <Text style={[styles.mono, { flex: 2 }]}>Product</Text>
        <Text style={[styles.mono, { width: 60, textAlign: 'right' }]}>Expected</Text>
        {devices.map((d) => <Text key={d} style={[styles.mono, { width: 64, textAlign: 'right' }]} numberOfLines={1}>{d}</Text>)}
        <Text style={[styles.mono, { width: 56, textAlign: 'right' }]}>Total</Text>
        <Text style={[styles.mono, { width: 56, textAlign: 'right' }]}>Diff</Text>
      </View>
      {(recon.items || []).map((item) => (
        <View key={item.product_id}>
          <View style={[styles.rowBetween, { paddingVertical: 5, backgroundColor: item.variance ? COLORS.warningSoft : 'transparent' }]}>
            <View style={{ flex: 2 }}>
              <Text style={{ fontSize: 13, color: COLORS.text, fontWeight: item.variance ? '700' : '500' }}>{item.product_name}</Text>
              {item.overridden && <Text style={styles.mono}>corrected by {item.override_by}: {item.override_remarks}</Text>}
            </View>
            <Text style={[styles.value, { width: 60, textAlign: 'right' }]}>{item.expected_remaining}</Text>
            {devices.map((d) => <Text key={d} style={[styles.value, { width: 64, textAlign: 'right' }]}>{item.by_device?.[d] ?? '\u2014'}</Text>)}
            <Text style={[styles.value, { width: 56, textAlign: 'right', fontWeight: '600' }]}>{item.declared_remaining}</Text>
            <Text style={[styles.value, { width: 56, textAlign: 'right', color: item.variance ? COLORS.warning : COLORS.muted, fontWeight: '700' }]}>
              {item.variance > 0 ? '+' : ''}{item.variance}
            </Text>
          </View>
          {canAdjust && editing !== item.product_id && !!item.variance && (
            <Button title="Correct this line" tone="ghost" small style={{ alignSelf: 'flex-start' }}
              onPress={() => { setEditing(item.product_id); setValue(String(item.declared_remaining)); setRemarks(''); }} />
          )}
          {editing === item.product_id && (
            <View style={{ padding: 10, backgroundColor: COLORS.surface, borderRadius: 8, marginBottom: 6 }}>
              <View style={[styles.row, styles.gap8]}>
                <View style={styles.flex1}><Field label="Returned (total for the trip)" value={value} onChangeText={(v) => setValue(v.replace(/[^0-9]/g, ''))} keyboardType="number-pad" /></View>
              </View>
              <Field label="Remarks (mandatory)" value={remarks} onChangeText={setRemarks} multiline placeholder="e.g. one more found in the trolley on unloading" />
              <View style={[styles.row, styles.gap8]}>
                <Button title="Save correction" small style={styles.flex1} onPress={() => save(item)} disabled={!remarks.trim()} />
                <Button title="Cancel" tone="ghost" small style={styles.flex1} onPress={() => setEditing(null)} />
              </View>
            </View>
          )}
        </View>
      ))}
      {canAdjust && <Text style={[styles.mono, { marginTop: 6 }]}>Lines that differ are highlighted. A correction needs remarks; after approval it also moves the difference in main inventory.</Text>}
    </View>
  );
}
