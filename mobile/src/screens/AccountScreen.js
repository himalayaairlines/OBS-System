/**
 * My Account: profile, change password and signed-in devices.
 *
 * In `forced` mode (the server set must_change_password, e.g. a weak demo
 * password) only the password form is usable; every other API call is refused
 * by the server until the password is changed.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { api } from '../api';
import { confirm } from '../dialog';
import { Columns, ago } from '../layout';
import { PasswordRules, passwordOk } from '../password';
import { COLORS, ROLE_LABELS, TYPE, styles } from '../theme';
import { Text } from '../text';
import { Banner, Button, Card, DataTable, Field, KeyValue, Notice, PageHeader, Pill, SectionTitle } from '../ui';
import { when } from './comms/shared';

export default function AccountScreen({ user, online, forced, onPasswordChanged, onLogout }) {
  const [account, setAccount] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    if (!online) return;
    try {
      const [a, s] = await Promise.all([api('/account'), api('/account/sessions')]);
      setAccount(a);
      setSessions(s);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, [online]);

  useEffect(() => { load(); }, [load]);

  async function endSession(session) {
    try {
      await api(`/account/sessions/${session.id}`, { method: 'DELETE' });
      setMessage({ tone: 'success', text: `${session.device || 'Device'} signed out.` });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function endOthers() {
    const sure = await confirm({
      title: 'Sign out all other devices',
      message: 'Every other browser and device signed in to your account will be signed out. This one stays signed in.',
      confirmText: 'Sign out others',
      tone: 'danger',
    });
    if (!sure) return;
    try {
      const r = await api('/account/sessions/revoke-others', { method: 'POST' });
      setMessage({ tone: 'success', text: `Signed out ${r.ended} other device(s).` });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  const person = account || user;
  const others = sessions.filter((s) => !s.current);

  const profile = (
    <Card>
      <View style={[styles.row, { gap: 14, marginBottom: 14 }]}>
        <View style={{
          width: 52, height: 52, borderRadius: 26, backgroundColor: COLORS.primarySoft,
          alignItems: 'center', justifyContent: 'center',
        }}>
          <Text style={{ color: COLORS.primary, fontWeight: '700', fontSize: 18 }}>
            {(person.name || '?').split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase()}
          </Text>
        </View>
        <View style={styles.flex1}>
          <Text style={TYPE.sectionTitle}>{person.name}</Text>
          <Text style={[TYPE.small, { color: COLORS.muted }]}>{person.email || 'No e-mail address'}</Text>
        </View>
      </View>
      <KeyValue label="Role" value={ROLE_LABELS[person.role] || person.role} />
      <KeyValue label="Employee ID" value={person.employee_id} />
      <KeyValue
        label="E-mail"
        value={!person.email ? 'Not set' : account?.email_verified ? 'Verified' : 'Not verified yet'}
        tone={person.email && account?.email_verified ? COLORS.success : COLORS.warning}
      />
      <KeyValue
        label="Two-factor sign-in"
        value={person.two_factor ? 'On (code by e-mail)' : 'Off'}
        tone={person.two_factor ? COLORS.success : COLORS.muted}
      />
      {account && (
        <>
          <KeyValue
            label="Last sign-in"
            value={account.last_login_at ? `${when(account.last_login_at)}${account.last_login_ip ? ` from ${account.last_login_ip}` : ''}` : '—'}
          />
          <KeyValue label="Password changed" value={account.password_changed_at ? when(account.password_changed_at) : 'Never'} />
        </>
      )}
      {!person.two_factor && !forced && (
        <Text style={[styles.mono, { marginTop: 10 }]}>
          Two-factor sign-in is switched on by an administrator under Users & Access.
        </Text>
      )}
    </Card>
  );

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="My Account"
        description="Your profile, password and the devices signed in to your account."
        right={forced && onLogout ? <Button title="Sign out" tone="ghost" small onPress={onLogout} /> : null}
      />
      {forced && (
        <Banner
          tone="warning"
          message="Your password must be changed before you continue. It does not meet the password policy or was set by an administrator."
        />
      )}
      {!online && <Banner tone="warning" message="Account settings need a connection." />}
      {message && <Notice message={message} />}

      <Columns flex={[1, 1]}>
        {profile}
        <ChangePassword
          person={person}
          onDone={(text) => {
            setMessage({ tone: 'success', text });
            load();
            onPasswordChanged?.();
          }}
        />
      </Columns>

      {!forced && (
        <Card>
          <SectionTitle
            title="Signed-in devices"
            description={account?.session_policy}
            right={others.length > 0 && (
              <Button title="Sign out all other devices" tone="danger" small onPress={endOthers} />
            )}
          />
          <DataTable
            columns={[
              {
                key: 'device', label: 'Device', flex: 1.6, render: (s) => (
                  <View style={[styles.row, { gap: 8, flexWrap: 'wrap' }]}>
                    <Text style={TYPE.bodyStrong}>{s.device || 'Unknown device'}</Text>
                    {s.current && <Pill label="THIS DEVICE" tone="info" />}
                    {s.two_factor && <Pill label="2FA" tone="success" />}
                  </View>
                ),
              },
              { key: 'ip', label: 'IP address', render: (s) => s.ip_address || '—' },
              { key: 'created', label: 'Signed in', render: (s) => when(s.created_at) },
              { key: 'seen', label: 'Last active', render: (s) => ago(s.last_seen_at) },
              {
                key: 'act', label: '', flex: 0.8, align: 'right', render: (s) => (s.current ? null : (
                  <Button title="Sign out" tone="ghost" small onPress={() => endSession(s)} />
                )),
              },
            ]}
            rows={sessions}
            rowKey={(s) => String(s.id)}
            emptyText="No open sessions."
            renderStacked={(s) => (
              <View style={styles.rowBetween}>
                <View style={styles.flex1}>
                  <View style={[styles.row, { gap: 8, flexWrap: 'wrap' }]}>
                    <Text style={TYPE.bodyStrong}>{s.device || 'Unknown device'}</Text>
                    {s.current && <Pill label="THIS DEVICE" tone="info" />}
                  </View>
                  <Text style={styles.mono}>
                    {s.ip_address || 'IP unknown'} · signed in {when(s.created_at)} · active {ago(s.last_seen_at)}
                  </Text>
                </View>
                {!s.current && <Button title="Sign out" tone="ghost" small onPress={() => endSession(s)} />}
              </View>
            )}
          />
        </Card>
      )}
    </ScrollView>
  );
}

function ChangePassword({ person, onDone }) {
  const empty = { old_password: '', new_password: '', confirm_password: '' };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const matches = form.new_password && form.new_password === form.confirm_password;
  const ready = form.old_password && passwordOk(form.new_password, person) && matches;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api('/change-password', { method: 'POST', body: form });
      setForm(empty);
      onDone('Password changed. Your other devices were signed out and a security notice was e-mailed to you.');
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <Card>
      <SectionTitle title="Change password" />
      {!!error && <Banner tone="danger" message={error} />}
      <Field
        label="Current password"
        value={form.old_password}
        onChangeText={(v) => setForm({ ...form, old_password: v })}
        secureTextEntry
        autoComplete="current-password"
      />
      <Field
        label="New password"
        value={form.new_password}
        onChangeText={(v) => setForm({ ...form, new_password: v })}
        secureTextEntry
        autoComplete="new-password"
      />
      <PasswordRules password={form.new_password} person={person} />
      <Field
        label="Repeat new password"
        value={form.confirm_password}
        onChangeText={(v) => setForm({ ...form, confirm_password: v })}
        secureTextEntry
        autoComplete="new-password"
        error={form.confirm_password && !matches ? 'The passwords do not match.' : undefined}
      />
      <Button title={busy ? 'Saving…' : 'Change password'} onPress={save} disabled={!ready || busy} />
      <Text style={[styles.mono, { marginTop: 10 }]}>
        Changing your password signs out every other device.
      </Text>
    </Card>
  );
}
