import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { api } from '../../api';
import { styles } from '../../theme';
import { Banner, Chip, PageHeader } from '../../ui';
import Alerts from './Alerts';
import Deliveries from './Deliveries';
import Escalation from './Escalation';
import Groups from './Groups';
import ItemAlerts from './ItemAlerts';
import Overview from './Overview';
import Rules from './Rules';
import Settings from './Settings';
import Templates from './Templates';
import { has } from './shared';

const TABS = [
  { key: 'overview', label: 'Overview', any: ['comms_view'] },
  { key: 'items', label: 'Stock alert settings', any: ['comms_view'] },
  { key: 'rules', label: 'Alert rules', any: ['comms_view'] },
  { key: 'alerts', label: 'Alerts', any: ['comms_view'] },
  { key: 'groups', label: 'Recipients & groups', any: ['comms_view'] },
  { key: 'escalation', label: 'Escalation', any: ['comms_view'] },
  { key: 'deliveries', label: 'Delivery log', any: ['comms_admin'] },
  { key: 'templates', label: 'Templates', any: ['comms_templates'] },
  { key: 'settings', label: 'Settings', any: ['comms_view', 'comms_admin', 'smtp_view'] },
];

/** Communication Manager: stock-alert rules, alerts, recipients and email. */
export default function CommsScreen({ user, online, initialAlertId, onHandled }) {
  const tabs = TABS.filter((t) => t.any.some((p) => has(user, p)));
  const [tab, setTab] = useState(initialAlertId ? 'alerts' : tabs[0]?.key);
  const [options, setOptions] = useState(null);
  const [error, setError] = useState(null);
  const [alertId, setAlertId] = useState(initialAlertId || null);

  useEffect(() => {
    if (initialAlertId) {
      setTab('alerts');
      setAlertId(initialAlertId);
      onHandled?.();
    }
  }, [initialAlertId, onHandled]);

  const loadOptions = useCallback(async () => {
    if (!has(user, 'comms_view') || !online) return;
    try {
      setOptions(await api('/communication/options'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [user, online]);

  useEffect(() => { loadOptions(); }, [loadOptions]);

  const props = { user, online, options, reloadOptions: loadOptions };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="Communication Manager"
        description="Stock-alert rules, recipients and email delivery. Products without a rule never send alerts."
      />
      {!online && <Banner tone="warning" message="The Communication Manager needs a connection." />}
      {!!error && <Banner tone="danger" message={error} />}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 14 }}>
        <View style={[styles.row, styles.gap8]}>
          {tabs.map((t) => (
            <Chip key={t.key} label={t.label} active={tab === t.key} onPress={() => setTab(t.key)} />
          ))}
        </View>
      </ScrollView>
      {online && tab === 'overview' && <Overview {...props} goTo={setTab} />}
      {online && tab === 'items' && <ItemAlerts {...props} />}
      {online && tab === 'rules' && <Rules {...props} />}
      {online && tab === 'alerts' && <Alerts {...props} focusId={alertId} clearFocus={() => setAlertId(null)} />}
      {online && tab === 'groups' && <Groups {...props} />}
      {online && tab === 'escalation' && <Escalation {...props} />}
      {online && tab === 'deliveries' && <Deliveries {...props} />}
      {online && tab === 'templates' && <Templates {...props} />}
      {online && tab === 'settings' && <Settings {...props} />}
    </ScrollView>
  );
}
