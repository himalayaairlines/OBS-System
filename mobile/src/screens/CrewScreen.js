import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api, cached, enqueueSale, orderRef, saveDutyGroup, writeCache } from '../api';
import { confirm, notify } from '../dialog';
import { COLORS, money, npr, styles } from '../theme';
import {
  Banner, Button, Card, Field, Metric, Notice, OptionModal, PageHeader, Pill, SearchBar,
  SeatMapModal, SectionTitle, Select,
} from '../ui';

// Mirrors DUTY_GROUP_SIZE in the backend. The server refuses anything else.
const DUTY_GROUP_SIZE = 4;

export default function CrewScreen({ user, online, onQueueChange, goToReports }) {
  const [flights, setFlights] = useState([]);
  const [products, setProducts] = useState([]);
  const [bundles, setBundles] = useState([]);
  const [rates, setRates] = useState([]);
  const [inventory, setInventory] = useState([]);
  const [stale, setStale] = useState(false);

  const [flightId, setFlightId] = useState(null);
  const [cart, setCart] = useState({});           // "p:3" / "b:1" -> qty
  const [seat, setSeat] = useState('');
  const [currency, setCurrency] = useState('NPR');
  const [saleType, setSaleType] = useState('NORMAL');
  const [remarks, setRemarks] = useState('');

  const [aircraftTypes, setAircraftTypes] = useState([]);
  const [aircraftType, setAircraftType] = useState('');
  const [aircraft, setAircraft] = useState('');
  const [typeAircraftPicker, setTypeAircraftPicker] = useState(false);
  const [pax, setPax] = useState('');
  const [lockRemarks, setLockRemarks] = useState('');

  // The crew working this sector, and which of them is making the sale.
  const [roster, setRoster] = useState([]);
  const [dutyGroup, setDutyGroup] = useState(null);
  const [dutyDraft, setDutyDraft] = useState([]);
  const [dutyPicker, setDutyPicker] = useState(false);
  // 'create' when the crew first enter their names, 'amend' for their one
  // correction afterwards, which needs a reason.
  const [dutyMode, setDutyMode] = useState('create');
  const [dutyRemarks, setDutyRemarks] = useState('');
  const [amending, setAmending] = useState(false);
  const [sellerId, setSellerId] = useState(null);
  const [sellerPicker, setSellerPicker] = useState(false);

  const [flightPicker, setFlightPicker] = useState(false);
  const [seatPicker, setSeatPicker] = useState(false);
  const [currencyPicker, setCurrencyPicker] = useState(false);
  const [typePicker, setTypePicker] = useState(false);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState('');

  // A finished leg drops off the picker, so it cannot be selected again.
  const sellableFlights = flights.filter((f) => f.status === 'OPEN');
  const flight = flights.find((f) => f.id === flightId) || null;

  /* ---------------------------- data ---------------------------- */
  const loadReference = useCallback(async () => {
    const [f, p, b, r, a, c] = await Promise.all([
      cached('flights', '/flights'),
      cached('products', '/products'),
      cached('bundles', '/bundles'),
      cached('rates', '/exchange-rates/active'),
      cached('aircraftTypes', '/aircraft-types'),
      // Cached deliberately: the crew pick their names on board, often with
      // no signal, so the roster has to be on the device already.
      cached('crewRoster', '/crew-roster'),
    ]);
    setFlights(f.data || []);
    setProducts(p.data || []);
    setBundles(b.data || []);
    setRates(r.data || []);
    setAircraftTypes(a.data || []);
    setRoster(c.data || []);
    setStale(f.stale || p.stale || b.stale || r.stale);
    setFlightId((current) => {
      const live = (f.data || []).filter((x) => x.status === 'OPEN');
      if (current && live.some((x) => x.id === current)) return current;
      return live[0]?.id ?? (f.data || [])[0]?.id ?? null;
    });
  }, []);

  const loadInventory = useCallback(async (id) => {
    if (!id) return setInventory([]);
    const result = await cached(`inventory.${id}`, `/inventory/${id}`);
    setInventory(result.data || []);
  }, []);

  const loadDutyGroup = useCallback(async (id) => {
    if (!id) return setDutyGroup(null);
    const result = await cached(`dutyGroup.${id}`, `/flights/${id}/duty-group`, null);
    setDutyGroup(result.data || null);
  }, []);

  useEffect(() => { loadReference(); }, [loadReference]);
  useEffect(() => { loadInventory(flightId); }, [flightId, loadInventory]);
  useEffect(() => { loadDutyGroup(flightId); }, [flightId, loadDutyGroup]);
  useEffect(() => {
    setAircraftType(flight?.aircraft_type || '');
    setAircraft(flight?.aircraft || '');
    setPax(flight?.passengers ? String(flight.passengers) : '');
    setLockRemarks('');
    setSeat('');
    // A new sector means a new group, so nothing carries over.
    setSellerId(null);
    setDutyDraft([]);
  }, [flightId, flight?.aircraft_type, flight?.aircraft, flight?.passengers]);

  const dutyMembers = useMemo(() => {
    if (!dutyGroup) return [];
    // A queued (offline) group holds ids only, so the names come from the roster.
    return (dutyGroup.members || []).map((m) => {
      if (m.name) return m;
      return roster.find((r) => r.id === m.id) || { id: m.id, name: `Crew #${m.id}` };
    });
  }, [dutyGroup, roster]);

  const seller = dutyMembers.find((m) => m.id === sellerId) || null;

  async function submitDutyGroup() {
    if (dutyDraft.length !== DUTY_GROUP_SIZE) {
      return notify({
        title: `Pick ${DUTY_GROUP_SIZE} crew`,
        message: `A duty group is exactly ${DUTY_GROUP_SIZE} cabin crew. `
          + `${dutyDraft.length} selected.`,
      });
    }
    setBusy(true);
    try {
      const { group, queued } = await saveDutyGroup(flight.id, dutyDraft);
      setDutyGroup(group);
      setDutyPicker(false);
      setSellerId(null);
      notify({
        title: queued ? 'Saved on this device' : 'Duty group recorded',
        message: queued
          ? 'There is no signal, so the crew list is held on the iPad and uploads by itself. '
            + 'You can start selling now.'
          : `${dutyDraft.length} crew recorded for this sector. Every sale will name who made it.`,
      });
      onQueueChange?.();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  /** The crew's single correction of the list. Needs a reason and is audited. */
  async function amendDutyGroup() {
    if (dutyDraft.length !== DUTY_GROUP_SIZE) {
      return notify({
        title: `Pick ${DUTY_GROUP_SIZE} crew`,
        message: `A duty group is exactly ${DUTY_GROUP_SIZE} cabin crew. `
          + `${dutyDraft.length} selected.`,
      });
    }
    if (!dutyRemarks.trim()) {
      return notify({
        title: 'Reason required',
        message: 'Say why the crew list is being corrected. It is kept with the sector.',
      });
    }
    const sure = await confirm({
      title: 'Correct the crew list?',
      message: 'This is the only correction the iPad can make. After it is saved the list '
        + 'stands, because it is what this sector\u2019s commission is paid on. Only '
        + 'Management can change it afterwards.',
      confirmText: 'Save correction',
      tone: 'danger',
    });
    if (!sure) return;

    setBusy(true);
    try {
      const updated = await api(`/flights/${flight.id}/duty-group`, {
        method: 'PUT',
        body: { member_ids: dutyDraft, remarks: dutyRemarks.trim() },
      });
      setDutyGroup(updated);
      await writeCache(`dutyGroup.${flight.id}`, updated);
      setAmending(false);
      setDutyRemarks('');
      setSellerId(null);
      setMessage({ tone: 'success', text: 'Crew list corrected. No further change is possible here.' });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
    setBusy(false);
  }

  // The seat map follows whichever aircraft is actually flying the leg.
  const seatSpec = aircraftTypes.find((t) => t.code === (flight?.aircraft_type || aircraftType));

  /* --------------------------- pricing -------------------------- */
  // per_unit_npr already accounts for currencies quoted per 10 or per 100.
  const rateRow = rates.find((r) => r.currency_code === currency);
  const rate = Number(rateRow?.per_unit_npr || rateRow?.rate_to_npr || 1) || 1;

  const stockFor = useCallback(
    (productId) => {
      const line = inventory.find((i) => Number(i.product_id) === Number(productId));
      return Math.max(0, Number(line?.remaining_quantity || 0));
    },
    [inventory],
  );

  // Units already committed by the cart, so the same trolley stock is not
  // promised twice within one transaction.
  const committed = useMemo(() => {
    const totals = {};
    Object.entries(cart).forEach(([key, quantity]) => {
      if (!quantity) return;
      const [kind, id] = key.split(':');
      if (kind === 'p') {
        totals[id] = (totals[id] || 0) + quantity;
      } else {
        const bundle = bundles.find((b) => b.id === Number(id));
        (bundle?.items || []).forEach((item) => {
          totals[item.product_id] = (totals[item.product_id] || 0) + item.quantity * quantity;
        });
      }
    });
    return totals;
  }, [cart, bundles]);

  const headroom = useCallback(
    (key) => {
      const [kind, id] = key.split(':');
      if (kind === 'p') return stockFor(id) - (committed[id] || 0);
      const bundle = bundles.find((b) => b.id === Number(id));
      if (!bundle?.items?.length) return 0;
      return Math.min(
        ...bundle.items.map((item) =>
          Math.floor((stockFor(item.product_id) - (committed[item.product_id] || 0)) / item.quantity)),
      );
    },
    [bundles, committed, stockFor],
  );

  const totalNpr = useMemo(() => {
    let sum = 0;
    Object.entries(cart).forEach(([key, quantity]) => {
      if (!quantity) return;
      const [kind, id] = key.split(':');
      if (kind === 'p') {
        const product = products.find((p) => p.id === Number(id));
        sum += Number(product?.selling_price || 0) * quantity;
      } else {
        const bundle = bundles.find((b) => b.id === Number(id));
        sum += Number(bundle?.bundle_price || 0) * quantity;
      }
    });
    return Math.round(sum * 100) / 100;
  }, [cart, products, bundles]);

  // One list of tiles covering products and bundles. Anything with no stock
  // left is dropped entirely rather than shown greyed out.
  const sellable = useMemo(() => {
    const term = search.trim().toLowerCase();
    const tiles = [];

    products.forEach((product) => {
      const key = `p:${product.id}`;
      const inCart = cart[key] || 0;
      const remaining = stockFor(product.id) - (committed[product.id] || 0) + inCart;
      if (remaining <= 0) return;
      tiles.push({
        key,
        title: product.product_name,
        subtitle: product.category,
        price: Number(product.selling_price || 0),
        remaining,
      });
    });

    bundles.forEach((bundle) => {
      const key = `b:${bundle.id}`;
      const inCart = cart[key] || 0;
      const room = Math.min(
        ...(bundle.items || []).map((item) =>
          Math.floor((stockFor(item.product_id) - (committed[item.product_id] || 0)) / item.quantity)),
      );
      const remaining = (Number.isFinite(room) ? room : 0) + inCart;
      if (remaining <= 0) return;
      tiles.push({
        key,
        title: bundle.bundle_name,
        subtitle: (bundle.items || []).map((i) => `${i.quantity}× ${i.product_name}`).join(' + '),
        price: Number(bundle.bundle_price || 0),
        remaining,
        badge: bundle.saving > 0 ? `Saves ${npr(bundle.saving)}` : 'Bundle',
      });
    });

    if (!term) return tiles;
    return tiles.filter((tile) =>
      `${tile.title} ${tile.subtitle}`.toLowerCase().includes(term));
  }, [products, bundles, cart, committed, stockFor, search]);

  const changeCart = (key, delta) => {
    setCart((previous) => {
      const current = Number(previous[key] || 0);
      // `headroom` is what is still free after everything already in the cart,
      // so an increase can never promise the same trolley stock twice.
      const room = Math.max(0, headroom(key));
      const next = delta > 0
        ? current + Math.min(delta, room)
        : Math.max(0, current + delta);
      return { ...previous, [key]: next };
    });
  };

  /* --------------------------- actions -------------------------- */
  async function saveAircraft() {
    if (!flight) return;
    try {
      const updated = await api(`/flights/${flight.id}/aircraft`, {
        method: 'PUT',
        body: { aircraft_type: aircraftType, aircraft: aircraft || null, remarks: lockRemarks || null },
      });
      setFlights((list) => list.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)));
      setMessage({
        tone: 'success',
        text: `Aircraft set to ${updated.aircraft_type}${updated.aircraft ? ` (${updated.aircraft})` : ''}.`,
      });
      setLockRemarks('');
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function savePax() {
    if (!flight) return;
    try {
      const updated = await api(`/flights/${flight.id}/pax-load`, {
        method: 'PUT',
        body: { passengers: Number(pax), remarks: lockRemarks || null },
      });
      setFlights((list) => list.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)));
      setMessage({ tone: 'success', text: `PAX load saved as ${updated.passengers}.` });
      setLockRemarks('');
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function finishFlight() {
    if (!flight) return;
    // The next leg only counts as "next" if it is still open for selling.
    const linked = flights.find((f) => f.id === flight.linked_flight_id && f.status === 'OPEN');

    const sure = await confirm({
      title: 'Are you sure?',
      message: `${flight.flight_number} ${flight.origin}\u2192${flight.destination} will be finished.\n\n`
        + 'No further sale can be recorded on this sector and you will not be able to select '
        + 'it again. The sales already recorded are kept, and each crew member keeps the '
        + 'commission on what they personally sold.'
        + (linked ? '\n\nThe returning crew will enter their own names before selling.' : ''),
      confirmText: 'Confirm',
      tone: 'danger',
    });
    if (!sure) return;

    try {
      const result = await api(`/flights/${flight.id}/finish`, { method: 'PUT' });
      setFlights((list) => list.map((f) => (f.id === result.id ? { ...f, ...result } : f)));
      setCart({});

      const finishedId = flight.id;
      const finishedNumber = flight.flight_number;

      if (linked) {
        setFlightId(linked.id);
        setMessage({
          tone: 'success',
          text: `${finishedNumber} finished: ${result.transactions} transaction(s), `
            + `${npr(result.revenue_npr)}. Now on ${linked.flight_number}.`,
        });
        // This crew's own reports come first, while the sector is fresh in mind.
        // The returning crew then start their sector from the Sales page.
        await notify({
          title: 'Now file this sector\u2019s reports',
          message: `${finishedNumber} is finished and its sales are recorded.\n\n`
            + 'Fill in the four cabin reports for this sector on the Reports page. They '
            + 'belong to your crew, and the returning crew will file their own.\n\n'
            + `After that, the return sector ${linked.flight_number} `
            + `${linked.origin}\u2192${linked.destination} is ready on the Sales page, where `
            + 'the returning crew enter the aircraft, the PAX load and their own names.',
        });
        goToReports?.(finishedId);
      } else {
        setMessage({
          tone: 'success',
          text: `${finishedNumber} finished: ${result.transactions} transaction(s), `
            + `${npr(result.revenue_npr)}.`,
        });
        // Last sector of the trip: the stock count comes first, because it is
        // what releases the trolley back to the warehouse.
        await notify({
          title: 'Journey complete',
          message: 'Two things left.\n\n'
            + '1. Count what is left in the trolley and submit it on the Sync page. The '
            + 'system shows what it expects to be there; type what you can actually see.\n\n'
            + '2. File this sector\u2019s four cabin reports on the Reports page.',
        });
      }
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function recordSale() {
    if (!flight) return notify({ title: 'Flight required', message: 'Select the flight you are working.' });
    if (!flight.passengers) return notify({ title: 'PAX required', message: 'Enter the PAX load before selling.' });
    if (!dutyGroup) {
      return notify({
        title: 'Crew list required',
        message: 'Enter the crew working this sector before recording a sale.',
      });
    }
    if (!seat.trim()) return notify({ title: 'Seat required', message: 'Choose the passenger seat.' });
    if (!sellerId) {
      return notify({
        title: 'Who is selling?',
        message: 'Choose the crew member making this sale so it counts towards their own commission.',
      });
    }
    if (totalNpr <= 0 && saleType === 'NORMAL') {
      return notify({ title: 'Nothing selected', message: 'Add at least one item to the cart.' });
    }
    if (saleType !== 'NORMAL' && !remarks.trim()) {
      return notify({ title: 'Remarks required', message: `Remarks are mandatory for a ${saleType.toLowerCase()} transaction.` });
    }

    const lines = Object.entries(cart)
      .filter(([, quantity]) => quantity > 0)
      .map(([key, quantity]) => {
        const [kind, id] = key.split(':');
        return kind === 'p'
          ? { product_id: Number(id), quantity }
          : { bundle_id: Number(id), quantity };
      });
    if (!lines.length) return notify({ title: 'Nothing selected', message: 'Add at least one item to the cart.' });

    const payload = {
      order_ref: orderRef(),
      flight_id: flight.id,
      seat_number: seat.trim().toUpperCase(),
      sold_by_crew_member_id: sellerId,
      lines,
      payment_currency: currency,
      payment_method: 'Cash',
      sale_type: saleType,
      remarks: saleType === 'NORMAL' ? null : remarks.trim(),
      transaction_time: new Date().toISOString(),
    };

    setBusy(true);
    let queued = false;
    try {
      if (!online) throw new Error('offline');
      await api('/sales', { method: 'POST', body: payload });
    } catch (error) {
      if (error.permanent) {
        setBusy(false);
        return notify({ title: 'Sale rejected', message: error.message });
      }
      // Transient: keep the cash sale safe on the device.
      await enqueueSale(payload);
      queued = true;
      onQueueChange?.();
    }

    // Reduce local stock straight away so an offline crew cannot oversell.
    // A cancellation records no stock movement, so it must not move it here
    // either. The reduced figure is written to the cache as well as to state:
    // without that, restarting the iPad mid-flight would restore the server's
    // stale count and the crew could sell the same stock twice.
    if (saleType !== 'CANCELLATION') {
      const reduced = inventory.map((row) => {
        const used = committed[row.product_id] || 0;
        return used ? { ...row, remaining_quantity: row.remaining_quantity - used } : row;
      });
      setInventory(reduced);
      await writeCache(`inventory.${flight.id}`, reduced);
    }

    setCart({});
    setSeat('');
    setSaleType('NORMAL');
    setRemarks('');
    setBusy(false);
    if (!queued) loadInventory(flight.id);

    notify({
      title: queued ? 'Saved on this device' : 'Sale recorded',
      message: queued
        ? 'The network is unavailable, so the sale is queued and will upload by itself once the '
          + 'iPad reaches Wi-Fi.'
        : `${money(totalNpr / rate, currency)} recorded on seat ${payload.seat_number}.`,
    });
  }

  /* ----------------------------- view --------------------------- */
  const canSell = flight && flight.passengers > 0 && flight.status === 'OPEN';

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Onboard Sales"
        description={`${user.name} · ${online ? 'Connected' : 'Offline — sales will be queued'}`}
        right={<Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'warning'} />}
      />

      {stale && (
        <Banner
          tone="warning"
          message="Showing the last saved copy of flights, products and rates. Connect before the flight to refresh."
        />
      )}
      {message && <Notice message={message} />}

      <Card>
        <SectionTitle title="Flight" description="Only flights assigned to this device are listed." />
        <Select
          valueLabel={
            flight
              ? `${flight.flight_number} · ${flight.origin}→${flight.destination} · ${flight.flight_date}`
              : null
          }
          placeholder="Select a flight"
          onPress={() => setFlightPicker(true)}
        />
        {!sellableFlights.length && (
          <Text style={styles.empty}>
            {flights.length
              ? 'Every leg has been finished. Submit the stock return from the Sync page.'
              : 'No flight is assigned to this iPad yet. Inventory assigns the device to a trip.'}
          </Text>
        )}

        {flight && (
          <>
            <View style={[styles.wrap, { marginBottom: 12 }]}>
              <Pill label={flight.direction} tone="info" />
              <Pill label={flight.trip_type.replace('_', ' ')} tone="muted" />
              {!!flight.aircraft_type && <Pill label={flight.aircraft_type} tone="info" />}
              {!!dutyGroup && (
                <Pill label={`${dutyMembers.length} crew`} tone={dutyGroup.pending ? 'warning' : 'muted'} />
              )}
              <Pill
                label={flight.status === 'OPEN' ? 'SELLING' : flight.status === 'FLOWN' ? 'FINISHED' : 'CLOSED'}
                tone={flight.status === 'OPEN' ? 'success' : flight.status === 'FLOWN' ? 'warning' : 'muted'}
              />
            </View>

            <View style={styles.divider} />

            <Select
              label={`Aircraft type${flight.aircraft_locked ? ' (locked)' : ''}`}
              valueLabel={aircraftType
                ? `${aircraftType} · ${aircraftTypes.find((t) => t.code === aircraftType)?.seats || '?'} seats`
                : null}
              placeholder="Select A320 or A319"
              onPress={() => {
                setTypeAircraftPicker(true);
              }}
            />
            <Field
              label="Registration (optional)"
              value={aircraft}
              onChangeText={setAircraft}
              autoCapitalize="characters"
              placeholder="9N-ABC"
              editable
              hint={
                flight.aircraft_locked
                  ? `Entered by ${flight.aircraft_entered_by}. Only a Crew Supervisor can change it.`
                  : 'The seat map follows the aircraft type you pick above.'
              }
            />
            <Field
              label={`PAX load${flight.pax_locked ? ' (locked)' : ''}`}
              value={pax}
              onChangeText={setPax}
              keyboardType="number-pad"
              placeholder="180"
              editable
              hint={
                flight.pax_locked
                  ? `Entered by ${flight.pax_entered_by}. Only a Crew Supervisor can change it.`
                  : 'Entered once by crew, then locked.'
              }
            />
            {!!(flight.aircraft_locked || flight.pax_locked) && (
              <Field
                label="Correction remarks (mandatory)"
                value={lockRemarks}
                onChangeText={setLockRemarks}
                multiline
                placeholder="Why is this being changed?"
              />
            )}
            <View style={[styles.row, styles.gap8]}>
              <Button
                title="Save aircraft"
                tone="secondary"
                small
                style={styles.flex1}
                disabled={!aircraftType}
                onPress={saveAircraft}
              />
              <Button
                title="Save PAX"
                tone="secondary"
                small
                style={styles.flex1}
                disabled={pax === ''}
                onPress={savePax}
              />
            </View>

            {flight.status === 'OPEN' && (
              <>
                <View style={styles.divider} />
                <Button
                  title="Finish this flight"
                  tone="warning"
                  onPress={finishFlight}
                  disabled={!online}
                />
                <Text style={[styles.mono, { marginTop: 6 }]}>
                  {flight.trip_type === 'ROUND_TRIP'
                    ? 'Press this when the sector is over. Selling stops here and the return sector opens, where the crew working it enter their own names.'
                    : 'Press this when the sector is over. Selling stops and the leftover stock can be counted.'}
                  {!online ? ' Needs a connection.' : ''}
                </Text>
              </>
            )}

            {flight.status === 'FLOWN' && (
              <Banner
                tone="info"
                message={`Finished by ${flight.finished_by || 'the crew'}. This sector is closed for selling. `
                  + 'Count the leftover stock on the Sync page once every sector is done.'}
              />
            )}
          </>
        )}
      </Card>

      {flight && flight.status === 'OPEN' && (
        <Card>
          <SectionTitle
            title="Crew on this sector"
            description={`The ${DUTY_GROUP_SIZE} cabin crew selling on this sector. Each sale records `
              + 'who made it, and that is what their commission is paid on.'}
            right={dutyGroup
              ? <Pill label={dutyGroup.pending ? 'Queued' : 'Recorded'} tone={dutyGroup.pending ? 'warning' : 'success'} />
              : <Pill label="Not set" tone="danger" />}
          />

          {dutyMembers.length > 0 && (
            <View style={[styles.wrap, { marginBottom: 10 }]}>
              {dutyMembers.map((m) => (
                <Pill key={m.id} label={m.name} tone={m.id === sellerId ? 'info' : 'muted'} />
              ))}
            </View>
          )}

          {!dutyGroup && (
            <Button
              title={`Enter the ${DUTY_GROUP_SIZE} crew working this sector`}
              tone="primary"
              onPress={() => { setDutyDraft([]); setDutyMode('create'); setDutyPicker(true); }}
              disabled={busy || !roster.length}
            />
          )}

          {!!dutyGroup && (
            <Text style={[styles.mono, { marginTop: 2 }]}>
              {dutyGroup.label ? `${dutyGroup.label} · ` : ''}
              {dutyGroup.created_by ? `entered by ${dutyGroup.created_by}` : 'entered on this iPad'}
              {dutyGroup.locked ? ' · locked by the first sale' : ''}
              {dutyGroup.amend_count ? ' · corrected once' : ''}
            </Text>
          )}

          {!!dutyGroup && !dutyGroup.pending && !amending && (
            dutyGroup.may_amend === false ? (
              <Banner
                tone="info"
                message={'The crew list has already been corrected once, so it can no longer be '
                  + 'changed here. Ask Management if it is still wrong.'}
              />
            ) : (
              <Button
                title="Correct the crew list"
                tone="ghost"
                small
                style={{ marginTop: 8 }}
                onPress={() => {
                  setDutyDraft(dutyMembers.map((m) => m.id));
                  setDutyRemarks('');
                  setDutyMode('amend');
                  setDutyPicker(true);
                }}
                disabled={busy || !online || !roster.length}
              />
            )
          )}

          {amending && (
            <>
              <View style={styles.divider} />
              <Text style={[styles.label, { marginBottom: 6 }]}>
                Correcting the crew list — one change only
              </Text>
              <View style={[styles.wrap, { marginBottom: 10 }]}>
                {dutyDraft.map((id) => {
                  const m = roster.find((r) => r.id === id);
                  return <Pill key={id} label={m ? m.name : `#${id}`} tone="info" />;
                })}
              </View>
              <Button
                title={`CHANGE WHO IS SELECTED (${dutyDraft.length} of ${DUTY_GROUP_SIZE})`}
                tone="secondary"
                small
                onPress={() => setDutyPicker(true)}
              />
              <Field
                label="Reason for the correction (mandatory)"
                value={dutyRemarks}
                onChangeText={setDutyRemarks}
                multiline
                placeholder="e.g. C. Gurung swapped with D. Sherpa before departure"
              />
              <View style={[styles.row, styles.gap8]}>
                <Button
                  title="Cancel"
                  tone="ghost"
                  style={styles.flex1}
                  onPress={() => { setAmending(false); setDutyRemarks(''); }}
                />
                <Button
                  title={busy ? 'Saving…' : 'Save correction'}
                  tone="warning"
                  style={styles.flex1}
                  onPress={amendDutyGroup}
                  disabled={busy || !online}
                />
              </View>
            </>
          )}

          {!roster.length && (
            <Banner
              tone="warning"
              message="The crew roster has not reached this iPad yet. Connect once before the flight so the names are available offline."
            />
          )}
        </Card>
      )}

      {canSell && (
        <>
          <Card>
            <SectionTitle
              title="Products"
              description={`Sold-out items are hidden. Prices in NPR${currency !== 'NPR' ? `, charged in ${currency}` : ''}.`}
              right={<Pill label={`${sellable.length} available`} tone="info" />}
            />
            <SearchBar value={search} onChangeText={setSearch} placeholder="Search product…" />
            <View style={styles.tileGrid}>
              {sellable.map((entry) => (
                <Tile
                  key={entry.key}
                  title={entry.title}
                  subtitle={entry.subtitle}
                  price={npr(entry.price)}
                  remaining={entry.remaining}
                  quantity={cart[entry.key] || 0}
                  onAdd={() => changeCart(entry.key, 1)}
                  onRemove={() => changeCart(entry.key, -1)}
                  badge={entry.badge}
                />
              ))}
            </View>
            {!sellable.length && (
              <Text style={styles.empty}>
                {search
                  ? `Nothing matches “${search}”.`
                  : 'Nothing is available to sell on this flight.'}
              </Text>
            )}
          </Card>

          <Card>
            <SectionTitle title="Transaction" />
            <Select
              label="Seat number"
              valueLabel={seat}
              placeholder={flight.aircraft_type ? 'Choose seat from the cabin map' : 'Enter the aircraft type first'}
              onPress={() => {
                if (!flight.aircraft_type) {
                  return notify({ title: 'Aircraft required', message: 'Select the aircraft type before choosing a seat.' });
                }
                setSeatPicker(true);
              }}
            />
            <Select
              label="Sold by"
              valueLabel={seller ? `${seller.name}${seller.crew_code ? ` · ${seller.crew_code}` : ''}` : ''}
              placeholder={dutyGroup ? 'Who made this sale?' : 'Enter the crew working this sector first'}
              onPress={() => {
                if (!dutyGroup) {
                  return notify({
                    title: 'Crew list required',
                    message: 'Enter the crew working this sector before recording a sale.',
                  });
                }
                setSellerPicker(true);
              }}
            />
            <Select
              label="Payment currency"
              valueLabel={currency === 'NPR'
                ? 'NPR — Nepalese Rupee'
                : `${currency} · 1 = NPR ${rate}`}
              onPress={() => setCurrencyPicker(true)}
            />
            <Select label="Transaction type" valueLabel={saleType} onPress={() => setTypePicker(true)} />
            {saleType !== 'NORMAL' && (
              <Field
                label="Remarks (mandatory)"
                value={remarks}
                onChangeText={setRemarks}
                multiline
                placeholder={saleType === 'CANCELLATION' ? 'Why was this cancelled?' : 'Why was this complementary?'}
              />
            )}

            <View style={[styles.wrap, { marginVertical: 10 }]}>
              <Metric label="Total (NPR)" value={npr(totalNpr)} tone="primary" />
              <Metric label={`Charge (${currency})`} value={money(totalNpr / rate, currency)} />
            </View>

            <Button
              title={busy ? 'Recording…' : online ? 'Record sale' : 'Record sale (will queue)'}
              onPress={recordSale}
              disabled={busy || (!Object.values(cart).some((q) => q > 0)) || !seat || !sellerId}
              tone={online ? 'primary' : 'warning'}
            />
            {!!Object.values(cart).some((q) => q > 0) && (
              <Button title="Clear cart" tone="ghost" small style={{ marginTop: 8 }} onPress={() => setCart({})} />
            )}
          </Card>
        </>
      )}

      {flight && !flight.passengers && flight.status === 'OPEN' && (
        <Banner tone="warning" message="Enter the PAX load above before recording any sale." />
      )}

      <OptionModal
        visible={dutyPicker}
        title={dutyMode === 'amend'
          ? `Correct the crew list — pick ${DUTY_GROUP_SIZE}`
          : `Who is working this sector? Pick ${DUTY_GROUP_SIZE}`}
        multi
        searchable
        searchPlaceholder="Search the crew roster…"
        options={roster.map((m) => ({
          value: m.id,
          label: m.name,
          sub: `${m.crew_code} · ${m.position}`,
        }))}
        selectedValue={dutyDraft}
        onSelect={(value) => setDutyDraft((current) => {
          if (current.includes(value)) return current.filter((id) => id !== value);
          if (current.length >= DUTY_GROUP_SIZE) {
            notify({
              title: `${DUTY_GROUP_SIZE} crew already selected`,
              message: 'Remove one before adding another.',
            });
            return current;
          }
          return [...current, value];
        })}
        onClose={() => {
          setDutyPicker(false);
          // Creating files the group straight away; correcting needs a reason
          // first, so the card below asks for it.
          if (dutyMode === 'create') submitDutyGroup();
          else setAmending(true);
        }}
      />

      <OptionModal
        visible={sellerPicker}
        title="Who made this sale?"
        options={dutyMembers.map((m) => ({
          value: m.id,
          label: m.name,
          sub: m.crew_code || '',
        }))}
        selectedValue={sellerId}
        onSelect={(value) => { setSellerId(value); setSellerPicker(false); }}
        onClose={() => setSellerPicker(false)}
      />

      <OptionModal
        visible={flightPicker}
        title="Select flight"
        options={sellableFlights.map((f) => ({
          value: f.id,
          label: `${f.flight_number} · ${f.origin}→${f.destination}`,
          sub: `${f.flight_date} · ${f.direction} · ${f.group_name || 'unassigned'}`,
        }))}
        selectedValue={flightId}
        onSelect={(value) => { setFlightId(value); setCart({}); setFlightPicker(false); }}
        onClose={() => setFlightPicker(false)}
      />
      <SeatMapModal
        visible={seatPicker}
        spec={seatSpec ? { ...seatSpec } : null}
        selected={seat}
        onSelect={setSeat}
        onClose={() => setSeatPicker(false)}
      />
      <OptionModal
        visible={typeAircraftPicker}
        title="Aircraft type"
        options={aircraftTypes.map((t) => ({
          value: t.code,
          label: t.code,
          sub: `${t.rows} rows · ${t.seats} seats`,
        }))}
        selectedValue={aircraftType}
        onSelect={(value) => { setAircraftType(value); setTypeAircraftPicker(false); }}
        onClose={() => setTypeAircraftPicker(false)}
      />
      <OptionModal
        visible={currencyPicker}
        title="Payment currency"
        options={rates.map((r) => ({
          value: r.currency_code,
          label: `${r.currency_code} — ${r.currency_name}`,
          sub: r.currency_code === 'NPR'
            ? 'Base currency'
            : `${r.unit > 1 ? `${r.unit} ${r.currency_code}` : `1 ${r.currency_code}`} = NPR ${r.rate_to_npr}`,
        }))}
        selectedValue={currency}
        onSelect={(value) => { setCurrency(value); setCurrencyPicker(false); }}
        onClose={() => setCurrencyPicker(false)}
      />
      <OptionModal
        visible={typePicker}
        title="Transaction type"
        options={[
          { value: 'NORMAL', label: 'Normal sale', sub: 'Revenue recorded, stock consumed' },
          { value: 'COMPLEMENTARY', label: 'Complementary', sub: 'No revenue, stock consumed' },
          { value: 'CANCELLATION', label: 'Cancellation', sub: 'No revenue, stock not consumed' },
        ]}
        selectedValue={saleType}
        onSelect={(value) => { setSaleType(value); setTypePicker(false); }}
        onClose={() => setTypePicker(false)}
      />
    </ScrollView>
  );
}

function Tile({ title, subtitle, price, remaining, quantity, onAdd, onRemove, badge }) {
  const low = remaining <= 5;
  return (
    <View style={[styles.tile, quantity > 0 && styles.tileActive]}>
      {!!badge && (
        <View style={{ alignSelf: 'flex-start', marginBottom: 4 }}>
          <Pill label={badge} tone="success" />
        </View>
      )}
      <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }} numberOfLines={2}>
        {title}
      </Text>
      <Text style={[styles.mono, { marginTop: 2, minHeight: 26 }]} numberOfLines={2}>
        {subtitle}
      </Text>
      <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.primary, marginTop: 4 }}>
        {price}
      </Text>
      <Text style={{ fontSize: 11, color: low ? COLORS.warning : COLORS.success, marginTop: 2 }}>
        {remaining} left
      </Text>

      <View style={[styles.rowBetween, { marginTop: 8 }]}>
        <Stepper label="−" onPress={onRemove} disabled={quantity <= 0} />
        <Text style={{ fontSize: 16, fontWeight: '700', color: COLORS.text }}>{quantity}</Text>
        <Stepper label="+" onPress={onAdd} disabled={remaining - quantity <= 0} />
      </View>
    </View>
  );
}

function Stepper({ label, onPress, disabled }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={{
        width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center',
        borderWidth: 1, borderColor: COLORS.border,
        backgroundColor: disabled ? COLORS.surfaceAlt : COLORS.surface,
        opacity: disabled ? 0.45 : 1,
      }}
    >
      <Text style={{ fontSize: 18, color: COLORS.primary, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}
