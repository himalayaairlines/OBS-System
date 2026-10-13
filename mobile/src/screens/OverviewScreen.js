/**
 * Administrator dashboard: who can sign in, who is signed in, and what the
 * sign-in traffic looks like. Read-only; data from GET /security/overview.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import Svg, { Line, Rect, Text as SvgText } from 'react-native-svg';

import { api } from '../api';
import { DonutChart, EmptyChart, RankBarChart } from '../charts';
import { Columns, ago, eventLabel } from '../layout';
import { COLORS, NUMERIC, ROLE_LABELS, TYPE, styles } from '../theme';
import { Text } from '../text';
import Icon from '../icons';
import { Banner, Button, Card, PageHeader, Pill, SectionTitle } from '../ui';

const STATUS_META = [
  ['ACTIVE', 'Active', COLORS.success],
  ['INVITED', 'Invited', COLORS.info],
  ['UNVERIFIED', 'Unverified', COLORS.warning],
  ['LOCKED', 'Locked', COLORS.danger],
  ['DISABLED', 'Disabled', COLORS.muted],
];

export default function OverviewScreen({ online, goTo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!online) return;
    setLoading(true);
    try {
      setData(await api('/security/overview'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, [online]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [load]);

  const u = data?.users;
  const s = data?.sessions;
  const sign = data?.sign_ins;
  const usable = u ? u.total - (u.by_status.DISABLED || 0) : 0;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll}>
      <PageHeader
        title="Dashboard"
        description="User accounts, sign-in activity and open sessions. Refreshes every minute."
        right={<Button title={loading ? 'Refreshing…' : 'Refresh'} tone="ghost" small onPress={load} disabled={loading} />}
      />
      {!online && <Banner tone="warning" message="The dashboard needs a connection." />}
      {!!error && <Banner tone="danger" message={error} />}
      {!data && !error && online && <Text style={styles.empty}>Loading…</Text>}

      {data && (
        <>
          <View style={[styles.wrap, { gap: 12, marginBottom: 12 }]}>
            <StatCard icon="users" label="User accounts" value={usable} sub={`${u.total} including disabled`} />
            <StatCard icon="account" label="Signed in now" value={s.users_online} sub={`${s.active} open session(s)`}
              color={COLORS.primary} />
            <StatCard icon="key" label="Signed in, 24 h" value={sign.users_24h} sub={`${sign.users_7d} in the last 7 days`} />
            <StatCard icon="audit" label="Failed sign-ins, 24 h" value={sign.failed_24h}
              sub={`${sign.denied_24h} access denied`} color={sign.failed_24h ? COLORS.danger : undefined} />
            <StatCard icon="settings" label="Two-factor on" value={`${u.two_factor} / ${usable}`}
              sub={usable ? `${Math.round((u.two_factor / usable) * 100)}% of accounts` : ''} />
          </View>

          <Columns flex={[2, 1]}>
            <Card>
              <SectionTitle title="Sign-ins, last 14 days" description="Successful sign-ins and failed attempts per day." />
              <TrendChart data={sign.trend} />
            </Card>
            <Card>
              <SectionTitle
                title="Accounts by status"
                right={goTo && <Button title="Manage" tone="ghost" small onPress={() => goTo('admin')} />}
              />
              {STATUS_META.map(([key, label, color]) => (
                <View key={key} style={{ marginBottom: 10 }}>
                  <View style={[styles.rowBetween, { marginBottom: 4 }]}>
                    <View style={[styles.row, { gap: 8 }]}>
                      <View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: color }} />
                      <Text style={TYPE.body}>{label}</Text>
                    </View>
                    <Text style={[TYPE.bodyStrong, NUMERIC]}>{u.by_status[key] || 0}</Text>
                  </View>
                  <View style={{ height: 6, borderRadius: 3, backgroundColor: COLORS.surfaceAlt, overflow: 'hidden' }}>
                    <View style={{ height: 6, width: `${u.total ? ((u.by_status[key] || 0) / u.total) * 100 : 0}%`, backgroundColor: color }} />
                  </View>
                </View>
              ))}
            </Card>
          </Columns>

          <Columns>
            <Card>
              <SectionTitle title="Users by role" description="Accounts that are not disabled." />
              <RankBarChart
                data={u.by_role.map((r) => ({ label: ROLE_LABELS[r.role] || r.role, value: r.count }))}
                format={(v) => String(v)}
              />
            </Card>
            <Card>
              <SectionTitle title="Open sessions by device" />
              {s.by_device.length
                ? <DonutChart data={s.by_device.map((d) => ({ label: d.device, value: d.count }))} size={150} format={(v) => String(v)} />
                : <EmptyChart label="Nobody is signed in." />}
            </Card>
            <Card>
              <SectionTitle title="Things to check" />
              <Check label="Must change a weak password" value={u.must_change_password} warn />
              <Check label="Demo accounts still enabled" value={u.demo_accounts} warn />
              <Check label="Never signed in" value={u.never_signed_in} />
              <Check label="E-mail not verified" value={u.with_email - u.verified_email} />
              <Check label="No e-mail (cannot reset or use 2FA)" value={usable - u.with_email} />
              {!!data.top_failing_ips.length && (
                <>
                  <Text style={[TYPE.label, { color: COLORS.muted, marginTop: 12, marginBottom: 4 }]}>
                    Most failed sign-ins by address (7 days)
                  </Text>
                  {data.top_failing_ips.map((ip) => (
                    <View key={ip.ip_address} style={[styles.rowBetween, { paddingVertical: 3 }]}>
                      <Text style={[styles.mono, { marginTop: 0 }]}>{ip.ip_address}</Text>
                      <Text style={[TYPE.bodyStrong, NUMERIC, { color: COLORS.danger }]}>{ip.failures}</Text>
                    </View>
                  ))}
                </>
              )}
            </Card>
          </Columns>

          <Card>
            <SectionTitle
              title="Recent security activity"
              right={goTo && <Button title="Open audit log" tone="ghost" small onPress={() => goTo('audit')} />}
            />
            {!data.recent.length && <Text style={styles.empty}>Nothing recorded yet.</Text>}
            {data.recent.slice(0, 4).map((r) => (
              <View key={r.id} style={[styles.rowBetween, { paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt, gap: 12 }]}>
                <View style={[styles.row, { gap: 10, flex: 1 }]}>
                  <Pill label={r.status === 'FAILED' ? 'FAILED' : 'OK'} tone={r.status === 'FAILED' ? 'danger' : 'success'} />
                  <View style={styles.flex1}>
                    <Text style={TYPE.bodyStrong} numberOfLines={1}>
                      {eventLabel(r.event)}{r.account ? ` · ${r.account}` : ''}
                    </Text>
                    <Text style={[TYPE.caption, { color: COLORS.muted }]} numberOfLines={1}>
                      {r.description}{r.by ? ` (by ${r.by})` : ''}
                    </Text>
                  </View>
                </View>
                <Text style={[TYPE.caption, { color: COLORS.muted }]}>{ago(r.timestamp)}</Text>
              </View>
            ))}
          </Card>
        </>
      )}
    </ScrollView>
  );
}

/** White KPI card, matching the other cards on the page. */
function StatCard({ icon, label, value, sub, color }) {
  return (
    <View style={{
      flexGrow: 1, flexBasis: 180, minWidth: 150, backgroundColor: COLORS.surface, borderRadius: 10,
      borderWidth: 1, borderColor: COLORS.border, padding: 16,
    }}>
      <View style={[styles.rowBetween, { alignItems: 'flex-start' }]}>
        <Text style={[TYPE.small, { color: COLORS.muted, fontWeight: '500', flex: 1 }]} numberOfLines={1}>{label}</Text>
        <View style={{
          width: 30, height: 30, borderRadius: 8, backgroundColor: COLORS.primarySoft,
          alignItems: 'center', justifyContent: 'center', marginLeft: 8,
        }}>
          <Icon name={icon} size={16} color={COLORS.primary} />
        </View>
      </View>
      <Text style={[TYPE.metric, NUMERIC, { color: color || COLORS.text, marginTop: 2 }]}>{value}</Text>
      {!!sub && <Text style={[TYPE.caption, { color: COLORS.muted, marginTop: 2 }]}>{sub}</Text>}
    </View>
  );
}

