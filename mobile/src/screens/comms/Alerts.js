import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { usePaged } from '../../paging';
import { COLORS, ROLE_LABELS, styles } from '../../theme';
import { Banner, Button, Card, Chip, Field, KeyValue, Notice, OptionModal, Pagination, SectionTitle } from '../../ui';
import { SeverityPill, StatusPill, qty, when } from './shared';

export default function Alerts({ options, focusId, clearFocus }) {
  const [status, setStatus] = useState('active');
  const [severity, setSeverity] = useState(null);
  const [category, setCategory] = useState(null);
  const [detail, setDetail] = useState(null);
  const [text, setText] = useState('');
  const [picker, setPicker] = useState(null);
  const [message, setMessage] = useState(null);
  const params = new URLSearchParams({ status });
  if (severity) params.set('severity', severity);
  if (category) params.set('category', category);
  const data = usePaged(`/communication/alerts?${params}`);
  const { loading } = data;
  const load = data.reload;

  const open = useCallback(async (id) => {
    try {
      setDetail(await api(`/communication/alerts/${id}`));
      setText('');
      setMessage(null);
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => {
    if (focusId) { open(focusId); clearFocus?.(); }
  }, [focusId, open, clearFocus]);

  async function act(path, body, success) {
    try {
      setDetail(await api(`/communication/alerts/${detail.id}/${path}`, { method: 'POST', body }));
      setText('');
      setMessage({ tone: 'success', text: success });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (detail) {
    const d = detail;
    const live = !['RESOLVED'].includes(d.status);
    return (
      <>
        {message && <Notice message={message} />}
        <Card>
          <SectionTitle
            title={`#${d.id} ${d.product_name}`}
            description={`${d.product_code} · ${d.category} · rule “${d.rule_name}”`}
            right={<View style={[styles.row, styles.gap8]}><SeverityPill severity={d.severity} /><StatusPill status={d.status} /></View>}
          />
          <KeyValue label="Warehouse stock now" value={`${qty(d.current_quantity)} ${d.unit || 'pcs'}`}
            tone={Number(d.current_quantity) <= 0 ? COLORS.danger : undefined} />
          <KeyValue label="Threshold when opened" value={qty(d.threshold_at_open)} />
          <KeyValue label="Reorder level" value={qty(d.reorder_level)} />
          <KeyValue label="Opened" value={when(d.opened_at)} />
          {!!d.acknowledged_at && <KeyValue label="Acknowledged" value={`${when(d.acknowledged_at)} by ${d.acknowledged_by}`} />}
          {!!d.assigned_to_name && <KeyValue label="Assigned to" value={d.assigned_to_name} />}
          {!!d.escalation_step && <KeyValue label="Escalation step" value={String(d.escalation_step)} />}
          {!!d.snoozed_until && new Date(d.snoozed_until) > new Date() && (
            <KeyValue label="Snoozed until" value={`${when(d.snoozed_until)} by ${d.snoozed_by} · ${d.snooze_reason || ''}`} />
          )}
          {!!d.rule_version && <KeyValue label="Rule version at opening" value={`v${d.rule_version}`} />}
          {!!d.resolved_at && <KeyValue label="Resolved" value={`${when(d.resolved_at)} · ${d.resolution || ''}`} />}
          <Text style={[styles.mono, { marginTop: 6 }]}>
            Acknowledging means someone is handling it. The alert resolves by itself when stock recovers.
          </Text>
          {d.can_act && live && (
            <View style={{ marginTop: 12 }}>
              <Field label="Comment or remarks" value={text} onChangeText={setText} multiline placeholder="What is being done?" />
              <View style={styles.wrap}>
                {['OPEN', 'ESCALATED'].includes(d.status) && (
                  <Button title="Acknowledge" tone="success" small onPress={() => act('acknowledge', { comment: text || null }, 'Acknowledged.')} />
                )}
                <Button title="Add comment" tone="secondary" small disabled={!text.trim()} onPress={() => act('comment', { comment: text }, 'Comment added.')} />
                <Button title="Assign" tone="secondary" small onPress={() => setPicker('assign')} />
                {['OPEN', 'ACKNOWLEDGED', 'ESCALATED'].includes(d.status)
                  && !(d.snoozed_until && new Date(d.snoozed_until) > new Date()) && [4, 24].map((h) => (
                  <Button key={h} title={`Snooze ${h} h`} tone="secondary" small disabled={text.trim().length < 3}
                    onPress={() => act('snooze', { hours: h, reason: text.trim() }, `Snoozed for ${h} hours. Reminders and escalation pause; a worse stock level ends the snooze.`)} />
                ))}
                {!!d.snoozed_until && new Date(d.snoozed_until) > new Date() && (
                  <Button title="End snooze" tone="secondary" small onPress={() => act('unsnooze', undefined, 'Snooze ended.')} />
                )}
                {d.status !== 'SUPPRESSED' && (
                  <Button title="Suppress" tone="warning" small disabled={text.trim().length < 3}
                    onPress={() => act('suppress', { remarks: text }, 'Suppressed. No more messages until stock recovers.')} />
                )}
              </View>
              <Text style={[styles.mono, { marginTop: 6 }]}>
                Snooze and suppress need a reason in the box. Snooze pauses reminders and escalation for a while; suppress stops all further messages for this alert.
              </Text>
            </View>
          )}
          {!d.can_act && live && (
            <Text style={[styles.mono, { marginTop: 8 }]}>Only the rule owner, its recipients or the assignee can act on this alert.</Text>
          )}
          <Button title="Back to alerts" tone="ghost" small style={{ marginTop: 12, alignSelf: 'flex-start' }} onPress={() => setDetail(null)} />
        </Card>

        <Card>
          <SectionTitle title="Timeline" />
          {d.events.map((e) => (
            <View key={e.id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontWeight: '600', color: COLORS.text }}>{e.event_type.replace('_', ' ')}</Text>
                <Text style={styles.mono}>{when(e.created_at)}</Text>
              </View>
              <Text style={styles.mono}>
                {e.actor}{e.quantity != null ? ` · stock ${qty(e.quantity)}` : ''}{e.detail ? ` · ${e.detail}` : ''}
              </Text>
            </View>
          ))}
        </Card>

        <Card>
          <SectionTitle title="Deliveries" description={`${d.in_app_notifications} in-app notification(s). Accepted means the mail server took the email.`} />
          {d.deliveries.length === 0 && <Text style={styles.empty}>No emails for this alert.</Text>}
          {d.deliveries.map((j) => (
            <View key={j.id} style={styles.listRow}>
              <View style={styles.rowBetween}>
                <Text style={{ fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>{j.kind} → {j.recipient_name || j.recipient_email}</Text>
                <StatusPill status={j.status} />
              </View>
              <Text style={styles.mono}>{j.recipient_email} · {j.attempts} attempt(s) · {when(j.accepted_at || j.created_at)}</Text>
              {!!j.last_error && <Text style={[styles.mono, { color: COLORS.danger }]}>{j.last_error}</Text>}
            </View>
          ))}
        </Card>

        <Card>
          <SectionTitle title="Recent stock movements" />
          {d.movements.map((m, index) => (
            <View key={`${m.created_at}-${index}`} style={[styles.rowBetween, { paddingVertical: 4 }]}>
              <Text style={styles.mono}>{when(m.created_at)} · {m.reason}{m.reference ? ` · ${m.reference}` : ''}</Text>
              <Text style={{ fontWeight: '600', color: m.change_qty < 0 ? COLORS.danger : COLORS.success }}>
                {m.change_qty > 0 ? '+' : ''}{m.change_qty} → {m.balance_after}
              </Text>
            </View>
          ))}
        </Card>

        <OptionModal visible={picker === 'assign'} title="Assign to" searchable
          options={(options?.users || []).filter((u) => u.can_receive).map((u) => ({ value: u.id, label: `${u.name} (${u.employee_id})`, sub: ROLE_LABELS[u.role] }))}
          selectedValue={d.assigned_to_user_id}
          onSelect={(v) => { setPicker(null); act('assign', { user_id: v }, 'Assigned.'); }}
          onClose={() => setPicker(null)} />
      </>
    );
  }

  return (
    <>
      {message && <Notice message={message} />}
      <Card>
        <SectionTitle title={`Alerts (${data.total})`} description="Raised only by active rules. Sorted by severity." />
        <View style={[styles.wrap, { marginBottom: 8 }]}>
          {[['active', 'Active'], ['resolved', 'Resolved'], ['all', 'All']].map(([k, l]) => (
            <Chip key={k} label={l} active={status === k} onPress={() => setStatus(k)} />
          ))}
        </View>
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {[['LOW', 'Low'], ['CRITICAL', 'Critical'], ['OUT', 'Out of stock']].map(([k, l]) => (
            <Chip key={k} label={l} tone={k === 'LOW' ? 'primary' : 'danger'} active={severity === k} onPress={() => setSeverity(severity === k ? null : k)} />
          ))}
          <Chip label={category || 'Any category'} active={!!category} onPress={() => setPicker('category')} />
        </View>
        {loading && <Text style={styles.empty}>Loading…</Text>}
        {!loading && data.items.length === 0 && <Text style={styles.empty}>No alerts here.</Text>}
        {data.items.map((i) => (
          <Pressable key={i.id} onPress={() => open(i.id)} style={styles.listRow} accessibilityRole="button"
            accessibilityLabel={`Alert ${i.id}, ${i.product_name}, ${i.severity}, ${i.status}`}>
            <View style={styles.rowBetween}>
              <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>{i.product_name}</Text>
              <View style={[styles.row, styles.gap8]}><SeverityPill severity={i.severity} /><StatusPill status={i.status} /></View>
            </View>
            <Text style={styles.mono}>
              Stock {qty(i.current_quantity)} {i.unit || 'pcs'} · threshold {qty(i.threshold_at_open)} · {i.category}
            </Text>
            <Text style={styles.mono}>#{i.id} · {i.rule_name} · opened {when(i.opened_at)}{i.assigned_to_name ? ` · ${i.assigned_to_name}` : ''}</Text>
          </Pressable>
        ))}
        {!!data.error && <Banner tone="danger" message={data.error} />}
        <Pagination pager={data} label="alerts" />
      </Card>
      <OptionModal visible={picker === 'category'} title="Category"
        options={[{ value: null, label: 'Any category' }, ...(options?.categories || []).map((c) => ({ value: c, label: c }))]}
        selectedValue={category} onSelect={(v) => { setCategory(v); setPicker(null); }} onClose={() => setPicker(null)} />
    </>
  );
}
