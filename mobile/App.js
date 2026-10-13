import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Image, Modal, Platform, Pressable, ScrollView, StatusBar, View } from 'react-native';
import { Text } from './src/text';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import NetInfo from '@react-native-community/netinfo';

import {
  API_BASE, KEYS, api, checkBackendVersion, flushAll, readQueue,
  readReconQueue, setSessionEndedHandler, setToken,
} from './src/api';
import { notify } from './src/dialog';
import { COLORS, CONTENT_MAX_WIDTH, ROLE_LABELS, initials, styles } from './src/theme';
import { Banner, Button, Card, DialogHost, Field, Pill } from './src/ui';
import Icon from './src/icons';
import { useSidebarLayout } from './src/layout';
import { PasswordRules } from './src/password';

import AccountScreen from './src/screens/AccountScreen';
import AdminScreen from './src/screens/AdminScreen';
import AuditLogScreen from './src/screens/AuditLogScreen';
import OverviewScreen from './src/screens/OverviewScreen';
import SmtpScreen from './src/screens/SmtpScreen';
import LoginScreen from './src/screens/LoginScreen';
import { loadFonts } from './src/fonts';
import SplashScreen from './src/SplashScreen';
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
  // Administrators land on the user-analytics dashboard.
  { key: 'overview', label: 'Dashboard', icon: 'analytics', permission: 'security_view' },
  { key: 'crew', label: 'Sales', icon: 'sales', permission: 'crew' },
  { key: 'inventory', label: 'Inventory', icon: 'inventory', permission: 'inventory' },
  { key: 'warehouse', label: 'Warehouse', icon: 'warehouse', permission: 'warehouse' },
  { key: 'dashboard', label: 'Dashboard', icon: 'dashboard', permission: 'dashboard' },
  { key: 'sync', label: 'Sync', icon: 'sync', permission: 'sync' },
  // Management see the whole screen; the inventory supervisor reaches it for
  // the sales ledger alone, so the tab is labelled for what they actually get.
  { key: 'logs', label: 'Logs', icon: 'ledger', permission: 'sales_view', fullPermission: 'logs', altLabel: 'Sales' },
  { key: 'rates', label: 'Rates', icon: 'rates', permission: 'rates' },
  // Crew file the post-flight cabin reports here; management read and correct them.
  { key: 'reports', label: 'Reports', icon: 'reports', permission: 'flight_reports' },
  { key: 'comms', label: 'Communication Manager', icon: 'comms', anyPermission: ['comms_view', 'comms_admin'] },
  { key: 'smtp', label: 'SMTP Settings', icon: 'smtp', permission: 'smtp_view' },
  { key: 'admin', label: 'Users & Access', icon: 'users', permission: 'users' },
  { key: 'audit', label: 'Audit Log', icon: 'audit', permission: 'security_view' },
  // Everyone; reached from the account menu (and the sidebar on laptops).
  { key: 'account', label: 'My Account', icon: 'account', always: true, menuOnly: true },
];

/** Sidebar sections on laptops and larger screens. */
const GROUPS = [
  { title: 'Overview', keys: ['overview', 'dashboard'] },
  { title: 'Operations', keys: ['crew', 'inventory', 'warehouse', 'sync', 'logs', 'rates', 'reports'] },
  { title: 'Communication', keys: ['comms', 'smtp'] },
  { title: 'Administration', keys: ['admin', 'audit', 'account'] },
];

const EXTRA_PAGES = { notifications: { label: 'Notifications', icon: 'bell' } };

const canSee = (item, permissions = []) => (item.always || (item.anyPermission
  ? item.anyPermission.some((p) => permissions.includes(p))
  : permissions.includes(item.permission)));

// Sidebar palette (dark navy, as in the reference design).
const SIDEBAR = {
  bg: '#0B1F3A', border: '#1B3253', text: '#C9D6E8', muted: '#7F93B0',
  active: 'rgba(255,255,255,0.10)', hover: 'rgba(255,255,255,0.05)', accent: '#4E9BE6',
};

