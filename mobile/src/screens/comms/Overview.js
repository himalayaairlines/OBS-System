import React, { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { api } from '../../api';
import { BarChart, RankBarChart } from '../../charts';
import { COLORS, styles } from '../../theme';
import { Banner, Button, Card, Metric, SectionTitle } from '../../ui';
import { when } from './shared';

export default function Overview({ goTo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await api('/communication/dashboard'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  if (error) return <Banner tone="danger" message={error} />;
  if (!data) return <Text style={styles.empty}>Loading…</Text>;
  const a = data.alerts;
  const d = data.deliveries;

  return (
    <>
      {data.alerts_enabled === false && (
        <Banner tone="warning" message="Stock alerts are switched off for the whole system (Settings). No new alerts, reminders or escalations are raised." />
      )}
      {!data.smtp_enabled && (
        <Banner tone="warning" message="Outgoing email is switched off. In-app alerts still work, and emails wait in the queue until SMTP is enabled in Settings." />
      )}
      <Card>
        <SectionTitle title="Alerts now" right={<Button title="View alerts" tone="secondary" small onPress={() => goTo('alerts')} />} />
        <View style={[styles.wrap]}>
          <Metric label="Low stock" value={a.low} tone={a.low ? 'warning' : 'default'} width={120} />
          <Metric label="Critical" value={a.critical} tone={a.critical ? 'danger' : 'default'} width={120} />
          <Metric label="Out of stock" value={a.out} tone={a.out ? 'danger' : 'default'} width={120} />
          <Metric label="Unacknowledged" value={a.unacknowledged} tone={a.unacknowledged ? 'danger' : 'default'} width={120} />
          <Metric label="Escalated" value={a.escalated} width={120} />
          {a.snoozed !== undefined && <Metric label="Snoozed" value={a.snoozed} width={120} />}
          <Metric label="Awaiting escalation" value={a.awaiting_escalation} width={120} />
          <Metric label="Resolved (30 days)" value={a.resolved_30d} tone="success" width={120} />
        </View>
      </Card>

      <Card>
        <SectionTitle title="Rules" right={(
          <View style={[styles.row, styles.gap8]}>
            <Button title="Stock alert settings" tone="secondary" small onPress={() => goTo('items')} />
            <Button title="Manage rules" tone="secondary" small onPress={() => goTo('rules')} />
          </View>
        )} />
        <View style={styles.wrap}>
          <Metric label="Active" value={data.rules.active} tone="success" width={100} />
          <Metric label="Draft" value={data.rules.draft} width={100} />
          <Metric label="Disabled" value={data.rules.disabled} width={100} />
          {data.item_overrides !== undefined && <Metric label="Item overrides" value={data.item_overrides} width={100} />}
          {data.items_disabled !== undefined && <Metric label="Items switched off" value={data.items_disabled} width={100} />}
        </View>
      </Card>

      <Card>
        <SectionTitle
          title="Email delivery"
          description="Accepted means the mail server took the message. It does not confirm it reached an inbox."
        />
        <View style={styles.wrap}>
          <Metric label="Queued" value={d.pending} width={100} />
          <Metric label="Accepted" value={d.accepted} tone="success" width={100} />
          <Metric label="Retrying" value={d.retrying} tone={d.retrying ? 'warning' : 'default'} width={100} />
          <Metric label="Failed" value={d.failed} tone={d.failed ? 'danger' : 'default'} width={100} />
        </View>
      </Card>

      <Card>
        <SectionTitle title="Alerts opened, last 14 days" description="By UTC date." />
        <BarChart
          data={data.trend.map((t) => ({ label: t.date.slice(8), value: t.opened }))}
          format={(v) => String(Math.round(v))}
        />
      </Card>

      <Card>
        <SectionTitle title="Active alerts by category" />
        <RankBarChart
          data={data.by_category.map((c) => ({ label: c.category, value: c.count }))}
          format={(v) => String(Math.round(v))}
        />
      </Card>

      <Text style={[styles.mono, { textAlign: 'center', color: COLORS.faint }]}>
        Last full check: {when(data.last_sweep_at)} · Timezone {data.timezone}
      </Text>
    </>
  );
}
