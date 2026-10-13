import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { COLORS, styles } from '../../theme';
import { Banner, Button, Card, Chip, Field, Notice, SectionTitle } from '../../ui';
import { when } from './shared';

const NAMES = {
  low_stock: 'Low stock', critical_stock: 'Critical stock', out_of_stock: 'Out of stock', reminder: 'Reminder',
  escalation: 'Escalation', recovery: 'Recovery', digest: 'Digest', smtp_test: 'SMTP test',
};

/** Editable, versioned email templates with a whitelist of placeholders. */
export default function Templates() {
  const [data, setData] = useState({ templates: [], placeholders: [] });
  const [key, setKey] = useState('low_stock');
  const [form, setForm] = useState(null);
  const [preview, setPreview] = useState(null);
  const [versions, setVersions] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await api('/communication/templates');
      setData(result);
      return result;
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
      return null;
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const current = data.templates.find((t) => t.key === key);
  useEffect(() => {
    if (current) setForm({ subject: current.subject, body: current.body });
    setPreview(null);
    setVersions(null);
  }, [key, current?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    try {
      const result = await api(`/communication/templates/${key}`, { method: 'PUT', body: form });
      setMessage({ tone: 'success', text: `Saved as version ${result.version}.` });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function runPreview() {
    try {
      setPreview(await api(`/communication/templates/${key}/preview`, { method: 'POST', body: form }));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function showVersions() {
    try {
      setVersions(await api(`/communication/templates/${key}/versions`));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function restore(version) {
    try {
      await api(`/communication/templates/${key}/restore/${version}`, { method: 'POST' });
      setMessage({ tone: 'success', text: `Version ${version} restored as a new version.` });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <>
      {message && <Notice message={message} />}
      <Card>
        <SectionTitle title="Email templates" description="Plain text. Values are inserted as text and escaped in the HTML copy; nothing in a template is executed." />
        <View style={[styles.wrap, { marginBottom: 12 }]}>
          {data.templates.map((t) => (
            <Chip key={t.key} label={NAMES[t.key] || t.key} active={key === t.key} onPress={() => setKey(t.key)} />
          ))}
        </View>
        {form && (
          <>
            <Field label="Subject" value={form.subject} onChangeText={(v) => setForm({ ...form, subject: v.replace(/[\r\n]/g, ' ') })} />
            <Field label="Body" value={form.body} onChangeText={(v) => setForm({ ...form, body: v })} multiline style={{ minHeight: 200 }} />
            <Text style={styles.label}>Placeholders</Text>
            <Text style={[styles.mono, { marginBottom: 10 }]}>{data.placeholders.map((p) => `{{${p}}}`).join('  ')}</Text>
            <Text style={[styles.mono, { marginBottom: 10 }]}>
              Version {current?.version} · {current?.updated_by || 'SYSTEM'}{current?.updated_at ? ` · ${when(current.updated_at)}` : ''}
            </Text>
            <View style={styles.wrap}>
              <Button title="Preview" tone="secondary" small onPress={runPreview} />
              <Button title="Save new version" small onPress={save} disabled={!form.subject.trim() || !form.body.trim()} />
              <Button title="History" tone="ghost" small onPress={showVersions} />
            </View>
          </>
        )}
        {preview && (
          <Card tight style={{ backgroundColor: COLORS.surfaceAlt, marginTop: 12 }}>
            {preview.unknown_placeholders.length > 0 && (
              <Banner tone="danger" message={`Unknown placeholders: ${preview.unknown_placeholders.join(', ')}. Saving will be refused.`} />
            )}
            <Text style={{ fontWeight: '600', color: COLORS.text }}>{preview.subject}</Text>
            <Text style={[styles.mono, { marginTop: 6 }]}>{preview.body}</Text>
          </Card>
        )}
        {versions && versions.map((v) => (
          <View key={v.version} style={[styles.listRow, styles.rowBetween]}>
            <Text style={styles.mono}>v{v.version} · {v.updated_by} · {when(v.updated_at)}</Text>
            {v.version !== current?.version && <Button title="Restore" tone="ghost" small onPress={() => restore(v.version)} />}
          </View>
        ))}
      </Card>
    </>
  );
}
