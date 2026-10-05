import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Modal, Platform, Pressable, SafeAreaView, ScrollView, StatusBar, Text, View,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';

import {
  API_BASE, KEYS, api, checkBackendVersion, flushAll, readQueue,
  readReconQueue, setToken,
} from './src/api';
import { notify } from './src/dialog';
import { COLORS, ROLE_LABELS, initials, styles } from './src/theme';
import { Banner, Button, Card, DialogHost, Field, Pill } from './src/ui';

import AdminScreen from './src/screens/AdminScreen';
import CrewScreen from './src/screens/CrewScreen';
import DashboardScreen from './src/screens/DashboardScreen';
import FinanceScreen from './src/screens/FinanceScreen';
import InventoryScreen from './src/screens/InventoryScreen';
import LogsScreen from './src/screens/LogsScreen';
import ReportsScreen from './src/screens/ReportsScreen';
import SyncScreen from './src/screens/SyncScreen';
import WarehouseScreen from './src/screens/WarehouseScreen';

/** Navigation is derived from the server's permission matrix, not hardcoded. */
const NAV = [
  { key: 'crew', label: 'Sales', permission: 'crew' },
  { key: 'inventory', label: 'Inventory', permission: 'inventory' },
  { key: 'warehouse', label: 'Warehouse', permission: 'warehouse' },
  { key: 'dashboard', label: 'Dashboard', permission: 'dashboard' },
  { key: 'sync', label: 'Sync', permission: 'sync' },
  // Management see the whole screen; the inventory supervisor reaches it for
  // the sales ledger alone, so the tab is labelled for what they actually get.
  { key: 'logs', label: 'Logs', permission: 'sales_view', fullPermission: 'logs', altLabel: 'Sales' },
  { key: 'rates', label: 'Rates', permission: 'rates' },
  // Crew file the post-flight cabin reports here; management read and correct them.
  { key: 'reports', label: 'Reports', permission: 'flight_reports' },
  { key: 'admin', label: 'Admin', permission: 'users' },
];

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  const [page, setPage] = useState('crew');
  const [online, setOnline] = useState(true);
  const [queueCount, setQueueCount] = useState(0);
  const [profileOpen, setProfileOpen] = useState(false);
  const [versionWarning, setVersionWarning] = useState(null);
  const [syncNotice, setSyncNotice] = useState(null);
  // Set when a sector is finished, so the crew land on that sector's reports
  // instead of having to find it again. Declared with the other state: every
  // hook must run on every render, and there are early returns further down.
  const [reportsFlightId, setReportsFlightId] = useState(null);
  const autoSyncBusy = useRef(false);

  /* --------------------------- session --------------------------- */
  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(KEYS.session);
        if (raw) {
          const session = JSON.parse(raw);
          setToken(session.token);
          // Confirm the token is still valid; fall back to it offline.
          try {
            const me = await api('/me');
            applyUser({ ...session, ...me });
          } catch (error) {
            if (error.permanent) await AsyncStorage.removeItem(KEYS.session);
            else applyUser(session);
          }
        }
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  function applyUser(session) {
    setUser(session);
    setToken(session.token);
    checkBackendVersion().then(setVersionWarning);
    const first = NAV.find((item) => session.permissions?.includes(item.permission));
    setPage(first?.key || 'sync');
  }

  // Re-check periodically so the warning clears by itself once the server is
  // restarted, instead of lingering until the next sign-in.
  useEffect(() => {
    if (!user) return undefined;
    const recheck = () => checkBackendVersion().then(setVersionWarning);
    const timer = setInterval(recheck, 20000);
    return () => clearInterval(timer);
  }, [user]);



  /* -------------------------- connectivity ------------------------ */
  const refreshQueue = useCallback(async () => {
    const [sales, recon] = await Promise.all([readQueue(), readReconQueue()]);
    setQueueCount(sales.length + recon.length);
  }, []);

  useEffect(() => { refreshQueue(); }, [refreshQueue]);

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => {
      setOnline(Boolean(state.isConnected && state.isInternetReachable !== false));
    });
    // A web browser reports connectivity through window events instead.
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      const goOnline = () => setOnline(true);
      const goOffline = () => setOnline(false);
      window.addEventListener('online', goOnline);
      window.addEventListener('offline', goOffline);
      return () => {
        unsubscribe();
        window.removeEventListener('online', goOnline);
        window.removeEventListener('offline', goOffline);
      };
    }
    NetInfo.fetch().then((state) => {
      setOnline(Boolean(state.isConnected && state.isInternetReachable !== false));
    });
    return unsubscribe;
  }, []);

  // Queued sales upload themselves. This runs on a short timer rather than only
  // on connectivity events, because the network can report "connected" while the
  // server is still unreachable, and because a device can come back online
  // without NetInfo firing.
  const autoSync = useCallback(async () => {
    if (autoSyncBusy.current || !user) return;
    const [queue, reconQueue] = await Promise.all([readQueue(), readReconQueue()]);
    if (!queue.length && !reconQueue.length) return;
    autoSyncBusy.current = true;
    try {
      const result = await flushAll();
      await refreshQueue();
      const parts = [];
      if (result.accepted) parts.push(`${result.accepted} sale(s)`);
      if (result.reconciliations) parts.push(`${result.reconciliations} stock count(s)`);
      if (parts.length) {
        setSyncNotice(`Uploaded ${parts.join(' and ')}.`);
        setTimeout(() => setSyncNotice(null), 6000);
      }
    } catch {
      // Still offline or the server is down; the next tick will try again.
    } finally {
      autoSyncBusy.current = false;
    }
  }, [user, refreshQueue]);

  useEffect(() => {
    if (!user) return undefined;
    const timer = setInterval(autoSync, 12000);
    return () => clearInterval(timer);
  }, [user, autoSync]);

  // Upload the moment the connection comes back rather than waiting for a tick.
  useEffect(() => {
    if (online && user) autoSync();
  }, [online, user, autoSync]);

  async function logout() {
    try { await api('/logout', { method: 'POST' }); } catch { /* offline logout is fine */ }
    await AsyncStorage.removeItem(KEYS.session);
    setToken(null);
    setUser(null);
    setProfileOpen(false);
  }

  if (booting) {
    return (
      <SafeAreaView style={[styles.screen, { justifyContent: 'center' }]}>
        <Text style={styles.empty}>Starting…</Text>
      </SafeAreaView>
    );
  }

  if (!user) {
    return (
      <>
        <DialogHost />
        <LoginScreen onLogin={applyUser} />
      </>
    );
  }

  const visible = NAV
    .filter((item) => user.permissions?.includes(item.permission))
    .map((item) => (
      item.fullPermission && !user.permissions?.includes(item.fullPermission)
        ? { ...item, label: item.altLabel }
        : item
    ));
  const goToReports = (flightId) => {
    setReportsFlightId(flightId || null);
    setPage('reports');
  };

  const screenProps = { user, online, onQueueChange: refreshQueue, goToReports };

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar barStyle="light-content" backgroundColor={COLORS.primaryDark} />

      <View style={{ backgroundColor: COLORS.primary, paddingHorizontal: 16, paddingVertical: 12 }}>
        <View style={styles.rowBetween}>
          <View style={styles.flex1}>
            <Text style={{ color: '#FFFFFF', fontSize: 15, fontWeight: '800' }}>
              Himalaya Airlines OBS
            </Text>
            <Text style={{ color: '#CFE0F0', fontSize: 11.5, marginTop: 1 }}>
              {user.name} · {ROLE_LABELS[user.role] || user.role}
            </Text>
          </View>
          <View style={[styles.row, styles.gap8]}>
            {queueCount > 0 && <Pill label={`${queueCount} to upload`} tone="warning" />}
            <Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'danger'} />
            <Pressable
              onPress={() => setProfileOpen(true)}
              style={{
                width: 34, height: 34, borderRadius: 17, backgroundColor: COLORS.primaryDark,
                alignItems: 'center', justifyContent: 'center',
              }}
            >
              <Text style={{ color: '#FFFFFF', fontWeight: '700', fontSize: 12 }}>
                {initials(user.name)}
              </Text>
            </Pressable>
          </View>
        </View>
      </View>

      <View style={{ backgroundColor: COLORS.surface, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ padding: 8, gap: 6 }}>
          {visible.map((item) => (
            <Pressable
              key={item.key}
              onPress={() => setPage(item.key)}
              style={{
                paddingVertical: 8, paddingHorizontal: 15, borderRadius: 18,
                backgroundColor: page === item.key ? COLORS.primary : COLORS.surfaceAlt,
              }}
            >
              <Text style={{
                color: page === item.key ? '#FFFFFF' : COLORS.muted,
                fontWeight: '700', fontSize: 12.5,
              }}>
                {item.label}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {!!syncNotice && (
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          <Banner tone="success" message={syncNotice} />
        </View>
      )}

      {!!versionWarning && (
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          <Banner tone="danger" message={versionWarning} />
        </View>
      )}

      {page === 'crew' && <CrewScreen {...screenProps} />}
      {page === 'inventory' && <InventoryScreen {...screenProps} />}
      {page === 'warehouse' && <WarehouseScreen {...screenProps} />}
      {page === 'dashboard' && <DashboardScreen {...screenProps} />}
      {page === 'sync' && <SyncScreen {...screenProps} />}
      {page === 'logs' && <LogsScreen {...screenProps} />}
      {page === 'rates' && <FinanceScreen {...screenProps} />}
      {page === 'reports' && (
        <ReportsScreen
          {...screenProps}
          initialFlightId={reportsFlightId}
          onHandled={() => setReportsFlightId(null)}
        />
      )}
      {page === 'admin' && <AdminScreen {...screenProps} />}

      <DialogHost />

      <ProfileModal
        visible={profileOpen}
        user={user}
        onClose={() => setProfileOpen(false)}
        onLogout={logout}
      />
    </SafeAreaView>
  );
}

/* ------------------------------- login ------------------------------- */
function LoginScreen({ onLogin }) {
  const [employeeId, setEmployeeId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // Break-glass reset for an administrator who is locked out or has forgotten
  // the password. Only an administrator can unlock accounts, so without this
  // one forgotten password would strand the whole system.
  const [recovering, setRecovering] = useState(false);
  const [recovery, setRecovery] = useState({
    employee_id: '', recovery_code: '', new_password: '', confirm_password: '',
  });
  const [recoveryMessage, setRecoveryMessage] = useState(null);

  const setRecoveryField = (key, value) => setRecovery((r) => ({ ...r, [key]: value }));

  async function submitRecovery() {
    setRecoveryMessage(null);
    if (!recovery.employee_id.trim() || !recovery.recovery_code.trim()) {
      return setRecoveryMessage({ tone: 'danger', text: 'Enter the Employee ID and the recovery code.' });
    }
    if (recovery.new_password.length < 4) {
      return setRecoveryMessage({ tone: 'danger', text: 'The new password must be at least 4 characters.' });
    }
    if (recovery.new_password !== recovery.confirm_password) {
      return setRecoveryMessage({ tone: 'danger', text: 'The new password and confirmation do not match.' });
    }
    setBusy(true);
    try {
      const result = await api('/admin-recovery', {
        method: 'POST',
        auth: false,
        body: {
          employee_id: recovery.employee_id.trim(),
          recovery_code: recovery.recovery_code.trim(),
          new_password: recovery.new_password,
          confirm_password: recovery.confirm_password,
        },
      });
      setEmployeeId(result.employee_id);
      setPassword('');
      setRecovering(false);
      setRecovery({ employee_id: '', recovery_code: '', new_password: '', confirm_password: '' });
      setError('');
      setRecoveryMessage(null);
      notify({
        title: 'Password changed',
        message: `${result.employee_id} can sign in with the new password. Any lock on the `
          + 'account has been cleared.',
      });
    } catch (err) {
      setRecoveryMessage({ tone: 'danger', text: err.message });
    }
    setBusy(false);
  }

  async function submit() {
    setError('');
    if (!employeeId.trim() || !password) return setError('Enter your Employee ID and password.');
    setBusy(true);
    try {
      const session = await api('/login', {
        method: 'POST',
        auth: false,
        body: { employee_id: employeeId.trim(), password },
      });
      await AsyncStorage.setItem(KEYS.session, JSON.stringify(session));
      onLogin(session);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={{ padding: 22, paddingTop: 60 }}>
        <Text style={{ fontSize: 25, fontWeight: '800', color: COLORS.primary }}>
          Himalaya Airlines
        </Text>
        <Text style={{ fontSize: 14, color: COLORS.muted, marginTop: 4, marginBottom: 26 }}>
          Onboard Sales Revenue Management
        </Text>

        <Card>
          {!!error && <Banner tone="danger" message={error} />}
          <Field
            label="Employee ID"
            value={employeeId}
            onChangeText={setEmployeeId}
            autoCapitalize="characters"
            placeholder="CREW001"
          />
          <Field
            label="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="••••"
            onSubmitEditing={submit}
          />
          <Button title={busy ? 'Signing in…' : 'SIGN IN'} onPress={submit} disabled={busy} />
          <Button
            title="Forgot password?"
            tone="ghost"
            small
            style={{ marginTop: 8 }}
            onPress={() => { setRecovering((r) => !r); setRecoveryMessage(null); }}
          />
        </Card>

        {recovering && (
          <Card>
            <Text style={[styles.sectionTitle, { marginBottom: 4 }]}>
              Administrator password recovery
            </Text>
            <Text style={[styles.mono, { marginBottom: 12 }]}>
              For an administrator account only. You need the recovery code held by the
              IT department. Every attempt is recorded.
            </Text>
            {!!recoveryMessage && (
              <Banner tone={recoveryMessage.tone} message={recoveryMessage.text} />
            )}
            <Field
              label="Employee ID"
              value={recovery.employee_id}
              onChangeText={(v) => setRecoveryField('employee_id', v)}
              autoCapitalize="characters"
              placeholder="ADMIN001"
            />
            <Field
              label="Recovery code"
              value={recovery.recovery_code}
              onChangeText={(v) => setRecoveryField('recovery_code', v)}
              secureTextEntry
              placeholder="••••••"
            />
            <Field
              label="New password"
              value={recovery.new_password}
              onChangeText={(v) => setRecoveryField('new_password', v)}
              secureTextEntry
              placeholder="At least 4 characters"
            />
            <Field
              label="Confirm new password"
              value={recovery.confirm_password}
              onChangeText={(v) => setRecoveryField('confirm_password', v)}
              secureTextEntry
              onSubmitEditing={submitRecovery}
            />
            <View style={[styles.row, styles.gap8]}>
              <Button
                title="CANCEL"
                tone="ghost"
                style={styles.flex1}
                onPress={() => { setRecovering(false); setRecoveryMessage(null); }}
              />
              <Button
                title={busy ? 'Saving…' : 'SET NEW PASSWORD'}
                tone="warning"
                style={styles.flex1}
                onPress={submitRecovery}
                disabled={busy}
              />
            </View>
          </Card>
        )}

        <Card>
          <Text style={[styles.sectionTitle, { marginBottom: 6 }]}>Demo accounts</Text>
          <Text style={styles.mono}>
            CREW001 · CSUP001 · INV001 · ISUP001 · MGT001 · FIN001 · ADMIN001{'\n'}
            Password for all: 1234
          </Text>
          <Text style={[styles.mono, { marginTop: 10 }]}>API: {API_BASE}</Text>
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

/* ------------------------------ profile ------------------------------ */
function ProfileModal({ visible, user, onClose, onLogout }) {
  const [form, setForm] = useState({ old_password: '', new_password: '', confirm_password: '' });
  const [message, setMessage] = useState(null);

  async function changePassword() {
    try {
      await api('/change-password', { method: 'POST', body: form });
      setMessage({ tone: 'success', text: 'Password changed.' });
      setForm({ old_password: '', new_password: '', confirm_password: '' });
    } catch (error) {
      setMessage({ tone: 'danger', text: error.message });
    }
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        onPress={onClose}
        style={{ flex: 1, backgroundColor: '#0009', justifyContent: 'center', alignItems: 'center', padding: 20 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{
            backgroundColor: COLORS.background, borderRadius: 16, padding: 20,
            width: '100%', maxWidth: 400,
          }}
        >
          <View>
            <Text style={{ fontSize: 17, fontWeight: '700', color: COLORS.text }} numberOfLines={1}>
              {user.name}
            </Text>
            <Text style={[styles.mono, { marginBottom: 12 }]} numberOfLines={1}>
              {user.employee_id} · {ROLE_LABELS[user.role] || user.role}
            </Text>

            <Card style={{ marginBottom: 12 }}>
              <Text style={[styles.sectionTitle, { marginBottom: 10 }]}>Change password</Text>
              {message && <Banner tone={message.tone} message={message.text} />}
              <Field
                label="Current password"
                value={form.old_password}
                onChangeText={(v) => setForm({ ...form, old_password: v })}
                secureTextEntry
              />
              <Field
                label="New password"
                value={form.new_password}
                onChangeText={(v) => setForm({ ...form, new_password: v })}
                secureTextEntry
              />
              <Field
                label="Confirm new password"
                value={form.confirm_password}
                onChangeText={(v) => setForm({ ...form, confirm_password: v })}
                secureTextEntry
              />
              <Button title="UPDATE PASSWORD" tone="secondary" onPress={changePassword} />
            </Card>

            <View style={[styles.row, styles.gap8]}>
              <Button title="Close" tone="ghost" style={styles.flex1} onPress={onClose} />
              <Button title="SIGN OUT" tone="danger" style={styles.flex1} onPress={onLogout} />
            </View>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
