/**
 * Security audit log: sign-ins, password and account changes, access denials
 * and SMTP changes. Read-only; data from GET /security/audit.
 */
import React, { useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { usePaged } from '../paging';
import { eventLabel } from '../layout';
import { COLORS, TYPE, styles } from '../theme';
import { Text } from '../text';
import {
  Banner, Button, CalendarModal, Card, Chip, DataTable, OptionModal, PageHeader, Pagination, Pill,
  SearchBar, Select,
} from '../ui';
import { when } from './comms/shared';

export default function AuditLogScreen({ online }) {
  const [event, setEvent] = useState('');
  const [module, setModule] = useState('');
  const [modules, setModules] = useState([]);
  const [outcome, setOutcome] = useState('');
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [from, setFrom] = useState(null);
  const [to, setTo] = useState(null);
  const [picker, setPicker] = useState(null);
  const [events, setEvents] = useState([]);

  // Wait for a pause in typing before asking the server.
  useEffect(() => {
    const t = setTimeout(() => setTerm(search.trim()), 350);
    return () => clearTimeout(t);
  }, [search]);

  const query = new URLSearchParams();
  if (module) query.set('module', module);
  if (event) query.set('event', event);
  if (outcome) query.set('outcome', outcome);
  if (term) query.set('q', term);
  if (from) query.set('date_from', from);
  if (to) query.set('date_to', to);
  const qs = query.toString();
  const pager = usePaged(`/security/audit${qs ? `?${qs}` : ''}`, { pageSize: 10, enabled: online });

  useEffect(() => {
    if (pager.raw?.events) setEvents(pager.raw.events);
    if (pager.raw?.modules?.length) setModules(pager.raw.modules);
  }, [pager.raw]);

  const filtered = module || event || outcome || term || from || to;

  const columns = [
    { key: 'time', label: 'Time', flex: 1.1, render: (r) => <Text style={[TYPE.small, { color: COLORS.text }]}>{when(r.timestamp)}</Text> },
    {
      key: 'event', label: 'Event', flex: 1.4, render: (r) => (
        <View>
          <Text style={TYPE.bodyStrong} numberOfLines={2}>{eventLabel(r.event)}</Text>
          <Text style={[TYPE.caption, { color: COLORS.muted }]}>{titleCase(r.module)}</Text>
        </View>
      ),
    },
    { key: 'status', label: 'Result', flex: 0.7, render: (r) => <ResultPill status={r.status} /> },
    { key: 'account', label: 'Account', flex: 0.9, render: (r) => <Mono value={r.account} /> },
    { key: 'by', label: 'By', flex: 0.8, render: (r) => <Mono value={r.by} /> },
    { key: 'ip', label: 'IP', flex: 0.9, render: (r) => <Mono value={r.ip_address} /> },
    { key: 'details', label: 'Details', flex: 2.4, render: (r) => <Details row={r} /> },
  ];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="Audit Log"
        description="Every recorded action: sign-ins and account changes, sales, stock returns, products, orders, approvals, corrections and settings. Codes and passwords are never recorded."
      />
      {!online && <Banner tone="warning" message="The audit log needs a connection." />}
      {!!pager.error && <Banner tone="danger" message={pager.error} />}

      <Card>
        <View style={[styles.wrap, { gap: 10, alignItems: 'flex-end' }]}>
          <Select
            label="Area"
            valueLabel={module ? titleCase(module) : 'All areas'}
            onPress={() => setPicker('module')}
            style={{ minWidth: 180, flexGrow: 1, flexBasis: 180 }}
          />
          <Select
            label="Event"
            valueLabel={event ? eventLabel(event) : 'All events'}
            onPress={() => setPicker('event')}
            style={{ minWidth: 220, flexGrow: 1, flexBasis: 220 }}
          />
          <Select label="From" valueLabel={from || 'Any date'} onPress={() => setPicker('from')} style={{ minWidth: 140, flexGrow: 1, flexBasis: 140 }} />
          <Select label="To" valueLabel={to || 'Any date'} onPress={() => setPicker('to')} style={{ minWidth: 140, flexGrow: 1, flexBasis: 140 }} />
        </View>
        <View style={[styles.wrap, { marginBottom: 10 }]}>
          <Chip label="All results" active={!outcome} onPress={() => setOutcome('')} />
          <Chip label="Success" active={outcome === 'SUCCESS'} onPress={() => setOutcome('SUCCESS')} />
          <Chip label="Failed" active={outcome === 'FAILED'} tone="danger" onPress={() => setOutcome('FAILED')} />
        </View>
        <SearchBar value={search} onChangeText={setSearch} placeholder="Search user, actor, IP or text…" />
        {filtered && (
          <Button
            title="Clear filters"
            tone="ghost"
            small
            onPress={() => { setModule(''); setEvent(''); setOutcome(''); setSearch(''); setFrom(null); setTo(null); }}
          />
        )}
      </Card>

      <Card>
        <DataTable
          columns={columns}
          rows={pager.items}
          rowKey={(r) => String(r.id)}
          breakpoint={900}
          emptyText={pager.loading ? 'Loading…' : 'No events match these filters.'}
          renderStacked={(r) => (
            <>
              <View style={styles.rowBetween}>
                <Text style={[TYPE.bodyStrong, styles.flex1]}>{eventLabel(r.event)}</Text>
                <ResultPill status={r.status} />
              </View>
              <Text style={styles.mono}>
                {when(r.timestamp)}{r.account ? ` · ${r.account}` : ''}{r.by ? ` · by ${r.by}` : ''}{r.ip_address ? ` · ${r.ip_address}` : ''}
              </Text>
              <Details row={r} />
            </>
          )}
        />
        <Pagination pager={pager} label="events" sizes={[10, 25, 50, 100]} />
      </Card>

      <OptionModal
        visible={picker === 'module'}
        title="Area"
        options={[{ value: '', label: 'All areas' }, ...modules.map((m) => ({ value: m, label: titleCase(m) }))]}
        selectedValue={module}
        onSelect={(v) => { setModule(v); setEvent(''); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <OptionModal
        visible={picker === 'event'}
        title="Event"
        options={[{ value: '', label: 'All events' }, ...events.map((e) => ({ value: e, label: eventLabel(e), sub: e }))]}
        selectedValue={event}
        onSelect={(v) => { setEvent(v); setPicker(null); }}
        onClose={() => setPicker(null)}
      />
      <CalendarModal
        visible={picker === 'from'}
        title="From"
        value={from}
        allowClear
        onSelect={setFrom}
        onClose={() => setPicker(null)}
      />
      <CalendarModal
        visible={picker === 'to'}
        title="To"
        value={to}
        minDate={from || undefined}
        allowClear
        onSelect={setTo}
        onClose={() => setPicker(null)}
      />
    </ScrollView>
  );
}

function titleCase(text) {
  return String(text || '').toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
}

function ResultPill({ status }) {
  const failed = status === 'FAILED';
  return <Pill label={failed ? 'FAILED' : 'SUCCESS'} tone={failed ? 'danger' : 'success'} />;
}

function Mono({ value }) {
  return <Text style={[TYPE.small, { color: value ? COLORS.text : COLORS.faint }]} numberOfLines={1}>{value || '—'}</Text>;
}

function Details({ row }) {
  const extra = Object.entries(row.details || {})
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${Array.isArray(v) ? v.join(', ')
      : v && typeof v === 'object' ? JSON.stringify(v) : v === true ? 'yes' : v === false ? 'no' : v}`)
    .join(' · ');
  return (
    <View>
      <Text style={[TYPE.small, { color: COLORS.text }]} numberOfLines={3}>{row.description}</Text>
      {!!extra && <Text style={[TYPE.caption, { color: COLORS.muted }]} numberOfLines={2}>{extra}</Text>}
    </View>
  );
}
