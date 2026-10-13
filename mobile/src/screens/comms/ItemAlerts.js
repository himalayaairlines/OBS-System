import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Platform, Pressable, Share, View } from 'react-native';
import { Text } from '../../text';

import { api, apiText } from '../../api';
import { usePaged } from '../../paging';
import { COLORS, ROLE_LABELS, styles } from '../../theme';
import {
  Banner, Button, Card, Chip, DataTable, Field, KeyValue, Metric, Notice, OptionModal, Pagination,
  Pill, SearchBar, SectionTitle, Select,
} from '../../ui';
import { FREQUENCY_LABELS, SeverityPill, StatusPill, Toggle, has, qty, when } from './shared';

/*
 * Stock Alert Settings (Communication Manager > Item alert settings).
 *
 * Rules decide who is told and how; this screen shows, for every item, what
 * actually applies to it and lets an authorised user override individual
 * thresholds or switch alerts off for one item. Everything shown here comes
 * from the server; there are no thresholds in this file.
 */

export const ITEM_STATUS = {
  CONFIGURED: ['Configured', 'success'],
  INHERITED: ['Inherited', 'info'],
  DISABLED: ['Disabled', 'muted'],
  NOT_CONFIGURED: ['No alert', 'warning'],
  INVALID: ['Needs attention', 'danger'],
  GLOBAL_OFF: ['Alerts off', 'muted'],
  INACTIVE: ['Inactive', 'muted'],
};
const MODE_LABELS = { INHERIT: 'Inherit from rules', OVERRIDE: 'Override thresholds', DISABLED: 'Disable alerts' };
const OUTCOME = {
  OPEN: ['Would alert', 'danger'], SEVERITY_UP: ['Severity rises', 'danger'], SUPPRESSED: ['No duplicate', 'muted'],
  RESOLVE: ['Would resolve', 'success'], HOLD: ['Stays open', 'warning'], NONE: ['No alert', 'success'],
  WATCH: ['Watching', 'warning'], BLOCKED: ['Blocked', 'muted'], SKIPPED: ['Not applied', 'muted'],
};
const STATUS_FILTERS = [null, 'CONFIGURED', 'INHERITED', 'DISABLED', 'NOT_CONFIGURED', 'INVALID'];

function ItemStatus({ status }) {
  const [label, tone] = ITEM_STATUS[status] || [status, 'info'];
  return <Pill label={label} tone={tone} />;
}

/** A threshold value, marked when it is the item's own override rather than inherited. */
function Value({ value, override }) {
  return (
    <Text style={{ color: value === null || value === undefined ? COLORS.faint : COLORS.text, fontWeight: override ? '600' : '400' }}>
      {qty(value)}{override ? ' ●' : ''}
    </Text>
  );
}

const num = (text) => (text === '' || text === null || text === undefined ? null : Number(text));
const numText = (v) => (v === null || v === undefined ? '' : String(v));