/** The web reset link: /reset-password?token=… (also used for invitations). */
function readResetToken() {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return null;
  try {
    const url = new URL(window.location.href);
    return url.pathname.replace(/\/+$/, '').endsWith('/reset-password') ? url.searchParams.get('token') : null;
  } catch {
    return null;
  }
}

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
  const desktop = useSidebarLayout(user?.role);
  const [resetToken, setResetToken] = useState(readResetToken);
  const [menuOpen, setMenuOpen] = useState(false);
  const [booting, setBooting] = useState(true);
  // Keep the splash up briefly so it doesn't just flash on fast starts.
  const [splashShown, setSplashShown] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSplashShown(true), 1200);
    return () => clearTimeout(t);
  }, []);
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
    if (!user || !online || user.must_change_password) return;
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
    await signedOut();
  }

  async function signedOut() {
    await AsyncStorage.removeItem(KEYS.session);
    setToken(null);
    setUser(null);
    setProfileOpen(false);
    setMenuOpen(false);
  }

  // The server ended the session (idle or time limit, signed out from another
  // device, password changed): go back to the sign-in page and say why.
  const endedRef = useRef(false);
  useEffect(() => {
    setSessionEndedHandler((detail) => {
      if (endedRef.current) return;
      endedRef.current = true;
      signedOut().then(() => {
        notify({ title: 'Signed out', message: detail || 'Your session has ended. Please sign in again.' });
        endedRef.current = false;
      });
    });
    return () => setSessionEndedHandler(null);
  }, []);

  // After a forced password change, pick up the cleared flag.
  async function refreshMe() {
    try {
      const me = await api('/me');
      const session = { ...user, ...me };
      await AsyncStorage.setItem(KEYS.session, JSON.stringify(session));
      setUser(session);
    } catch { /* keep the current state */ }
  }

  // Keep the browser tab title in step with the page.
  const pageMeta = NAV.find((n) => n.key === page) || EXTRA_PAGES[page];
  useEffect(() => {
    if (Platform.OS === 'web' && typeof document !== 'undefined') {
      document.title = user && pageMeta ? `${pageMeta.label} · Himalaya Airlines OBS` : 'Himalaya Airlines OBS';
    }
  }, [user, pageMeta]);

  if (booting || !splashShown) {
    return <SplashScreen />;
  }

  // A reset or invitation link opened in the browser.
  if (resetToken) {
    return (
      <>
        <DialogHost />
        <LoginScreen
          onLogin={applyUser}
          notify={notify}
          resetToken={resetToken}
          onResetDone={() => {
            if (Platform.OS === 'web' && typeof window !== 'undefined') window.history.replaceState({}, '', '/');
            setResetToken(null);
            // A reset signs out every session, including any in this browser.
            if (user) signedOut();
          }}
        />
      </>
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

  // A weak or administrator-set password has to be replaced before anything else.
  if (user.must_change_password) {
    return (
      <SafeAreaView style={styles.screen} edges={['left', 'right', 'bottom']}>
        <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
        <View style={{ backgroundColor: COLORS.primary, paddingTop: insets.top + 12, paddingBottom: 12, paddingHorizontal: 16 }}>
          <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '600', width: '100%', maxWidth: CONTENT_MAX_WIDTH, alignSelf: 'center' }}>
            Himalaya Airlines OBS
          </Text>
        </View>
        <AccountScreen user={user} online={online} forced onPasswordChanged={refreshMe} onLogout={logout} />
        <DialogHost />
      </SafeAreaView>
    );
  }

  const allowed = NAV
    .filter((item) => canSee(item, user.permissions))
    .map((item) => (
      item.fullPermission && !user.permissions?.includes(item.fullPermission)
        ? { ...item, label: item.altLabel }
        : item
    ));
  // Phones and tablets: the existing top tab row (My Account is in the profile menu).
  const visible = allowed.filter((item) => !item.menuOnly);
  const goToReports = (flightId) => {
    setReportsFlightId(flightId || null);
    setPage('reports');
  };

  const screenProps = { user, online, onQueueChange: refreshQueue, goToReports, goTo: setPage };

  const banners = (
    <>
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
    </>
  );

  const content = (
    <>
      {page === 'overview' && <OverviewScreen {...screenProps} />}
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
      {page === 'audit' && <AuditLogScreen {...screenProps} />}
      {page === 'smtp' && <SmtpScreen {...screenProps} />}
      {page === 'account' && <AccountScreen {...screenProps} onLogout={logout} />}
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
    </>
  );

  /* ---------------- laptop and larger: sidebar + top bar ---------------- */
  if (desktop) {
    return (
      <View style={{ flex: 1, flexDirection: 'row', backgroundColor: COLORS.background }}>
        <Sidebar items={allowed} page={page} onSelect={setPage} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <TopBar
            meta={pageMeta}
            user={user}
            online={online}
            queueCount={queueCount}
            unread={unread}
            onBell={() => setPage('notifications')}
            onMenu={() => setMenuOpen(true)}
          />
          {banners}
          {content}
        </View>
        <UserMenu
          visible={menuOpen}
          user={user}
          onClose={() => setMenuOpen(false)}
          onAccount={() => { setMenuOpen(false); setPage('account'); }}
          onLogout={logout}
        />
        <DialogHost />
      </View>
    );
  }

  /* ---------------- phones and tablets: unchanged layout ---------------- */
  return (
    <SafeAreaView style={styles.screen} edges={['left', 'right', 'bottom']}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />

      {/* Blue header runs under the status bar; its content starts below it. */}
      <View style={{
        backgroundColor: COLORS.primary, paddingHorizontal: 16,
        paddingTop: insets.top + 10, paddingBottom: 10,
      }}>
        <View style={[styles.rowBetween, { width: '100%', maxWidth: CONTENT_MAX_WIDTH, alignSelf: 'center' }]}>
          <View style={[styles.flex1, { flexDirection: 'row', alignItems: 'center' }]}>
          <Image
            source={require('./assets/app-icon.png')}
            style={{ width: 38, height: 38, borderRadius: 10, marginRight: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.4)' }}
            resizeMode="contain"
            accessibilityLabel="Himalaya Airlines OBS"
          />
          <View style={styles.flex1}>
            <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '600' }} numberOfLines={1}>
              Himalaya Airlines OBS
            </Text>
            <Text style={{ color: '#D7E5F3', fontSize: 12, marginTop: 1 }} numberOfLines={1}>
              {user.name} · {ROLE_LABELS[user.role] || user.role}
            </Text>
          </View>
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

      {banners}

      {content}

      <DialogHost />

      <ProfileModal
        visible={profileOpen}
        user={user}
        onClose={() => setProfileOpen(false)}
        onLogout={logout}
        onAccount={() => { setProfileOpen(false); setPage('account'); }}
      />
    </SafeAreaView>
  );
}

