import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';
import { Text, TextInput } from '../text';

import { api, readCache, writeCache } from '../api';
import {
  applyJournalToPosition, buildLocalPosition, findLeg, journalLeg, readJournal, tripAction,
} from '../tripLocal';
import { usePaged } from '../paging';
import { confirm, notify } from '../dialog';
import { COLORS, npr, styles } from '../theme';
import { Banner, Button, Card, Field, Pagination, Pill, RemarkLine, SectionTitle } from '../ui';

/*
 * 7.18: the stock cards of the Sync & Return Stock page, and the sector
 * counts Management and Inventory read on the Reports page.
 *
 * There is one trolley and one count of it. Every crew member sells on their
 * own phone, offline, so each phone only knows its own sales; the figure that
 * counts is what is physically in the trolley. Whoever counts it submits it;
 * the others press "Another crew member submitted it". The server adds up
 * every phone's sales when they upload and shows any difference.
 */

const when = (s) => String(s || '').replace('T', ' ').slice(0, 16);

/** The remaining stock after a sector that is not the last one. */
export function SectorStockCard({ flightId, rootId, flight, online, user, onChanged }) {
  const me = user?.employee_id;
  const [position, setPosition] = useState(null);
  const [declared, setDeclared] = useState({});
  const [remarks, setRemarks] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [local, setLocal] = useState(null);      // this phone's own step, from the journal

  const load = useCallback(async () => {
    let p = null;
    if (online) {
      try {
        p = await api(`/flights/${flightId}/sector-stock`);
        await writeCache(`sectorStock.${flightId}`, p);
      } catch (e) {
        if (e.permanent) { setError(e.message); return; }
      }
    }
    if (!p) p = await readCache(`sectorStock.${flightId}`, null);
    if (!p) {
      const inventory = (await readCache(`inventory.${flightId}`, null)) || (await readCache(`inventory.${rootId}`, [])) || [];
      p = buildLocalPosition(flight, inventory, me);
    }
    if (!p) { setError('The trolley stock is not on this phone yet. Open the Sales page once with a connection.'); return; }
    const leg = findLeg(await readJournal(), flightId);
    setLocal(leg || null);
    p = applyJournalToPosition(p, leg, me);
    setPosition(p);
    setDeclared(Object.fromEntries((p.items || []).map((i) => [i.product_id, String(i.expected_remaining)])));
    setError(null);
  }, [flightId, rootId, flight, me, online]);
  useEffect(() => { load(); }, [load]);

  async function submit() {
    const lines = position.items.map((i) => ({ product_id: i.product_id, declared_remaining: Number(declared[i.product_id] || 0) }));
    if (lines.some((l) => !Number.isInteger(l.declared_remaining) || l.declared_remaining < 0)) {
      return notify({ title: 'Check the quantities', message: 'Every remaining quantity must be a whole number, 0 or more.' });
    }
    const left = lines.reduce((a, l) => a + l.declared_remaining, 0);
    const sure = await confirm({
      title: 'Submit the remaining stock?',
      message: `${position.flight_number} ${position.origin}→${position.destination}: ${left} unit(s) left in the trolley. `
        + 'This is the count for the whole crew; the others press "Another crew member submitted it".',
      confirmText: 'Submit',
    });
    if (!sure) return;
    setBusy(true);
    try {
      const { result, queued } = await tripAction({ endpoint: `/flights/${flightId}/sector-stock`,
        body: { lines, remarks: remarks.trim() || null }, label: `Remaining stock ${position.flight_number}`, flightId });
      await journalLeg(rootId, flightId, {
        stock_count_at: new Date().toISOString(),
        stock_lines: Object.fromEntries(lines.map((l) => [l.product_id, l.declared_remaining])),
        stock_remarks: remarks.trim() || null,
      });
      await load();
      onChanged?.();
      notify({
        title: result?.conflict ? 'Another count was already in' : 'Remaining stock submitted',
        message: result?.conflict
          ? `${result.submitted_by} had already submitted a different count. Theirs stands; yours is shown to Management as a difference.`
          : (queued ? 'Saved on this phone; it uploads by itself on Wi-Fi. ' : '')
            + 'Next: “Start second flight” on the Sales page.',
      });
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  async function byOther() {
    const sure = await confirm({
      title: 'Another crew member submitted it?',
      message: 'Your phone moves on to the second flight. The count itself comes from the colleague who did it.',
      confirmText: 'Yes, a colleague did it',
    });
    if (!sure) return;
    setBusy(true);
    try {
      await tripAction({ endpoint: `/flights/${flightId}/sector-stock`, body: { by_other: true },
        label: `Remaining stock ${position?.flight_number || ''}: by a colleague`, flightId });
      await journalLeg(rootId, flightId, { stock_by_other_at: new Date().toISOString() });
      await load();
      onChanged?.();
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  if (error) return <Card><Banner tone="danger" message={error} /></Card>;
  if (!position) return null;
  const someoneElse = position.submitted && position.submitted_by && position.submitted_by !== me;
  const mine = !!local?.stock_count_at || (position.submitted && position.submitted_by === me);
  const handed = !!local?.stock_by_other_at;
  const done = mine || handed || someoneElse;

  return (
    <Card>
      <SectionTitle
        title={`Remaining stock after ${position.origin}→${position.destination}`}
        description={someoneElse
          ? `Submitted by ${position.submitted_by} ${when(position.submitted_at)}. Nothing more to do here.`
          : mine ? 'You submitted the count for the crew.'
            : handed ? 'You said another crew member submitted it. Their count arrives when their phone uploads.'
              : 'Count what is left in the trolley and submit it once for the whole crew, or say another crew member did it.'}
        right={<Pill label={done ? 'Done' : 'To do'} tone={done ? 'success' : 'warning'} />}
      />
      {position.offline_built && !done && (
        <Banner tone="info" message="No signal: the Expected column is what this phone knows (the load less your own sales). The other phones' sales are added when they upload — count what is actually there." />
      )}
      <View style={[styles.row, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border }]}>
        <Text style={[styles.mono, { flex: 2 }]}>Product</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>Loaded</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>Sold</Text>
        <Text style={[styles.mono, { flex: 1, textAlign: 'right' }]}>Expected</Text>
        <Text style={[styles.mono, { flex: 1.2, textAlign: 'right' }]}>Counted</Text>
      </View>
      {(position.items || []).map((i) => {
        const counted = done ? (i.declared_remaining ?? i.expected_remaining) : Number(declared[i.product_id] || 0);
        const diff = counted - i.expected_remaining;
        return (
          <View key={i.product_id} style={[styles.row, { paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt }]}>
            <View style={{ flex: 2 }}>
              <Text style={{ fontSize: 13, color: COLORS.text }} numberOfLines={2}>{i.product_name}</Text>
              {diff !== 0 && <Text style={[styles.mono, { color: COLORS.warning }]}>{diff > 0 ? '+' : ''}{diff} vs expected</Text>}
            </View>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{i.loaded_quantity}</Text>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{(i.sold_before || 0) + (i.sold_quantity || 0)}</Text>
            <Text style={[styles.value, { flex: 1, textAlign: 'right' }]}>{i.expected_remaining}</Text>
            <View style={{ flex: 1.2, alignItems: 'flex-end' }}>
              {done ? (
                <Text style={[styles.value, { fontWeight: '600' }]}>{counted}</Text>
              ) : (
                <TextInput
                  value={declared[i.product_id] ?? ''}
                  onChangeText={(v) => setDeclared((d) => ({ ...d, [i.product_id]: v.replace(/[^0-9]/g, '') }))}
                  keyboardType="number-pad"
                  accessibilityLabel={`Counted ${i.product_name}`}
                  style={[styles.input, { width: 64, textAlign: 'center', paddingVertical: 6 }]}
                />
              )}
            </View>
          </View>
        );
      })}
      {!done && (
        <>
          <Field label="Remarks (optional)" value={remarks} onChangeText={setRemarks} multiline maxLength={500} placeholder="e.g. one can damaged" />
          <Button title={busy ? 'Saving…' : 'Submit remaining stock'} onPress={submit} disabled={busy} />
          <Button title="Another crew member submitted it" tone="ghost" small style={{ marginTop: 8 }} onPress={byOther} disabled={busy} />
        </>
      )}
      {!!position.remarks && <RemarkLine label="Remarks" text={position.remarks} />}
    </Card>
  );
}

/** What this sector earned: the pool split across the crew, combos to the seller. */
export function CommissionCard({ flightId, online, user, title }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!online) return;
    api(`/flights/${flightId}/commission`)
      .then(async (d) => { setData(d); setError(null); await writeCache(`commission.${flightId}`, d); })
      .catch(async (e) => { const c = await readCache(`commission.${flightId}`, null); if (c) setData(c); else setError(e.message); });
  }, [flightId, online]);
  useEffect(() => {
    if (online) return;
    readCache(`commission.${flightId}`, null).then((c) => { if (c) setData(c); });
  }, [flightId, online]);
  if (error) return <Card><Banner tone="danger" message={error} /></Card>;
  if (!data) return null;
  const s = data.sector || {};
  const me = (data.members || []).find((m) => m.crew_code === user?.employee_id);
  return (
    <Card>
      <SectionTitle
        title={title || `Commission · ${data.flight ? `${data.flight.flight_number} ${data.flight.origin}→${data.flight.destination}` : ''}`}
        description={`${Math.round((data.commission_rate || 0.1) * 100)}% of product sales is shared equally by the ${s.member_count || 0} crew; `
          + 'a combo’s commission goes to whoever sold it.' + (online ? '' : ' (Last uploaded figures.)')}
        right={<Pill label={npr(data.total_commission_npr)} tone="success" />}
      />
      {!!me && (
        <View style={{ padding: 10, borderRadius: 8, backgroundColor: COLORS.successSoft, marginBottom: 8 }}>
          <Text style={{ fontSize: 14, fontWeight: '700', color: COLORS.text }}>Your commission: {npr(me.total_npr)}</Text>
          <Text style={styles.mono}>
            share {npr(me.share_npr)}{me.combo_commission_npr ? ` + your combos ${npr(me.combo_commission_npr)}` : ''}
            {me.own_sales_npr ? ` · you sold ${npr(me.own_sales_npr)} (${me.own_transactions} transaction(s))` : ''}
          </Text>
        </View>
      )}
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        <Pill label={`Product sales ${npr(s.product_revenue_npr || 0)}`} tone="muted" />
        <Pill label={`Pool ${npr(s.commission_pool_npr || 0)} ÷ ${s.member_count || 0} = ${npr(s.share_npr || 0)} each`} tone="info" />
        {!!s.combo_revenue_npr && <Pill label={`Combos ${npr(s.combo_revenue_npr)} → ${npr(s.combo_commission_npr)}`} tone="warning" />}
      </View>
      {(data.members || []).map((m) => (
        <View key={m.crew_member_id} style={[styles.rowBetween, { paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 13, color: COLORS.text, fontWeight: m.crew_code === user?.employee_id ? '700' : '400' }}>{m.name}</Text>
            <Text style={styles.mono}>
              share {npr(m.share_npr)}{m.combo_commission_npr ? ` + combo ${npr(m.combo_commission_npr)}` : ''}
              {m.own_sales_npr ? ` · sold ${npr(m.own_sales_npr)}` : ''}
            </Text>
          </View>
          <Text style={[styles.value, { fontWeight: '700' }]}>{npr(m.total_npr)}</Text>
        </View>
      ))}
      {!(data.members || []).length && <Text style={styles.empty}>No crew recorded on this sector yet.</Text>}
    </Card>
  );
}

/** Plain-language explanation of one product's line in a sector count. */
export function explainLine(i) {
  const sold = (i.sold_before || 0) + (i.sold_quantity || 0);
  const wasted = i.wasted_quantity || 0;
  const base = `${i.loaded_quantity} loaded − ${sold} sold${wasted ? ` − ${wasted} wasted` : ''} = ${i.expected_remaining} should be left. `
    + `${i.declared_remaining} were counted.`;
  if (!i.variance) return `${base} It matches.`;
  if (i.variance < 0) {
    return `${base} ${-i.variance} ${-i.variance === 1 ? 'is' : 'are'} missing: a sale that was not recorded, a breakage `
      + 'or a miscount. Check the phones’ upload queues first — a sale still waiting to upload shows up as missing stock.';
  }
  return `${base} ${i.variance} more than expected: a sale recorded twice or on the wrong product, `
    + 'or a load count that was too low.';
}

/** Management, Inventory and the administrator: every confirmed sector count. */
export function SectorStockList() {
  const pager = usePaged('/sector-stock-counts');
  const [open, setOpen] = useState(null);      // flight id expanded
  const [detail, setDetail] = useState(null);

  async function toggle(flightId) {
    if (open === flightId) { setOpen(null); setDetail(null); return; }
    setOpen(flightId);
    setDetail(null);
    try { setDetail(await api(`/flights/${flightId}/sector-stock`)); } catch (e) { setDetail({ error: e.message }); }
  }

  return (
    <Card>
      <SectionTitle
        title="Remaining stock after each sector"
        description="The count after the first sector of a round trip. A highlighted line differs from the system; open it to see what the difference means."
      />
      {pager.items.map((c) => (
        <View key={c.id}>
          <Pressable onPress={() => toggle(c.flight_id)} accessibilityRole="button"
            style={[styles.listRow, { paddingVertical: 12 }, c.total_variance ? { backgroundColor: COLORS.warningSoft, borderRadius: 8, paddingHorizontal: 8 } : null]}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flex: 1 }}>
                {c.flight_number} · {c.origin}→{c.destination} · {c.flight_date}
              </Text>
              <Pill label={c.total_variance ? `${c.total_variance > 0 ? '+' : ''}${c.total_variance} vs system` : 'Matches system'}
                    tone={c.total_variance ? 'warning' : 'success'} />
            </View>
            <Text style={styles.mono}>
              {c.total_declared} unit(s) left · counted by {c.submitted_by} · {when(c.submitted_at)} · tap for details
            </Text>
            {!!c.remarks && <RemarkLine label="Crew remarks" text={c.remarks} />}
          </Pressable>
          {open === c.flight_id && (
            <View style={{ paddingHorizontal: 8, paddingBottom: 10 }}>
              {!detail && <Text style={styles.mono}>Loading…</Text>}
              {!!detail?.error && <Banner tone="danger" message={detail.error} />}
              {!!detail?.items && detail.items.map((i) => (
                <View key={i.product_id} style={{ paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt }}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontSize: 13, fontWeight: i.variance ? '700' : '500', color: i.variance ? COLORS.warning : COLORS.text, flex: 1 }}>
                      {i.product_name}
                    </Text>
                    <Text style={[styles.mono, { color: i.variance ? COLORS.warning : COLORS.muted }]}>
                      {i.variance ? `${i.variance > 0 ? '+' : ''}${i.variance}` : 'OK'}
                    </Text>
                  </View>
                  <Text style={styles.mono}>{explainLine(i)}</Text>
                </View>
              ))}
            </View>
          )}
        </View>
      ))}
      {!!pager.error && <Banner tone="danger" message={pager.error} />}
      {!pager.items.length && !pager.loading && <Text style={styles.empty}>No sector count yet.</Text>}
      <Pagination pager={pager} label="counts" />
    </Card>
  );
}
