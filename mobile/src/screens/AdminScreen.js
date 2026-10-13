import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { usePagedArray } from '../paging';
import { confirm } from '../dialog';
import { COLORS, ROLE_LABELS, styles } from '../theme';
import {
  Banner, Button, Card, Chip, Field, OptionModal, PageHeader, Pagination, Pill,
  SearchBar, SectionTitle, Select,
} from '../ui';

const ROLES = Object.keys(ROLE_LABELS);

export default function AdminScreen() {
  const [tab, setTab] = useState('crew');
  const [message, setMessage] = useState(null);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Administration"
        description="Crew names used to build groups, and the login accounts for each role."
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        <Chip label="Crew roster" active={tab === 'crew'} onPress={() => setTab('crew')} />
        <Chip label="Login accounts" active={tab === 'users'} onPress={() => setTab('users')} />
      </View>
      {message && <Banner tone={message.tone} message={message.text} />}

      {tab === 'crew' ? <CrewRoster notify={setMessage} /> : <Users notify={setMessage} />}
    </ScrollView>
  );
}

function CrewRoster({ notify }) {
  const [members, setMembers] = useState([]);
  const [positions, setPositions] = useState(['Purser', 'Senior Cabin Crew', 'Cabin Crew']);
  const [form, setForm] = useState({ crew_code: '', name: '', position: 'Cabin Crew' });
  const [editingId, setEditingId] = useState(null);
  const [positionPicker, setPositionPicker] = useState(false);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');

  // The roster is a small reference list held on the device, so it is
  // filtered and paged here rather than on the server.
  const term = search.trim().toLowerCase();
  const filteredMembers = members
    .filter((m) => !filter || m.position === filter)
    .filter((m) => !term || m.name.toLowerCase().includes(term) || m.crew_code.toLowerCase().includes(term));
  const roster = usePagedArray(filteredMembers, { resetKey: `${term}|${filter}` });

  const load = useCallback(async () => {
    try {
      setMembers(await api('/crew-members?include_inactive=true'));
      setPositions(await api('/crew-positions'));
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      const body = {
        crew_code: form.crew_code,
        name: form.name,
        position: form.position,
        active: true,
      };
      if (editingId) await api(`/crew-members/${editingId}`, { method: 'PUT', body });
      else await api('/crew-members', { method: 'POST', body });
      notify({ tone: 'success', text: `Crew member ${form.name} saved.` });
      setForm({ crew_code: '', name: '', position: 'Cabin Crew' });
      setEditingId(null);
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function remove(member) {
    const sure = await confirm({
      title: 'Remove crew member',
      message: `Remove ${member.name} from the roster? If they belong to a group they are `
        + 'deactivated rather than deleted, so past commission stays intact.',
      confirmText: 'Remove',
      tone: 'danger',
    });
    if (!sure) return;
    try {
      const result = await api(`/crew-members/${member.id}`, { method: 'DELETE' });
      notify({ tone: 'info', text: result.detail });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  return (
    <>
      <Card>
        <SectionTitle
          title={editingId ? 'Edit crew member' : 'Add a crew member'}
          description="These names are what Inventory picks from when building groups of four."
        />
        <View style={[styles.row, styles.gap8]}>
          <Field
            label="Crew code"
            value={form.crew_code}
            onChangeText={(v) => setForm({ ...form, crew_code: v })}
            autoCapitalize="characters"
            placeholder="10761"
            style={{ flex: 1 }}
          />
          <Field
            label="Full name"
            value={form.name}
            onChangeText={(v) => setForm({ ...form, name: v })}
            placeholder="SUNITA KARKI"
            autoCapitalize="characters"
            style={{ flex: 2 }}
          />
        </View>
        <Select label="Position" valueLabel={form.position} onPress={() => setPositionPicker(true)} />
        <View style={[styles.row, styles.gap8]}>
          <Button
            title={editingId ? 'Save' : 'Add crew member'}
            style={styles.flex1}
            onPress={save}
            disabled={!form.crew_code || !form.name}
          />
          {editingId && (
            <Button
              title="Cancel"
              tone="ghost"
              style={styles.flex1}
              onPress={() => { setEditingId(null); setForm({ crew_code: '', name: '', position: 'Cabin Crew' }); }}
            />
          )}
        </View>
      </Card>

      <Card>
        <SectionTitle title={`Roster (${members.filter((m) => m.active).length} active)`} />
        <SearchBar value={search} onChangeText={setSearch} placeholder="Search name or code…" />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          <Chip label="All" active={!filter} onPress={() => setFilter('')} />
          {positions.map((position) => (
            <Chip
              key={position}
              label={position}
              active={filter === position}
              onPress={() => setFilter(filter === position ? '' : position)}
            />
          ))}
        </View>
        {roster.items.map((member) => (
          <View key={member.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <View style={styles.flex1}>
                <Text style={{ fontSize: 14, fontWeight: '600' }}>{member.name}</Text>
                <Text style={styles.mono}>{member.crew_code} · {member.position}</Text>
              </View>
              {!member.active && <Pill label="INACTIVE" tone="muted" />}
            </View>
            <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
              <Button
                title="Edit"
                tone="ghost"
                small
                onPress={() => {
                  setEditingId(member.id);
                  setForm({
                    crew_code: member.crew_code,
                    name: member.name,
                    position: member.position,
                  });
                }}
              />
              <Button title="Remove" tone="ghost" small onPress={() => remove(member)} />
            </View>
          </View>
        ))}
        {!members.length && <Text style={styles.empty}>No crew members yet.</Text>}
        {!!members.length && !filteredMembers.length && <Text style={styles.empty}>No crew member matches.</Text>}
        <Pagination pager={roster} label="crew members" />
      </Card>

      <OptionModal
        visible={positionPicker}
        title="Position"
        options={positions.map((position) => ({ value: position, label: position }))}
        selectedValue={form.position}
        onSelect={(value) => { setForm({ ...form, position: value }); setPositionPicker(false); }}
        onClose={() => setPositionPicker(false)}
      />
    </>
  );
}

function Users({ notify }) {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({ employee_id: '', name: '', password: '', role: 'crew', email: '' });
  const [rolePicker, setRolePicker] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api('/users'));
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  async function create() {
    try {
      await api('/users', { method: 'POST', body: form });
      notify({ tone: 'success', text: `Account ${form.employee_id.toUpperCase()} created.` });
      setForm({ employee_id: '', name: '', password: '', role: 'crew', email: '' });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  /** Clear a lock caused by five wrong passwords. */
  async function unlock(user) {
    try {
      await api(`/users/${user.id}/unlock`, { method: 'POST' });
      notify({
        tone: 'success',
        text: `${user.employee_id} unlocked. They can sign in with their existing password.`,
      });
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  async function update(user, changes) {
    try {
      await api(`/users/${user.id}`, {
        method: 'PUT',
        body: { name: user.name, role: user.role, active: !!user.active, ...changes },
      });
      notify({ tone: 'success', text: `Account ${user.employee_id} updated.` });
      setEditing(null);
      load();
    } catch (error) {
      notify({ tone: 'danger', text: error.message });
    }
  }

  return (
    <>
      <Card>
        <SectionTitle title="Create a login account" description="Crew accounts are shared by the four members of a group." />
        <View style={[styles.row, styles.gap8]}>
          <Field
            label="Employee ID"
            value={form.employee_id}
            onChangeText={(v) => setForm({ ...form, employee_id: v })}
            autoCapitalize="characters"
            placeholder="CREW002"
            style={{ flex: 1 }}
          />
          <Field
            label="Display name"
            value={form.name}
            onChangeText={(v) => setForm({ ...form, name: v })}
            placeholder="Crew iPad 02"
            style={{ flex: 1.4 }}
          />
        </View>
        <Select label="Role" valueLabel={ROLE_LABELS[form.role]} onPress={() => setRolePicker(true)} />
        <Field
          label="Password"
          value={form.password}
          onChangeText={(v) => setForm({ ...form, password: v })}
          secureTextEntry
          placeholder="At least 4 characters"
        />
        <Field
          label="Email (optional)"
          hint="Needed to receive stock-alert emails."
          value={form.email}
          onChangeText={(v) => setForm({ ...form, email: v.trim() })}
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="name@himalaya-airlines.com"
        />
        <Button
          title="Create account"
          onPress={create}
          disabled={!form.employee_id || !form.name || form.password.length < 4}
        />
      </Card>

      <Card>
        <SectionTitle title={`Accounts (${users.length})`} />
        {users.map((user) => (
          <View key={user.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <View style={styles.flex1}>
                <Text style={{ fontSize: 14, fontWeight: '600' }}>{user.name}</Text>
                <Text style={styles.mono}>{user.employee_id} · {ROLE_LABELS[user.role] || user.role}</Text>
                <Text style={styles.mono}>{user.email || 'No email address'}</Text>
              </View>
              <View style={[styles.row, styles.gap8]}>
                {!!user.locked_at && <Pill label="LOCKED" tone="danger" />}
                <Pill label={user.active ? 'ACTIVE' : 'DISABLED'} tone={user.active ? 'success' : 'muted'} />
              </View>
            </View>
            {!!user.locked_at && (
              <Text style={{ fontSize: 12, color: COLORS.danger, marginTop: 3 }}>
                Locked after {user.failed_attempts} incorrect passwords on{' '}
                {String(user.locked_at).replace('T', ' at ').slice(0, 19)}
              </Text>
            )}
            {!user.locked_at && !!user.failed_attempts && (
              <Text style={{ fontSize: 12, color: COLORS.warning, marginTop: 3 }}>
                {user.failed_attempts} incorrect password attempt(s) since the last sign-in
              </Text>
            )}
            <View style={[styles.row, styles.gap8, { marginTop: 8 }]}>
              {!!user.locked_at && (
                <Button
                  title="Unlock"
                  tone="warning"
                  small
                  onPress={() => unlock(user)}
                />
              )}
              <Button
                title={user.active ? 'Disable' : 'Enable'}
                tone="ghost"
                small
                onPress={() => update(user, { active: !user.active })}
              />
              <Button
                title="Reset password"
                tone="ghost"
                small
                onPress={() => setEditing(editing?.id === user.id && editing.mode === 'password' ? null : { ...user, mode: 'password' })}
              />
              <Button
                title="Email"
                tone="ghost"
                small
                onPress={() => setEditing(editing?.id === user.id && editing.mode === 'email' ? null : { ...user, mode: 'email', newEmail: user.email || '' })}
              />
            </View>
            {editing?.id === user.id && editing.mode === 'email' && (
              <View style={{ marginTop: 10 }}>
                <Field
                  label="Email address"
                  value={editing.newEmail}
                  onChangeText={(v) => setEditing({ ...editing, newEmail: v.trim() })}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                  placeholder="Leave empty to remove"
                />
                <Button title="Save email" small onPress={() => update(user, { email: editing.newEmail })} />
              </View>
            )}
            {editing?.id === user.id && editing.mode === 'password' && (
              <View style={{ marginTop: 10 }}>
                <Field
                  label="New password"
                  value={editing.password || ''}
                  onChangeText={(v) => setEditing({ ...editing, password: v })}
                  secureTextEntry
                />
                <Button
                  title="Set password"
                  small
                  onPress={() => update(user, { password: editing.password })}
                  disabled={(editing.password || '').length < 4}
                />
              </View>
            )}
          </View>
        ))}
      </Card>

      <OptionModal
        visible={rolePicker}
        title="Role"
        options={ROLES.map((role) => ({ value: role, label: ROLE_LABELS[role] }))}
        selectedValue={form.role}
        onSelect={(value) => { setForm({ ...form, role: value }); setRolePicker(false); }}
        onClose={() => setRolePicker(false)}
      />
    </>
  );
}
