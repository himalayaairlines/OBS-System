import React, { useCallback, useEffect, useState } from 'react';
import { Platform, ScrollView, Text, TextInput, View } from 'react-native';

import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { api, downloadReport, uploadFile } from '../api';
import { confirm } from '../dialog';
import { COLORS, npr, styles, todayString } from '../theme';
import {
  Banner, Button, CalendarModal, Card, Chip, Field, KeyValue, OptionModal,
  FrozenTable, PageHeader, Pill, SearchBar, SectionTitle, Select,
} from '../ui';

const TABS = [
  { key: 'flights', label: 'Flights' },
  { key: 'products', label: 'Products' },
  { key: 'load', label: 'Load' },
  { key: 'groups', label: 'Crew & devices' },
  { key: 'bundles', label: 'Bundles' },
  { key: 'register', label: 'Daily register' },
];

export default function InventoryScreen({ user }) {
  const canBundle = user.role === 'inventory_supervisor' || user.role === 'management';
  const [tab, setTab] = useState('flights');
  const [message, setMessage] = useState(null);

  const tabs = TABS.filter((t) => t.key !== 'bundles' || canBundle);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Inventory Operations"
        description="Create flights, load the trolley from main inventory, and assign the OBS iPad."
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        {tabs.map((item) => (
          <Chip key={item.key} label={item.label} active={tab === item.key} onPress={() => setTab(item.key)} />
        ))}
      </View>
      {message && <Banner tone={message.tone} message={message.text} />}

      {tab === 'flights' && <FlightsTab notify={setMessage} />}
      {tab === 'products' && <ProductsTab notify={setMessage} />}
      {tab === 'load' && <LoadTab notify={setMessage} />}
      {tab === 'groups' && <GroupsTab notify={setMessage} user={user} />}
      {tab === 'bundles' && canBundle && <BundlesTab notify={setMessage} user={user} />}
      {tab === 'register' && <RegisterTab notify={setMessage} user={user} />}
    </ScrollView>
  );
}

