import React, { useCallback, useEffect, useState } from 'react';
import { Text, View } from 'react-native';

import { api } from '../../api';
import { COLORS, styles } from '../../theme';
import { Banner, Button, Card, Chip, Field, KeyValue, SectionTitle } from '../../ui';
import { StatusPill, Toggle, WEEKDAYS, has, when } from './shared';

export default function Settings({ user }) {
  return (
    <>
      {(has(user, 'comms_view') || has(user, 'comms_admin')) && <CommsSettings user={user} />}
      {has(user, 'smtp_view') && <SmtpSettings user={user} />}
    </>
  );
}

/* ----------------------------- communication ---------------------------- */
function CommsSettings({ user }) {
  const editable = has(user, 'comms_admin');
  const [form, setForm] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const s = await api('/communication/settings');
      setForm({ ...s, external_email_domains: s.external_email_domains.join(', '), sweep_minutes: String(s.sweep_minutes) });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      await api('/communication/settings', {
        method: 'PUT',
        body: {
          timezone: form.timezone.trim(), business_start: form.business_start, business_end: form.business_end,
          business_days: form.business_days, digest_time: form.digest_time, digest_weekday: form.digest_weekday,
          external_email_domains: form.external_email_domains.split(',').map((d) => d.trim()).filter(Boolean),
          sweep_minutes: Number(form.sweep_minutes) || 5,
        },
      });
      setMessage({ tone: 'success', text: 'Communication settings saved.' });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (!form) return message ? <Banner tone={message.tone} message={message.text} /> : null;
  const toggleDay = (d) => setForm({
    ...form, business_days: form.business_days.includes(d) ? form.business_days.filter((x) => x !== d) : [...form.business_days, d],
  });

  return (
    <Card>
      <SectionTitle title="Communication defaults" description="Timezone and hours used for business-hours rules, daily reminders and digests." />
      {message && <Banner tone={message.tone} message={message.text} />}
      <Field label="Timezone (IANA name)" value={form.timezone} editable={editable} onChangeText={(v) => setForm({ ...form, timezone: v })} />
      <View style={[styles.row, styles.gap8]}>
        <Field label="Business hours start" value={form.business_start} editable={editable} style={{ flex: 1 }}
          onChangeText={(v) => setForm({ ...form, business_start: v })} placeholder="09:00" />
        <Field label="Business hours end" value={form.business_end} editable={editable} style={{ flex: 1 }}
          onChangeText={(v) => setForm({ ...form, business_end: v })} placeholder="18:00" />
      </View>
      <Text style={styles.label}>Business days</Text>
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {WEEKDAYS.map((label, d) => (
          <Chip key={label} label={label} active={form.business_days.includes(d)} disabled={!editable} onPress={() => toggleDay(d)} />
        ))}
      </View>
      <Field label="Digest time" value={form.digest_time} editable={editable} onChangeText={(v) => setForm({ ...form, digest_time: v })} placeholder="09:00" />
      <Text style={styles.label}>Weekly digest day</Text>
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {WEEKDAYS.map((label, d) => (
          <Chip key={label} label={label} active={form.digest_weekday === d} disabled={!editable} onPress={() => setForm({ ...form, digest_weekday: d })} />
        ))}
      </View>
      <Field label="Allowed external email domains" editable={editable}
        hint="Comma separated. Rules may email addresses outside the user list only at these domains. Empty means registered users only."
        value={form.external_email_domains} autoCapitalize="none"
        onChangeText={(v) => setForm({ ...form, external_email_domains: v })} />
      <Field label="Full re-check every (minutes)" editable={editable} value={form.sweep_minutes} keyboardType="number-pad"
        hint="Catches anything missed while the server was down, and drives time-based conditions."
        onChangeText={(v) => setForm({ ...form, sweep_minutes: v.replace(/[^0-9]/g, '') })} />
      <Text style={[styles.mono, { marginBottom: 10 }]}>Last full check: {when(form.last_sweep_at)}</Text>
      {editable && <Button title="Save settings" onPress={save} />}
    </Card>
  );
}