function Check({ label, value, warn }) {
  const tone = value ? (warn ? COLORS.danger : COLORS.warning) : COLORS.success;
  return (
    <View style={[styles.rowBetween, { paddingVertical: 5 }]}>
      <Text style={[TYPE.body, styles.flex1]}>{label}</Text>
      <Text style={[TYPE.bodyStrong, NUMERIC, { color: tone }]}>{value}</Text>
    </View>
  );
}

/** Stacked daily bars: successful (blue) and failed (red) sign-ins. */
function TrendChart({ data = [], height = 220 }) {
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState(null);
  if (!data.some((d) => d.success || d.failed)) return <EmptyChart label="No sign-ins in this period." />;
  const padLeft = 32;
  const padBottom = 24;
  const plotW = Math.max(0, width - padLeft - 4);
  const plotH = height - padBottom - 8;
  const peak = Math.max(...data.map((d) => d.success + d.failed), 1);
  const step = peak <= 5 ? 1 : peak <= 10 ? 2 : peak <= 25 ? 5 : peak <= 50 ? 10 : Math.ceil(peak / 50) * 10;
  const max = Math.ceil(peak / step) * step;
  const slot = data.length ? plotW / data.length : 0;
  const bar = Math.min(28, slot * 0.6);
  const y = (v) => 8 + plotH - (v / max) * plotH;
  const ticks = [];
  for (let v = 0; v <= max; v += step) ticks.push(v);
  const label = (iso) => `${Number(iso.slice(8, 10))}/${Number(iso.slice(5, 7))}`;
  // One date label per ~44 px, counted back from today so the latest day is labelled.
  const every = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(plotW / 44))));
  const shown = hover !== null ? data[hover] : null;

  return (
    <View onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0 && (
        <Svg width={width} height={height}>
          {ticks.map((t) => (
            <React.Fragment key={t}>
              <Line x1={padLeft} x2={width - 4} y1={y(t)} y2={y(t)} stroke={COLORS.surfaceAlt} strokeWidth={1} />
              <SvgText x={padLeft - 6} y={y(t) + 4} fontSize="11" fill={COLORS.muted} textAnchor="end">{t}</SvgText>
            </React.Fragment>
          ))}
          {data.map((d, i) => {
            const x = padLeft + slot * i + (slot - bar) / 2;
            const okTop = y(d.success);
            const failTop = y(d.success + d.failed);
            return (
              <React.Fragment key={d.date}>
                {d.success > 0 && <Rect x={x} y={okTop} width={bar} height={y(0) - okTop} fill={COLORS.primary} rx={2} />}
                {d.failed > 0 && <Rect x={x} y={failTop} width={bar} height={okTop - failTop} fill={COLORS.danger} rx={2} />}
                {(data.length - 1 - i) % every === 0 && (
                  <SvgText x={x + bar / 2} y={height - 6} fontSize="11" fill={COLORS.muted} textAnchor="middle">{label(d.date)}</SvgText>
                )}
                <Rect x={padLeft + slot * i} y={0} width={slot} height={height} fill="transparent"
                  onPressIn={() => setHover(i)} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
              </React.Fragment>
            );
          })}
        </Svg>
      )}
      <View style={[styles.row, { gap: 16, marginTop: 6, flexWrap: 'wrap' }]}>
        <Legend color={COLORS.primary} label="Successful" />
        <Legend color={COLORS.danger} label="Failed / blocked" />
        {shown && (
          <Text style={[TYPE.caption, NUMERIC, { color: COLORS.text }]}>
            {shown.date}: {shown.success} successful, {shown.failed} failed
          </Text>
        )}
      </View>
    </View>
  );
}

function Legend({ color, label }) {
  return (
    <View style={[styles.row, { gap: 6 }]}>
      <View style={{ width: 10, height: 10, borderRadius: 2, backgroundColor: color }} />
      <Text style={[TYPE.caption, { color: COLORS.muted }]}>{label}</Text>
    </View>
  );
}
