/**
 * Users & Access: login accounts (invite, lock, reset, two-factor, sessions),
 * the crew roster used to build duty groups, and the sign-in settings.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { usePaged, usePagedArray } from '../paging';
import { confirm } from '../dialog';
import { ago } from '../layout';
import { PasswordRules, passwordOk } from '../password';
import { COLORS, NUMERIC, ROLE_LABELS, TYPE, styles } from '../theme';
import {
  Banner, Button, Card, Chip, DataTable, Field, KeyValue, Notice, OptionModal, PageHeader,
  Pagination, Pill, SearchBar, SectionTitle, Select,
} from '../ui';
import { Toggle, when } from './comms/shared';

const ROLES = Object.keys(ROLE_LABELS);
// The legacy shared OBS iPads sign in with a password set here and have no
// mailbox. 7.18: individual crew logins change their password at first login.
const DEVICE_ROLES = ['crew_supervisor'];

const STATUS = {
  ACTIVE: { label: 'Active', tone: 'success', color: COLORS.success },
  INVITED: { label: 'Invited', tone: 'info', color: COLORS.info },
  UNVERIFIED: { label: 'Unverified', tone: 'warning', color: COLORS.warning },
  LOCKED: { label: 'Locked', tone: 'danger', color: COLORS.danger },
  DISABLED: { label: 'Disabled', tone: 'muted', color: COLORS.muted },
};

export default function AdminScreen({ user, online }) {
  const [tab, setTab] = useState('users');
  const [message, setMessage] = useState(null);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="Users & Access"
        description="Login accounts and what they can do, the crew roster used for duty groups, and sign-in rules."
      />
      <View style={[styles.wrap, { marginBottom: 14 }]}>
        <Chip label="Login accounts" active={tab === 'users'} onPress={() => { setTab('users'); setMessage(null); }} />
        <Chip label="Crew roster" active={tab === 'crew'} onPress={() => { setTab('crew'); setMessage(null); }} />
        <Chip label="Sign-in settings" active={tab === 'settings'} onPress={() => { setTab('settings'); setMessage(null); }} />
      </View>
      {!online && <Banner tone="warning" message="Users & Access needs a connection." />}
      {message && <Notice message={message} />}

      {online && tab === 'users' && <LoginAccounts me={user} notify={setMessage} />}
      {online && tab === 'crew' && <CrewRoster notify={setMessage} />}
      {online && tab === 'settings' && <SignInSettings notify={setMessage} />}
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
  const roster = usePagedArray(filteredMembers, { resetKey: `${term}|${filter}`, pageSize: 10 });

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
          description="Each crew member gets their own login automatically: the user name is the crew code and the first password is Him@9841, which must be changed at first login. Removing a crew member disables the login."
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


/* ------------------------------ login accounts ------------------------------ */
function LoginAccounts({ me, notify }) {
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [panel, setPanel] = useState(null); // 'invite' | 'device'
  const [picker, setPicker] = useState(false);
  const [editing, setEditing] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => setTerm(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const query = new URLSearchParams();
  if (term) query.set('q', term);
  if (role) query.set('role', role);
  if (status) query.set('status', status);
  const qs = query.toString();
  const pager = usePaged(`/users${qs ? `?${qs}` : ''}`, { pageSize: 10 });
  const counts = pager.raw?.counts || {};

  const run = useCallback(async (fn, success) => {
    try {
      const result = await fn();
      notify({ tone: 'success', text: typeof success === 'function' ? success(result) : success });
      pager.reload();
      return result;
    } catch (e) {
      notify({ tone: 'danger', text: e.message });
      return null;
    }
  }, [notify, pager]);

  async function lock(u) {
    const sure = await confirm({
      title: 'Lock account',
      message: `Lock ${u.name} (${u.employee_id})? They are signed out everywhere and cannot sign in until unlocked.`,
      confirmText: 'Lock',
      tone: 'danger',
    });
    if (sure) run(() => api(`/users/${u.id}/lock`, { method: 'POST', body: {} }), `${u.employee_id} locked and signed out.`);
  }

  const unlock = (u) => run(() => api(`/users/${u.id}/unlock`, { method: 'POST' }), `${u.employee_id} unlocked.`);
  const sendReset = (u) => run(() => api(`/users/${u.id}/send-reset`, { method: 'POST', timeout: 60000 }),
    `Reset link sent to ${u.email}. It is valid for 30 minutes.`);
  const resendInvite = (u) => run(() => api(`/users/${u.id}/resend-invite`, { method: 'POST', timeout: 60000 }),
    `Invitation sent again to ${u.email}.`);
  const enable2fa = (u) => run(() => api(`/users/${u.id}/two-factor`, { method: 'POST', body: { enabled: true } }),
    `Two-factor sign-in is on for ${u.employee_id}. A code is e-mailed at each sign-in.`);

  const actions = (u) => (
    <View style={[styles.row, { gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }]}>
      <Button title="Edit" tone="ghost" small onPress={() => setEditing(u)} />
      {u.status === 'INVITED' && <Button title="Resend invite" tone="ghost" small onPress={() => resendInvite(u)} />}
      {u.status !== 'INVITED' && !!u.email && u.active && (
        <Button title="Send reset link" tone="ghost" small onPress={() => sendReset(u)} />
      )}
      {u.status === 'LOCKED' || u.locked_at
        ? <Button title="Unlock" tone="warning" small onPress={() => unlock(u)} />
        : u.active && u.id !== me.id && <Button title="Lock" tone="ghost" small onPress={() => lock(u)} />}
    </View>
  );

  const twoFactor = (u) => {
    if (u.two_factor) return <Pill label="ON" tone="success" />;
    if (!u.email || !u.active) return <Text style={[TYPE.small, { color: COLORS.faint }]}>—</Text>;
    return (
      <Pressable onPress={() => enable2fa(u)} accessibilityRole="button" hitSlop={6}>
        <Text style={[TYPE.small, { color: COLORS.primary, fontWeight: '600' }]}>Enable 2FA</Text>
      </Pressable>
    );
  };

  const who = (u) => (
    <View style={styles.flex1}>
      <View style={[styles.row, { gap: 6, flexWrap: 'wrap' }]}>
        <Text style={TYPE.bodyStrong} numberOfLines={1}>{u.name}</Text>
        {u.is_demo && <Pill label="DEMO" tone="warning" />}
        {u.must_change_password && <Pill label="MUST CHANGE PASSWORD" tone="muted" />}
      </View>
      <Text style={[TYPE.caption, { color: COLORS.muted }]} numberOfLines={1}>
        {u.employee_id}{u.email ? ` · ${u.email}` : ' · no e-mail'}
      </Text>
    </View>
  );

  const lastSignIn = (u) => (
    <View>
      <Text style={TYPE.small}>{u.last_login_at ? ago(u.last_login_at) : 'Never'}</Text>
      {!!u.last_login_ip && <Text style={[TYPE.caption, { color: COLORS.muted }]}>{u.last_login_ip}</Text>}
    </View>
  );

  return (
    <>
      <View style={[styles.wrap, { gap: 10, marginBottom: 14 }]}>
        {Object.entries(STATUS).map(([key, meta]) => {
          const active = status === key;
          return (
            <Pressable
              key={key}
              onPress={() => setStatus(active ? '' : key)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              style={({ hovered }) => ({
                flexGrow: 1, flexBasis: 130, minWidth: 120, padding: 12, borderRadius: 10,
                backgroundColor: active ? COLORS.primarySoft : COLORS.surface,
                borderWidth: 1, borderColor: active ? COLORS.primary : hovered ? COLORS.borderStrong : COLORS.border,
                borderLeftWidth: 4, borderLeftColor: meta.color,
              })}
            >
              <Text style={[TYPE.caption, { color: COLORS.muted, fontWeight: '600' }]}>{meta.label.toUpperCase()}</Text>
              <Text style={[TYPE.metric, NUMERIC, { color: COLORS.text, marginTop: 2 }]}>{counts[key] ?? '—'}</Text>
            </Pressable>
          );
        })}
      </View>

      <Card>
        <View style={[styles.wrap, { gap: 10, alignItems: 'flex-start' }]}>
          <View style={{ flexGrow: 3, flexBasis: 260 }}>
            <SearchBar value={search} onChangeText={setSearch} placeholder="Search name, Employee ID or e-mail…" />
          </View>
          <View style={{ flexGrow: 1, flexBasis: 180 }}>
            <Select valueLabel={role ? ROLE_LABELS[role] : 'All roles'} onPress={() => setPicker(true)} />
          </View>
          <Button title="+ Invite user" onPress={() => setPanel(panel === 'invite' ? null : 'invite')} />
          <Button title="Add device account" tone="secondary" onPress={() => setPanel(panel === 'device' ? null : 'device')} />
        </View>
        {(status || role || term) && (
          <View style={[styles.row, { gap: 8, marginBottom: 6 }]}>
            <Text style={[TYPE.small, { color: COLORS.muted }]}>
              Filtered{status ? ` · ${STATUS[status].label}` : ''}{role ? ` · ${ROLE_LABELS[role]}` : ''}
            </Text>
            <Button title="Clear" tone="ghost" small onPress={() => { setStatus(''); setRole(''); setSearch(''); }} />
          </View>
        )}
      </Card>

      {panel === 'invite' && (
        <InvitePanel
          onClose={() => setPanel(null)}
          onDone={(u) => {
            setPanel(null);
            notify({
              tone: u.delivery === 'ACCEPTED' ? 'success' : 'warning',
              text: u.delivery === 'ACCEPTED'
                ? `Invitation sent to ${u.email}. The link is valid for 72 hours.`
                : `${u.employee_id} was created but the e-mail was not sent (${String(u.delivery).toLowerCase()}). Check SMTP, then use Resend invite.`,
            });
            pager.reload();
          }}
        />
      )}
      {panel === 'device' && (
        <DevicePanel
          onClose={() => setPanel(null)}
          onDone={(u) => {
            setPanel(null);
            notify({ tone: 'success', text: `Account ${u.employee_id} created.` });
            pager.reload();
          }}
        />
      )}

      <Card>
        <DataTable
          breakpoint={1000}
          columns={[
            { key: 'user', label: 'User', flex: 2.4, render: who },
            { key: 'role', label: 'Role', flex: 1.1, render: (u) => ROLE_LABELS[u.role] || u.role },
            { key: 'status', label: 'Status', flex: 0.9, render: (u) => <Pill label={STATUS[u.status]?.label.toUpperCase() || u.status} tone={STATUS[u.status]?.tone} /> },
            { key: '2fa', label: '2FA', flex: 0.8, render: twoFactor },
            { key: 'last', label: 'Last sign-in', flex: 1.1, render: lastSignIn },
            { key: 'sessions', label: 'Sessions', flex: 0.6, align: 'center', render: (u) => String(u.sessions) },
            { key: 'actions', label: '', flex: 2.4, align: 'right', render: actions },
          ]}
          rows={pager.items}
          rowKey={(u) => String(u.id)}
          emptyText={pager.loading ? 'Loading…' : 'No accounts match.'}
          renderStacked={(u) => (
            <>
              <View style={[styles.rowBetween, { alignItems: 'flex-start', gap: 8 }]}>
                {who(u)}
                <Pill label={STATUS[u.status]?.label.toUpperCase() || u.status} tone={STATUS[u.status]?.tone} />
              </View>
              <Text style={styles.mono}>
                {ROLE_LABELS[u.role] || u.role} · last sign-in {u.last_login_at ? ago(u.last_login_at) : 'never'}
                {u.last_login_ip ? ` (${u.last_login_ip})` : ''} · {u.sessions} session(s)
              </Text>
              <View style={[styles.row, { gap: 8, marginTop: 6, alignItems: 'center' }]}>
                <Text style={[TYPE.small, { color: COLORS.muted }]}>2FA</Text>
                {twoFactor(u)}
              </View>
              <View style={{ marginTop: 8, alignItems: 'flex-start' }}>{actions(u)}</View>
            </>
          )}
        />
        <Pagination pager={pager} label="accounts" />
      </Card>

      <OptionModal
        visible={picker}
        title="Role"
        options={[{ value: '', label: 'All roles' }, ...ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))]}
        selectedValue={role}
        onSelect={(v) => { setRole(v); setPicker(false); }}
        onClose={() => setPicker(false)}
      />

      {editing && (
        <EditUserModal
          account={editing}
          me={me}
          onClose={() => setEditing(null)}
          onSaved={(text) => { setEditing(null); notify({ tone: 'success', text }); pager.reload(); }}
        />
      )}
    </>
  );
}

