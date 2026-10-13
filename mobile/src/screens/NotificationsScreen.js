import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { Text } from '../text';

import { api } from '../api';
import { usePaged } from '../paging';
import { COLORS, styles } from '../theme';
import { Banner, Button, Card, Chip, PageHeader, Pagination } from '../ui';
import { Dot, SeverityPill, StatusPill, has, when } from './comms/shared';

/** The signed-in user's own in-app notifications. */
export default function NotificationsScreen({ user, online, openAlert, onChange }) {
  const [filter, setFilter] = useState('all');
  const [severity, setSeverity] = useState(null);
  const [message, setMessage] = useState(null);
  const params = new URLSearchParams();
  if (filter === 'unread') params.set('unread_only', 'true');
  if (severity) params.set('severity', severity);
  const pager = usePaged(`/notifications${params.toString() ? `?${params}` : ''}`, { enabled: online });
  const data = { items: pager.items, total: pager.total, unread: pager.raw.unread || 0 };
  const { loading } = pager;
  const load = pager.reload;

  async function markRead(item) {
    if (item.read_at) return;
    try {
      await api(`/notifications/${item.id}/read`, { method: 'POST' });
      onChange?.();
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  async function markAll() {
    try {
      await api('/notifications/read-all', { method: 'POST' });
      onChange?.();
      load();
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  const canOpen = has(user, 'comms_view');

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Notifications"
        description="Stock alerts sent to you. Only messages addressed to you appear here."
        right={data.unread > 0 ? <Button title="Mark all read" tone="secondary" small onPress={markAll} /> : null}
      />
      {!online && <Banner tone="warning" message="Notifications need a connection." />}
      {message && <Banner tone={message.tone} message={message.text} />}

      <View style={[styles.wrap, { marginBottom: 12 }]}>
        <Chip label="All" active={filter === 'all'} onPress={() => setFilter('all')} />
        <Chip label={`Unread (${data.unread})`} active={filter === 'unread'} onPress={() => setFilter('unread')} />
        <Chip label="Low" active={severity === 'LOW'} onPress={() => setSeverity(severity === 'LOW' ? null : 'LOW')} />
        <Chip label="Critical" active={severity === 'CRITICAL'} tone="danger"
          onPress={() => setSeverity(severity === 'CRITICAL' ? null : 'CRITICAL')} />
        <Chip label="Out of stock" active={severity === 'OUT'} tone="danger"
          onPress={() => setSeverity(severity === 'OUT' ? null : 'OUT')} />
      </View>

      <Card>
        {loading && <Text style={styles.empty}>Loading…</Text>}
        {!loading && data.items.length === 0 && (
          <Text style={styles.empty}>
            {filter === 'unread' ? 'Nothing unread.' : 'No notifications yet. You only receive alerts from rules that name you.'}
          </Text>
        )}
        {data.items.map((item) => (
          <Pressable
            key={item.id}
            onPress={() => markRead(item)}
            style={[styles.listRow, { backgroundColor: item.read_at ? 'transparent' : COLORS.primarySoft }]}
            accessibilityRole="button"
            accessibilityLabel={`${item.title}${item.read_at ? '' : ', unread'}`}
          >
            <View style={styles.rowBetween}>
              <View style={[styles.row, styles.gap8, styles.flex1]}>
                {!item.read_at && <Dot color={COLORS.info} />}
                <Text style={{ fontSize: 14, fontWeight: item.read_at ? '500' : '700', color: COLORS.text, flexShrink: 1 }}>
                  {item.title}
                </Text>
              </View>
              <SeverityPill severity={item.severity} />
            </View>
            <Text style={[styles.mono, { marginTop: 4 }]}>{item.body}</Text>
            <View style={[styles.rowBetween, { marginTop: 6 }]}>
              <Text style={styles.mono}>{when(item.created_at)}</Text>
              <View style={[styles.row, styles.gap8]}>
                {!!item.incident_status && <StatusPill status={item.incident_status} />}
                {canOpen && !!item.incident_id && (
                  <Button title="Open alert" tone="ghost" small
                    onPress={() => { markRead(item); openAlert?.(item.incident_id); }} />
                )}
              </View>
            </View>
          </Pressable>
        ))}
        {!!pager.error && <Banner tone="danger" message={pager.error} />}
        <Pagination pager={pager} label="notifications" />
      </Card>
    </ScrollView>
  );
}