/* ------------------------------- flights ------------------------------- */
function FlightsTab({ notify }) {
  const [trips, setTrips] = useState([]);
  const [form, setForm] = useState({
    flight_number: '', flight_date: todayString(), origin: '', destination: '',
    trip_type: 'ONE_WAY', return_flight_number: '', return_flight_date: '',
  });
  const [calendar, setCalendar] = useState(null);

  const load = useCallback(async () => {
    try {
      setTrips(await api('/trips'));
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  async function create() {
    try {
      await api('/flights', {
        method: 'POST',
        body: {
          ...form,
          return_flight_number: form.trip_type === 'ROUND_TRIP' ? form.return_flight_number || form.flight_number : null,
          return_flight_date: form.trip_type === 'ROUND_TRIP' ? form.return_flight_date || form.flight_date : null,
        },
      });
      notify({
        tone: 'success',
        text: form.trip_type === 'ROUND_TRIP'
          ? 'Round trip created as two sectors. Assign the iPad, then the crew enter their own names on board.'
          : 'Flight created.',
      });
      setForm({ ...form, flight_number: '', origin: '', destination: '', return_flight_number: '' });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  return (
    <>
      <Card>
        <SectionTitle
          title="New flight"
          description="Aircraft registration and PAX load are entered later by the crew."
        />
        <Field
          label="Flight number"
          value={form.flight_number}
          onChangeText={(v) => setForm({ ...form, flight_number: v })}
          autoCapitalize="characters"
          placeholder="H9-123"
        />
        <Select label="Flight date" valueLabel={form.flight_date} onPress={() => setCalendar('flight_date')} />
        <View style={[styles.row, styles.gap8]}>
          <Field
            label="Origin"
            value={form.origin}
            onChangeText={(v) => setForm({ ...form, origin: v })}
            autoCapitalize="characters"
            placeholder="KTM"
            style={{ flex: 1 }}
          />
          <Field
            label="Destination"
            value={form.destination}
            onChangeText={(v) => setForm({ ...form, destination: v })}
            autoCapitalize="characters"
            placeholder="DXB"
            style={{ flex: 1 }}
          />
        </View>

        <Text style={styles.label}>Trip type</Text>
        <View style={[styles.wrap, { marginBottom: 12 }]}>
          <Chip label="One way" active={form.trip_type === 'ONE_WAY'} onPress={() => setForm({ ...form, trip_type: 'ONE_WAY' })} />
          <Chip label="Round trip" active={form.trip_type === 'ROUND_TRIP'} onPress={() => setForm({ ...form, trip_type: 'ROUND_TRIP' })} />
        </View>

        {form.trip_type === 'ROUND_TRIP' && (
          <>
            <Field
              label="Return flight number"
              value={form.return_flight_number}
              onChangeText={(v) => setForm({ ...form, return_flight_number: v })}
              autoCapitalize="characters"
              placeholder="H9-124"
              hint="Leave blank to reuse the outbound number."
            />
            <Select
              label="Return date"
              valueLabel={form.return_flight_date || form.flight_date}
              onPress={() => setCalendar('return_flight_date')}
            />
            <Banner
              tone="info"
              message={`The return sector is created automatically as ${form.destination || 'DEST'}→${form.origin || 'ORIGIN'}. The same iPad works both sectors, and the crew working the way back enter their own names.`}
            />
          </>
        )}

        <Button
          title="CREATE FLIGHT"
          onPress={create}
          disabled={!form.flight_number || !form.origin || !form.destination}
        />
      </Card>

      <Card>
        <SectionTitle
          title="Scheduled trips"
          description="A round trip is one trip with two legs, loaded once and reconciled once."
        />
        {trips.map((trip) => (
          <View key={trip.root_id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14.5, fontWeight: '700' }}>{trip.route}</Text>
              <View style={[styles.row, styles.gap8]}>
                {trip.round_trip && <Pill label="ROUND TRIP" tone="info" />}
                <Pill
                  label={trip.status}
                  tone={trip.status === 'OPEN' ? 'success' : trip.status === 'CLOSED' ? 'muted' : 'warning'}
                />
              </View>
            </View>
            <Text style={styles.mono}>{trip.flight_date} · {trip.flight_numbers}</Text>
            {trip.legs.map((leg) => (
              <Text key={leg.id} style={[styles.mono, { marginTop: 2 }]}>
                {'   '}{leg.direction === 'OUTBOUND' ? 'Going  ' : 'Return '}
                {leg.flight_number} {leg.origin}→{leg.destination} ·
                {' '}{leg.duty_group_size ? `${leg.duty_group_size} crew on board` : 'crew not entered yet'} ·
                {' '}{leg.aircraft_type || 'aircraft pending'} · PAX {leg.passengers || '—'}
              </Text>
            ))}
          </View>
        ))}
        {!trips.length && <Text style={styles.empty}>No trips yet.</Text>}
      </Card>

      <CalendarModal
        visible={!!calendar}
        title="Select date"
        value={form[calendar] || todayString()}
        onSelect={(value) => setForm({ ...form, [calendar]: value })}
        onClose={() => setCalendar(null)}
      />
    </>
  );
}

/* --------------------------------- load -------------------------------- */
function LoadTab({ notify }) {
  const [trips, setTrips] = useState([]);
  const [stock, setStock] = useState([]);
  const [flightId, setFlightId] = useState(null);
  const [picker, setPicker] = useState(false);
  const [lines, setLines] = useState({});
  const [inventory, setInventory] = useState([]);
  const [search, setSearch] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [t, m] = await Promise.all([api('/trips'), api('/main-inventory')]);
        const open = t.filter((x) => x.status === 'OPEN');
        setTrips(open);
        setStock(m);
        // A round trip is loaded once, against its outbound leg.
        setFlightId((current) => current ?? open[0]?.root_id ?? null);
      } catch (error) {
        notify({ tone: 'danger', text: error.message });
      }
    })();
  }, [notify]);

  useEffect(() => {
    if (!flightId) return;
    (async () => {
      const rows = await api(`/inventory/${flightId}`);
      setInventory(rows);
      const initial = {};
      rows.forEach((row) => { initial[row.product_id] = String(row.loaded_quantity); });
      setLines(initial);
    })();
  }, [flightId]);

  async function save() {
    try {
      const rows = await api('/inventory/load', {
        method: 'POST',
        body: {
          flight_id: flightId,
          lines: stock.map((item) => ({
            product_id: item.product_id,
            loaded_quantity: Number(lines[item.product_id] || 0),
          })).filter((line) => line.loaded_quantity > 0
            || inventory.some((i) => i.product_id === line.product_id)),
        },
      });
      setInventory(rows);
      setStock(await api('/main-inventory'));
      notify({ tone: 'success', text: 'Trolley loaded. Main inventory has been adjusted automatically.' });
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  const trip = trips.find((t) => t.root_id === flightId);

  return (
    <>
      <Card>
        <SectionTitle
          title="Load the trolley"
          description="Quantities are drawn from main inventory as soon as you save."
        />
        <Select
          label="Trip"
          valueLabel={trip ? `${trip.route} · ${trip.flight_numbers} · ${trip.flight_date}` : null}
          placeholder="Select the trip to load"
          onPress={() => setPicker(true)}
        />
        {trip?.round_trip && (
          <Banner
            tone="info"
            message={`${trip.route} is a round trip. Load it once — the return leg carries on with `
              + 'whatever the outbound leg leaves, and the stock return covers both.'}
          />
        )}
        <SearchBar value={search} onChangeText={setSearch} placeholder="Search product name…" />
        {stock
          .filter((item) => item.product_name.toLowerCase().includes(search.trim().toLowerCase()))
          .map((item) => {
          const onBoard = inventory.find((i) => i.product_id === item.product_id);
          const value = lines[item.product_id] ?? '0';
          const delta = Number(value || 0) - Number(onBoard?.loaded_quantity || 0);
          return (
            <View key={item.product_id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontSize: 13.5, fontWeight: '600', flex: 1 }}>{item.product_name}</Text>
                <TextInput
                  value={String(value)}
                  onChangeText={(text) =>
                    setLines((previous) => ({ ...previous, [item.product_id]: text.replace(/[^0-9]/g, '') }))}
                  keyboardType="number-pad"
                  style={[styles.input, { width: 82, textAlign: 'center', paddingVertical: 7 }]}
                />
              </View>
              <Text style={styles.mono}>
                Warehouse {item.quantity} {item.unit} · {npr(item.selling_price)}
                {onBoard ? ` · sold ${onBoard.sold_quantity}` : ''}
              </Text>
              {delta !== 0 && (
                <Text style={{ fontSize: 11.5, color: delta > 0 ? COLORS.warning : COLORS.info, marginTop: 2 }}>
                  {delta > 0 ? `Will take ${delta} from the warehouse` : `Will return ${-delta} to the warehouse`}
                  {delta > item.quantity ? ' — not enough stock' : ''}
                </Text>
              )}
            </View>
          );
        })}
        {!stock.filter((i) => i.product_name.toLowerCase().includes(search.trim().toLowerCase())).length && (
          <Text style={styles.empty}>No product matches “{search}”.</Text>
        )}
        <Button title="SAVE LOAD" style={{ marginTop: 12 }} onPress={save} disabled={!flightId} />
      </Card>

      <OptionModal
        visible={picker}
        title="Select trip"
        options={trips.map((t) => ({
          value: t.root_id,
          label: t.route,
          sub: `${t.flight_date} · ${t.flight_numbers}`
            + (t.round_trip ? ' · round trip, loaded once' : ''),
        }))}
        selectedValue={flightId}
        onSelect={(value) => { setFlightId(value); setPicker(false); }}
        onClose={() => setPicker(false)}
      />
    </>
  );
}