function RolePicker({ value, onChange, label = 'Role' }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Select label={label} valueLabel={ROLE_LABELS[value]} onPress={() => setOpen(true)} />
      <OptionModal
        visible={open}
        title={label}
        options={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))}
        selectedValue={value}
        onSelect={(v) => { onChange(v); setOpen(false); }}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

function InvitePanel({ onClose, onDone }) {
  const [form, setForm] = useState({ employee_id: '', name: '', email: '', role: 'inventory', two_factor: false });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm({ ...form, ...patch });

  async function send() {
    setBusy(true);
    setError(null);
    try {
      onDone(await api('/users/invite', { method: 'POST', body: form, timeout: 60000 }));
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <Card>
      <SectionTitle
        title="Invite a user"
        description="They receive an e-mail with a link to choose their own password (valid 72 hours, single use). Nobody else ever knows it."
        right={<Button title="Cancel" tone="ghost" small onPress={onClose} />}
      />
      {!!error && <Banner tone="danger" message={error} />}
      <View style={[styles.wrap, { gap: 10 }]}>
        <Field label="Employee ID" value={form.employee_id} autoCapitalize="characters" placeholder="INV002"
          onChangeText={(v) => set({ employee_id: v })} style={{ flexGrow: 1, flexBasis: 160 }} />
        <Field label="Full name" value={form.name} placeholder="Sita Gurung"
          onChangeText={(v) => set({ name: v })} style={{ flexGrow: 2, flexBasis: 220 }} />
        <Field label="Work e-mail" value={form.email} autoCapitalize="none" keyboardType="email-address" autoCorrect={false}
          placeholder="name@himalaya-airlines.com" onChangeText={(v) => set({ email: v.trim() })} style={{ flexGrow: 2, flexBasis: 240 }} />
      </View>
      <RolePicker value={form.role} onChange={(v) => set({ role: v })} />
      <Toggle label="Require two-factor sign-in" value={form.two_factor} onChange={(v) => set({ two_factor: v })}
        hint="A 6-digit code is e-mailed at every sign-in. Needs working SMTP." />
      <Button
        title={busy ? 'Sending…' : 'Send invitation'}
        onPress={send}
        disabled={busy || !form.employee_id || !form.name || !form.email.includes('@')}
        style={{ marginTop: 8, alignSelf: 'flex-start' }}
      />
    </Card>
  );
}

function DevicePanel({ onClose, onDone }) {
  const [form, setForm] = useState({ employee_id: '', name: '', role: 'crew', password: '', email: '' });
  const [error, setError] = useState(null);
  const set = (patch) => setForm({ ...form, ...patch });
  const person = { name: form.name, employee_id: form.employee_id, email: form.email };

  async function create() {
    setError(null);
    try {
      onDone(await api('/users', { method: 'POST', body: { ...form, email: form.email || null } }));
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <Card>
      <SectionTitle
        title="Add a device account"
        description="For shared cabin iPads and other accounts without a mailbox. You set the password here and give it to the crew."
        right={<Button title="Cancel" tone="ghost" small onPress={onClose} />}
      />
      {!!error && <Banner tone="danger" message={error} />}
      <View style={[styles.wrap, { gap: 10 }]}>
        <Field label="Employee ID" value={form.employee_id} autoCapitalize="characters" placeholder="CREW002"
          onChangeText={(v) => set({ employee_id: v })} style={{ flexGrow: 1, flexBasis: 160 }} />
        <Field label="Display name" value={form.name} placeholder="Crew iPad 02"
          onChangeText={(v) => set({ name: v })} style={{ flexGrow: 2, flexBasis: 220 }} />
      </View>
      <RolePicker value={form.role} onChange={(v) => set({ role: v })} />
      <Field label="Password" value={form.password} secureTextEntry autoComplete="new-password"
        onChangeText={(v) => set({ password: v })} />
      <PasswordRules password={form.password} person={person} />
      <Field label="E-mail (optional)" hint="Needed for stock-alert e-mails, reset links and two-factor."
        value={form.email} autoCapitalize="none" keyboardType="email-address" autoCorrect={false}
        onChangeText={(v) => set({ email: v.trim() })} />
      <Button
        title="Create account"
        onPress={create}
        disabled={!form.employee_id || !form.name || !passwordOk(form.password, person)}
        style={{ alignSelf: 'flex-start' }}
      />
    </Card>
  );
}

function EditUserModal({ account, me, onClose, onSaved }) {
  const self = account.id === me.id;
  const [form, setForm] = useState({
    name: account.name, email: account.email || '', role: account.role, active: account.active,
    two_factor: account.two_factor, password: '',
    require_change: !DEVICE_ROLES.includes(account.role),
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (patch) => setForm({ ...form, ...patch });
  const person = { name: form.name, employee_id: account.employee_id, email: form.email };
  const passwordBad = !!form.password && !passwordOk(form.password, person);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: form.name, role: form.role, active: form.active, email: form.email,
        two_factor: form.two_factor,
      };
      if (form.password) Object.assign(body, { password: form.password, require_change: form.require_change });
      await api(`/users/${account.id}`, { method: 'PUT', body });
      onSaved(`Account ${account.employee_id} updated.`);
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  async function signOutEverywhere() {
    try {
      await api(`/users/${account.id}/revoke-sessions`, { method: 'POST' });
      onSaved(`${account.employee_id} was signed out of every device.`);
    } catch (e) {
      setError(e.message);
    }
  }

  async function deactivate() {
    const sure = await confirm({
      title: 'Deactivate account',
      message: `Deactivate ${account.employee_id}? Their history is kept; they can no longer sign in.`,
      confirmText: 'Deactivate',
      tone: 'danger',
    });
    if (!sure) return;
    try {
      await api(`/users/${account.id}`, { method: 'DELETE' });
      onSaved(`${account.employee_id} deactivated.`);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: COLORS.overlay, justifyContent: 'center', alignItems: 'center', padding: 20 }}>
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: COLORS.background, borderRadius: 12, width: '100%', maxWidth: 560, maxHeight: '90%' }}
        >
          <ScrollView contentContainerStyle={{ padding: 20 }} keyboardShouldPersistTaps="handled">
            <SectionTitle
              title={`Edit ${account.employee_id}`}
              description={`Created ${when(account.created_at)} · password changed ${account.password_changed_at ? when(account.password_changed_at) : 'never'}`}
            />
            {!!error && <Banner tone="danger" message={error} />}
            <Field label="Name" value={form.name} onChangeText={(v) => set({ name: v })} />
            <Field label="E-mail" value={form.email} autoCapitalize="none" keyboardType="email-address" autoCorrect={false}
              hint={account.email && form.email !== account.email ? 'Changing it marks the address unverified until they next use it.' : undefined}
              onChangeText={(v) => set({ email: v.trim() })} placeholder="Leave empty for a device without a mailbox" />
            {self
              ? <KeyValue label="Role" value={`${ROLE_LABELS[form.role]} (you cannot change your own role)`} />
              : <RolePicker value={form.role} onChange={(v) => set({ role: v })} />}
            <Toggle label="Account enabled" value={form.active} disabled={self} onChange={(v) => set({ active: v })}
              hint="Disabling signs them out of every device." />
            <Toggle label="Two-factor sign-in" value={form.two_factor} onChange={(v) => set({ two_factor: v })}
              hint="A 6-digit code is e-mailed at each sign-in. Needs an e-mail address and working SMTP." />

            <Card tight style={{ backgroundColor: COLORS.surfaceAlt, marginTop: 10 }}>
              <Text style={[TYPE.bodyStrong, { marginBottom: 8 }]}>Set a new password</Text>
              <Text style={[styles.mono, { marginTop: 0, marginBottom: 10 }]}>
                {account.email
                  ? 'Usually better: “Send reset link”, so only they know the password.'
                  : 'For device accounts without e-mail. Leave empty to keep the current password.'}
              </Text>
              <Field label="New password" value={form.password} secureTextEntry autoComplete="new-password"
                onChangeText={(v) => set({ password: v })} />
              {!!form.password && <PasswordRules password={form.password} person={person} />}
              {!!form.password && (
                <Toggle label="They must choose a new one at next sign-in" value={form.require_change}
                  onChange={(v) => set({ require_change: v })}
                  hint="Turn off for shared crew devices whose password you keep." />
              )}
            </Card>

            <View style={[styles.wrap, { marginTop: 14 }]}>
              <Button title={busy ? 'Saving…' : 'Save changes'} onPress={save} disabled={busy || !form.name || passwordBad} />
              <Button title="Close" tone="ghost" onPress={onClose} />
            </View>
            {!self && (
              <View style={[styles.wrap, { marginTop: 14, paddingTop: 14, borderTopWidth: 1, borderTopColor: COLORS.border }]}>
                <Button title={`Sign out everywhere (${account.sessions})`} tone="secondary" small
                  disabled={!account.sessions} onPress={signOutEverywhere} />
                <Button title="Deactivate account" tone="danger" small onPress={deactivate} disabled={!account.active} />
              </View>
            )}
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

/* ------------------------------ sign-in settings ----------------------------- */
function SignInSettings({ notify }) {
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);

  const load = useCallback(async () => {
    try {
      const s = await api('/security/settings');
      setSaved(s);
      setForm(Object.fromEntries(Object.entries(s).map(([k, v]) => [k, typeof v === 'number' ? String(v) : v])));
    } catch (e) {
      notify({ tone: 'danger', text: e.message });
    }
  }, [notify]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      const body = {
        session_idle_minutes: Number(form.session_idle_minutes), session_hours: Number(form.session_hours),
        device_session_hours: Number(form.device_session_hours), lockout_attempts: Number(form.lockout_attempts),
        lockout_minutes: Number(form.lockout_minutes), require_strong_passwords: !!form.require_strong_passwords,
        app_url: form.app_url || '',
      };
      await api('/security/settings', { method: 'PUT', body });
      notify({ tone: 'success', text: 'Sign-in settings saved. New limits apply from each session’s next request.' });
      load();
    } catch (e) {
      notify({ tone: 'danger', text: e.message });
    }
  }

  if (!form) return null;
  const set = (patch) => setForm({ ...form, ...patch });
  const num = (key) => (v) => set({ [key]: v.replace(/[^0-9]/g, '') });

  return (
    <Card>
      <SectionTitle title="Sessions" description="When a signed-in browser or app is signed out automatically." />
      <View style={[styles.wrap, { gap: 10 }]}>
        <Field label="Idle limit (minutes)" value={form.session_idle_minutes} keyboardType="number-pad"
          onChangeText={num('session_idle_minutes')} style={{ flexGrow: 1, flexBasis: 180 }} hint="5 – 1440" />
        <Field label="Maximum session (hours)" value={form.session_hours} keyboardType="number-pad"
          onChangeText={num('session_hours')} style={{ flexGrow: 1, flexBasis: 180 }} hint="1 – 72" />
        <Field label="Cabin device session (hours)" value={form.device_session_hours} keyboardType="number-pad"
          onChangeText={num('device_session_hours')} style={{ flexGrow: 1, flexBasis: 180 }}
          hint="Crew supervisor devices; no idle limit, so a flight is never interrupted." />
      </View>

      <SectionTitle title="Lockout" description="Protects accounts against password guessing." />
      <View style={[styles.wrap, { gap: 10 }]}>
        <Field label="Wrong passwords before lock" value={form.lockout_attempts} keyboardType="number-pad"
          onChangeText={num('lockout_attempts')} style={{ flexGrow: 1, flexBasis: 180 }} hint="3 – 20" />
        <Field label="Lock lasts (minutes)" value={form.lockout_minutes} keyboardType="number-pad"
          onChangeText={num('lockout_minutes')} style={{ flexGrow: 1, flexBasis: 180 }}
          hint="Then the account unlocks by itself. Administrator locks never expire." />
      </View>

      <SectionTitle title="Passwords" />
      <Toggle
        label="Require strong passwords"
        value={!!form.require_strong_passwords}
        onChange={(v) => set({ require_strong_passwords: v })}
        hint="Anyone signing in with a password that breaks the policy (10+ characters, 3 of lower/upper/digit/symbol, no name) must change it first."
      />
      <Text style={[styles.mono, { marginBottom: 12 }]}>
        Application address for e-mail links: {saved.app_url_from_environment || saved.app_url || 'not set'} (change it under SMTP Settings).
      </Text>
      <Button title="Save sign-in settings" onPress={save} style={{ alignSelf: 'flex-start' }} />
    </Card>
  );
}
