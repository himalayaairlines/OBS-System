import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { confirm } from '../../dialog';
import { COLORS, ROLE_LABELS, styles } from '../../theme';
import { Banner, Button, Card, Field, Notice, OptionModal, Pill, SectionTitle, Select } from '../../ui';
import { Toggle, has } from './shared';

const EMPTY = { name: '', description: '', active: true, user_ids: [] };

/** Recipient groups. Independent of login roles. */
export default function Groups({ user, options, reloadOptions }) {
  const [groups, setGroups] = useState([]);
  const [form, setForm] = useState(null);
  const [picker, setPicker] = useState(false);
  const [message, setMessage] = useState(null);
  const canManage = has(user, 'comms_groups');
  const eligible = (options?.users || []).filter((u) => u.can_receive);

  const load = useCallback(async () => {
    try {
      setGroups(await api('/communication/groups'));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      const body = { name: form.name, description: form.description || null, active: form.active, user_ids: form.user_ids };
      if (form.id) await api(`/communication/groups/${form.id}`, { method: 'PUT', body });
      else await api('/communication/groups', { method: 'POST', body });
      setMessage({ tone: 'success', text: `Group "${form.name}" saved.` });
      setForm(null);
      load();
      reloadOptions?.();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function remove(group) {
    if (!(await confirm({ title: 'Delete group?', message: `Delete "${group.name}"?`, confirmText: 'Delete', tone: 'danger' }))) return;
    try {
      await api(`/communication/groups/${group.id}`, { method: 'DELETE' });
      setMessage({ tone: 'success', text: 'Group deleted.' });
      load();
      reloadOptions?.();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  const toggleMember = (id) => setForm({
    ...form, user_ids: form.user_ids.includes(id) ? form.user_ids.filter((x) => x !== id) : [...form.user_ids, id],
  });

  return (
    <>
      {message && <Notice message={message} />}
      {form && (
        <Card>
          <SectionTitle title={form.id ? 'Edit group' : 'New recipient group'} />
          <Field label="Name" value={form.name} onChangeText={(v) => setForm({ ...form, name: v })} placeholder="e.g. Warehouse team" maxLength={80} />
          <Field label="Description" value={form.description} onChangeText={(v) => setForm({ ...form, description: v })} />
          <Toggle label="Active" value={form.active} onChange={(v) => setForm({ ...form, active: v })}
            hint="A disabled group receives nothing, even if rules still name it." />
          <Select label="Members" valueLabel={`${form.user_ids.length} selected`} onPress={() => setPicker(true)} />
          {form.user_ids.map((id) => {
            const u = eligible.find((x) => x.id === id);
            return <Text key={id} style={styles.mono}>• {u ? `${u.name} (${u.employee_id})${u.has_email ? '' : ' · no email'}` : `User #${id}`}</Text>;
          })}
          <Text style={[styles.mono, { marginTop: 6 }]}>Only people whose role can see warehouse stock can be members.</Text>
          <View style={[styles.row, styles.gap8, { marginTop: 12 }]}>
            <Button title="Cancel" tone="ghost" style={styles.flex1} onPress={() => setForm(null)} />
            <Button title="Save group" style={styles.flex1} disabled={!form.name.trim()} onPress={save} />
          </View>
        </Card>
      )}
      <Card>
        <SectionTitle
          title={`Recipient groups (${groups.length})`}
          description="Use a group in a rule to notify a team without listing each person."
          right={canManage && !form ? <Button title="New group" small onPress={() => setForm({ ...EMPTY })} /> : null}
        />
        {groups.length === 0 && <Text style={styles.empty}>No groups yet.</Text>}
        {groups.map((g) => (
          <View key={g.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }}>{g.name}</Text>
              <Pill label={g.active ? 'ACTIVE' : 'DISABLED'} tone={g.active ? 'success' : 'muted'} />
            </View>
            {!!g.description && <Text style={styles.mono}>{g.description}</Text>}
            <Text style={styles.mono}>
              {g.members.map((m) => `${m.name}${m.has_email ? '' : ' (no email)'}${m.active ? '' : ' (inactive)'}`).join(', ') || 'No members'}
            </Text>
            <Text style={styles.mono}>Used by {g.used_by_rules} rule(s)</Text>
            {canManage && (
              <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
                <Button title="Edit" tone="ghost" small onPress={() => setForm({
                  id: g.id, name: g.name, description: g.description || '', active: !!g.active, user_ids: g.members.map((m) => m.id),
                })} />
                <Button title="Delete" tone="ghost" small onPress={() => remove(g)} />
              </View>
            )}
          </View>
        ))}
      </Card>
      {form && (
        <OptionModal visible={picker} title="Members" multi searchable
          options={eligible.map((u) => ({ value: u.id, label: `${u.name} (${u.employee_id})`, sub: `${ROLE_LABELS[u.role]}${u.has_email ? '' : ' · no email'}` }))}
          selectedValue={form.user_ids} onSelect={toggleMember} onClose={() => setPicker(false)} />
      )}
    </>
  );
}