/* ---------------------------------- SMTP --------------------------------- */
function SmtpSettings({ user }) {
  const canManage = has(user, 'smtp_manage');
  const canTest = has(user, 'smtp_test');
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState(null);
  const [password, setPassword] = useState('');
  const [testTo, setTestTo] = useState('');
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const c = await api('/settings/smtp');
      setCfg(c);
      setForm({
        enabled: !!c.enabled, host: c.host || '', port: String(c.port || 587), security: c.security || 'starttls',
        auth_enabled: !!c.auth_enabled, username: c.username || '', sender_email: c.sender_email || '',
        sender_name: c.sender_name || '', reply_to: c.reply_to || '', timeout_seconds: String(c.timeout_seconds || 20),
        max_attempts: String(c.max_attempts || 6), rate_limit_per_minute: String(c.rate_limit_per_minute || 30),
      });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save(clearPassword = false) {
    setBusy(true);
    try {
      await api('/settings/smtp', {
        method: 'PUT',
        body: {
          ...form, port: Number(form.port), timeout_seconds: Number(form.timeout_seconds),
          max_attempts: Number(form.max_attempts), rate_limit_per_minute: Number(form.rate_limit_per_minute),
          password: password || null, clear_password: clearPassword,
        },
      });
      setPassword('');
      setMessage({ tone: 'success', text: 'SMTP settings saved. The password is stored encrypted and cannot be shown again.' });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  async function testConnection() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api('/settings/smtp/test-connection', { method: 'POST', timeout: 60000 });
      setResult({ kind: 'connection', ...r });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  async function sendTest() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api('/settings/smtp/send-test', { method: 'POST', body: { to_email: testTo }, timeout: 60000 });
      setResult({ kind: 'email', ...r });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setBusy(false);
  }

  if (!form) return message ? <Banner tone={message.tone} message={message.text} /> : null;
  const set = (patch) => setForm({ ...form, ...patch });
  const securityOptions = [['starttls', 'STARTTLS (587)'], ['ssl', 'SSL/TLS (465)']];
  if (cfg?.plaintext_allowed || form.security === 'none') securityOptions.push(['none', 'None (internal relay)']);

  return (
    <Card>
      <SectionTitle
        title="SMTP / email"
        description="Outgoing mail server. TLS certificates are always verified. Microsoft 365 needs SMTP AUTH enabled for the mailbox; OAuth-only tenants are not supported yet."
        right={<StatusPill status={cfg.enabled ? 'ACTIVE' : 'DISABLED'} />}
      />
      {message && <Banner tone={message.tone} message={message.text} />}
      <Toggle label="Send email" value={form.enabled} disabled={!canManage} onChange={(v) => set({ enabled: v })}
        hint="When off, emails queue up and nothing is lost; in-app alerts keep working." />
      <Field label="SMTP host" value={form.host} editable={canManage} autoCapitalize="none" onChangeText={(v) => set({ host: v.trim() })} placeholder="smtp.office365.com" />
      <Field label="Port" value={form.port} editable={canManage} keyboardType="number-pad" onChangeText={(v) => set({ port: v.replace(/[^0-9]/g, '') })} />
      <Text style={styles.label}>Encryption</Text>
      <View style={[styles.wrap, { marginBottom: 12 }]}>
        {securityOptions.map(([k, l]) => (
          <Chip key={k} label={l} active={form.security === k} disabled={!canManage}
            onPress={() => set({ security: k, port: k === 'ssl' ? '465' : form.port === '465' ? '587' : form.port })} />
        ))}
      </View>
      <Toggle label="Server requires authentication" value={form.auth_enabled} disabled={!canManage} onChange={(v) => set({ auth_enabled: v })} />
      {form.auth_enabled && (
        <>
          <Field label="Username" value={form.username} editable={canManage} autoCapitalize="none" onChangeText={(v) => set({ username: v.trim() })} />
          {canManage && (
            <Field
              label={cfg.password_set ? 'Password (stored. Leave empty to keep it)' : 'Password'}
              value={password} onChangeText={setPassword} secureTextEntry autoCapitalize="none"
              placeholder={cfg.password_set ? '••••••••' : ''}
            />
          )}
          {!canManage && <KeyValue label="Password" value={cfg.password_set ? 'Stored (hidden)' : 'Not set'} />}
        </>
      )}
      <Field label="Sender email" value={form.sender_email} editable={canManage} autoCapitalize="none" keyboardType="email-address" onChangeText={(v) => set({ sender_email: v.trim() })} />
      <Field label="Sender name" value={form.sender_name} editable={canManage} onChangeText={(v) => set({ sender_name: v })} />
      <Field label="Reply-To (optional)" value={form.reply_to} editable={canManage} autoCapitalize="none" keyboardType="email-address" onChangeText={(v) => set({ reply_to: v.trim() })} />
      <View style={[styles.row, styles.gap8]}>
        <Field label="Timeout (s)" value={form.timeout_seconds} editable={canManage} keyboardType="number-pad" style={{ flex: 1 }}
          onChangeText={(v) => set({ timeout_seconds: v.replace(/[^0-9]/g, '') })} />
        <Field label="Max attempts" value={form.max_attempts} editable={canManage} keyboardType="number-pad" style={{ flex: 1 }}
          onChangeText={(v) => set({ max_attempts: v.replace(/[^0-9]/g, '') })} />
        <Field label="Emails / minute" value={form.rate_limit_per_minute} editable={canManage} keyboardType="number-pad" style={{ flex: 1 }}
          onChangeText={(v) => set({ rate_limit_per_minute: v.replace(/[^0-9]/g, '') })} />
      </View>
      <Text style={[styles.mono, { marginBottom: 10 }]}>
        Last changed {when(cfg.updated_at)} by {cfg.updated_by || '—'} · Queue: {Object.entries(cfg.queue || {}).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(', ') || 'empty'}
      </Text>
      {canManage && (
        <View style={styles.wrap}>
          <Button title="Save SMTP settings" disabled={busy} onPress={() => save(false)} />
          {cfg.password_set && <Button title="Remove stored password" tone="ghost" disabled={busy} onPress={() => save(true)} />}
        </View>
      )}

      {canTest && (
        <View style={{ marginTop: 16 }}>
          <SectionTitle title="Test" description="Tests use the saved settings. Save first." />
          <Button title="Test connection" tone="secondary" disabled={busy} onPress={testConnection} />
          <Field label="Send a test email to" value={testTo} onChangeText={(v) => setTestTo(v.trim())} autoCapitalize="none"
            keyboardType="email-address" placeholder="you@himalaya-airlines.com" style={{ marginTop: 10 }} />
          <Button title="Send test email" tone="secondary" disabled={busy || !testTo} onPress={sendTest} />
          {result && (
            <Card tight style={{ backgroundColor: COLORS.surfaceAlt, marginTop: 12 }}>
              {result.kind === 'connection' ? (
                <>
                  <KeyValue label="Result" value={result.ok ? 'Connected' : 'Failed'} tone={result.ok ? COLORS.success : COLORS.danger} />
                  <KeyValue label="Stages reached" value={result.stages.join(' → ') || 'none'} />
                  <Text style={styles.mono}>{result.message}</Text>
                  <Text style={styles.mono}>Tested {when(result.tested_at)}. A successful connection does not send any email.</Text>
                </>
              ) : (
                <>
                  <KeyValue label="Job" value={`#${result.id}`} />
                  <KeyValue label="Status" value={result.status} tone={result.status === 'ACCEPTED' ? COLORS.success : COLORS.danger} />
                  {!!result.smtp_response && <KeyValue label="Server" value={result.smtp_response} />}
                  {!!result.message_id && <Text style={styles.mono}>Message-ID {result.message_id}</Text>}
                  {!!result.last_error && <Text style={[styles.mono, { color: COLORS.danger }]}>{result.last_error}</Text>}
                  <Text style={styles.mono}>{result.note}</Text>
                </>
              )}
            </Card>
          )}
        </View>
      )}
    </Card>
  );
}
