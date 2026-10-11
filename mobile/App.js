import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Modal, Platform, Pressable, ScrollView, StatusBar, Text, View,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import NetInfo from '@react-native-community/netinfo';

import {
  API_BASE, KEYS, api, checkBackendVersion, flushAll, readQueue,
  readReconQueue, setToken,
} from './src/api';
import { notify } from './src/dialog';
import { COLORS, CONTENT_MAX_WIDTH, ROLE_LABELS, initials, styles } from './src/theme';
import { Banner, Button, Card, DialogHost, Field, Pill } from './src/ui';

import AdminScreen from './src/screens/AdminScreen';
import LoginScreen from './src/screens/LoginScreen';
import { loadFonts } from './src/fonts';
import CommsScreen from './src/screens/comms/CommsScreen';
import NotificationsScreen from './src/screens/NotificationsScreen';
import { BellIcon } from './src/screens/comms/shared';
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
  // Stock alerts. Administrators reach it for SMTP and the delivery log.
  { key: 'comms', label: 'Communication Manager', anyPermission: ['comms_view', 'comms_admin', 'smtp_view'] },
  { key: 'admin', label: 'Admin', permission: 'users' },
];

const canSee = (item, permissions = []) => (item.anyPermission
  ? item.anyPermission.some((p) => permissions.includes(p))
  : permissions.includes(item.permission));

export default function App() {
  return (
    <SafeAreaProvider>
      <AppContent />
    </SafeAreaProvider>
  );
}

