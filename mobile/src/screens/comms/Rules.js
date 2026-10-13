import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { confirm } from '../../dialog';
import { COLORS, styles } from '../../theme';
import { Banner, Button, Card, Chip, KeyValue, Notice, SearchBar, SectionTitle } from '../../ui';
import RuleEditor from './RuleEditor';
import SimulationResult from './SimulationResult';
import { FREQUENCY_LABELS, SeverityPill, StatusPill, has, recipientLabel, when } from './shared';

export default function Rules({ user, options, reloadOptions }) {
  const [rules, setRules] = useState([]);
  const [status, setStatus] = useState('ALL');
  const [term, setTerm] = useState('');
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null); // 'new' | rule
  const [simulation, setSimulation] = useState(null);
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [versions, setVersions] = useState(null);

  const load = useCallback(async () => {
    try {
      setRules(await api('/communication/rules'));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function open(rule) {
    try {
      setSelected(await api(`/communication/rules/${rule.id}`));
      setSimulation(null);
      setVersions(null);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function act(path, method = 'POST', success) {
    try {
      const result = await api(path, { method });
      setMessage({ tone: 'success', text: success });
      await load();
      if (method === 'DELETE') setSelected(null);
      else if (result?.id) setSelected(result);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  async function remove(rule) {
    const ok = await confirm({
      title: 'Delete rule?',
      message: `"${rule.name}" will be deleted. Rules with alert history cannot be deleted; disable them instead.`,
      confirmText: 'Delete',
      tone: 'danger',
    });
    if (ok) act(`/communication/rules/${rule.id}`, 'DELETE', 'Rule deleted.');
  }

  async function simulate(rule) {
    try {
      setSimulation(await api(`/communication/rules/${rule.id}/simulate`, { method: 'POST' }));
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (editing) {
    return (
      <RuleEditor
        rule={editing === 'new' ? null : editing}
        options={options}
        onCancel={() => setEditing(null)}
        onDone={(saved, text) => {
          setEditing(null);
          setMessage({ tone: 'success', text });
          setSelected(saved);
          load();
          reloadOptions?.();
        }}
      />
    );
  }

  if (selected) {
    const r = selected;
    return (
      <>
        {message && <Notice message={message} />}
        <Card>
          <SectionTitle title={r.name} description={r.description} right={<StatusPill status={r.status} />} />
          <KeyValue label="Owner" value={r.owner_name || '—'} />
          <KeyValue label="Products" value={r.scope_text} />
          <KeyValue label="Condition" value={r.condition_text} />
          <KeyValue label="Recovery margin" value={String(r.recovery_margin)} />
          <KeyValue label="Recipients" value={r.recipients.map((x) => recipientLabel(x, options)).join(', ') || '—'} />
          <KeyValue label="Channels" value={[r.channel_in_app && 'In-app', r.channel_email && 'Email'].filter(Boolean).join(' + ')} />
          <KeyValue label="Frequency" value={FREQUENCY_LABELS[r.frequency]} />
          <KeyValue label="Business hours only" value={r.business_hours_only ? 'Yes' : 'No'} />
          <KeyValue label="Escalation" value={(options?.policies || []).find((p) => p.id === r.escalation_policy_id)?.name || 'None'} />
          <KeyValue label="Exclusive" value={r.exclusive ? 'Yes' : 'No'} />
          <KeyValue label="Version" value={`${r.version} · updated ${when(r.updated_at)} by ${r.updated_by || '—'}`} />
          <View style={[styles.wrap, { marginTop: 12 }]}>
            <Button title="Back" tone="ghost" small onPress={() => setSelected(null)} />
            <Button title="Simulate" tone="secondary" small onPress={() => simulate(r)} />
            {r.can_edit && <Button title="Edit" small onPress={() => setEditing(r)} />}
            {has(user, 'comms_rules') && (
              <Button title="Duplicate" tone="secondary" small onPress={() => act(`/communication/rules/${r.id}/duplicate`, 'POST', 'Copied as a draft.')} />
            )}
            {r.can_edit && r.status !== 'ACTIVE' && (
              <Button title="Activate" tone="success" small onPress={() => act(`/communication/rules/${r.id}/activate`, 'POST', 'Rule activated.')} />
            )}
            {r.can_edit && r.status === 'ACTIVE' && (
              <Button title="Disable" tone="warning" small onPress={() => act(`/communication/rules/${r.id}/disable`, 'POST', 'Rule disabled. Its open alerts are resolved.')} />
            )}
            {r.can_edit && <Button title="Delete" tone="danger" small onPress={() => remove(r)} />}
          </View>
          {simulation && <SimulationResult result={simulation} />}
        </Card>
        <Card>
          <SectionTitle title="Version history"
            description="Every saved state of this rule. Each alert records the version that opened it."
            right={!versions && (
              <Button title="Show" tone="ghost" small onPress={async () => {
                try { setVersions(await api(`/communication/rules/${r.id}/versions`)); } catch (e) { setMessage({ tone: 'danger', text: e.message }); }
              }} />
            )} />
          {versions && versions.length === 0 && <Text style={styles.empty}>No versions recorded.</Text>}
          {versions && versions.map((v) => (
            <View key={v.version} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontWeight: '600', color: COLORS.text }}>v{v.version} · {v.action.replace(/_/g, ' ').toLowerCase()}</Text>
                <StatusPill status={v.status} />
              </View>
              <Text style={styles.mono}>{when(v.changed_at)} by {v.changed_by || '—'}</Text>
              <Text style={styles.mono}>{v.condition_text} · {v.scope_text} · {FREQUENCY_LABELS[v.frequency] || v.frequency}</Text>
              <Text style={styles.mono}>Recipients: {(v.recipients || []).map((x) => recipientLabel(x, options)).join(', ') || '—'}</Text>
            </View>
          ))}
        </Card>
        <Card>
          <SectionTitle title="Recent alerts from this rule" />
          {r.incidents.length === 0 && <Text style={styles.empty}>None yet.</Text>}
          {r.incidents.map((i) => (
            <View key={i.id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontWeight: '600', color: COLORS.text }}>#{i.id} {i.product_name}</Text>
                <View style={[styles.row, styles.gap8]}><SeverityPill severity={i.severity} /><StatusPill status={i.status} /></View>
              </View>
              <Text style={styles.mono}>Opened {when(i.opened_at)}{i.resolved_at ? ` · resolved ${when(i.resolved_at)}` : ''}</Text>
            </View>
          ))}
        </Card>
      </>
    );
  }

  const needle = term.trim().toLowerCase();
  const shown = rules.filter((r) => (status === 'ALL' || r.status === status)
    && (!needle || `${r.name} ${r.scope_text} ${r.owner_name}`.toLowerCase().includes(needle)));

  return (
    <>
      {message && <Notice message={message} />}
      <Card>
        <SectionTitle
          title={`Alert rules (${rules.length})`}
          description="Each rule names its products, its threshold and its recipients."
          right={has(user, 'comms_rules') ? <Button title="New rule" small onPress={() => setEditing('new')} disabled={!options} /> : null}
        />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {['ALL', 'ACTIVE', 'DRAFT', 'DISABLED'].map((s) => (
            <Chip key={s} label={s === 'ALL' ? 'All' : s[0] + s.slice(1).toLowerCase()} active={status === s} onPress={() => setStatus(s)} />
          ))}
        </View>
        <SearchBar value={term} onChangeText={setTerm} placeholder="Search rules" />
        {loading && <Text style={styles.empty}>Loading…</Text>}
        {!loading && shown.length === 0 && (
          <Text style={styles.empty}>{rules.length ? 'No rule matches.' : 'No rules yet. Without a rule, no stock alert is ever sent.'}</Text>
        )}
        {shown.map((r) => (
          <Pressable key={r.id} onPress={() => open(r)} style={styles.listRow} accessibilityRole="button" accessibilityLabel={`Rule ${r.name}`}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>{r.name}</Text>
              <StatusPill status={r.status} />
            </View>
            <Text style={styles.mono}>{r.scope_text}</Text>
            <Text style={styles.mono}>{r.condition_text} · {FREQUENCY_LABELS[r.frequency]}</Text>
            <Text style={styles.mono}>
              Owner {r.owner_name || '—'} · {r.recipients.length} recipient entr{r.recipients.length === 1 ? 'y' : 'ies'}
              {r.active_incidents ? ` · ${r.active_incidents} alert(s) open` : ''}
            </Text>
          </Pressable>
        ))}
      </Card>
    </>
  );
}
