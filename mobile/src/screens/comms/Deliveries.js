import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { usePaged } from '../../paging';
import { COLORS, styles } from '../../theme';
import { Banner, Button, Card, Chip, Notice, Pagination, SectionTitle } from '../../ui';
import { StatusPill, when } from './shared';

const STATUSES = [
  [null, 'All'], ['PENDING', 'Queued'], ['ACCEPTED', 'Accepted'], ['TEMP_FAILED', 'Retrying'],
  ['FAILED', 'Failed'], ['CANCELLED', 'Cancelled'],
];

/** Every queued email, for administrators. */
export default function Deliveries() {
  const [status, setStatus] = useState(null);
  const [message, setMessage] = useState(null);
  const data = usePaged(`/communication/deliveries${status ? `?status=${status}` : ''}`);
  const load = data.reload;

  async function act(job, action) {
    try {
      await api(`/communication/deliveries/${job.id}/${action}`, { method: 'POST' });
      setMessage({ tone: 'success', text: action === 'retry' ? `Delivery #${job.id} re-queued.` : `Delivery #${job.id} cancelled.` });
      load();
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  return (
    <>
      {message && <Notice message={message} />}
      <Card>
        <SectionTitle
          title={`Delivery log (${data.total})`}
          description="Accepted means the SMTP server took the message; it does not prove it reached the inbox. Failed jobs are kept, never dropped."
          right={<Button title="Refresh" tone="ghost" small onPress={load} />}
        />
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          {STATUSES.map(([key, label]) => (
            <Chip key={label} label={label} active={status === key} onPress={() => setStatus(key)} />
          ))}
        </View>
        {data.items.length === 0 && <Text style={styles.empty}>Nothing here.</Text>}
        {data.items.map((j) => (
          <View key={j.id} style={styles.listRow}>
            <View style={styles.rowBetween}>
              <Text style={{ fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>#{j.id} {j.subject}</Text>
              <StatusPill status={j.status} />
            </View>
            <Text style={styles.mono}>{j.kind} → {j.recipient_name ? `${j.recipient_name} <${j.recipient_email}>` : j.recipient_email}</Text>
            <Text style={styles.mono}>
              Queued {when(j.created_at)} · attempts {j.attempts}/{j.max_attempts}
              {j.accepted_at ? ` · accepted ${when(j.accepted_at)}` : ''}
              {['PENDING', 'TEMP_FAILED'].includes(j.status) ? ` · next try ${when(j.next_attempt_at)}` : ''}
              {j.incident_id ? ` · alert #${j.incident_id}` : ''}
            </Text>
            {!!j.last_error && <Text style={[styles.mono, { color: COLORS.danger }]}>{j.last_error}</Text>}
            <View style={[styles.row, styles.gap8, { marginTop: 6 }]}>
              {['FAILED', 'TEMP_FAILED', 'CANCELLED'].includes(j.status) && (
                <Button title="Retry" tone="secondary" small onPress={() => act(j, 'retry')} />
              )}
              {['PENDING', 'TEMP_FAILED'].includes(j.status) && (
                <Button title="Cancel" tone="ghost" small onPress={() => act(j, 'cancel')} />
              )}
            </View>
          </View>
        ))}
        {!!data.error && <Banner tone="danger" message={data.error} />}
        <Pagination pager={data} label="deliveries" />
      </Card>
    </>
  );
}
