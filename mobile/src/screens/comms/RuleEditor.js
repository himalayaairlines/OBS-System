import React, { useMemo, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { COLORS, ROLE_LABELS, styles } from '../../theme';
import {
  Banner, Button, CalendarModal, Card, Chip, Field, KeyValue, Notice, OptionModal, SectionTitle,
  Select,
} from '../../ui';
import {
  FREQUENCY_LABELS, LEVEL_LABELS, RECIPIENT_TYPE_LABELS, TRIGGER_LABELS, Toggle, qty, recipientLabel,
} from './shared';
import SimulationResult from './SimulationResult';

const STEPS = ['Basics', 'Products', 'Condition', 'Recipients', 'Channels', 'Frequency', 'Escalation', 'Review'];

function blank(options) {
  return {
    name: '', description: '', owner_user_id: options?.me?.id || null, priority: '100',
    effective_from: null, effective_to: null,
    scope_all: false, exclusive: false, products: [], categories: [], exclude_products: [], exclude_categories: [],
    trigger_type: 'BELOW', threshold_source: 'FIXED', threshold: '', critical_threshold: '', percent: '',
    duration_minutes: '', recovery_margin: '0',
    channel_in_app: true, channel_email: true,
    frequency: 'ONCE', repeat_hours: '', max_reminders: '10',
    business_hours_only: false, notify_on_critical: true, notify_on_recovery: false,
    escalation_policy_id: null,
    recipients: [{ recipient_type: 'OWNER', recipient_value: null, level: 'ALL' }],
  };
}

function fromRule(rule) {
  const pick = (type, include) => rule.scopes
    .filter((s) => s.scope_type === type && !!s.include === include)
    .map((s) => (type === 'PRODUCT' ? Number(s.scope_value) : s.scope_value));
  const str = (v) => (v === null || v === undefined ? '' : String(v));
  return {
    name: rule.name, description: rule.description || '', owner_user_id: rule.owner_user_id,
    priority: str(rule.priority), effective_from: rule.effective_from, effective_to: rule.effective_to,
    scope_all: !!rule.scope_all, exclusive: !!rule.exclusive,
    products: pick('PRODUCT', true), categories: pick('CATEGORY', true),
    exclude_products: pick('PRODUCT', false), exclude_categories: pick('CATEGORY', false),
    trigger_type: rule.trigger_type, threshold_source: rule.threshold_source, threshold: str(rule.threshold),
    critical_threshold: str(rule.critical_threshold), percent: str(rule.percent),
    duration_minutes: str(rule.duration_minutes), recovery_margin: str(rule.recovery_margin),
    channel_in_app: !!rule.channel_in_app, channel_email: !!rule.channel_email,
    frequency: rule.frequency, repeat_hours: str(rule.repeat_hours), max_reminders: str(rule.max_reminders),
    business_hours_only: !!rule.business_hours_only, notify_on_critical: !!rule.notify_on_critical,
    notify_on_recovery: !!rule.notify_on_recovery, escalation_policy_id: rule.escalation_policy_id,
    recipients: rule.recipients.map((r) => ({ ...r })),
  };
}

function toPayload(form, activate) {
  const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v));
  return {
    name: form.name.trim(), description: form.description || null, owner_user_id: form.owner_user_id,
    priority: num(form.priority) || 100, effective_from: form.effective_from, effective_to: form.effective_to,
    scope_all: form.scope_all, exclusive: form.exclusive, products: form.products, categories: form.categories,
    exclude_products: form.exclude_products, exclude_categories: form.exclude_categories,
    trigger_type: form.trigger_type, threshold_source: form.threshold_source,
    threshold: num(form.threshold), critical_threshold: num(form.critical_threshold), percent: num(form.percent),
    duration_minutes: num(form.duration_minutes), recovery_margin: num(form.recovery_margin) || 0,
    channel_in_app: form.channel_in_app, channel_email: form.channel_email,
    frequency: form.frequency, repeat_hours: num(form.repeat_hours), max_reminders: num(form.max_reminders) ?? 10,
    business_hours_only: form.business_hours_only, notify_on_critical: form.notify_on_critical,
    notify_on_recovery: form.notify_on_recovery, escalation_policy_id: form.escalation_policy_id,
    recipients: form.recipients.map((r) => ({
      recipient_type: r.recipient_type, recipient_value: r.recipient_value == null ? null : String(r.recipient_value),
      level: r.level || 'ALL',
    })),
    activate,
  };
}