function AppContent() {
  const insets = useSafeAreaInsets();
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
  const [unread, setUnread] = useState({ unread: 0, urgent: false });
  const [alertFocus, setAlertFocus] = useState(null);
  const autoSyncBusy = useRef(false);
  // Keeps the selected module tab visible when the tab row scrolls on a phone.
  const navRef = useRef(null);
  const navOffsets = useRef({});
  useEffect(() => {
    const x = navOffsets.current[page];
    if (navRef.current && x !== undefined) navRef.current.scrollTo({ x: Math.max(0, x - 24), animated: true });
  }, [page]);

  /* ------------------------ notifications ------------------------ */
  // The bell's unread count. Polled, because the alert engine runs on the server.
  const refreshUnread = useCallback(async () => {
    if (!user || !online) return;
    try {
      setUnread(await api('/notifications/unread-count'));
    } catch { /* offline or signed out: keep the last count */ }
  }, [user, online]);

  useEffect(() => {
    refreshUnread();
    const timer = setInterval(refreshUnread, 30000);
    return () => clearInterval(timer);
  }, [refreshUnread]);

  /* --------------------------- session --------------------------- */
  useEffect(() => {
    (async () => {
      try {
        // Work Sans. Never blocks for long: offline devices fall back to the system font.
        await loadFonts();
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
    const first = NAV.find((item) => canSee(item, session.permissions));
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
        <LoginScreen onLogin={applyUser} notify={notify} />
      </>
    );
  }

  const visible = NAV
    .filter((item) => canSee(item, user.permissions))
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
    <SafeAreaView style={styles.screen} edges={['left', 'right', 'bottom']}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />

      {/* Blue header runs under the status bar; its content starts below it. */}
      <View style={{
        backgroundColor: COLORS.primary, paddingHorizontal: 16,
        paddingTop: insets.top + 10, paddingBottom: 10,
      }}>
        <View style={[styles.rowBetween, { width: '100%', maxWidth: CONTENT_MAX_WIDTH, alignSelf: 'center' }]}>
          <View style={styles.flex1}>
            <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '600' }} numberOfLines={1}>
              Himalaya Airlines OBS
            </Text>
            <Text style={{ color: '#D7E5F3', fontSize: 12, marginTop: 1 }} numberOfLines={1}>
              {user.name} · {ROLE_LABELS[user.role] || user.role}
            </Text>
          </View>
          <View style={[styles.row, styles.gap8]}>
            {queueCount > 0 && <Pill label={`${queueCount} to upload`} tone="warning" />}
            <Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'danger'} />
            <Pressable
              onPress={() => setPage('notifications')}
              accessibilityRole="button"
              accessibilityLabel={`Notifications, ${unread.unread} unread`}
              style={{ width: 40, height: 40, alignItems: 'center', justifyContent: 'center' }}
            >
              <BellIcon />
              {unread.unread > 0 && (
                <View style={{
                  position: 'absolute', top: 3, right: 3, minWidth: 17, height: 17, borderRadius: 9,
                  paddingHorizontal: 4, alignItems: 'center', justifyContent: 'center',
                  backgroundColor: unread.urgent ? COLORS.danger : COLORS.accent,
                }}>
                  <Text style={{ color: '#FFFFFF', fontSize: 11, fontWeight: '700' }}>
                    {unread.unread > 99 ? '99+' : unread.unread}
                  </Text>
                </View>
              )}
            </Pressable>
            <Pressable
              onPress={() => setProfileOpen(true)}
              accessibilityRole="button"
              accessibilityLabel={`Account: ${user.name}`}
              style={{
                width: 36, height: 36, borderRadius: 18, backgroundColor: COLORS.primaryDark,
                alignItems: 'center', justifyContent: 'center',
              }}
            >
              <Text style={{ color: '#FFFFFF', fontWeight: '600', fontSize: 12 }}>
                {initials(user.name)}
              </Text>
            </Pressable>
          </View>
        </View>
      </View>

      {/* Module tabs: underlined, so they read as the top level and the chip
          rows inside each screen read as the second level. */}
      <View style={{ backgroundColor: COLORS.surface, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
        <ScrollView
          ref={navRef}
          horizontal
          showsHorizontalScrollIndicator={false}
          style={{ width: '100%', maxWidth: CONTENT_MAX_WIDTH + 32, alignSelf: 'center' }}
          contentContainerStyle={{ paddingHorizontal: 8 }}
          accessibilityRole="tablist"
        >
          {visible.map((item) => {
            const active = page === item.key;
            return (
              <Pressable
                key={item.key}
                onPress={() => setPage(item.key)}
                onLayout={(e) => { navOffsets.current[item.key] = e.nativeEvent.layout.x; }}
                accessibilityRole="tab"
                accessibilityState={{ selected: active }}
                style={({ hovered }) => ({
                  paddingTop: 12, paddingBottom: 10, paddingHorizontal: 14, minHeight: 44,
                  justifyContent: 'center',
                  borderBottomWidth: 2,
                  borderBottomColor: active ? COLORS.primary : 'transparent',
                  backgroundColor: hovered && !active ? COLORS.surfaceAlt : 'transparent',
                })}
              >
                <Text style={{
                  color: active ? COLORS.primary : COLORS.muted,
                  fontWeight: active ? '600' : '500', fontSize: 14,
                }}>
                  {item.label}
                </Text>
              </Pressable>
            );
          })}
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
      {page === 'comms' && (
        <CommsScreen
          {...screenProps}
          initialAlertId={alertFocus}
          onHandled={() => setAlertFocus(null)}
        />
      )}
      {page === 'notifications' && (
        <NotificationsScreen
          {...screenProps}
          onChange={refreshUnread}
          openAlert={(id) => { setAlertFocus(id); setPage('comms'); }}
        />
      )}

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
        style={{ flex: 1, backgroundColor: COLORS.overlay, justifyContent: 'center', alignItems: 'center', padding: 20 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{
            backgroundColor: COLORS.background, borderRadius: 12, padding: 20,
            width: '100%', maxWidth: 420,
          }}
        >
          <View>
            <Text style={{ fontSize: 16, fontWeight: '600', color: COLORS.text }} numberOfLines={1}>
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
              <Button title="Update password" tone="secondary" onPress={changePassword} />
            </Card>

            <View style={[styles.row, styles.gap8]}>
              <Button title="Close" tone="ghost" style={styles.flex1} onPress={onClose} />
              <Button title="Sign out" tone="danger" style={styles.flex1} onPress={onLogout} />
            </View>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
