/**
 * SMTP Settings: the outgoing mail server (used by stock alerts and by the
 * security e-mails: sign-in codes, reset links, invitations) and the
 * application address those e-mails link to.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView } from 'react-native';

import { api } from '../api';
import { Columns } from '../layout';
import { styles } from '../theme';
import { Text } from '../text';
import { Banner, Button, Card, Field, Notice, PageHeader, SectionTitle } from '../ui';
import { SmtpSettings } from './comms/Settings';
import { has } from './comms/shared';

export default function SmtpScreen({ user, online }) {
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="SMTP Settings"
        description="The mail server used for stock alerts and for security e-mails: sign-in codes, password reset links and invitations."
      />
      {!online && <Banner tone="warning" message="SMTP settings need a connection." />}
      {online && (
        <Columns flex={[3, 2]}>
          <SmtpSettings user={user} />
          {has(user, 'users') && <AppAddress />}
        </Columns>
      )}
    </ScrollView>
  );
}

/** The public address put into reset and invitation links. */
function AppAddress() {
  const [settings, setSettings] = useState(null);
  const [value, setValue] = useState('');
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const s = await api('/security/settings');
      setSettings(s);
      setValue(s.app_url || '');
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save() {
    try {
      const { app_url_from_environment: _, ...rest } = settings;
      const s = await api('/security/settings', { method: 'PUT', body: { ...rest, app_url: value.trim() } });
      setSettings(s);
      setValue(s.app_url || '');
      setMessage({ tone: 'success', text: 'Application address saved.' });
    } catch (e) {
      setMessage({ tone: 'danger', text: e.message });
    }
  }

  if (!settings) return message ? <Notice message={message} /> : null;
  const fromEnv = settings.app_url_from_environment;

  return (
    <Card>
      <SectionTitle
        title="Application address"
        description="The web address people open from reset and invitation e-mails, e.g. https://obs.himalaya-airlines.com. Links are never built from the address a request arrived on."
      />
      {message && <Notice message={message} />}
      {fromEnv ? (
        <>
          <Banner tone="info" message={`Set by the server (OBS_APP_URL): ${fromEnv}`} />
          <Text style={styles.mono}>Change OBS_APP_URL in the server environment to change it.</Text>
        </>
      ) : (
        <>
          {!settings.app_url && (
            <Banner tone="warning" message="Not set: password reset and invitation e-mails cannot be sent until it is." />
          )}
          <Field
            label="Address"
            value={value}
            onChangeText={setValue}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            placeholder="https://obs.himalaya-airlines.com"
          />
          <Button title="Save address" onPress={save} disabled={value.trim() === (settings.app_url || '')} />
        </>
      )}
    </Card>
  );
}