/* --------------------------- crew & devices ---------------------------- */
function GroupsTab({ notify, user }) {
  const [trips, setTrips] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [dutyGroups, setDutyGroups] = useState([]);
  const [assign, setAssign] = useState({ root_id: null, crew_account: '' });
  const [picker, setPicker] = useState(null);

  const load = useCallback(async () => {
    try {
      const [t, a, d] = await Promise.all([
        api('/trips'), api('/crew-accounts'), api('/duty-groups'),
      ]);
      setTrips(t.filter((x) => x.status === 'OPEN'));
      setAccounts(a);
      setDutyGroups(d);
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  async function saveAssignment() {
    try {
      const result = await api(`/trips/${assign.root_id}/assign`, {
        method: 'POST',
        body: { crew_account: assign.crew_account },
      });
      notify({
        tone: 'success',
        text: `${result.route} assigned to ${assign.crew_account}. The crew enter their own `
          + 'names on the iPad at the start of each sector.',
      });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  const assignTrip = trips.find((t) => t.root_id === assign.root_id);

  return (
    <>
      <Card>
        <SectionTitle
          title="Assign the OBS iPad to a trip"
          description="One device works the whole trip, both sectors. Who sells is decided on board."
        />
        <Select
          label="Trip"
          valueLabel={assignTrip
            ? `${assignTrip.route} · ${assignTrip.flight_numbers} · ${assignTrip.flight_date}`
            : null}
          placeholder="Select a trip"
          onPress={() => setPicker('trip')}
        />

        {assignTrip && (
          <>
            <View style={[styles.listRow, { paddingVertical: 10 }]}>
              <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>
                {assignTrip.route}
              </Text>
              <Text style={styles.mono}>{assignTrip.flight_date} · {assignTrip.flight_numbers}</Text>
            </View>

            <Select
              label="OBS iPad account"
              valueLabel={assign.crew_account
                ? `${assign.crew_account} — ${accounts.find((a) => a.employee_id === assign.crew_account)?.name || ''}`
                : null}
              placeholder="Select the device working this trip"
              onPress={() => setPicker('crew_account')}
            />

            <Button
              title="ASSIGN DEVICE TO TRIP"
              onPress={saveAssignment}
              disabled={!assign.crew_account}
            />
            <Text style={[styles.mono, { marginTop: 6 }]}>
              Crew names are no longer set here. At the start of each sector the crew pick
              themselves from the roster on the iPad, and every sale records which of them made it.
            </Text>
          </>
        )}
      </Card>

      <Card>
        <SectionTitle
          title="Duty groups formed on board"
          description="Read-only. This is who the crew recorded as working each sector."
        />
        {dutyGroups.map((group) => (
          <View key={group.id} style={[styles.listRow, { paddingVertical: 10 }]}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 13.5, fontWeight: '700', color: COLORS.text }}>
                {group.label}
              </Text>
              <Pill
                label={group.locked ? 'SELLING STARTED' : 'NOT YET SELLING'}
                tone={group.locked ? 'success' : 'muted'}
              />
            </View>
            <Text style={styles.mono}>
              {group.origin}→{group.destination} · entered by {group.created_by} at{' '}
              {String(group.created_at || '').replace('T', ' ').slice(0, 16)}
            </Text>
            <View style={[styles.wrap, { marginTop: 6 }]}>
              {(group.members || []).map((m) => (
                <Pill key={m.id} label={`${m.name} · ${m.position}`} tone="muted" />
              ))}
            </View>
            {!!group.amend_remarks && (
              <Text style={[styles.mono, { marginTop: 4 }]}>
                Changed by {group.amended_by}: {group.amend_remarks}
              </Text>
            )}
          </View>
        ))}
        {!dutyGroups.length && (
          <Text style={styles.empty}>
            No duty groups yet. One appears here as soon as a crew enter their names on the iPad.
          </Text>
        )}
      </Card>

      <OptionModal
        visible={picker === 'trip'}
        title="Select trip"
        options={trips.map((t) => ({
          value: t.root_id,
          label: t.route,
          sub: `${t.flight_date} · ${t.flight_numbers}`
            + (t.fully_assigned ? ' · device assigned' : ''),
        }))}
        selectedValue={assign.root_id}
        onSelect={(value) => {
          const trip = trips.find((t) => t.root_id === value);
          setAssign({ root_id: value, crew_account: trip?.outbound?.crew_account || '' });
          setPicker(null);
        }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'crew_account'}
        title="OBS iPad account"
        options={accounts.map((a) => ({
          value: a.employee_id, label: a.employee_id, sub: a.name,
        }))}
        selectedValue={assign.crew_account}
        onSelect={(value) => { setAssign({ ...assign, crew_account: value }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
    </>
  );
}

/* ------------------------------- bundles ------------------------------- */
function BundlesTab({ notify, user }) {
  const canDelete = user.role === 'inventory_supervisor' || user.role === 'management';
  const [products, setProducts] = useState([]);
  const [bundles, setBundles] = useState([]);
  const [form, setForm] = useState({ bundle_code: '', bundle_name: '', bundle_price: '' });
  const [items, setItems] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [picker, setPicker] = useState(false);

  const load = useCallback(async () => {
    try {
      setProducts(await api('/products'));
      setBundles(await api('/bundles?include_inactive=true'));
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  const selected = Object.entries(items).filter(([, q]) => Number(q) > 0);
  const listValue = selected.reduce((sum, [id, quantity]) => {
    const product = products.find((p) => p.id === Number(id));
    return sum + Number(product?.selling_price || 0) * Number(quantity);
  }, 0);
  const price = Number(form.bundle_price || 0);

  async function restoreBundle(bundle) {
    try {
      await api(`/bundles/${bundle.id}`, {
        method: 'PUT',
        body: {
          bundle_code: bundle.bundle_code,
          bundle_name: bundle.bundle_name,
          bundle_price: bundle.bundle_price,
          active: true,
          items: bundle.items.map((i) => ({ product_id: i.product_id, quantity: i.quantity })),
        },
      });
      notify({ tone: 'success', text: `${bundle.bundle_name} is on sale again.` });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function removeBundle(bundle) {
    const sure = await confirm({
      title: 'Delete bundle',
      message: `Remove ${bundle.bundle_name} from sale? Past sales keep it; you can restore `
        + 'it later.',
      confirmText: 'Delete',
      tone: 'danger',
    });
    if (!sure) return;
    try {
      await api(`/bundles/${bundle.id}`, { method: 'DELETE' });
      notify({ tone: 'info', text: `${bundle.bundle_name} withdrawn from sale.` });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function save() {
    try {
      const body = {
        bundle_code: form.bundle_code,
        bundle_name: form.bundle_name,
        bundle_price: price,
        active: true,
        items: selected.map(([id, quantity]) => ({ product_id: Number(id), quantity: Number(quantity) })),
      };
      if (editingId) await api(`/bundles/${editingId}`, { method: 'PUT', body });
      else await api('/bundles', { method: 'POST', body });
      notify({ tone: 'success', text: `Bundle ${form.bundle_code} saved.` });
      setForm({ bundle_code: '', bundle_name: '', bundle_price: '' });
      setItems({}); setEditingId(null);
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  return (
    <>
      <Card>
        <SectionTitle
          title={editingId ? 'Edit bundle' : 'Create a bundle'}
          description="Pick several products and set one special price for the set."
        />
        <View style={[styles.row, styles.gap8]}>
          <Field
            label="Code"
            value={form.bundle_code}
            onChangeText={(v) => setForm({ ...form, bundle_code: v })}
            autoCapitalize="characters"
            placeholder="COMBO1"
            style={{ flex: 1 }}
          />
          <Field
            label="Bundle price (NPR)"
            value={form.bundle_price}
            onChangeText={(v) => setForm({ ...form, bundle_price: v.replace(/[^0-9.]/g, '') })}
            keyboardType="decimal-pad"
            placeholder="850"
            style={{ flex: 1 }}
          />
        </View>
        <Field
          label="Bundle name"
          value={form.bundle_name}
          onChangeText={(v) => setForm({ ...form, bundle_name: v })}
          placeholder="Snack & Drink Combo"
        />
        <Select
          label={`Products in the bundle (${selected.length})`}
          valueLabel={selected.length
            ? selected.map(([id, q]) => `${q}× ${products.find((p) => p.id === Number(id))?.product_name}`).join(', ')
            : null}
          placeholder="Choose products"
          onPress={() => setPicker(true)}
        />

        {selected.map(([id, quantity]) => {
          const product = products.find((p) => p.id === Number(id));
          return (
            <View key={id} style={[styles.rowBetween, styles.listRow]}>
              <Text style={{ flex: 1, fontSize: 13.5 }}>{product?.product_name}</Text>
              <TextInput
                value={String(quantity)}
                onChangeText={(text) =>
                  setItems((previous) => ({ ...previous, [id]: text.replace(/[^0-9]/g, '') }))}
                keyboardType="number-pad"
                style={[styles.input, { width: 64, textAlign: 'center', paddingVertical: 6 }]}
              />
            </View>
          );
        })}

        {selected.length > 0 && (
          <View style={{ marginTop: 10 }}>
            <KeyValue label="Sold separately" value={npr(listValue)} />
            <KeyValue label="Bundle price" value={npr(price)} />
            <KeyValue
              label="Passenger saves"
              value={npr(listValue - price)}
              tone={listValue - price > 0 ? COLORS.success : COLORS.danger}
            />
            {listValue - price <= 0 && (
              <Banner tone="warning" message="The bundle costs the passenger more than buying the items separately." />
            )}
          </View>
        )}

        <Button
          title={editingId ? 'SAVE BUNDLE' : 'CREATE BUNDLE'}
          style={{ marginTop: 12 }}
          onPress={save}
          disabled={!form.bundle_code || !form.bundle_name || !price || !selected.length}
        />
      </Card>

      <Card>
        <SectionTitle
          title="Existing bundles"
          description={canDelete
            ? 'Edit or delete any bundle. A deleted bundle stops being offered but past sales keep it.'
            : 'Only the Inventory Supervisor can change these.'}
        />
        {bundles.map((bundle) => (
          <View key={bundle.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '700', flex: 1 }}>
                {bundle.bundle_name} <Text style={styles.mono}>({bundle.bundle_code})</Text>
              </Text>
              <View style={[styles.row, styles.gap8]}>
                <Button
                  title="Edit"
                  tone="ghost"
                  small
                  onPress={() => {
                    setEditingId(bundle.id);
                    setForm({
                      bundle_code: bundle.bundle_code,
                      bundle_name: bundle.bundle_name,
                      bundle_price: String(bundle.bundle_price),
                    });
                    const next = {};
                    bundle.items.forEach((item) => { next[item.product_id] = String(item.quantity); });
                    setItems(next);
                  }}
                />
                {canDelete && (bundle.active
                  ? <Button title="Delete" tone="ghost" small onPress={() => removeBundle(bundle)} />
                  : (
                    <Button
                      title="Restore"
                      tone="ghost"
                      small
                      onPress={() => restoreBundle(bundle)}
                    />
                  ))}
              </View>
            </View>
            <Text style={styles.mono}>
              {bundle.items.map((i) => `${i.quantity}× ${i.product_name}`).join(' + ')}
            </Text>
            <Text style={styles.mono}>
              {npr(bundle.bundle_price)} · saves {npr(bundle.saving)} · {bundle.active ? 'active' : 'inactive'}
            </Text>
          </View>
        ))}
        {!bundles.length && <Text style={styles.empty}>No bundles yet.</Text>}
      </Card>

      <OptionModal
        visible={picker}
        multi
        title="Select products"
        options={products.map((p) => ({
          value: p.id, label: p.product_name, sub: `${p.category} · ${npr(p.selling_price)}`,
        }))}
        selectedValue={selected.map(([id]) => Number(id))}
        onSelect={(value) => setItems((previous) => {
          const next = { ...previous };
          if (Number(next[value] || 0) > 0) delete next[value];
          else next[value] = '1';
          return next;
        })}
        onClose={() => setPicker(false)}
      />
    </>
  );
}

/* ------------------------------- products ------------------------------ */
/**
 * Read-only view of the catalogue. Products are not created here — they come
 * from the warehouse, either as an existing item or via a new-product line on
 * a purchase order, which creates the product once the order is approved.
 */
function ProductsTab({ notify }) {
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');

  const load = useCallback(async () => {
    try {
      setProducts(await api('/main-inventory'));
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  const categories = [...new Set(products.map((p) => p.category))].sort();
  const term = search.trim().toLowerCase();
  const shown = products
    .filter((p) => !filter || p.category === filter)
    .filter((p) => !term || p.product_name.toLowerCase().includes(term)
      || p.product_code.toLowerCase().includes(term));

  return (
    <>
      <Banner
        tone="info"
        message="Products come from the warehouse. To add one, raise an order request in Warehouse and choose “New product” — it is created once the Inventory Supervisor approves it."
      />
      <Card>
        <SectionTitle
          title={`Catalogue (${products.length})`}
          description="Stock figures are live from main inventory."
        />
        <SearchBar value={search} onChangeText={setSearch} placeholder="Search product or code…" />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          <Chip label="All" active={!filter} onPress={() => setFilter('')} />
          {categories.map((category) => (
            <Chip
              key={category}
              label={category}
              active={filter === category}
              onPress={() => setFilter(filter === category ? '' : category)}
            />
          ))}
        </View>
        {shown.map((product) => (
          <View key={product.product_id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 13.5, fontWeight: '600', flex: 1 }}>{product.product_name}</Text>
              <Pill
                label={product.quantity === 0 ? 'OUT' : product.low_stock ? 'LOW' : `${product.quantity}`}
                tone={product.quantity === 0 ? 'danger' : product.low_stock ? 'warning' : 'success'}
              />
            </View>
            <Text style={styles.mono}>
              {product.product_code} · {product.category} · {npr(product.selling_price)} ·
              {' '}{product.quantity} {product.unit} in warehouse · {product.on_aircraft} on aircraft
            </Text>
          </View>
        ))}
        {!shown.length && <Text style={styles.empty}>No product matches that search.</Text>}
      </Card>
    </>
  );
}

/* --------------------------- daily register ---------------------------- */
function RegisterTab({ notify, user }) {
  const canEdit = user.role === 'inventory_supervisor' || user.role === 'management';
  const [month, setMonth] = useState(todayString().slice(0, 7));
  const [data, setData] = useState(null);
  const [calendar, setCalendar] = useState(false);
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState('month');
  const [day, setDay] = useState(todayString());
  const [editing, setEditing] = useState(null);
  const [edit, setEdit] = useState({ received_qty: '', sales_qty: '', remarks: '' });
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const fileInput = React.useRef(null);

  const load = useCallback(async () => {
    try {
      setData(await api(`/obs-register?month=${month}`));
    } catch (error) {
      setData(null);
      notify({ tone: 'danger', text: `Register unavailable: ${error.message}` });
    }
  }, [month, notify]);

  useEffect(() => { load(); }, [load]);

  function openCell(row, cell) {
    if (!canEdit) return;
    setEditing({ row, cell });
    setEdit({
      received_qty: String(cell.received_qty),
      sales_qty: String(cell.sales_qty),
      remarks: '',
    });
  }

  async function saveEdit() {
    try {
      await api('/obs-register/adjust', {
        method: 'POST',
        body: {
          stock_date: editing.cell.date,
          product_id: editing.row.product_id,
          received_qty: edit.received_qty === '' ? null : Number(edit.received_qty),
          sales_qty: edit.sales_qty === '' ? null : Number(edit.sales_qty),
          remarks: edit.remarks,
        },
      });
      notify({ tone: 'success', text: 'Register updated. The edit and its remarks are in the audit log.' });
      setEditing(null);
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function revert() {
    try {
      await api(
        `/obs-register/adjust?stock_date=${editing.cell.date}&product_id=${editing.row.product_id}`,
        { method: 'DELETE' },
      );
      notify({ tone: 'info', text: 'Reverted to the system figure.' });
      setEditing(null);
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function runImport(file) {
    if (!file) return;
    setImporting(true);
    setImportResult(null);
    try {
      const result = await uploadFile('/obs-register/import', file, { month });
      setImportResult(result);
      notify({
        tone: result.unmatched_products.length ? 'warning' : 'success',
        text: `Imported ${result.products_imported} product(s) across ${result.days_written} day(s) for ${result.month}.`
          + (result.unmatched_products.length
            ? ` ${result.unmatched_products.length} product name(s) did not match the catalogue.`
            : ''),
      });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: `Import failed: ${error.message}` });
    }
    setImporting(false);
  }

  function pickFile() {
    if (Platform.OS === 'web') {
      fileInput.current?.click();
      return;
    }
    notify({
      tone: 'info',
      text: 'Open the app in a browser to import a spreadsheet, then reopen it here.',
    });
  }

  async function exportRegister() {
    setExporting(true);
    try {
      await downloadReport(`month=${month}`, FileSystem, Sharing, '/reports/obs-register.xlsx');
      notify({ tone: 'success', text: 'Register exported.' });
    } catch (error) {
      notify({ tone: 'danger', text: `Export failed: ${error.message}` });
    }
    setExporting(false);
  }

  if (!data) {
    return (
      <Card>
        <SectionTitle title="OBS stock register" />
        <Text style={styles.empty}>
          Could not load the register. Check that the backend is running the current build.
        </Text>
        <Button title="Retry" tone="secondary" onPress={load} />
      </Card>
    );
  }

  const rows = data.rows.filter((r) =>
    r.product_name.toLowerCase().includes(search.trim().toLowerCase()));
  const dates = scope === 'month' ? data.dates : data.dates.filter((d) => d === day);

  // Header spans three sub-columns per date, exactly as the workbook does.
  const groupHeader = [{ label: 'Opening', width: 66 }];
  const columns = [{ label: 'Opening balance_Qty', width: 66, align: 'right' }];
  dates.forEach((date) => {
    groupHeader.push({ label: date.slice(5), width: 56 * 3 });
    columns.push(
      { label: 'Received Qty.', width: 56, align: 'right' },
      { label: 'Sales', width: 56, align: 'right' },
      { label: 'Closing balance_Qty', width: 56, align: 'right' },
    );
  });

  const tableRows = rows.map((row) => {
    const cells = [{ text: String(row.opening_qty), faint: true }];
    dates.forEach((date) => {
      const cell = row.days.find((c) => c.date === date);
      if (!cell) {
        cells.push({ text: '' }, { text: '' }, { text: '' });
        return;
      }
      cells.push(
        {
          text: cell.received_qty ? String(cell.received_qty) : '',
          tone: COLORS.success,
          highlight: cell.adjusted,
          onPress: () => openCell(row, cell),
        },
        {
          text: String(cell.sales_qty),
          highlight: cell.adjusted,
          onPress: () => openCell(row, cell),
        },
        {
          text: String(cell.closing_qty),
          bold: true,
          highlight: cell.adjusted,
          onPress: () => openCell(row, cell),
        },
      );
    });
    return { key: String(row.product_id), cells };
  });

  return (
    <>
      <Card>
        <SectionTitle
          title="OBS stock register"
          description="Opening + received − sales = closing, carried day to day. Received and sales come from real trolley loads and real sales."
          right={<Pill label={canEdit ? 'EDITABLE' : 'READ ONLY'} tone={canEdit ? 'info' : 'muted'} />}
        />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          <Chip label="Whole month" active={scope === 'month'} onPress={() => setScope('month')} />
          <Chip label="Single day" active={scope === 'day'} onPress={() => setScope('day')} />
        </View>
        <Select
          label={scope === 'month' ? 'Month' : 'Date'}
          valueLabel={scope === 'month' ? month : day}
          onPress={() => setCalendar(true)}
        />
        <SearchBar value={search} onChangeText={setSearch} placeholder="Search product…" />
        <View style={[styles.row, styles.gap8]}>
          <Button
            title={exporting ? 'Preparing…' : 'DOWNLOAD (EXCEL)'}
            tone="secondary"
            style={styles.flex1}
            onPress={exportRegister}
            disabled={exporting}
          />
          {canEdit && (
            <Button
              title={importing ? 'Importing…' : 'IMPORT PREVIOUS DATA'}
              tone="secondary"
              style={styles.flex1}
              onPress={pickFile}
              disabled={importing}
            />
          )}
        </View>
        {Platform.OS === 'web' && canEdit && (
          <input
            ref={fileInput}
            type="file"
            accept=".xlsx,.xlsm"
            style={{ display: 'none' }}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              runImport(file);
            }}
          />
        )}
        {!!importResult && (
          <Banner
            tone={importResult.unmatched_products.length ? 'warning' : 'success'}
            message={`${importResult.month}: ${importResult.products_imported} products, `
              + `${importResult.days_written} day entries, ${importResult.dates_found} dates found.`
              + (importResult.unmatched_products.length
                ? ` Not matched to the catalogue: ${importResult.unmatched_products.join(', ')}.`
                : '')}
          />
        )}
        {canEdit && (
          <Text style={[styles.mono, { marginTop: 8 }]}>
            Tap any figure to correct it. Remarks are mandatory and the system value is kept alongside.
            Importing a workbook fills the opening balances and daily figures for that month.
          </Text>
        )}
      </Card>

      <Card tight>
        {rows.length ? (
          <FrozenTable
            frozen={{
              width: 132,
              header: ['SNO.', 'Product'],
              cells: rows.map((row) => `${row.sno}. ${row.product_name}`),
            }}
            groupHeader={groupHeader}
            columns={columns}
            rows={tableRows}
          />
        ) : (
          <Text style={styles.empty}>No product matches that search.</Text>
        )}
        <Text style={[styles.mono, { marginTop: 8 }]}>
          Scroll sideways for the rest of the month. Shaded cells were corrected by hand.
        </Text>
      </Card>

      {!!editing && (
        <Card>
          <SectionTitle
            title={`${editing.row.product_name} · ${editing.cell.date}`}
            description={`System figures: ${editing.cell.computed_received} received, ${editing.cell.computed_sales} sold.`}
          />
          <View style={[styles.row, styles.gap8]}>
            <Field
              label="Received"
              value={edit.received_qty}
              onChangeText={(v) => setEdit({ ...edit, received_qty: v.replace(/[^0-9]/g, '') })}
              keyboardType="number-pad"
              style={{ flex: 1 }}
            />
            <Field
              label="Sales"
              value={edit.sales_qty}
              onChangeText={(v) => setEdit({ ...edit, sales_qty: v.replace(/[^0-9]/g, '') })}
              keyboardType="number-pad"
              style={{ flex: 1 }}
            />
          </View>
          <Field
            label="Remarks (mandatory)"
            value={edit.remarks}
            onChangeText={(v) => setEdit({ ...edit, remarks: v })}
            multiline
            placeholder="Why does this differ from the system figure?"
          />
          <View style={[styles.row, styles.gap8]}>
            <Button title="SAVE" style={styles.flex1} onPress={saveEdit} disabled={!edit.remarks.trim()} />
            {editing.cell.adjusted && (
              <Button title="Revert" tone="warning" style={styles.flex1} onPress={revert} />
            )}
            <Button title="Cancel" tone="ghost" style={styles.flex1} onPress={() => setEditing(null)} />
          </View>
        </Card>
      )}

      <CalendarModal
        visible={calendar}
        title={scope === 'month' ? 'Pick any date in the month' : 'Select date'}
        value={scope === 'month' ? `${month}-01` : day}
        onSelect={(value) => {
          const picked = value || todayString();
          setDay(picked);
          setMonth(picked.slice(0, 7));
        }}
        onClose={() => setCalendar(false)}
      />
    </>
  );
}
