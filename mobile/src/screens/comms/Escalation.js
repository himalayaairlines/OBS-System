import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { confirm } from '../../dialog';
import { COLORS, ROLE_LABELS, styles } from '../../theme';
import { Banner, Button, Card, Chip, Field, OptionModal, Pill, SectionTitle, Select } from '../../ui';
import { RECIPIENT_TYPE_LABELS, Toggle, has, recipientLabel } from './shared';

const EMPTY = { name: '', description: '', active: true, steps: [{ step_no: 1, after_minutes: '240', recipient_type: 'USER', recipient_value: null }] };

function minutesLabel(m) {
  const n = Number(m);
  if (n >= 1440 && n % 1440 === 0) return `${n / 1440} day(s)`;
  if (n >= 60 && n % 60 === 0) return `${n / 60} hour(s)`;
  return `${n} min`;
}

export default function Escalation({ user, options, reloadOptions }) {
  const [policies, setPolicies] = useState([]);
  const [form, setForm] = useState(null);
  const [picker, setPicker] = useState(null); // index of step being edited
  const [message, setMessage] = useState(null);
  const canManage = has(user, 'comms_groups');

  const load = useCallback(async () => {
    try {
      setPolicies(await api('/communication/escalation-policies'));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setStep = (index, patch) => setForm({ ...form, steps: form.steps.map((s, i) => (i === index ? { ...s, ...patch } : s)) });

  async function save() {
    try {
      const body = {
        name: form.name, description: form.description || null, active: form.active,
        steps: form.steps.map((s) => ({
          step_no: Number(s.step_no), after_minutes: Number(s.after_minutes), recipient_type: s.recipient_type,
          recipient_value: s.recipient_value == null ? null : String(s.recipient_value),
        })),
      };
      if (form.id) await api(`/communication/escalation-policies/${form.id}`, { method: 'PUT', body });
      else await api('/communication/escalation-policies', { method: 'POST', body });
      setMessage({ tone: 'success', text: `Policy "${form.name}" saved.` });
      setForm(null);
      load();
      reloadOptions?.();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function remove(policy) {
    if (!(await confirm({ title: 'Delete policy?', message: `Delete "${policy.name}"?`, confirmText: 'Delete', tone: 'danger' }))) return;
    try {
      await api(`/communication/escalation-policies/${policy.id}`, { method: 'DELETE' });
      load();
      reloadOptions?.();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  const valueOptions = (type) => ({
    USER: (options?.users || []).filter((u) => u.can_receive).map((u) => ({ value: u.id, label: `${u.name} (${u.employee_id})`, sub: ROLE_LABELS[u.role] })),
    ROLE: (options?.roles || []).filter((r) => r.can_receive).map((r) => ({ value: r.value, label: ROLE_LABELS[r.value] })),
    GROUP: (options?.groups || []).map((g) => ({ value: g.id, label: g.name })),
  }[type] || []);

  return (
    <>
      {message && <Banner tone={message.tone} message={message.text} />}
      {form && (
        <Card>
          <SectionTitle title={form.id ? 'Edit escalation policy' : 'New escalation policy'}
            description="Each step fires when an alert has stayed unacknowledged for its wait time, counted from when the alert opened." />
          <Field label="Name" value={form.name} onChangeText={(v) => setForm({ ...form, name: v })} maxLength={80} />
          <Field label="Description" value={form.description} onChangeText={(v) => setForm({ ...form, description: v })} />
          <Toggle label="Active" value={form.active} onChange={(v) => setForm({ ...form, active: v })} />
          {form.steps.map((s, index) => (
            <Card key={index} tight style={{ backgroundColor: COLORS.surfaceAlt }}>
              <View style={[styles.row, styles.gap8]}>
                <Field label="Step" value={String(s.step_no)} keyboardType="number-pad" style={{ width: 60 }}
                  onChangeText={(v) => setStep(index, { step_no: v.replace(/[^0-9]/g, '') })} />
                <Field label="After (minutes)" value={String(s.after_minutes)} keyboardType="number-pad" style={{ flex: 1 }}
                  hint={s.after_minutes ? minutesLabel(s.after_minutes) : ''}
                  onChangeText={(v) => setStep(index, { after_minutes: v.replace(/[^0-9]/g, '') })} />
              </View>
              <View style={[styles.wrap, { marginBottom: 8 }]}>
                {['USER', 'ROLE', 'GROUP', 'OWNER', 'EMAIL'].map((t) => (
                  <Chip key={t} label={RECIPIENT_TYPE_LABELS[t]} active={s.recipient_type === t}
                    onPress={() => setStep(index, { recipient_type: t, recipient_value: null })} />
                ))}
              </View>
              {['USER', 'ROLE', 'GROUP'].includes(s.recipient_type) && (
                <Select label="Notify" valueLabel={s.recipient_value ? recipientLabel(s, options) : 'Choose…'} onPress={() => setPicker(index)} />
              )}
              {s.recipient_type === 'EMAIL' && (
                <Field label="Email" value={s.recipient_value || ''} autoCapitalize="none" keyboardType="email-address"
                  onChangeText={(v) => setStep(index, { recipient_value: v.trim() })} />
              )}
              <Button title="Remove step" tone="ghost" small disabled={form.steps.length === 1}
                onPress={() => setForm({ ...form, steps: form.steps.filter((_, i) => i !== index) })} />
            </Card>
          ))}
          <Button title="Add step or recipient" tone="secondary" small style={{ alignSelf: 'flex-start' }}
            onPress={() => {
              const last = form.steps[form.steps.length - 1];
              setForm({ ...form, steps: [...form.steps, { step_no: Number(last.step_no) + 1, after_minutes: String(Number(last.after_minutes) * 6 || 1440), recipient_type: 'USER', recipient_value: null }] });
            }} />
          <Text style={[styles.mono, { marginTop: 6 }]}>To notify several people at the same step, give them the same step number and wait.</Text>
          <View style={[styles.row, styles.gap8, { marginTop: 12 }]}>
            <Button title="Cancel" tone="ghost" style={styles.flex1} onPress={() => setForm(null)} />
            <Button title="Save policy" style={styles.flex1} disabled={!form.name.trim()} onPress={save} />
          </View>
        </Card>
      )}
      <Card>
        <SectionTitle title={`Escalation policies (${policies.length})`}
          right={canManage && !form ? <Button title="New policy" small onPress={() => setForm(JSON.parse(JSON.stringify(EMPTY)))} /> : null} />
        {policies.length === 0 && <Text style={styles.empty}>No escalation policies yet.</Text>}
        {policies.map((p) => (
          <View key={p.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>{p.name}</Text>
              <Pill label={p.active ? 'ACTIVE' : 'DISABLED'} tone={p.active ? 'success' : 'muted'} />
            </View>
            {p.steps.map((s, i) => (
              <Text key={i} style={styles.mono}>Step {s.step_no} after {minutesLabel(s.after_minutes)}: {recipientLabel(s, options)}</Text>
            ))}
            <Text style={styles.mono}>Used by {p.used_by_rules} rule(s)</Text>
            {canManage && (
              <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
                <Button title="Edit" tone="ghost" small onPress={() => setForm({
                  id: p.id, name: p.name, description: p.description || '', active: !!p.active,
                  steps: p.steps.map((s) => ({ ...s, after_minutes: String(s.after_minutes) })),
                })} />
                <Button title="Delete" tone="ghost" small onPress={() => remove(p)} />
              </View>
            )}
          </View>
        ))}
      </Card>
      {form && picker !== null && (
        <OptionModal visible title="Notify" searchable options={valueOptions(form.steps[picker]?.recipient_type)}
          selectedValue={form.steps[picker]?.recipient_value}
          onSelect={(v) => { setStep(picker, { recipient_value: v }); setPicker(null); }}
          onClose={() => setPicker(null)} />
      )}
    </>
  );
}