export default function RuleEditor({ rule, options, onDone, onCancel }) {
  const [form, setForm] = useState(() => (rule ? fromRule(rule) : blank(options)));
  const [step, setStep] = useState(0);
  const [picker, setPicker] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [draft, setDraft] = useState({ recipient_type: 'USER', recipient_value: null, level: 'ALL' });
  const [preview, setPreview] = useState(null);
  const [simulation, setSimulation] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (patch) => { setForm((f) => ({ ...f, ...patch })); setSimulation(null); setPreview(null); };
  const products = options?.products || [];
  const productById = useMemo(() => Object.fromEntries(products.map((p) => [p.id, p])), [products]);
  const eligibleUsers = (options?.users || []).filter((u) => u.can_receive);

  const matched = useMemo(() => products.filter((p) => {
    if (form.exclude_products.includes(p.id) || form.exclude_categories.includes(p.category)) return false;
    return form.scope_all || form.products.includes(p.id) || form.categories.includes(p.category);
  }), [products, form]);

  const toggleIn = (key, value) => {
    const list = form[key];
    set({ [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] });
  };

  const needsThreshold = ['BELOW', 'AT_OR_BELOW', 'BELOW_FOR_DURATION'].includes(form.trigger_type);

  async function runPreview() {
    try {
      setPreview(await api('/communication/recipients/preview', { method: 'POST', body: toPayload(form, false) }));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function simulate() {
    try {
      setSimulation(await api('/communication/rules/simulate', { method: 'POST', body: toPayload(form, false) }));
      setMessage(null);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function save(activate) {
    setBusy(true);
    try {
      const body = toPayload(form, activate);
      const saved = rule
        ? await api(`/communication/rules/${rule.id}`, { method: 'PUT', body })
        : await api('/communication/rules', { method: 'POST', body });
      onDone(saved, activate || saved.status === 'ACTIVE'
        ? `Rule "${saved.name}" is active.` : `Rule "${saved.name}" saved as a draft.`);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  function addRecipient() {
    if (draft.recipient_type !== 'OWNER' && !draft.recipient_value) return;
    const exists = form.recipients.some((r) => r.recipient_type === draft.recipient_type
      && String(r.recipient_value) === String(draft.recipient_value) && r.level === draft.level);
    if (!exists) set({ recipients: [...form.recipients, { ...draft }] });
    setDraft({ ...draft, recipient_value: null });
  }

  const valueOptions = {
    USER: eligibleUsers.map((u) => ({
      value: u.id, label: `${u.name} (${u.employee_id})`,
      sub: `${ROLE_LABELS[u.role] || u.role}${u.has_email ? '' : ' · no email address'}`,
    })),
    ROLE: (options?.roles || []).filter((r) => r.can_receive).map((r) => ({ value: r.value, label: ROLE_LABELS[r.value] || r.value })),
    GROUP: (options?.groups || []).map((g) => ({
      value: g.id, label: g.name, sub: `${g.members} member(s)${g.active ? '' : ' · disabled'}`,
    })),
  };

  /* ------------------------------ steps ------------------------------ */
  const steps = [
    // Basics
    <View key="basics">
      <Field label="Rule name" value={form.name} onChangeText={(v) => set({ name: v })} placeholder="e.g. Pringles low in warehouse" maxLength={120} />
      <Field label="Description (optional)" value={form.description} onChangeText={(v) => set({ description: v })} multiline />
      <Field label="Priority" hint="Lower numbers are listed first. Overlapping rules all run unless one is exclusive."
        value={form.priority} onChangeText={(v) => set({ priority: v.replace(/[^0-9]/g, '') })} keyboardType="number-pad" />
      {options?.me?.can_edit_all && (
        <Select label="Rule owner"
          valueLabel={eligibleUsers.find((u) => u.id === form.owner_user_id)?.name || 'Me'}
          onPress={() => setPicker('owner')} />
      )}
      <View style={[styles.row, styles.gap8]}>
        <Select style={styles.flex1} label="Starts (optional)" valueLabel={form.effective_from || 'Immediately'} onPress={() => setCalendar('effective_from')} />
        <Select style={styles.flex1} label="Ends (optional)" valueLabel={form.effective_to || 'No end date'} onPress={() => setCalendar('effective_to')} />
      </View>
    </View>,

    // Products
    <View key="scope">
      <Text style={[styles.mono, { marginBottom: 10 }]}>
        Pick the products this rule watches. Each product is checked on its own against warehouse stock.
      </Text>
      <Select label="Specific products" valueLabel={form.products.length ? `${form.products.length} selected` : 'None'} onPress={() => setPicker('products')} />
      <Select label="Whole categories" valueLabel={form.categories.length ? form.categories.join(', ') : 'None'} onPress={() => setPicker('categories')} />
      <Toggle label="Every product" value={form.scope_all} onChange={(v) => set({ scope_all: v })}
        hint="Only switch this on if you really mean every active product." />
      <Select label="Exclude products" valueLabel={form.exclude_products.length ? `${form.exclude_products.length} excluded` : 'None'} onPress={() => setPicker('exclude_products')} />
      <Select label="Exclude categories" valueLabel={form.exclude_categories.length ? form.exclude_categories.join(', ') : 'None'} onPress={() => setPicker('exclude_categories')} />
      <Toggle label="Exclusive for the products named above" value={form.exclusive} onChange={(v) => set({ exclusive: v })}
        hint="Other rules that reach these products only through a category or 'every product' stop checking them." />
      <Card tight style={{ backgroundColor: COLORS.surfaceAlt, marginTop: 8 }}>
        <Text style={{ fontWeight: '600', color: COLORS.text }}>{matched.length} product(s) covered</Text>
        {matched.slice(0, 12).map((p) => (
          <Text key={p.id} style={styles.mono}>{p.product_name} · {p.category} · stock {qty(p.quantity)} · reorder {qty(p.reorder_level)}</Text>
        ))}
        {matched.length > 12 && <Text style={styles.mono}>…and {matched.length - 12} more</Text>}
      </Card>
    </View>,

    // Condition
    <View key="condition">
      <Text style={[styles.mono, { marginBottom: 8 }]}>Measured on warehouse on-hand stock (the main inventory figure).</Text>
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {Object.entries(TRIGGER_LABELS).map(([key, label]) => (
          <Chip key={key} label={label} active={form.trigger_type === key} onPress={() => set({ trigger_type: key })} />
        ))}
      </View>
      {needsThreshold && (
        <>
          <View style={[styles.wrap, { marginBottom: 10 }]}>
            <Chip label="Fixed quantity" active={form.threshold_source === 'FIXED'} onPress={() => set({ threshold_source: 'FIXED' })} />
            <Chip label="Each product's reorder level" active={form.threshold_source === 'REORDER_LEVEL'} onPress={() => set({ threshold_source: 'REORDER_LEVEL' })} />
          </View>
          {form.threshold_source === 'FIXED' && (
            <Field label="Alert threshold (pieces)" value={form.threshold} keyboardType="decimal-pad"
              onChangeText={(v) => set({ threshold: v.replace(/[^0-9.]/g, '') })} placeholder="10" />
          )}
        </>
      )}
      {form.trigger_type === 'PERCENT_OF_REORDER' && (
        <Field label="Percentage of reorder level" value={form.percent} keyboardType="decimal-pad"
          onChangeText={(v) => set({ percent: v.replace(/[^0-9.]/g, '') })} placeholder="50" />
      )}
      {form.trigger_type === 'BELOW_FOR_DURATION' && (
        <Field label="Must stay low for (minutes)" value={form.duration_minutes} keyboardType="number-pad"
          onChangeText={(v) => set({ duration_minutes: v.replace(/[^0-9]/g, '') })} placeholder="120" />
      )}
      {form.trigger_type !== 'OUT_OF_STOCK' && (
        <Field label="Critical threshold (optional)" hint="Stock below this is critical and can go to different people."
          value={form.critical_threshold} keyboardType="decimal-pad"
          onChangeText={(v) => set({ critical_threshold: v.replace(/[^0-9.]/g, '') })} />
      )}
      <Field label="Recovery margin" hint="Stock must clear the threshold by this much before the alert resolves. Stops alerts flapping."
        value={form.recovery_margin} keyboardType="decimal-pad"
        onChangeText={(v) => set({ recovery_margin: v.replace(/[^0-9.]/g, '') })} />
    </View>,

    // Recipients
    <View key="recipients">
      {form.recipients.length === 0 && <Text style={styles.empty}>No recipients yet.</Text>}
      {form.recipients.map((r, index) => (
        <View key={`${r.recipient_type}-${r.recipient_value}-${r.level}`} style={[styles.listRow, styles.rowBetween]}>
          <View style={styles.flex1}>
            <Text style={{ fontWeight: '600', color: COLORS.text }}>{recipientLabel(r, options)}</Text>
            <Text style={styles.mono}>{LEVEL_LABELS[r.level || 'ALL']}</Text>
          </View>
          <Button title="Remove" tone="ghost" small onPress={() => set({ recipients: form.recipients.filter((_, i) => i !== index) })} />
        </View>
      ))}
      <Card tight style={{ backgroundColor: COLORS.surfaceAlt, marginTop: 10 }}>
        <SectionTitle title="Add a recipient" />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {Object.entries(RECIPIENT_TYPE_LABELS).map(([key, label]) => (
            <Chip key={key} label={label} active={draft.recipient_type === key}
              onPress={() => setDraft({ ...draft, recipient_type: key, recipient_value: null })} />
          ))}
        </View>
        {['USER', 'ROLE', 'GROUP'].includes(draft.recipient_type) && (
          <Select label={RECIPIENT_TYPE_LABELS[draft.recipient_type]}
            valueLabel={draft.recipient_value ? recipientLabel(draft, options) : 'Choose…'}
            onPress={() => setPicker('recipient')} />
        )}
        {draft.recipient_type === 'EMAIL' && (
          <Field label="Email address"
            hint={options?.external_email_domains?.length
              ? `Allowed domains: ${options.external_email_domains.join(', ')}`
              : 'No external domains are allowed yet. An administrator can add them in Settings.'}
            value={draft.recipient_value || ''} autoCapitalize="none" keyboardType="email-address"
            onChangeText={(v) => setDraft({ ...draft, recipient_value: v.trim() })} />
        )}
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {Object.entries(LEVEL_LABELS).map(([key, label]) => (
            <Chip key={key} label={label} active={draft.level === key} onPress={() => setDraft({ ...draft, level: key })} />
          ))}
        </View>
        <Button title="Add recipient" tone="secondary" small onPress={addRecipient}
          disabled={draft.recipient_type !== 'OWNER' && !draft.recipient_value} />
      </Card>
      <Button title="Preview who will be notified" tone="ghost" onPress={runPreview} style={{ marginTop: 10 }} />
      {preview && ['LOW', 'CRITICAL'].map((level) => (
        <View key={level} style={{ marginTop: 10 }}>
          <Text style={{ fontWeight: '600', color: COLORS.text }}>{level === 'LOW' ? 'Low-stock alerts go to' : 'Critical / out-of-stock alerts go to'}</Text>
          {preview[level].recipients.length === 0 && <Text style={styles.mono}>Nobody</Text>}
          {preview[level].recipients.map((p) => (
            <Text key={p.key} style={styles.mono}>• {p.name}{p.employee_id ? ` (${p.employee_id})` : ''}{p.email ? '' : ' · in-app only, no email address'}</Text>
          ))}
          {preview[level].excluded.map((x) => (
            <Text key={`${x.who}-${x.via}`} style={[styles.mono, { color: COLORS.danger }]}>✕ {x.who}: {x.reason}</Text>
          ))}
        </View>
      ))}
    </View>,

    // Channels
    <View key="channels">
      <Toggle label="In-app notification" value={form.channel_in_app} onChange={(v) => set({ channel_in_app: v })}
        hint="Appears under the bell for each recipient." />
      <Toggle label="Email" value={form.channel_email} onChange={(v) => set({ channel_email: v })}
        hint="Sent through the SMTP server configured in Settings. Recipients without an email address get in-app only." />
      <Text style={[styles.mono, { marginTop: 8 }]}>Teams, SMS and push are not available yet.</Text>
    </View>,

    // Frequency
    <View key="frequency">
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {Object.entries(FREQUENCY_LABELS).map(([key, label]) => (
          <Chip key={key} label={label} active={form.frequency === key} onPress={() => set({ frequency: key })} />
        ))}
      </View>
      {form.frequency === 'REPEAT_HOURS' && (
        <Field label="Hours between reminders" value={form.repeat_hours} keyboardType="number-pad"
          onChangeText={(v) => set({ repeat_hours: v.replace(/[^0-9]/g, '') })} placeholder="4" />
      )}
      {['REPEAT_HOURS', 'DAILY'].includes(form.frequency) && (
        <Field label="Maximum reminders per alert" value={form.max_reminders} keyboardType="number-pad"
          onChangeText={(v) => set({ max_reminders: v.replace(/[^0-9]/g, '') })} />
      )}
      {form.frequency.endsWith('DIGEST') && (
        <Banner tone="info" message="Digest rules send in-app alerts straight away and one summary email per recipient at the digest time set in Settings." />
      )}
      <Toggle label="Only email during business hours" value={form.business_hours_only} onChange={(v) => set({ business_hours_only: v })}
        hint="Emails raised outside hours wait until the next business day starts." />
      <Toggle label="Notify again when it becomes critical" value={form.notify_on_critical} onChange={(v) => set({ notify_on_critical: v })} />
      <Toggle label="Notify when stock recovers" value={form.notify_on_recovery} onChange={(v) => set({ notify_on_recovery: v })} />
    </View>,

    // Escalation
    <View key="escalation">
      <Select label="Escalation policy"
        valueLabel={(options?.policies || []).find((p) => p.id === form.escalation_policy_id)?.name || 'No escalation'}
        onPress={() => setPicker('policy')} />
      <Text style={styles.mono}>
        Escalation notifies more people when an alert stays unacknowledged. Policies are set up under Escalation.
      </Text>
    </View>,

    // Review
    <View key="review">
      <KeyValue label="Rule" value={form.name || '—'} />
      <KeyValue label="Products" value={form.scope_all ? `Every product (${matched.length})` : `${matched.length} product(s)`} />
      <KeyValue label="Stock figure" value="Warehouse on-hand" />
      <KeyValue label="Condition" value={TRIGGER_LABELS[form.trigger_type]} />
      {needsThreshold && <KeyValue label="Threshold" value={form.threshold_source === 'REORDER_LEVEL' ? 'Reorder level' : form.threshold || '—'} />}
      {!!form.critical_threshold && <KeyValue label="Critical below" value={form.critical_threshold} />}
      <KeyValue label="Recipients" value={form.recipients.map((r) => recipientLabel(r, options)).join(', ') || '—'} />
      <KeyValue label="Channels" value={[form.channel_in_app && 'In-app', form.channel_email && 'Email'].filter(Boolean).join(' + ') || '—'} />
      <KeyValue label="Frequency" value={FREQUENCY_LABELS[form.frequency]} />
      <KeyValue label="Escalation" value={(options?.policies || []).find((p) => p.id === form.escalation_policy_id)?.name || 'None'} />
      <Button title="Simulate against current stock" tone="secondary" onPress={simulate} style={{ marginTop: 12 }} />
      {simulation && <SimulationResult result={simulation} />}
    </View>,
  ];

  return (
    <Card>
      <SectionTitle title={rule ? `Edit rule: ${rule.name}` : 'New alert rule'} right={<Button title="Cancel" tone="ghost" small onPress={onCancel} />} />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        {STEPS.map((label, index) => (
          <Chip key={label} label={`${index + 1}. ${label}`} active={step === index} onPress={() => setStep(index)} />
        ))}
      </View>
      {message && <Notice message={message} />}
      {steps[step]}
      <View style={[styles.row, styles.gap8, { marginTop: 16 }]}>
        <Button title="Back" tone="ghost" style={styles.flex1} disabled={step === 0} onPress={() => setStep(step - 1)} />
        {step < STEPS.length - 1 ? (
          <Button title="Next" style={styles.flex1} onPress={() => setStep(step + 1)} />
        ) : (
          <>
            {(!rule || rule.status !== 'ACTIVE') && (
              <Button title="Save draft" tone="secondary" style={styles.flex1} disabled={busy || !form.name} onPress={() => save(false)} />
            )}
            <Button title={rule?.status === 'Active' ? 'Save changes' : 'Save and activate'} style={styles.flex1}
              disabled={busy || !form.name} onPress={() => save(true)} />
          </>
        )}
      </View>
      {rule?.status === 'ACTIVE' && step === STEPS.length - 1 && (
        <Text style={[styles.mono, { marginTop: 8 }]}>Saving an active rule applies it straight away. Alerts it no longer covers are resolved.</Text>
      )}

      <OptionModal visible={picker === 'products'} title="Products" multi searchable
        options={products.map((p) => ({ value: p.id, label: p.product_name, sub: `${p.product_code} · ${p.category} · stock ${qty(p.quantity)}` }))}
        selectedValue={form.products} onSelect={(v) => toggleIn('products', v)} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'exclude_products'} title="Exclude products" multi searchable
        options={products.map((p) => ({ value: p.id, label: p.product_name, sub: `${p.product_code} · ${p.category}` }))}
        selectedValue={form.exclude_products} onSelect={(v) => toggleIn('exclude_products', v)} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'categories'} title="Categories" multi
        options={(options?.categories || []).map((c) => ({ value: c, label: c, sub: `${products.filter((p) => p.category === c).length} products` }))}
        selectedValue={form.categories} onSelect={(v) => toggleIn('categories', v)} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'exclude_categories'} title="Exclude categories" multi
        options={(options?.categories || []).map((c) => ({ value: c, label: c }))}
        selectedValue={form.exclude_categories} onSelect={(v) => toggleIn('exclude_categories', v)} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'recipient'} title={RECIPIENT_TYPE_LABELS[draft.recipient_type]} searchable
        options={valueOptions[draft.recipient_type] || []} selectedValue={draft.recipient_value}
        onSelect={(v) => { setDraft({ ...draft, recipient_value: v }); setPicker(null); }} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'owner'} title="Rule owner" searchable
        options={eligibleUsers.map((u) => ({ value: u.id, label: `${u.name} (${u.employee_id})`, sub: ROLE_LABELS[u.role] }))}
        selectedValue={form.owner_user_id}
        onSelect={(v) => { set({ owner_user_id: v }); setPicker(null); }} onClose={() => setPicker(null)} />
      <OptionModal visible={picker === 'policy'} title="Escalation policy"
        options={[{ value: null, label: 'No escalation' }, ...(options?.policies || []).map((p) => ({ value: p.id, label: p.name, sub: p.active ? null : 'Disabled' }))]}
        selectedValue={form.escalation_policy_id}
        onSelect={(v) => { set({ escalation_policy_id: v }); setPicker(null); }} onClose={() => setPicker(null)} />
      <CalendarModal visible={!!calendar} title={calendar === 'effective_from' ? 'Starts' : 'Ends'}
        value={form[calendar]} allowClear minDate={calendar === 'effective_to' ? form.effective_from : undefined}
        onSelect={(v) => set({ [calendar]: v })} onClose={() => setCalendar(null)} />
    </Card>
  );
}
