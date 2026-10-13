import React from 'react';
import { View } from 'react-native';
import { Text } from '../../text';

import { COLORS, styles } from '../../theme';
import { Banner, Pill } from '../../ui';
import { SeverityPill, qty } from './shared';

/** Read-only result of /communication/rules/.../simulate. Nothing was sent. */
export default function SimulationResult({ result }) {
  return (
    <View style={{ marginTop: 12 }}>
      <Banner tone="info" message={result.note} />
      <Text style={{ fontWeight: '600', color: COLORS.text, marginBottom: 6 }}>
        {result.matched} product(s) covered · {result.breached} below threshold now · {result.would_notify} would notify
      </Text>
      {result.items.length === 0 && <Text style={styles.mono}>This rule does not cover any active product.</Text>}
      {result.items.map((item) => (
        <View key={item.product_id} style={styles.listRow}>
          <View style={styles.rowBetween}>
            <Text style={{ fontWeight: '600', color: COLORS.text, flexShrink: 1 }}>{item.product_name}</Text>
            {item.breached ? <SeverityPill severity={item.severity} /> : <Pill label="OK" tone="success" />}
          </View>
          <Text style={styles.mono}>
            Stock {qty(item.quantity)} {item.unit} · threshold {qty(item.threshold)}
            {item.critical_threshold != null ? ` · critical ${qty(item.critical_threshold)}` : ''}
            {' · '}matched by {item.matched_by.toLowerCase()}
          </Text>
          {!!item.overridden_by && (
            <Text style={[styles.mono, { color: COLORS.warning }]}>Not checked here: exclusive rule “{item.overridden_by}” covers it.</Text>
          )}
          {!!item.existing_incident_id && (
            <Text style={styles.mono}>Already alerting (incident #{item.existing_incident_id}), so no new message.</Text>
          )}
        </View>
      ))}
      {['LOW', 'CRITICAL'].map((level) => (
        <View key={level} style={{ marginTop: 8 }}>
          <Text style={{ fontWeight: '600', color: COLORS.text }}>
            {level === 'LOW' ? 'Low alerts would go to' : 'Critical alerts would go to'}
          </Text>
          {result.recipients[level].recipients.length === 0 && <Text style={styles.mono}>Nobody</Text>}
          {result.recipients[level].recipients.map((p) => (
            <Text key={p.key} style={styles.mono}>• {p.name}{p.email ? '' : ' (in-app only)'}</Text>
          ))}
          {result.recipients[level].excluded.map((x) => (
            <Text key={`${x.who}-${x.via}`} style={[styles.mono, { color: COLORS.danger }]}>✕ {x.who}: {x.reason}</Text>
          ))}
        </View>
      ))}
    </View>
  );
}
