import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';
import Icon from '../icons';

import { api, cached, enqueueSale, orderRef, saveDutyGroup, writeCache } from '../api';
import {
  applyJournalToFlights, applyJournalToProgress, buildLocalProgress, cachedProgress, journalLeg, journalTrip,
  legFinished, legSellable, pruneJournal, readJournal, rootOf, tripAction,
} from '../tripLocal';
import { confirm, notify } from '../dialog';
import { COLORS, money, npr, styles } from '../theme';
import {
  Banner, Button, Card, Field, Metric, Notice, OptionModal, PageHeader, Pill, SearchBar,
  SeatMapModal, SectionTitle, Select,
} from '../ui';

// Mirrors DUTY_GROUP_MIN / DUTY_GROUP_MAX in the backend. The server refuses anything else.
const DUTY_GROUP_MIN = 2;
const DUTY_GROUP_MAX = 10;
const groupSizeOk = (n) => n >= DUTY_GROUP_MIN && n <= DUTY_GROUP_MAX;

// A return leg opens for selling only after "Start second flight" on THIS
// iPad (7.5 / 7.12); a leg this iPad has finished is no longer sellable here.
const isSellable = (f) => legSellable(f);
const route = (l) => `${l.origin}\u2192${l.destination}`;

export default function CrewScreen({ user, online, onQueueChange, goToReports, goTo }) {
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
  // The aircraft / PAX / crew card folds away once everything is saved (7.9);
  // the "Flight info" button at the top opens it again for a correction.
  const [infoOpen, setInfoOpen] = useState(true);
  const [infoTouched, setInfoTouched] = useState(false);

  const [flightPicker, setFlightPicker] = useState(false);
  const [seatPicker, setSeatPicker] = useState(false);
  const [currencyPicker, setCurrencyPicker] = useState(false);
  const [typePicker, setTypePicker] = useState(false);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState('');

  // Legs still in play: selling, finished but the trip not yet closed off,
  // or the second flight waiting to be started.
  const sellableFlights = flights.filter(isSellable);
  const activeFlights = flights.filter((f) => (f.status === 'OPEN' || f.status === 'FLOWN') && !f.trip_completed_at && !f.my_completed_at);
  const flight = flights.find((f) => f.id === flightId) || null;
  const selling = isSellable(flight);
  const [trip, setTrip] = useState(null);
  // An iPad account works the trip Inventory assigned to it: no flight picker (7.11).
  const isDevice = user?.role === 'crew_supervisor';

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
    // This iPad's own steps (taken with or without a signal) laid over the
    // server copy; a trip finished on this iPad has left it.
    const journal = await pruneJournal(f.data || []);
    const list = applyJournalToFlights(f.data || [], journal);
    setFlights(list);
    setProducts(p.data || []);
    setBundles(b.data || []);
    setRates(r.data || []);
    setAircraftTypes(a.data || []);
    setRoster(c.data || []);
    setStale(f.stale || p.stale || b.stale || r.stale);
    setFlightId((current) => {
      const all = list;
      const active = all.filter((x) => (x.status === 'OPEN' || x.status === 'FLOWN') && !x.trip_completed_at && !x.my_completed_at);
      if (current && active.some((x) => x.id === current)) return current;
      // Never fall back to a finished trip: with nothing active the page says so.
      return all.filter(isSellable)[0]?.id ?? active[0]?.id ?? null;
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

  // Where the trip stands: which step is next, and whether it may be taken.
  // Works with no signal too (7.12): the last server copy, or the cached
  // flight list, with this iPad's own steps laid over it.
  const loadTrip = useCallback(async () => {
    if (!flightId) return setTrip(null);
    const journal = await readJournal();
    let server = null;
    if (online) {
      try {
        server = await api(`/flights/${flightId}/trip-progress`);
        await writeCache(`tripProgress.${flightId}`, server);
      } catch { /* fall back to the cached copy below */ }
    }
    if (!server) server = await cachedProgress(flightId);
    if (server && server.root_id !== rootOf(flights.find((x) => x.id === flightId)) && flights.length) server = null;
    setTrip(server ? applyJournalToProgress(server, journal) : buildLocalProgress(flights, flightId, journal));
  }, [flightId, online, flights]);
  useEffect(() => { loadTrip(); }, [loadTrip]);
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
  const cartHasCombo = Object.entries(cart).some(([key, qty]) => key.startsWith('b:') && qty > 0);

  // Everything the sector needs before selling. Until it is all there the card
  // stays open; once saved it hides by itself.
  const infoComplete = !!flight && !!flight.aircraft_type && !!flight.passengers && !!dutyGroup;
  useEffect(() => { setInfoTouched(false); }, [flightId]);
  useEffect(() => {
    if (!infoTouched) setInfoOpen(!infoComplete);
  }, [infoComplete, infoTouched, flightId]);

  async function submitDutyGroup() {
    if (!groupSizeOk(dutyDraft.length)) {
      return notify({
        title: `Pick ${DUTY_GROUP_MIN} to ${DUTY_GROUP_MAX} crew`,
        message: `A duty group is between ${DUTY_GROUP_MIN} and ${DUTY_GROUP_MAX} cabin crew. `
          + `${dutyDraft.length} selected.`,
      });
    }
    setBusy(true);
    try {
      const { group, queued } = await saveDutyGroup(flight.id, dutyDraft);
      setDutyGroup(group);
      setDutyPicker(false);
      setSellerId(null);
      setInfoTouched(false);
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
    if (!groupSizeOk(dutyDraft.length)) {
      return notify({
        title: `Pick ${DUTY_GROUP_MIN} to ${DUTY_GROUP_MAX} crew`,
        message: `A duty group is between ${DUTY_GROUP_MIN} and ${DUTY_GROUP_MAX} cabin crew. `
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
      const { result, queued } = await tripAction({
        method: 'PUT', endpoint: `/flights/${flight.id}/aircraft`, label: 'Aircraft', flightId: flight.id,
        body: { aircraft_type: aircraftType, aircraft: aircraft || null, remarks: lockRemarks || null },
      });
      await journalLeg(rootOf(flight), flight.id, { aircraft_type: aircraftType, aircraft: aircraft || null });
      const updated = result || { id: flight.id, aircraft_type: aircraftType, aircraft: aircraft || null, aircraft_locked: 1 };
      setFlights((list) => list.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)));
      setMessage({
        tone: 'success',
        text: `Aircraft set to ${updated.aircraft_type}${updated.aircraft ? ` (${updated.aircraft})` : ''}.`
          + (queued ? ' Saved on this iPad; it uploads on Wi-Fi.' : ''),
      });
      onQueueChange?.();
      setLockRemarks('');
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function savePax() {
    if (!flight) return;
    try {
      const { result, queued } = await tripAction({
        method: 'PUT', endpoint: `/flights/${flight.id}/pax-load`, label: 'PAX load', flightId: flight.id,
        body: { passengers: Number(pax), remarks: lockRemarks || null },
      });
      await journalLeg(rootOf(flight), flight.id, { passengers: Number(pax) });
      const updated = result || { id: flight.id, passengers: Number(pax), pax_locked: 1 };
      setFlights((list) => list.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)));
      setMessage({ tone: 'success', text: `PAX load saved as ${updated.passengers}.` + (queued ? ' Saved on this iPad; it uploads on Wi-Fi.' : '') });
      onQueueChange?.();
      setLockRemarks('');
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  /** Aircraft and PAX in one go. When the crew list is in too, the card folds away. */
  async function saveFlightInfo() {
    if (!flight) return;
    const aircraftChanged = aircraftType && (aircraftType !== flight.aircraft_type || (aircraft || '') !== (flight.aircraft || ''));
    const paxChanged = pax !== '' && Number(pax) !== Number(flight.passengers || 0);
    if ((aircraftChanged && flight.aircraft_locked) || (paxChanged && flight.pax_locked)) {
      if (!lockRemarks.trim()) {
        return notify({ title: 'Remarks required', message: 'Say why the aircraft or PAX load is being corrected.' });
      }
    }
    setBusy(true);
    if (aircraftChanged) await saveAircraft();
    if (paxChanged) await savePax();
    setBusy(false);
    setInfoTouched(false);
    if (dutyGroup) {
      setInfoOpen(false);
      notify({ title: 'Flight info saved', message: 'Aircraft, PAX load and crew are recorded. Tap \u201cFlight info\u201d at the top to see or correct them.' });
    } else if (!dutyGroup && roster.length) {
      setDutyDraft([]); setDutyMode('create'); setDutyPicker(true);
    }
  }

  // "Finish this flight" lives on the Reports page (7.9), above the sector's forms.

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
    // 7.11: normal sales pay into the sector's pooled commission, so no seller is
    // picked. A combo names who sold it, because that person earns its commission.
    if (cartHasCombo && !sellerId) {
      return notify({
        title: 'Who sold the combo?',
        message: 'Choose the crew member who sold this combo. Combo commission is paid to that person.',
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
      sold_by_crew_member_id: cartHasCombo ? sellerId : null,
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
  const canSell = flight && flight.passengers > 0 && selling;

  async function startSecond() {
    const second = trip?.legs?.[1];
    if (!second) return;
    const sure = await confirm({
      title: 'Start the second flight?',
      message: `${second.flight_number} ${route(second)} opens for selling.\n\n`
        + `The forms for ${route(trip.legs[0])} are locked and stay with Management. `
        + 'They can no longer be changed on this iPad.',
      confirmText: 'Start second flight',
    });
    if (!sure) return;
    try {
      const { queued } = await tripAction({ endpoint: `/flights/${second.id}/start-second`, label: 'Start second flight', flightId: second.id });
      const now = new Date().toISOString();
      await journalLeg(trip.root_id, second.id, { started_at: now });
      await journalLeg(trip.root_id, trip.legs[0].id, { forms_locked_at: now });
      await writeCache(`sectorReports.${trip.legs[0].id}`, null);
      await loadReference();
      setFlightId(second.id);
      await loadTrip();
      onQueueChange?.();
      setMessage({ tone: 'success', text: `${second.flight_number} ${route(second)} started. Enter the aircraft, PAX load and crew.`
        + (queued ? ' (No signal: this step uploads with the sales on Wi-Fi.)' : '') });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function completeTrip() {
    const sure = await confirm({
      title: 'Finish the whole flight?',
      message: 'Everything for this trip is closed: sales, forms and the stock return. '
        + 'Nothing can be edited on the iPad afterwards.',
      confirmText: 'Finish whole flight',
      tone: 'danger',
    });
    if (!sure) return;
    try {
      const { queued } = await tripAction({ endpoint: `/flights/${trip.root_id}/complete-trip`, label: 'Finish whole flight', flightId: trip.root_id });
      const now = new Date().toISOString();
      await journalTrip(trip.root_id, { completed_at: now, synced: !queued });
      for (const leg of trip.legs) await journalLeg(trip.root_id, leg.id, { forms_locked_at: now });
      await loadReference();
      onQueueChange?.();
      setMessage({ tone: 'success', text: 'The whole flight is finished. Thank you — nothing more to do on this trip.'
        + (queued ? ' Everything uploads by itself when the iPad reaches Wi-Fi.' : '') });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

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
      {!!trip && (trip.step === 'FORMS_FIRST' || trip.step === 'FORMS_LAST') && (
        <Banner
          tone="warning"
          message={`Reminder: ${(trip.step === 'FORMS_FIRST' ? trip.legs[0] : trip.legs[trip.legs.length - 1]).reports_total
            - (trip.step === 'FORMS_FIRST' ? trip.legs[0] : trip.legs[trip.legs.length - 1]).reports_filed} form(s) for `
            + `${route(trip.step === 'FORMS_FIRST' ? trip.legs[0] : trip.legs[trip.legs.length - 1])} are still to fill in on the Reports page.`}
        />
      )}

      <Card>
        {/* While selling, the whole flight section folds into this bar once the
            aircraft, PAX load and crew are saved; tap it to see or correct them. */}
        {!!flight && selling && (
          <>
            <Pressable
              onPress={() => { setInfoTouched(true); setInfoOpen((v) => !v); }}
              accessibilityRole="button"
              style={[styles.rowBetween, {
                paddingVertical: 10, paddingHorizontal: 12, borderRadius: 8,
                backgroundColor: infoComplete ? COLORS.successSoft : COLORS.warningSoft,
                borderWidth: 1, borderColor: infoComplete ? COLORS.success : COLORS.warning,
              }]}
            >
              <View style={[styles.row, { gap: 8, flex: 1, flexWrap: 'wrap' }]}>
                <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>Flight info</Text>
                <Pill label={`${flight.flight_number} \u00b7 ${flight.origin}\u2192${flight.destination}`} tone="info" />
                {!!flight.aircraft_type && <Pill label={flight.aircraft_type + (flight.aircraft ? ` ${flight.aircraft}` : '')} tone="muted" />}
                {!!flight.passengers && <Pill label={`PAX ${flight.passengers}`} tone="muted" />}
                {!!dutyGroup && <Pill label={`${dutyMembers.length} crew`} tone="muted" />}
                {infoComplete
                  ? <Pill label="SELLING" tone="success" />
                  : <Pill label="Fill in before selling" tone="warning" />}
              </View>
              <Icon name={infoOpen ? 'chevronDown' : 'chevronRight'} size={18} color={COLORS.muted} />
            </Pressable>
            {infoOpen && <View style={{ height: 12 }} />}
          </>
        )}

        {(!flight || !selling || infoOpen) && (
          <>
            <SectionTitle
              title="Flight"
              description={isDevice
                ? 'Set by Inventory for this iPad. The next trip appears here once this one is finished.'
                : 'Only flights assigned to this device are listed.'}
            />
            {isDevice && flight ? (
              <View style={[styles.listRow, { paddingVertical: 10, marginBottom: 8 }]}>
                <Text style={{ fontSize: 15, fontWeight: '600', color: COLORS.text }}>
                  {flight.flight_number} · {flight.origin}→{flight.destination}
                </Text>
                <Text style={styles.mono}>
                  {flight.flight_date}
                  {activeFlights.length > 1 ? ` \u00b7 ${activeFlights.map((f) => f.flight_number + ' ' + route(f)).join(' / ')}` : ''}
                  {flight.crew_account ? ` \u00b7 iPads: ${String(flight.crew_account).split(',').join(' + ')}` : ''}
                </Text>
              </View>
            ) : (
              <Select
                valueLabel={
                  flight
                    ? `${flight.flight_number} \u00b7 ${flight.origin}\u2192${flight.destination} \u00b7 ${flight.flight_date}`
                    : null
                }
                placeholder="Select a flight"
                onPress={() => setFlightPicker(true)}
              />
            )}
            {!activeFlights.length && (
              <Text style={styles.empty}>
                No trip in progress on this iPad. Inventory assigns the device to a trip; it appears here by itself.
              </Text>
            )}
          </>
        )}

        {flight && (
          <>
            {(!selling || infoOpen) && (
              <View style={[styles.wrap, { marginBottom: 12 }]}>
                <Pill label={flight.direction} tone="info" />
                <Pill label={flight.trip_type.replace('_', ' ')} tone="muted" />
                {!!flight.aircraft_type && <Pill label={flight.aircraft_type} tone="info" />}
                {!!dutyGroup && (
                  <Pill label={`${dutyMembers.length} crew`} tone={dutyGroup.pending ? 'warning' : 'muted'} />
                )}
                <Pill
                  label={selling ? 'SELLING' : legFinished(flight) ? (flight.status === 'CLOSED' ? 'CLOSED' : 'FINISHED') : 'NOT STARTED'}
                  tone={selling ? 'success' : legFinished(flight) && flight.status !== 'CLOSED' ? 'warning' : 'muted'}
                />
              </View>
            )}

            {!!trip && (!selling || infoOpen) && <TripSteps trip={trip} />}

            {selling && (
            <>
            {infoOpen && (
            <>
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
            <Button
              title={busy ? 'Saving\u2026' : dutyGroup ? 'Save flight info' : 'Save and enter the crew'}
              tone="primary"
              disabled={busy || !aircraftType || pax === ''}
              onPress={saveFlightInfo}
            />
            <Text style={[styles.mono, { marginTop: 6 }]}>
              Aircraft, PAX load and the crew list are saved together. Once all three are in, this section hides itself.
            </Text>
            </>
            )}
            </>
            )}

            {selling && infoOpen && (
              <>
                <View style={styles.divider} />
                <Button
                  title="Sector over? Go to Reports to finish it"
                  tone="ghost"
                  small
                  onPress={() => goToReports?.(flight.id)}
                />
                <Text style={[styles.mono, { marginTop: 6 }]}>
                  {'\u201cFinish this flight\u201d is on the Reports page, just above the forms for this sector.'}
                </Text>
              </>
            )}

            {!selling && !!trip && (
              <>
                <View style={styles.divider} />
                <TripAction
                  trip={trip}
                  online={online}
                  onStartSecond={startSecond}
                  onComplete={completeTrip}
                  goToReports={goToReports}
                  goTo={goTo}
                />
              </>
            )}

          </>
        )}
      </Card>

      {selling && infoOpen && (
        <Card>
          <SectionTitle
            title="Crew on this sector"
            description={`The cabin crew selling on this sector (${DUTY_GROUP_MIN} to ${DUTY_GROUP_MAX}). The sector's commission `
              + 'is shared equally between these names; a combo is paid to whoever sold it.'}
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
              title="Enter the crew working this sector"
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
                title={`CHANGE WHO IS SELECTED (${dutyDraft.length} selected, ${DUTY_GROUP_MIN}\u2013${DUTY_GROUP_MAX})`}
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
            {cartHasCombo ? (
              <Select
                label="Combo sold by (earns the combo commission)"
                valueLabel={seller ? `${seller.name}${seller.crew_code ? ` · ${seller.crew_code}` : ''}` : ''}
                placeholder={dutyGroup ? 'Who sold this combo?' : 'Enter the crew working this sector first'}
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
            ) : (
              <Text style={[styles.mono, { marginBottom: 10 }]}>
                Commission on this sale is shared equally by the {dutyMembers.length || ''} crew on this sector. Only a combo asks who sold it.
              </Text>
            )}
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
              disabled={busy || (!Object.values(cart).some((q) => q > 0)) || !seat || (cartHasCombo && !sellerId)}
              tone={online ? 'primary' : 'warning'}
            />
            {!!Object.values(cart).some((q) => q > 0) && (
              <Button title="Clear cart" tone="ghost" small style={{ marginTop: 8 }} onPress={() => setCart({})} />
            )}
          </Card>
          {!infoOpen && (
            <Button
              title="Sector over? Go to Reports to finish this flight"
              tone="ghost"
              small
              style={{ marginBottom: 12 }}
              onPress={() => goToReports?.(flight.id)}
            />
          )}
        </>
      )}

      {selling && !flight.passengers && (
        <Banner tone="warning" message="Enter the PAX load above before recording any sale." />
      )}

      <OptionModal
        visible={dutyPicker}
        title={dutyMode === 'amend'
          ? `Correct the crew list \u2014 pick ${DUTY_GROUP_MIN} to ${DUTY_GROUP_MAX}`
          : `Who is working this sector? Pick ${DUTY_GROUP_MIN} to ${DUTY_GROUP_MAX}`}
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
          if (current.length >= DUTY_GROUP_MAX) {
            notify({
              title: `${DUTY_GROUP_MAX} crew already selected`,
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
        title="Who sold the combo?"
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
        options={activeFlights.map((f) => ({
          value: f.id,
          label: `${f.flight_number} · ${f.origin}→${f.destination}`,
          sub: `${f.flight_date} · ${f.direction} · `
            + (isSellable(f) ? 'selling' : legFinished(f) ? 'finished' : 'not started yet'),
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

/* ------------------------------------------------------------------ */
/* Trip progress: the steps and the one action that comes next         */
/* ------------------------------------------------------------------ */
function TripSteps({ trip }) {
  const [first, second] = trip.legs;
  // This iPad's own steps (7.12): the other iPad never holds it up.
  const stock = trip.my_stock_return_done ?? ({ NOT_SUBMITTED: false, WAITING: false, REJECTED: false, PENDING: true, APPROVED: true }[trip.stock_return]);
  const steps = [];
  trip.legs.forEach((leg, index) => {
    const label = trip.round_trip ? (index === 0 ? 'first segment' : 'second segment') : 'this flight';
    if (index === 1) steps.push({ label: `Start second flight ${route(leg)}`, done: leg.started });
    steps.push({ label: `Sell and finish ${route(leg)}`, done: leg.finished });
    if (leg.stock_count_needed) {
      steps.push({ label: `Remaining stock after ${route(leg)} confirmed on this iPad`, done: !!leg.stock_count_done });
    } else {
      steps.push({ label: 'Final stock return from this iPad', done: !!stock });
    }
    steps.push({
      label: `Forms (${label}) ${leg.reports_handled ?? leg.reports_filed}/${leg.reports_total} handled`
        + ((leg.reports_awaiting_other || []).length ? ` · ${leg.reports_awaiting_other.length} awaiting the other iPad` : ''),
      done: leg.reports_done,
    });
  });
  steps.push({ label: 'Finish whole flight', done: trip.completed });
  const current = steps.findIndex((s) => !s.done);
  return (
    <View style={{ marginBottom: 6, padding: 10, borderRadius: 8, backgroundColor: COLORS.surfaceAlt }}>
      <Text style={[styles.label, { marginBottom: 4 }]}>
        {trip.round_trip ? `Trip ${route(first)} / ${route(second)}` : `Trip ${route(first)}`}
      </Text>
      {steps.map((step, i) => (
        <Text key={step.label} style={{
          fontSize: 13, lineHeight: 20,
          color: step.done ? COLORS.success : i === current ? COLORS.text : COLORS.faint,
          fontWeight: i === current ? '600' : '400',
        }}>
          {step.done ? '\u2713  ' : i === current ? '\u25B6  ' : '\u25CB  '}{step.label}
        </Text>
      ))}
    </View>
  );
}

function TripAction({ trip, online, onStartSecond, onComplete, goToReports, goTo }) {
  const [first, second] = trip.legs;
  const last = trip.legs[trip.legs.length - 1];
  const need = !online ? ' (No signal: the step is saved on this iPad and uploads on Wi-Fi.)' : '';

  if (trip.step === 'DONE') {
    return <Banner tone="success" message="The whole flight is finished. Nothing on this trip can be edited any more." />;
  }
  if (trip.step === 'FORMS_FIRST' || trip.step === 'STOCK_FIRST' || trip.step === 'START_SECOND') {
    return (
      <>
        <Button
          title={`Start second flight ${route(second)}`}
          tone="primary"
          onPress={onStartSecond}
          disabled={!trip.can_start_second}
        />
        <Text style={[styles.mono, { marginTop: 6 }]}>
          {trip.can_start_second
            ? `The forms and the remaining stock for ${route(first)} lock and stay with Management when you start.`
            : trip.step === 'STOCK_FIRST'
              ? `Confirm the remaining stock on this iPad after ${route(first)} on the Reports page first.`
              : `Handle all ${first.reports_total} forms for ${route(first)} first `
                + `(${first.reports_handled ?? first.reports_filed} handled): fill them in or mark them as submitted by the other iPad.`}
          {need}
        </Text>
        {!trip.can_start_second && (
          <Button title={trip.step === 'STOCK_FIRST' ? 'Open the remaining stock' : 'Open the forms'} tone="ghost" small style={{ marginTop: 8 }}
            onPress={() => goToReports?.(first.id)} />
        )}
      </>
    );
  }
  if (['FORMS_LAST', 'STOCK_RETURN', 'COMPLETE'].includes(trip.step)) {
    return (
      <>
        <Button
          title="Finish whole flight"
          tone="danger"
          onPress={onComplete}
          disabled={!trip.can_complete}
        />
        <Text style={[styles.mono, { marginTop: 6 }]}>
          {trip.step === 'FORMS_LAST'
            ? `Handle all ${last.reports_total} forms for ${route(last)} first (${last.reports_handled ?? last.reports_filed} handled).`
            : trip.step === 'STOCK_RETURN'
              ? 'Submit this iPad\u2019s final stock return first (Sync page).'
              : 'Everything is in. Finishing locks the whole trip.'}
          {need}
        </Text>
        {trip.step === 'FORMS_LAST' && (
          <Button title="Open the forms" tone="ghost" small style={{ marginTop: 8 }}
            onPress={() => goToReports?.(last.id)} />
        )}
        {trip.step === 'STOCK_RETURN' && (
          <Button title="Open the final stock return" tone="ghost" small style={{ marginTop: 8 }}
            onPress={() => goTo?.('sync')} />
        )}
      </>
    );
  }
  return null;
}