/* ------------------------------ profile ------------------------------ */
function ProfileModal({ visible, user, onClose, onLogout, onAccount }) {
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
              {!!form.new_password && <PasswordRules password={form.new_password} person={user} />}
              <Field
                label="Confirm new password"
                value={form.confirm_password}
                onChangeText={(v) => setForm({ ...form, confirm_password: v })}
                secureTextEntry
              />
              <Button title="Update password" tone="secondary" onPress={changePassword} />
            </Card>

            <Button title="My account & devices" tone="secondary" style={{ marginBottom: 8 }} onPress={onAccount} />
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

/* ------------------------- laptop shell (≥ 1024 px) ------------------------- */
function Sidebar({ items, page, onSelect }) {
  const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
  // Two "Dashboard" entries only if someone has both; name them apart then.
  const both = byKey.overview && byKey.dashboard;
  return (
    <View style={{ width: 248, backgroundColor: SIDEBAR.bg, borderRightWidth: 1, borderRightColor: SIDEBAR.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 18, height: 64, borderBottomWidth: 1, borderBottomColor: SIDEBAR.border }}>
        <Image
          source={require('./assets/app-icon.png')}
          style={{ width: 34, height: 34, borderRadius: 8 }}
          resizeMode="contain"
          accessibilityLabel="Himalaya Airlines OBS"
        />
        <View style={{ flex: 1 }}>
          <Text style={{ color: '#FFFFFF', fontSize: 15, fontWeight: '600' }} numberOfLines={1}>Himalaya Airlines</Text>
          <Text style={{ color: SIDEBAR.muted, fontSize: 12 }} numberOfLines={1}>Onboard Sales (OBS)</Text>
        </View>
      </View>
      <ScrollView contentContainerStyle={{ paddingVertical: 12 }} accessibilityRole="menu">
        {GROUPS.map((group) => {
          const entries = group.keys.map((k) => byKey[k]).filter(Boolean);
          if (!entries.length) return null;
          return (
            <View key={group.title} style={{ marginBottom: 10 }}>
              <Text style={{
                color: SIDEBAR.muted, fontSize: 11, fontWeight: '600', letterSpacing: 0.8,
                paddingHorizontal: 20, paddingTop: 8, paddingBottom: 6,
              }}>
                {group.title.toUpperCase()}
              </Text>
              {entries.map((item) => {
                const active = page === item.key;
                const label = both && item.key === 'dashboard' ? 'Sales dashboard' : item.label;
                return (
                  <Pressable
                    key={item.key}
                    onPress={() => onSelect(item.key)}
                    accessibilityRole="menuitem"
                    accessibilityState={{ selected: active }}
                    style={({ hovered }) => ({
                      flexDirection: 'row', alignItems: 'center', gap: 12, marginHorizontal: 10,
                      paddingHorizontal: 12, height: 40, borderRadius: 8,
                      backgroundColor: active ? SIDEBAR.active : hovered ? SIDEBAR.hover : 'transparent',
                    })}
                  >
                    {active && (
                      <View style={{ position: 'absolute', left: -10, top: 8, bottom: 8, width: 3, borderRadius: 2, backgroundColor: SIDEBAR.accent }} />
                    )}
                    <Icon name={item.icon} size={18} color={active ? '#FFFFFF' : SIDEBAR.text} />
                    <Text
                      style={{ color: active ? '#FFFFFF' : SIDEBAR.text, fontSize: 14, fontWeight: active ? '600' : '500', flex: 1 }}
                      numberOfLines={1}
                    >
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          );
        })}
      </ScrollView>
      <Text style={{ color: SIDEBAR.muted, fontSize: 11, paddingHorizontal: 20, paddingVertical: 12, borderTopWidth: 1, borderTopColor: SIDEBAR.border }}>
        Onboard Sales Revenue System
      </Text>
    </View>
  );
}

function TopBar({ meta, user, online, queueCount, unread, onBell, onMenu }) {
  return (
    <View style={{
      height: 64, backgroundColor: COLORS.surface, borderBottomWidth: 1, borderBottomColor: COLORS.border,
      flexDirection: 'row', alignItems: 'center', paddingHorizontal: 24, gap: 12,
    }}>
      <View style={{ width: 34, height: 34, borderRadius: 8, backgroundColor: COLORS.primarySoft, alignItems: 'center', justifyContent: 'center' }}>
        <Icon name={meta?.icon || 'dashboard'} size={18} color={COLORS.primary} />
      </View>
      <Text style={{ fontSize: 18, fontWeight: '600', color: COLORS.text, flex: 1 }} numberOfLines={1} accessibilityRole="header">
        {meta?.label || ''}
      </Text>
      {queueCount > 0 && <Pill label={`${queueCount} to upload`} tone="warning" />}
      <Pill label={online ? 'ONLINE' : 'OFFLINE'} tone={online ? 'success' : 'danger'} />
      <Pressable
        onPress={onBell}
        accessibilityRole="button"
        accessibilityLabel={`Notifications, ${unread.unread} unread`}
        style={({ hovered }) => ({
          width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center',
          backgroundColor: hovered ? COLORS.surfaceAlt : 'transparent',
        })}
      >
        <Icon name="bell" size={20} color={COLORS.muted} />
        {unread.unread > 0 && (
          <View style={{
            position: 'absolute', top: 4, right: 4, minWidth: 17, height: 17, borderRadius: 9,
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
        onPress={onMenu}
        accessibilityRole="button"
        accessibilityLabel={`Account menu: ${user.name}`}
        style={({ hovered }) => ({
          flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 6, paddingRight: 10, height: 44,
          borderRadius: 22, backgroundColor: hovered ? COLORS.surfaceAlt : 'transparent',
        })}
      >
        <View style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: COLORS.primary, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: '#FFFFFF', fontWeight: '600', fontSize: 12 }}>{initials(user.name)}</Text>
        </View>
        <View style={{ maxWidth: 180 }}>
          <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }} numberOfLines={1}>{user.name}</Text>
          <Text style={{ fontSize: 12, color: COLORS.muted }} numberOfLines={1}>{ROLE_LABELS[user.role] || user.role}</Text>
        </View>
        <Icon name="chevronDown" size={16} color={COLORS.muted} />
      </Pressable>
    </View>
  );
}

function UserMenu({ visible, user, onClose, onAccount, onLogout }) {
  const item = (icon, label, onPress, color = COLORS.text) => (
    <Pressable
      onPress={onPress}
      accessibilityRole="menuitem"
      style={({ hovered }) => ({
        flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16, height: 42,
        backgroundColor: hovered ? COLORS.surfaceAlt : 'transparent',
      })}
    >
      <Icon name={icon} size={18} color={color} />
      <Text style={{ fontSize: 14, color }}>{label}</Text>
    </Pressable>
  );
  return (
    <Modal visible={visible} transparent animationType="none" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1 }}>
        <Pressable
          onPress={(e) => e.stopPropagation()}
          accessibilityRole="menu"
          style={{
            position: 'absolute', top: 58, right: 20, width: 260, backgroundColor: COLORS.surface,
            borderRadius: 10, borderWidth: 1, borderColor: COLORS.border, paddingVertical: 6,
            shadowColor: '#0A1628', shadowOpacity: 0.16, shadowRadius: 18, shadowOffset: { width: 0, height: 8 },
            elevation: 8,
          }}
        >
          <View style={{ paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt, marginBottom: 4 }}>
            <Text style={{ fontSize: 14, fontWeight: '600', color: COLORS.text }} numberOfLines={1}>{user.name}</Text>
            <Text style={{ fontSize: 12, color: COLORS.muted }} numberOfLines={1}>{user.email || user.employee_id}</Text>
          </View>
          {item('account', 'My account', onAccount)}
          {item('logout', 'Sign out', onLogout, COLORS.danger)}
        </Pressable>
      </Pressable>
    </Modal>
  );
}