export default function ItemAlerts({ user, options }) {
  const canEdit = has(user, 'comms_item_settings');
  const [q, setQ] = useState('');
  const [category, setCategory] = useState(null);
  const [status, setStatus] = useState(null);
  const [below, setBelow] = useState(false);
  const [picker, setPicker] = useState(false);
  const [coverage, setCoverage] = useState(null);
  const [message, setMessage] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [selected, setSelected] = useState([]);
  const [panel, setPanel] = useState(null); // 'bulk' | 'import'

  const path = useMemo(() => {
    const params = [];
    if (q.trim()) params.push(`q=${encodeURIComponent(q.trim())}`);
    if (category) params.push(`category=${encodeURIComponent(category)}`);
    if (status) params.push(`status=${status}`);
    if (below) params.push('below=true');
    return `/communication/items${params.length ? `?${params.join('&')}` : ''}`;
  }, [q, category, status, below]);
  const data = usePaged(path);

  const loadCoverage = useCallback(async () => {
    try { setCoverage(await api('/communication/coverage')); } catch (e) { setMessage({ tone: 'danger', text: e.message }); }
  }, []);
  useEffect(() => { loadCoverage(); }, [loadCoverage]);

  const refresh = () => { data.reload(); loadCoverage(); };

  async function exportCsv() {
    try {
      const csv = await apiText('/communication/items-export');
      if (Platform.OS === 'web' && typeof document !== 'undefined') {
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'stock-alert-item-settings.csv';
        a.click();
        URL.revokeObjectURL(url);
      } else {
        await Share.share({ title: 'Stock alert item settings', message: csv });
      }
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (openId) {
    return <ItemDetail productId={openId} canEdit={canEdit} options={options} user={user}
      onBack={() => { setOpenId(null); refresh(); }} />;
  }

  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const categories = options?.categories || [];

  return (
    <>
      {message && <Notice message={message} />}
      {coverage?.global_off && (
        <Banner tone="warning" message="Stock alerts are switched off for the whole system (Settings). No item will alert until they are switched back on." />
      )}
      {coverage && (
        <Card>
          <SectionTitle title="Alert coverage"
            description="Which items would actually raise an alert. An item with no rule never notifies anyone, whatever its stock." />
          <View style={[styles.wrap, { gap: 8 }]}>
            <Metric label="Configured for the item" value={coverage.explicitly_configured} tone="success" />
            <Metric label="Inheriting a rule" value={coverage.inheriting} tone="primary" />
            <Metric label="Disabled" value={coverage.excluded} />
            <Metric label="No alert configured" value={coverage.missing} tone={coverage.missing ? 'warning' : 'default'} />
            <Metric label="Needs attention" value={coverage.invalid} tone={coverage.invalid ? 'danger' : 'default'} />
            <Metric label="Below threshold now" value={coverage.below_threshold} tone={coverage.below_threshold ? 'danger' : 'default'} />
          </View>
        </Card>
      )}

      {coverage && coverage.explicitly_configured + coverage.inheriting === 0 && !coverage.global_off && (
        <Banner tone="info" message="No item has an alert yet. Click an item's name, then fill in “Alert for this item” (levels and who to tell) and press Create alert. For a whole category at once, use Alert rules." />
      )}
      <Card>
        <SectionTitle
          title={`Item alert settings (${data.total})`}
          description="To start alerting on an item, open it and fill in “Alert for this item”, or tick several items and choose “Set up alert”. Thresholds shown are what applies now; ● marks a value set on the item itself."
          right={(
            <View style={[styles.row, styles.gap8]}>
              <Button title="Export CSV" tone="ghost" small onPress={exportCsv} />
              {canEdit && <Button title="Import" tone="ghost" small onPress={() => setPanel(panel === 'import' ? null : 'import')} />}
            </View>
          )}
        />
        {panel === 'import' && <ImportPanel onDone={() => { setPanel(null); refresh(); }} />}
        <SearchBar value={q} onChangeText={setQ} placeholder="Search item name or code…" />
        <View style={[styles.wrap, { marginBottom: 8 }]}>
          <Select label={null} valueLabel={category ? `Category: ${category}` : 'All categories'} onPress={() => setPicker(true)}
            style={{ marginBottom: 0, minWidth: 200 }} />
          <Chip label="Below threshold" active={below} onPress={() => setBelow(!below)} tone="danger" />
        </View>
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {STATUS_FILTERS.map((s) => (
            <Chip key={s || 'all'} label={s ? ITEM_STATUS[s][0] : 'All'} active={status === s} onPress={() => setStatus(s)} />
          ))}
        </View>

        {canEdit && selected.length > 0 && (
          <View style={{ backgroundColor: COLORS.primarySoft, borderRadius: 8, padding: 10, marginBottom: 10 }}>
            <View style={styles.rowBetween}>
              <Text style={{ color: COLORS.primary, fontWeight: '600' }}>{selected.length} selected</Text>
              <View style={[styles.row, styles.gap8]}>
                {has(user, 'comms_rules') && (
                  <Button title="Set up alert" small onPress={() => setPanel('setup')} />
                )}
                <Button title="Bulk edit" tone="secondary" small onPress={() => setPanel('bulk')} />
                <Button title="Clear" tone="ghost" small onPress={() => { setSelected([]); setPanel(null); }} />
              </View>
            </View>
          </View>
        )}
        {panel === 'setup' && selected.length > 0 && (
          <QuickSetup
            products={data.items.filter((r) => selected.includes(r.product_id))}
            productIds={selected}
            options={options}
            onCancel={() => setPanel(null)}
            onDone={(text) => { setPanel(null); setSelected([]); setMessage({ tone: 'success', text }); refresh(); }}
          />
        )}
        {panel === 'bulk' && selected.length > 0 && (
          <BulkPanel ids={selected} onDone={(applied) => { setPanel(null); if (applied) { setSelected([]); refresh(); } }} />
        )}

        <DataTable
          rows={data.items}
          rowKey={(r) => String(r.product_id)}
          emptyText={data.loading ? 'Loading…' : (status === 'CONFIGURED' || status === 'INHERITED')
            ? 'No items have an alert yet. Choose All, then open an item and fill in “Alert for this item” (or tick several and choose “Set up alert”).'
            : 'No items match.'}
          columns={[
            ...(canEdit ? [{
              key: 'sel', label: '', flex: 0.3,
              render: (r) => <Check checked={selected.includes(r.product_id)} onPress={() => toggle(r.product_id)} />,
            }] : []),
            {
              key: 'item', label: 'Item', flex: 2.2,
              render: (r) => (
                <Pressable onPress={() => setOpenId(r.product_id)} accessibilityRole="link">
                  <Text style={{ color: COLORS.primary, fontWeight: '600' }} numberOfLines={1}>{r.product_name}</Text>
                  <Text style={styles.mono}>{r.product_code} · {r.category}</Text>
                </Pressable>
              ),
            },
            { key: 'quantity', label: 'Stock', align: 'right', flex: 0.8, render: (r) => (
              <Text style={{ color: r.below_threshold ? COLORS.danger : COLORS.text, fontWeight: r.below_threshold ? '600' : '400' }}>
                {qty(r.quantity)}
              </Text>
            ) },
            { key: 'low', label: 'Alert at', align: 'right', flex: 0.8, render: (r) => <Value value={r.low} override={r.low_is_override} /> },
            { key: 'critical', label: 'Critical', align: 'right', flex: 0.8, render: (r) => <Value value={r.critical} override={r.critical_is_override} /> },
            { key: 'recovery', label: 'Recovers at', align: 'right', flex: 0.9, render: (r) => <Value value={r.recovery} override={r.recovery_is_override} /> },
            { key: 'status', label: 'Status', flex: 1.1, render: (r) => (
              <View style={[styles.row, styles.gap8]}><ItemStatus status={r.status} /><SeverityPill severity={r.open_severity} /></View>
            ) },
            { key: 'source', label: 'Source', flex: 1.6, render: (r) => <Text style={styles.mono} numberOfLines={2}>{r.source || '—'}</Text> },
            { key: 'last', label: 'Last alert', flex: 1, render: (r) => <Text style={styles.mono}>{when(r.last_alert_at)}</Text> },
          ]}
          renderStacked={(r) => (
            <Pressable onPress={() => setOpenId(r.product_id)} onLongPress={() => canEdit && toggle(r.product_id)}>
              <View style={styles.rowBetween}>
                <View style={[styles.row, styles.gap8, { flexShrink: 1 }]}>
                  {canEdit && <Check checked={selected.includes(r.product_id)} onPress={() => toggle(r.product_id)} />}
                  <Text style={{ fontWeight: '600', color: COLORS.text, flexShrink: 1 }} numberOfLines={1}>{r.product_name}</Text>
                </View>
                <ItemStatus status={r.status} />
              </View>
              <Text style={styles.mono}>{r.product_code} · {r.category}</Text>
              <Text style={styles.mono}>
                Stock {qty(r.quantity)} · alert at {qty(r.low)}{r.low_is_override ? ' ●' : ''} · critical {qty(r.critical)}{r.critical_is_override ? ' ●' : ''}
                {' '}· recovers at {qty(r.recovery)}{r.recovery_is_override ? ' ●' : ''}
              </Text>
              {!!r.source && <Text style={styles.mono}>{r.source}</Text>}
              <View style={[styles.row, styles.gap8, { marginTop: 4 }]}>
                <SeverityPill severity={r.open_severity} />
                {r.below_threshold && <Pill label="BELOW THRESHOLD" tone="danger" />}
              </View>
            </Pressable>
          )}
        />
        {!!data.error && <Banner tone="danger" message={data.error} />}
        <Pagination pager={data} label="items" />
      </Card>

      <OptionModal
        visible={picker}
        title="Category"
        options={[{ label: 'All categories', value: null }, ...categories.map((c) => ({ label: c, value: c }))]}
        selectedValue={category}
        onSelect={(v) => { setCategory(v); setPicker(false); }}
        onClose={() => setPicker(false)}
      />
    </>
  );
}

function Check({ checked, onPress }) {
  return (
    <Pressable onPress={onPress} hitSlop={8} accessibilityRole="checkbox" accessibilityState={{ checked }}
      style={{
        width: 20, height: 20, borderRadius: 4, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center',
        borderColor: checked ? COLORS.primary : COLORS.borderStrong, backgroundColor: checked ? COLORS.primary : COLORS.surface,
      }}>
      {checked && <Text style={{ color: '#FFFFFF', fontSize: 12, fontWeight: '700' }}>✓</Text>}
    </Pressable>
  );
}

/* ------------------------------ settings form ----------------------------- */
function SettingForm({ form, setForm, inherited }) {
  const set = (patch) => setForm({ ...form, ...patch });
  const placeholder = (v) => (v === null || v === undefined ? 'Not set' : `Inherited: ${qty(v)}`);
  const digits = (v) => v.replace(/[^0-9]/g, '');
  return (
    <>
      <Text style={styles.label}>Alerts for this item</Text>
      <View style={[styles.wrap, { marginBottom: 10 }]}>
        {Object.entries(MODE_LABELS).map(([mode, label]) => (
          <Chip key={mode} label={label} active={form.mode === mode} onPress={() => set({ mode })} />
        ))}
      </View>
      {form.mode === 'OVERRIDE' && (
        <>
          <Text style={[styles.mono, { marginBottom: 8 }]}>
            Leave a box empty to keep the value from the rule. Stock is counted in whole units. 0 is a real threshold, not “empty”.
          </Text>
          <View style={[styles.wrap, { gap: 8 }]}>
            <Field label="Alert threshold" value={form.low} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
              placeholder={placeholder(inherited?.low)} onChangeText={(v) => set({ low: digits(v) })} />
            <Field label="Critical threshold" value={form.critical} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
              placeholder={placeholder(inherited?.critical)} onChangeText={(v) => set({ critical: digits(v) })} />
            <Field label="Recovers at (stock ≥)" value={form.recovery} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
              placeholder={placeholder(inherited?.recovery)} onChangeText={(v) => set({ recovery: digits(v) })} />
          </View>
        </>
      )}
      {form.mode !== 'INHERIT' && (
        <Field label="Note (why)" value={form.note} onChangeText={(v) => set({ note: v })} placeholder="Optional" />
      )}
    </>
  );
}

function body(form) {
  const override = form.mode === 'OVERRIDE';
  return {
    mode: form.mode,
    low_threshold: override ? num(form.low) : null,
    critical_threshold: override ? num(form.critical) : null,
    recovery_threshold: override ? num(form.recovery) : null,
    note: form.mode === 'INHERIT' ? null : (form.note || null),
  };
}

function formFrom(setting) {
  return {
    mode: setting?.mode || 'INHERIT',
    low: numText(setting?.low_threshold), critical: numText(setting?.critical_threshold),
    recovery: numText(setting?.recovery_threshold), note: setting?.note || '',
  };
}

/* ------------------------------- item detail ------------------------------ */
function ItemDetail({ productId, canEdit, onBack, options, user }) {
  const [detail, setDetail] = useState(null);
  const [form, setForm] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [simQty, setSimQty] = useState('');
  const [sim, setSim] = useState(null);

  const load = useCallback(async () => {
    try {
      const d = await api(`/communication/items/${productId}`);
      setDetail(d);
      setForm(formFrom(d.configuration.item_setting));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, [productId]);
  useEffect(() => { load(); }, [load]);

  if (!detail || !form) {
    return message ? <Notice message={message} /> : <Text style={styles.empty}>Loading…</Text>;
  }
  const { item, configuration: config, suggestion } = detail;
  const saved = formFrom(config.item_setting);
  const dirty = JSON.stringify(saved) !== JSON.stringify(form);
  // What the rules alone would give, for the "Inherited: x" hints.
  const ruleOnly = config.rules.find((r) => r.applies);
  const inherited = ruleOnly && {
    low: ruleOnly.sources.low?.code === 'ITEM_OVERRIDE' ? null : ruleOnly.low,
    critical: ruleOnly.sources.critical?.code === 'ITEM_OVERRIDE' ? null : ruleOnly.critical,
    recovery: ruleOnly.sources.recovery?.code === 'ITEM_OVERRIDE' ? null : ruleOnly.recovery,
  };

  async function save() {
    setBusy(true);
    try {
      const d = await api(`/communication/items/${productId}/alert-settings`, {
        method: 'PUT', body: { ...body(form), expected_version: config.item_setting?.version ?? null },
      });
      setDetail(d);
      setForm(formFrom(d.configuration.item_setting));
      setSim(null);
      setMessage({ tone: 'success', text: 'Saved. The new settings apply from the next stock check, which has already run.' });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  async function simulate() {
    try {
      setSim(await api(`/communication/items/${productId}/simulate`, { method: 'POST', body: { quantity: Number(simQty) } }));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <>
      {message && <Notice message={message} />}
      <Card>
        <SectionTitle
          title={item.product_name}
          description={`${item.product_code} · ${item.category} · main warehouse`}
          right={<ItemStatus status={config.status} />}
        />
        <KeyValue label="Stock now" value={`${qty(item.quantity)} ${item.unit || 'pcs'}`}
          tone={item.below_threshold ? COLORS.danger : undefined} />
        <KeyValue label="Reorder level" value={qty(item.reorder_level)} />
        <KeyValue label="Alert at" value={qty(config.low)} />
        <KeyValue label="Critical at" value={qty(config.critical)} />
        <KeyValue label="Recovers at" value={qty(config.recovery)} />
        <KeyValue label="Last alert" value={when(item.last_alert_at)} />
        <Banner tone={['CONFIGURED', 'INHERITED'].includes(config.status) ? 'info' : 'warning'} message={config.explanation} />
        <Button title="Back to items" tone="ghost" small style={{ alignSelf: 'flex-start' }} onPress={onBack} />
      </Card>

      <ItemRuleCard detail={detail} options={options} productId={productId}
        onSaved={(d, text) => { setDetail(d); setForm(formFrom(d.configuration.item_setting)); setSim(null); setMessage({ tone: 'success', text }); }}
        onError={(text) => setMessage({ tone: 'danger', text })} />

      <Card>
        <SectionTitle title="Where the values come from"
          description="Every active rule that reaches this item, in priority order. An item override replaces the value in each rule; recipients and timing always come from the rule." />
        {config.rules.length === 0 && <Text style={styles.empty}>No active rule reaches this item.</Text>}
        {config.rules.map((r) => (
          <View key={r.rule_id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>{r.rule_name} <Text style={styles.mono}>v{r.rule_version}</Text></Text>
              <Pill label={r.applies ? r.matched_label : 'Not applied'} tone={r.applies ? 'info' : 'muted'} />
            </View>
            <Text style={styles.mono}>
              Alert at {qty(r.low)} ({r.sources.low?.label || '—'}) · critical {qty(r.critical)}
              {r.sources.critical ? ` (${r.sources.critical.label})` : ''} · recovers at {qty(r.recovery)}
              {r.sources.recovery ? ` (${r.sources.recovery.label})` : ''} · compare {r.comparison === '<=' ? 'at or below' : 'below'}
            </Text>
            <Text style={styles.mono}>
              {FREQUENCY_LABELS[r.frequency] || r.frequency}
              {r.channel_email ? ' · email' : ''}{r.channel_in_app ? ' · in-app' : ''}
              {r.business_hours_only ? ' · business hours' : ''}{r.escalation_policy_id ? ' · escalates' : ''}
              {r.notify_on_recovery ? ' · recovery message' : ''}
            </Text>
            {r.warnings.map((w) => <Text key={w} style={[styles.mono, { color: COLORS.warning }]}>{w}</Text>)}
          </View>
        ))}
      </Card>

      <Card>
        <SectionTitle title="Override or switch off (advanced)"
          description={canEdit
            ? 'For items covered by a category or every-product rule: replace that rule’s levels for this item only, or switch all alerts for this item off.'
            : 'Read only. Ask an inventory supervisor or administrator to change these.'}
          right={dirty ? <Pill label="UNSAVED CHANGES" tone="warning" /> : <Pill label="SAVED" tone="success" />} />
        {canEdit ? (
          <>
            <SettingForm form={form} setForm={setForm} inherited={inherited} />
            <View style={[styles.row, styles.gap8]}>
              <Button title="Save" disabled={!dirty || busy} loading={busy} onPress={save} />
              {dirty && <Button title="Discard changes" tone="ghost" onPress={() => setForm(saved)} />}
            </View>
            {!!config.item_setting && (
              <Text style={[styles.mono, { marginTop: 8 }]}>
                Version {config.item_setting.version} · last changed {when(config.item_setting.updated_at)} by {config.item_setting.updated_by}
              </Text>
            )}
          </>
        ) : (
          <KeyValue label="Mode" value={MODE_LABELS[form.mode]} />
        )}
      </Card>

      <Card>
        <SectionTitle title="Usage-based suggestion" description="A recommendation only. Nothing changes unless you save it." />
        {!suggestion.available ? (
          <Text style={styles.mono}>{suggestion.reason}</Text>
        ) : (
          <>
            <KeyValue label="Average daily usage" value={`${suggestion.average_daily_usage} (${suggestion.history_days} days)`} />
            <KeyValue label="Days of cover at current stock" value={String(suggestion.days_of_cover)} />
            <KeyValue label="Supplier lead time" value={suggestion.lead_time_days != null ? `${suggestion.lead_time_days} days (${suggestion.purchase_orders} orders)` : 'Unknown'} />
            {suggestion.suggested_low != null ? (
              <>
                <KeyValue label={`Suggested alert threshold (${suggestion.safety_days} safety days)`} value={String(suggestion.suggested_low)} />
                {suggestion.suggested_critical != null && <KeyValue label="Suggested critical threshold" value={String(suggestion.suggested_critical)} />}
                <Text style={[styles.mono, { marginTop: 4 }]}>{suggestion.formula}</Text>
                {canEdit && (
                  <Button title="Put suggestion in the form" tone="secondary" small style={{ alignSelf: 'flex-start', marginTop: 8 }}
                    onPress={() => setForm({ ...form, mode: 'OVERRIDE', low: String(suggestion.suggested_low),
                      critical: suggestion.suggested_critical != null ? String(suggestion.suggested_critical) : form.critical })} />
                )}
              </>
            ) : <Text style={styles.mono}>{suggestion.note}</Text>}
            <Text style={[styles.mono, { marginTop: 4 }]}>{suggestion.basis}</Text>
          </>
        )}
      </Card>

      <Card>
        <SectionTitle title="Test the configuration"
          description="Enter a stock figure to see what would happen with the saved settings. Nothing is changed and nothing is sent." />
        <View style={[styles.row, styles.gap8, { alignItems: 'flex-end' }]}>
          <Field label="Simulated stock" value={simQty} keyboardType="number-pad" style={{ flex: 1 }}
            onChangeText={(v) => setSimQty(v.replace(/[^0-9]/g, ''))} placeholder={String(item.quantity)} />
          <Button title="Simulate" disabled={simQty === ''} onPress={simulate} style={{ marginBottom: 12 }} />
        </View>
        {dirty && <Text style={[styles.mono, { color: COLORS.warning }]}>The simulation uses saved settings. Save first to test your changes.</Text>}
        {sim && (
          <>
            <Banner tone="info" message={`${sim.explanation} ${sim.note}`} />
            {sim.results.length === 0 && <Text style={styles.empty}>No rule would evaluate this item.</Text>}
            {sim.results.map((r) => {
              const [label, tone] = OUTCOME[r.outcome] || [r.outcome, 'info'];
              return (
                <View key={r.rule_id} style={styles.listRow}>
                  <View style={styles.rowBetween}>
                    <Text style={{ fontWeight: '600', color: COLORS.text }}>{r.rule_name}</Text>
                    <View style={[styles.row, styles.gap8]}><SeverityPill severity={r.severity} /><Pill label={label} tone={tone} /></View>
                  </View>
                  <Text style={styles.mono}>{r.reason}</Text>
                  <Text style={styles.mono}>
                    Recipients: {r.recipients.length ? r.recipients.map((p) => p.name).join(', ') : 'none'}
                    {r.excluded.length ? ` · ${r.excluded.length} excluded` : ''}
                  </Text>
                  {!!r.email_preview && (
                    <View style={{ backgroundColor: COLORS.surfaceAlt, borderRadius: 8, padding: 10, marginTop: 6 }}>
                      <Text style={{ fontWeight: '600', color: COLORS.text }}>{r.email_preview.subject}</Text>
                      <Text style={[styles.mono, { marginTop: 4 }]}>{r.email_preview.body}</Text>
                    </View>
                  )}
                </View>
              );
            })}
          </>
        )}
      </Card>

      <Card>
        <SectionTitle title="Alert history" description="The rule version records which thresholds and recipients were in force." />
        {detail.incidents.length === 0 && <Text style={styles.empty}>No alerts for this item yet.</Text>}
        {detail.incidents.map((i) => (
          <View key={i.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontWeight: '600', color: COLORS.text }}>#{i.id} · {i.rule_name}{i.rule_version ? ` v${i.rule_version}` : ''}</Text>
              <View style={[styles.row, styles.gap8]}><SeverityPill severity={i.severity} /><StatusPill status={i.status} /></View>
            </View>
            <Text style={styles.mono}>
              Opened {when(i.opened_at)} at threshold {qty(i.threshold_at_open)} · last stock {qty(i.last_quantity)}
              {i.resolved_at ? ` · resolved ${when(i.resolved_at)}` : ''}
            </Text>
          </View>
        ))}
      </Card>
    </>
  );
}

/* --------------------------------- bulk ----------------------------------- */
function BulkPanel({ ids, onDone }) {
  const [form, setForm] = useState(formFrom(null));
  const [preview, setPreview] = useState(null);
  const [message, setMessage] = useState(null);

  async function run(dryRun) {
    try {
      const r = await api('/communication/items/bulk', { method: 'POST', body: { ...body(form), product_ids: ids, dry_run: dryRun } });
      setPreview(r);
      if (r.applied) {
        setMessage({ tone: 'success', text: `Applied to ${r.count} items.` });
        onDone(true);
      } else if (r.errors.length) {
        setMessage({ tone: 'danger', text: 'Some items would end up with contradictory thresholds. Nothing was saved.' });
      } else {
        setMessage(null);
      }
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <View style={{ borderWidth: 1, borderColor: COLORS.border, borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <Text style={[styles.sectionTitle, { marginBottom: 8 }]}>Bulk edit {ids.length} items</Text>
      {message && <Notice message={message} />}
      <SettingForm form={form} setForm={(f) => { setForm(f); setPreview(null); }} />
      <View style={[styles.row, styles.gap8]}>
        <Button title="Preview" tone="secondary" onPress={() => run(true)} />
        <Button title="Apply" disabled={!preview || preview.errors.length > 0 || preview.applied} onPress={() => run(false)} />
        <Button title="Cancel" tone="ghost" onPress={() => onDone(false)} />
      </View>
      {preview && !preview.applied && (
        <View style={{ marginTop: 10 }}>
          {preview.errors.map((e) => (
            <Text key={e.product_code} style={[styles.mono, { color: COLORS.danger }]}>{e.product_code}: {e.problems.join(' ')}</Text>
          ))}
          {preview.items.map((i) => (
            <Text key={i.product_id} style={styles.mono}>
              {i.product_code} {i.product_name}: {ITEM_STATUS[i.before.status]?.[0]} {qty(i.before.low)}/{qty(i.before.critical)}/{qty(i.before.recovery)}
              {'  →  '}{ITEM_STATUS[i.after.status]?.[0]} {qty(i.after.low)}/{qty(i.after.critical)}/{qty(i.after.recovery)}
            </Text>
          ))}
          <Text style={[styles.mono, { marginTop: 4 }]}>Shown as alert / critical / recovers-at.</Text>
        </View>
      )}
    </View>
  );
}

/* -------------------------------- import ---------------------------------- */
function ImportPanel({ onDone }) {
  const [csv, setCsv] = useState('');
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState(null);

  async function run(dryRun) {
    try {
      const r = await api('/communication/items-import', { method: 'POST', body: { csv, dry_run: dryRun }, timeout: 60000 });
      setResult(r);
      if (r.applied) {
        setMessage({ tone: 'success', text: `Imported ${r.changes.length} changes.` });
        onDone();
      } else {
        setMessage(null);
      }
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <View style={{ borderWidth: 1, borderColor: COLORS.border, borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <Text style={[styles.sectionTitle, { marginBottom: 4 }]}>Import item settings</Text>
      <Text style={[styles.mono, { marginBottom: 8 }]}>
        Paste CSV with the export's columns (export first to get the template). Check the dry run, then apply. If any line has an error, nothing is applied.
      </Text>
      {message && <Notice message={message} />}
      <Field label="CSV" value={csv} multiline onChangeText={(v) => { setCsv(v); setResult(null); }}
        placeholder="product_code,product_name,category,mode,low_threshold,critical_threshold,recovery_threshold,note" />
      <View style={[styles.row, styles.gap8]}>
        <Button title="Dry run" tone="secondary" disabled={!csv.trim()} onPress={() => run(true)} />
        <Button title="Apply" disabled={!result || result.errors.length > 0 || !result.changes.length || result.applied} onPress={() => run(false)} />
      </View>
      {result && !result.applied && (
        <View style={{ marginTop: 10 }}>
          {result.errors.map((e) => <Text key={e} style={[styles.mono, { color: COLORS.danger }]}>{e}</Text>)}
          {!result.errors.length && <Text style={styles.mono}>{result.changes.length} items would change.</Text>}
          {result.changes.map((c) => (
            <Text key={c.product_id} style={styles.mono}>
              {c.product_code}: {c.before.mode} → {c.after.mode} {qty(c.after.low_threshold)}/{qty(c.after.critical_threshold)}/{qty(c.after.recovery_threshold)}
            </Text>
          ))}
        </View>
      )}
    </View>
  );
}

/* ------------------------------ quick set-up ------------------------------ */
/**
 * Creates an ordinary alert rule for the chosen items in one step: thresholds,
 * who is told at each level, how often and through which channel. It is the
 * same rule the Alert rules tab edits, so everything stays in one place.
 */
function QuickSetup({ products, productIds, options, onCancel, onDone }) {
  const users = (options?.users || []).filter((u) => u.can_receive);
  const me = options?.me || {};
  const single = productIds.length === 1;
  const [form, setForm] = useState({
    name: single ? `Stock alert: ${products[0]?.product_name || 'item'}` : `Stock alert: ${productIds.length} items`,
    low: '', critical: '', recovery: '', atOrBelow: false,
    lowTo: [], criticalTo: [], frequency: 'ONCE', repeat: '4', email: true, inApp: true,
    owner: null,
  });
  const [picker, setPicker] = useState(null); // 'lowTo' | 'criticalTo' | 'owner'
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const digits = (v) => v.replace(/[^0-9]/g, '');
  const name = (id) => users.find((u) => u.id === id)?.name || `#${id}`;
  const meCanReceive = users.some((u) => u.id === me.id);
  // Whoever owns the rule must be able to see warehouse stock.
  const owner = form.owner || (meCanReceive ? me.id : form.lowTo[0] || form.criticalTo[0] || null);

  function recipients() {
    const hasCritical = form.critical !== '';
    const both = form.lowTo.filter((id) => form.criticalTo.includes(id));
    const list = [];
    form.lowTo.forEach((id) => list.push({
      recipient_type: 'USER', recipient_value: String(id),
      level: !hasCritical || both.includes(id) ? 'ALL' : 'LOW',
    }));
    if (hasCritical) {
      form.criticalTo.filter((id) => !both.includes(id)).forEach((id) => list.push({
        recipient_type: 'USER', recipient_value: String(id), level: 'CRITICAL',
      }));
    }
    return list;
  }

  async function save() {
    const low = num(form.low);
    const critical = num(form.critical);
    const recovery = num(form.recovery);
    if (low === null) return setMessage({ tone: 'danger', text: 'Enter the alert threshold.' });
    if (critical !== null && critical >= low) return setMessage({ tone: 'danger', text: 'The critical threshold must be lower than the alert threshold.' });
    if (recovery !== null && recovery <= low) return setMessage({ tone: 'danger', text: 'The recovery level must be above the alert threshold.' });
    if (!form.lowTo.length && !form.criticalTo.length) return setMessage({ tone: 'danger', text: 'Choose at least one person to notify.' });
    if (!form.email && !form.inApp) return setMessage({ tone: 'danger', text: 'Choose email, in-app, or both.' });
    if (!owner) return setMessage({ tone: 'danger', text: 'Choose a rule owner who can see warehouse stock.' });
    setBusy(true);
    try {
      await api('/communication/rules', {
        method: 'POST',
        body: {
          name: form.name.trim() || 'Stock alert', products: productIds, owner_user_id: owner,
          trigger_type: form.atOrBelow ? 'AT_OR_BELOW' : 'BELOW', threshold: low, critical_threshold: critical,
          // 'Resolves at' means stock >= that figure, whichever comparison is used.
          recovery_margin: recovery === null ? 0 : recovery - low - (form.atOrBelow ? 1 : 0),
          recipients: recipients(), channel_email: form.email, channel_in_app: form.inApp,
          frequency: form.frequency, repeat_hours: form.frequency === 'REPEAT_HOURS' ? Number(form.repeat) || 4 : null,
          notify_on_critical: true, activate: true,
        },
      });
      onDone(`Alert set up for ${single ? products[0]?.product_name || 'the item' : `${productIds.length} items`}. It is checked against current stock now.`);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  const userOptions = users.map((u) => ({
    value: u.id, label: `${u.name} (${u.employee_id})`,
    sub: `${ROLE_LABELS[u.role] || u.role}${u.has_email ? '' : ' · no email address, in-app only'}`,
  }));
  const toggleUser = (key, id) => set({ [key]: form[key].includes(id) ? form[key].filter((x) => x !== id) : [...form[key], id] });

  return (
    <Card>
      <SectionTitle
        title={single ? `Set up alert: ${products[0]?.product_name || ''}` : `Set up alert for ${productIds.length} items`}
        description="Creates an active alert rule for this item only. You can change it later under Alert rules or with item overrides." />
      {message && <Notice message={message} />}
      <Field label="Rule name" value={form.name} onChangeText={(v) => set({ name: v })} />
      <View style={[styles.wrap, { gap: 8 }]}>
        <Field label="Alert when stock is below" required value={form.low} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
          onChangeText={(v) => set({ low: digits(v) })} placeholder="e.g. 10" />
        <Field label="Critical below (optional)" value={form.critical} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
          onChangeText={(v) => set({ critical: digits(v) })} placeholder="e.g. 5" />
        <Field label="Resolves at (optional)" value={form.recovery} keyboardType="number-pad" style={{ flex: 1, minWidth: 140 }}
          onChangeText={(v) => set({ recovery: digits(v) })} placeholder="e.g. 12" />
      </View>
      <Toggle label="Include the threshold itself (at or below)" value={form.atOrBelow} onChange={(v) => set({ atOrBelow: v })}
        hint={`With a threshold of 10: ${form.atOrBelow ? '10 or less alerts' : '9 or less alerts, 10 does not'}. Out of stock (0) is always reported as out of stock.`} />

      <Select label={form.critical ? 'Notify at low stock' : 'Notify'} placeholder="Choose people"
        valueLabel={form.lowTo.map(name).join(', ')} onPress={() => setPicker('lowTo')} />
      {form.critical !== '' && (
        <Select label="Notify at critical / out of stock" placeholder="Choose people (optional)"
          valueLabel={form.criticalTo.map(name).join(', ')} onPress={() => setPicker('criticalTo')} />
      )}
      {me.can_edit_all && (
        <Select label="Rule owner" valueLabel={owner ? name(owner) : ''} placeholder="Choose the person responsible"
          onPress={() => setPicker('owner')} />
      )}

      <Text style={styles.label}>How often</Text>
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        {Object.entries(FREQUENCY_LABELS).map(([key, label]) => (
          <Chip key={key} label={label} active={form.frequency === key} onPress={() => set({ frequency: key })} />
        ))}
      </View>
      {form.frequency === 'REPEAT_HOURS' && (
        <Field label="Hours between reminders" value={form.repeat} keyboardType="number-pad" onChangeText={(v) => set({ repeat: digits(v) })} />
      )}
      <Toggle label="Email" value={form.email} onChange={(v) => set({ email: v })} />
      <Toggle label="In-app notification" value={form.inApp} onChange={(v) => set({ inApp: v })} />
      <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
        <Button title="Save and activate" loading={busy} disabled={busy} onPress={save} />
        <Button title="Cancel" tone="ghost" onPress={onCancel} />
      </View>
      {!users.length && <Text style={[styles.mono, { marginTop: 8 }]}>No active user can receive stock alerts. Add one with warehouse access first.</Text>}

      <OptionModal
        visible={picker === 'lowTo' || picker === 'criticalTo'}
        multi
        title={picker === 'criticalTo' ? 'Notify at critical / out of stock' : 'Notify'}
        options={userOptions}
        selectedValue={picker ? form[picker] || [] : []}
        onSelect={(id) => toggleUser(picker, id)}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'owner'}
        title="Rule owner"
        options={userOptions}
        selectedValue={owner}
        onSelect={(id) => { set({ owner: id }); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
    </Card>
  );
}

/* ------------------------ alert for this one item ------------------------- */
const QUICK_FREQUENCIES = ['ONCE', 'REPEAT_HOURS', 'DAILY', 'DAILY_DIGEST'];

function ruleForm(rule) {
  return {
    low: numText(rule?.low_threshold), critical: numText(rule?.critical_threshold),
    recovery: rule && rule.recovery_threshold !== rule.low_threshold ? numText(rule.recovery_threshold) : '',
    comparison: rule?.comparison || 'BELOW',
    lowIds: rule?.low_recipient_user_ids || [], critIds: rule?.critical_recipient_user_ids || [],
    email: rule ? rule.channel_email : true, inApp: rule ? rule.channel_in_app : true,
    frequency: rule?.frequency || 'ONCE', repeatHours: numText(rule?.repeat_hours) || '4',
    recoveryMessage: !!rule?.notify_on_recovery,
  };
}

/**
 * The quickest way to give one item an alert: thresholds and the people to
 * tell, on the item page. Saves the item's own rule (visible under Alert rules).
 */
function ItemRuleCard({ detail, options, productId, onSaved, onError }) {
  const rule = detail.item_rule;
  const canEdit = detail.can_edit_rule;
  const [form, setForm] = useState(ruleForm(rule));
  const [picker, setPicker] = useState(null); // 'low' | 'crit'
  const [busy, setBusy] = useState(false);
  useEffect(() => { setForm(ruleForm(rule)); }, [rule?.rule_id, rule?.version, rule?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const people = (options?.users || []).filter((u) => u.can_receive);
  const nameOf = (id) => {
    const u = people.find((x) => x.id === id) || (options?.users || []).find((x) => x.id === id);
    return u ? u.name : `User #${id}`;
  };
  const set = (patch) => setForm({ ...form, ...patch });
  const digits = (v) => v.replace(/[^0-9]/g, '');
  const active = rule?.status === 'ACTIVE';
  const saved = JSON.stringify(ruleForm(rule));
  const dirty = !!rule && saved !== JSON.stringify(form);

  async function save(makeActive) {
    setBusy(true);
    try {
      const d = await api(`/communication/items/${productId}/alert-rule`, {
        method: 'PUT',
        body: {
          low_threshold: num(form.low), critical_threshold: num(form.critical),
          recovery_threshold: num(form.recovery), comparison: form.comparison,
          low_recipient_user_ids: form.lowIds, critical_recipient_user_ids: form.critIds,
          channel_email: form.email, channel_in_app: form.inApp, frequency: form.frequency,
          repeat_hours: form.frequency === 'REPEAT_HOURS' ? Number(form.repeatHours) || 4 : null,
          notify_on_recovery: form.recoveryMessage, active: makeActive,
        },
      });
      onSaved(d, makeActive
        ? (rule ? 'Alert updated. It applies from now.' : 'Alert created and switched on. The item is now checked on every stock change.')
        : 'Alert switched off for this item. Its settings are kept.');
    } catch (e) {
      onError(e.message);
    }
    setBusy(false);
  }

  if (!canEdit && !rule) {
    return (
      <Card>
        <SectionTitle title="Alert for this item" description="No alert is set up for this item. Ask an inventory supervisor or administrator to set one up." />
      </Card>
    );
  }
  if (rule && !rule.editable_here) {
    return (
      <Card>
        <SectionTitle title="Alert for this item" description={`This item has its own rule “${rule.name}”, which uses a condition that is edited under Alert rules.`} />
      </Card>
    );
  }

  const missingEmail = form.email && [...form.lowIds, ...form.critIds].some((id) => {
    const u = people.find((x) => x.id === id);
    return u && !u.has_email;
  });

  return (
    <Card>
      <SectionTitle
        title="Alert for this item"
        description={rule
          ? `Saved as the rule “${rule.name}” (version ${rule.version}). It also appears under Alert rules.`
          : 'Set the stock levels and who to tell. Saving switches the alert on straight away.'}
        right={rule ? (dirty ? <Pill label="UNSAVED CHANGES" tone="warning" /> : <StatusPill status={rule.status} />) : <Pill label="NOT SET UP" tone="warning" />}
      />
      {!canEdit && <Text style={[styles.mono, { marginBottom: 8 }]}>Read only.</Text>}
      <View style={[styles.wrap, { gap: 8 }]}>
        <Field label="Alert when stock falls below" required value={form.low} keyboardType="number-pad" editable={canEdit}
          style={{ flex: 1, minWidth: 150 }} placeholder="e.g. 10" onChangeText={(v) => set({ low: digits(v) })} />
        <Field label="Critical below (optional)" value={form.critical} keyboardType="number-pad" editable={canEdit}
          style={{ flex: 1, minWidth: 150 }} placeholder="e.g. 5" onChangeText={(v) => set({ critical: digits(v) })} />
        <Field label="Resolved when stock reaches (optional)" value={form.recovery} keyboardType="number-pad" editable={canEdit}
          style={{ flex: 1, minWidth: 150 }} placeholder={form.low ? `Default ${form.low}` : 'e.g. 12'}
          onChangeText={(v) => set({ recovery: digits(v) })} />
      </View>
      <View style={[styles.wrap, { marginBottom: 10 }]}>
        <Chip label="Below the level" active={form.comparison === 'BELOW'} disabled={!canEdit} onPress={() => set({ comparison: 'BELOW' })} />
        <Chip label="At or below the level" active={form.comparison === 'AT_OR_BELOW'} disabled={!canEdit} onPress={() => set({ comparison: 'AT_OR_BELOW' })} />
      </View>

      <Select label="Tell about low stock" disabled={!canEdit} onPress={() => setPicker('low')}
        valueLabel={form.lowIds.length ? form.lowIds.map(nameOf).join(', ') : null} placeholder="Choose people" />
      <Select label="Tell about critical / out of stock" disabled={!canEdit} onPress={() => setPicker('crit')}
        valueLabel={form.critIds.length ? form.critIds.map(nameOf).join(', ') : null}
        placeholder={form.critical ? 'Choose people' : 'Set a critical level first (optional)'} />
      <Text style={[styles.mono, { marginBottom: 8 }]}>
        Someone in both lists gets every alert. Only people who can see warehouse stock are listed.
      </Text>
      {missingEmail && (
        <Banner tone="warning" message="Someone chosen has no email address, so they will only get in-app alerts. Add it under Admin → Login accounts." />
      )}

      <Text style={styles.label}>How often</Text>
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        {QUICK_FREQUENCIES.map((f) => (
          <Chip key={f} label={FREQUENCY_LABELS[f]} active={form.frequency === f} disabled={!canEdit} onPress={() => set({ frequency: f })} />
        ))}
      </View>
      {form.frequency === 'REPEAT_HOURS' && (
        <Field label="Hours between reminders" value={form.repeatHours} keyboardType="number-pad" editable={canEdit}
          onChangeText={(v) => set({ repeatHours: digits(v) })} />
      )}
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        <Chip label="Email" active={form.email} disabled={!canEdit} onPress={() => set({ email: !form.email })} />
        <Chip label="In-app" active={form.inApp} disabled={!canEdit} onPress={() => set({ inApp: !form.inApp })} />
        <Chip label="Message when resolved" active={form.recoveryMessage} disabled={!canEdit} onPress={() => set({ recoveryMessage: !form.recoveryMessage })} />
      </View>

      {canEdit && (
        <View style={[styles.wrap, { marginTop: 4 }]}>
          <Button title={rule ? (active ? 'Save changes' : 'Save and switch on') : 'Create alert'} loading={busy}
            disabled={busy || form.low === '' || (!form.lowIds.length && !form.critIds.length) || (rule && active && !dirty)}
            onPress={() => save(true)} />
          {rule && active && <Button title="Switch off" tone="warning" disabled={busy} onPress={() => save(false)} />}
          {dirty && <Button title="Discard changes" tone="ghost" onPress={() => setForm(ruleForm(rule))} />}
        </View>
      )}

      <OptionModal
        visible={!!picker}
        multi
        title={picker === 'crit' ? 'Tell about critical / out of stock' : 'Tell about low stock'}
        options={people.map((u) => ({ value: u.id, label: u.name, sub: `${u.employee_id} · ${u.role}${u.has_email ? '' : ' · no email'}` }))}
        selectedValue={picker === 'crit' ? form.critIds : form.lowIds}
        onSelect={(id) => {
          const key = picker === 'crit' ? 'critIds' : 'lowIds';
          const list = form[key];
          set({ [key]: list.includes(id) ? list.filter((x) => x !== id) : [...list, id] });
        }}
        onClose={() => setPicker(null)}
      />
    </Card>
  );
}
