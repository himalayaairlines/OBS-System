/**
 * QR Menu (administrators only).
 *
 * Passengers scan a QR code and see the onboard menu at /menu: no sign-in, no
 * links out, and the back button keeps them on the menu. Here the
 * administrator edits every picture, name, description and price, adds and
 * removes items, sections and posters, prints the QR code and downloads an
 * offline copy for the onboard laptop.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Image, Modal, Platform, Pressable, ScrollView, View, useWindowDimensions } from 'react-native';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import { SvgXml } from 'react-native-svg';

import { API_BASE, api, apiText, downloadReport, uploadFile } from '../api';
import { confirm, notify } from '../dialog';
import { usePagedArray } from '../paging';
import { COLORS, RADIUS, SPACE, TYPE, styles } from '../theme';
import { Text } from '../text';
import {
  Banner, Button, Card, Chip, DataTable, Field, Loading, OptionModal, PageHeader, Pagination, Pill,
  SearchBar, SectionTitle, Select,
} from '../ui';

const TABS = [
  { key: 'items', label: 'Items' },
  { key: 'sections', label: 'Sections' },
  { key: 'posters', label: 'Posters' },
  { key: 'settings', label: 'Settings & QR' },
];

export const mediaUrl = (name) => (name ? `${API_BASE}/public/menu/media/${encodeURIComponent(name)}` : null);

function money(value, currency = 'NPR') {
  if (value === null || value === undefined || value === '') return '—';
  return `${currency} ${Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
}

function defaultMenuUrl(appUrl) {
  const origin = appUrl || (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');
  return origin ? `${origin.replace(/\/+$/, '')}/menu` : '';
}

async function fail(error) {
  await notify({ title: 'Something went wrong', message: error.message });
}

export default function QrMenuScreen({ online }) {
  const [tab, setTab] = useState('items');
  const [menu, setMenu] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setMenu(await api('/qr-menu'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { if (online) load(); }, [online, load]);

  async function downloadOffline() {
    setBusy(true);
    try {
      await downloadReport('', FileSystem, Sharing, '/qr-menu/offline.zip');
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function restoreKoseli() {
    const ok = await confirm({
      title: 'Restore the printed Koseli menu?',
      message: 'Every item, section, poster and text will be replaced by the printed Koseli menu. Changes made here will be lost.',
      confirmText: 'Restore', tone: 'danger',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api('/qr-menu/restore-koseli', { method: 'POST' });
      await load();
      notify({ title: 'Menu restored', message: `${r.items} items in ${r.sections} sections and ${r.posters} posters are back on the QR menu.` });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function openMenu() {
    if (Platform.OS === 'web') window.open(API_BASE.startsWith('/') ? '/menu' : `${API_BASE}/public/menu`, '_blank', 'noopener');
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <PageHeader
        title="QR Menu"
        description="The passenger menu opened by scanning the QR code. Only administrators can change it."
        right={(
          <View style={[styles.wrap, { justifyContent: 'flex-end' }]}>
            {Platform.OS === 'web' && <Button small tone="ghost" title="Open menu" onPress={openMenu} />}
            <Button small tone="secondary" title="Download offline package" loading={busy} onPress={downloadOffline} />
            <Button small tone="ghost" title="Restore printed menu" disabled={busy} onPress={restoreKoseli} />
          </View>
        )}
      />
      {!online && <Banner tone="warning" message="The QR menu needs a connection to the server." />}
      {!!error && <Banner tone="danger" message={error} />}
      <View style={[styles.wrap, { marginBottom: SPACE.lg }]}>
        {TABS.map((t) => <Chip key={t.key} label={t.label} active={tab === t.key} onPress={() => setTab(t.key)} />)}
      </View>
      {online && !menu && !error && <Loading />}
      {menu && tab === 'items' && <ItemsTab menu={menu} reload={load} />}
      {menu && tab === 'sections' && <SectionsTab menu={menu} reload={load} />}
      {menu && tab === 'posters' && <PostersTab menu={menu} reload={load} />}
      {menu && tab === 'settings' && <SettingsTab menu={menu} reload={load} />}
    </ScrollView>
  );
}

/* ----------------------------------------------------------------- */
/* Pictures                                                           */
/* ----------------------------------------------------------------- */

/** Thumbnail plus Upload / Remove. Uploading works in a browser. */
function PhotoPicker({ label = 'Picture', value, onChange, wide }) {
  const input = useRef(null);
  const [uploading, setUploading] = useState(false);

  async function upload(file) {
    if (!file) return;
    setUploading(true);
    try {
      const result = await uploadFile('/qr-menu/images', file);
      onChange(result.name);
    } catch (e) {
      fail(e);
    } finally {
      setUploading(false);
    }
  }

  function pick() {
    if (Platform.OS === 'web') {
      input.current?.click();
      return;
    }
    notify({ title: 'Use a browser', message: 'Pictures are uploaded from a laptop browser. Open OBS there to change them.' });
  }

  const size = wide ? { width: 150, height: 200 } : { width: 120, height: 120 };
  return (
    <View style={{ marginBottom: SPACE.md }}>
      <Text style={styles.label}>{label}</Text>
      <View style={[styles.row, { gap: SPACE.md, alignItems: 'flex-start' }]}>
        <View style={[size, {
          borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border, backgroundColor: COLORS.surfaceAlt,
          overflow: 'hidden', alignItems: 'center', justifyContent: 'center',
        }]}
        >
          {value
            ? <Image source={{ uri: mediaUrl(value) }} style={{ width: '100%', height: '100%' }} resizeMode="cover" />
            : <Text style={[TYPE.caption, { color: COLORS.faint }]}>No picture</Text>}
        </View>
        <View style={{ gap: SPACE.sm }}>
          <Button small tone="secondary" title={value ? 'Replace picture' : 'Upload picture'} loading={uploading} onPress={pick} />
          {!!value && <Button small tone="ghost" title="Remove" onPress={() => onChange('')} />}
          <Text style={[TYPE.caption, { color: COLORS.faint, maxWidth: 200 }]}>JPG, PNG or WebP, up to 6 MB.</Text>
        </View>
      </View>
      {Platform.OS === 'web' && (
        <input
          ref={input}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          style={{ display: 'none' }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            upload(file);
          }}
        />
      )}
    </View>
  );
}

function Thumb({ name, size = 44 }) {
  return (
    <View style={{
      width: size, height: size, borderRadius: RADIUS.md, overflow: 'hidden', backgroundColor: COLORS.surfaceAlt,
      borderWidth: 1, borderColor: COLORS.border,
    }}
    >
      {!!name && <Image source={{ uri: mediaUrl(name) }} style={{ width: size, height: size }} resizeMode="cover" />}
    </View>
  );
}

/* ----------------------------------------------------------------- */
/* Shared editor modal                                                */
/* ----------------------------------------------------------------- */
function EditorModal({ visible, title, onClose, onSave, saving, children, saveLabel = 'Save' }) {
  const { width, height } = useWindowDimensions();
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: COLORS.overlay, alignItems: 'center', justifyContent: 'center', padding: SPACE.lg }}>
        <View style={{
          width: Math.min(width - 32, 620), maxHeight: height - 48, backgroundColor: COLORS.surface,
          borderRadius: RADIUS.xl, overflow: 'hidden',
        }}
        >
          <View style={{ padding: SPACE.lg, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
            <Text style={styles.sectionTitle}>{title}</Text>
          </View>
          <ScrollView contentContainerStyle={{ padding: SPACE.lg }} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
          <View style={[styles.row, {
            justifyContent: 'flex-end', gap: SPACE.sm, padding: SPACE.lg, borderTopWidth: 1, borderTopColor: COLORS.border,
          }]}
          >
            <Button tone="ghost" title="Cancel" onPress={onClose} disabled={saving} />
            <Button title={saveLabel} onPress={onSave} loading={saving} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

function ActiveToggle({ value, onChange }) {
  return (
    <View style={{ marginBottom: SPACE.md }}>
      <Text style={styles.label}>On the menu</Text>
      <View style={styles.wrap}>
        <Chip label="Shown" tone="success" active={value} onPress={() => onChange(true)} />
        <Chip label="Hidden" tone="danger" active={!value} onPress={() => onChange(false)} />
      </View>
    </View>
  );
}

function TwoCols({ children }) {
  const { width } = useWindowDimensions();
  return <View style={{ flexDirection: width < 560 ? 'column' : 'row', gap: width < 560 ? 0 : SPACE.md }}>{children}</View>;
}

const Col = ({ children }) => <View style={styles.flex1}>{children}</View>;

function RowActions({ onEdit, onDelete }) {
  return (
    <View style={[styles.row, { gap: SPACE.xs }]}>
      <Button small tone="ghost" title="Edit" onPress={onEdit} />
      <Button small tone="ghost" title="Delete" onPress={onDelete} />
    </View>
  );
}

/* ----------------------------------------------------------------- */
/* Items                                                              */
/* ----------------------------------------------------------------- */
const EMPTY_ITEM = {
  section_id: null, name_en: '', name_zh: '', desc_en: '', desc_zh: '', price: '', photo: '', sort_order: '0', active: true,
};

function ItemsTab({ menu, reload }) {
  const [section, setSection] = useState('all');
  const [term, setTerm] = useState('');
  const [editing, setEditing] = useState(null);
  const currency = menu.settings.currency || 'NPR';
  const sectionName = useMemo(() => Object.fromEntries(menu.sections.map((s) => [s.id, s.name_en])), [menu]);

  const all = useMemo(() => [...menu.sections.flatMap((s) => s.items), ...(menu.unsectioned || [])], [menu]);
  const rows = useMemo(() => {
    const needle = term.trim().toLowerCase();
    return all.filter((i) => (section === 'all' || i.section_id === section)
      && (!needle || [i.name_en, i.name_zh, i.desc_en].some((v) => (v || '').toLowerCase().includes(needle))));
  }, [all, section, term]);
  const pager = usePagedArray(rows, { resetKey: `${section}|${term}` });

  function startNew() {
    if (!menu.sections.length) {
      notify({ title: 'Add a section first', message: 'Items belong to a section such as Snacks or Drinks. Create one on the Sections tab.' });
      return;
    }
    setEditing({ ...EMPTY_ITEM, section_id: section === 'all' ? menu.sections[0].id : section });
  }

  async function remove(item) {
    const ok = await confirm({
      title: 'Delete this item?',
      message: `${item.name_en} will be removed from the QR menu. This cannot be undone.`,
      confirmText: 'Delete', tone: 'danger',
    });
    if (!ok) return;
    try {
      await api(`/qr-menu/items/${item.id}`, { method: 'DELETE' });
      reload();
    } catch (e) {
      fail(e);
    }
  }

  const columns = [
    { key: 'photo', label: '', flex: 0.5, render: (i) => <Thumb name={i.photo} /> },
    {
      key: 'name', label: 'Item', flex: 2.4, render: (i) => (
        <View>
          <Text style={TYPE.bodyStrong} numberOfLines={1}>{i.name_en}</Text>
          {!!i.name_zh && <Text style={[TYPE.caption, { color: COLORS.muted }]} numberOfLines={1}>{i.name_zh}</Text>}
        </View>
      ),
    },
    { key: 'section', label: 'Section', flex: 1.2, render: (i) => sectionName[i.section_id] || '—' },
    { key: 'price', label: 'Price', flex: 1, align: 'right', render: (i) => money(i.price, currency) },
    { key: 'active', label: 'Status', flex: 0.9, render: (i) => <Pill tone={i.active ? 'success' : 'muted'} label={i.active ? 'Shown' : 'Hidden'} /> },
    { key: 'actions', label: '', flex: 1.3, align: 'right', render: (i) => <RowActions onEdit={() => setEditing(toForm(i))} onDelete={() => remove(i)} /> },
  ];

  return (
    <Card>
      <SectionTitle
        title="Menu items"
        description={`${all.length} item(s). Tap Edit to change the picture, names, description or price.`}
        right={<Button small title="New item" onPress={startNew} />}
      />
      <View style={[styles.wrap, { marginVertical: SPACE.md }]}>
        <Chip label="All" active={section === 'all'} onPress={() => setSection('all')} />
        {menu.sections.map((s) => (
          <Chip key={s.id} label={`${s.name_en} (${s.items.length})`} active={section === s.id} onPress={() => setSection(s.id)} />
        ))}
      </View>
      <SearchBar value={term} onChangeText={setTerm} placeholder="Search items…" />
      <DataTable
        columns={columns}
        rows={pager.items}
        rowKey={(i) => String(i.id)}
        emptyText={all.length ? 'No items match.' : 'No items yet. Tap New item to add the first one.'}
        renderStacked={(i) => (
          <View style={[styles.row, { gap: SPACE.md }]}>
            <Thumb name={i.photo} size={56} />
            <View style={styles.flex1}>
              <Text style={TYPE.bodyStrong}>{i.name_en}</Text>
              <Text style={[TYPE.caption, { color: COLORS.muted }]}>
                {[sectionName[i.section_id], money(i.price, currency), i.active ? null : 'Hidden'].filter(Boolean).join(' | ')}
              </Text>
            </View>
            <RowActions onEdit={() => setEditing(toForm(i))} onDelete={() => remove(i)} />
          </View>
        )}
      />
      <Pagination pager={pager} label="items" />
      {!!editing && (
        <ItemEditor
          item={editing}
          sections={menu.sections}
          currency={currency}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      )}
    </Card>
  );
}

function toForm(item) {
  return {
    ...EMPTY_ITEM,
    ...item,
    price: item.price === null || item.price === undefined ? '' : String(item.price),
    sort_order: String(item.sort_order ?? 0),
    active: !!item.active,
    name_zh: item.name_zh || '', desc_en: item.desc_en || '', desc_zh: item.desc_zh || '', photo: item.photo || '',
  };
}

function ItemEditor({ item, sections, currency, onClose, onSaved }) {
  const [form, setForm] = useState(item);
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const section = sections.find((s) => s.id === form.section_id);

  async function save() {
    if (!form.name_en.trim()) {
      notify({ title: 'Name needed', message: 'Enter the item name in English.' });
      return;
    }
    const price = form.price === '' ? null : Number(String(form.price).replace(/,/g, ''));
    if (price !== null && (Number.isNaN(price) || price < 0)) {
      notify({ title: 'Check the price', message: 'Enter the price as a number, for example 1200.' });
      return;
    }
    const body = {
      section_id: form.section_id,
      name_en: form.name_en.trim(), name_zh: form.name_zh.trim() || null,
      desc_en: form.desc_en.trim() || null, desc_zh: form.desc_zh.trim() || null,
      price, photo: form.photo || null, sort_order: parseInt(form.sort_order, 10) || 0, active: form.active,
    };
    setSaving(true);
    try {
      await api(form.id ? `/qr-menu/items/${form.id}` : '/qr-menu/items', { method: form.id ? 'PUT' : 'POST', body });
      onSaved();
    } catch (e) {
      fail(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <EditorModal visible title={form.id ? 'Edit item' : 'New item'} onClose={onClose} onSave={save} saving={saving}>
      <PhotoPicker value={form.photo} onChange={set('photo')} />
      <TwoCols>
        <Col><Field label="Name (English)" required value={form.name_en} onChangeText={set('name_en')} maxLength={160} /></Col>
        <Col><Field label="Name (中文)" value={form.name_zh} onChangeText={set('name_zh')} maxLength={160} /></Col>
      </TwoCols>
      <TwoCols>
        <Col><Field label="Description (English)" multiline value={form.desc_en} onChangeText={set('desc_en')} maxLength={500} /></Col>
        <Col><Field label="Description (中文)" multiline value={form.desc_zh} onChangeText={set('desc_zh')} maxLength={500} /></Col>
      </TwoCols>
      <TwoCols>
        <Col>
          <Field label={`Price (${currency})`} value={form.price} onChangeText={set('price')} keyboardType="decimal-pad" placeholder="e.g. 1200" hint="Leave empty to show no price." />
        </Col>
        <Col><Select label="Section" valueLabel={section?.name_en} onPress={() => setPicking(true)} /></Col>
      </TwoCols>
      <TwoCols>
        <Col><Field label="Order" value={form.sort_order} onChangeText={set('sort_order')} keyboardType="number-pad" hint="Lower numbers come first." /></Col>
        <Col><ActiveToggle value={form.active} onChange={set('active')} /></Col>
      </TwoCols>
      <OptionModal
        visible={picking}
        title="Section"
        options={sections.map((s) => ({ value: s.id, label: s.name_en }))}
        selectedValue={form.section_id}
        onSelect={(value) => { set('section_id')(value); setPicking(false); }}
        onClose={() => setPicking(false)}
      />
    </EditorModal>
  );
}

/* ----------------------------------------------------------------- */
/* Sections                                                           */
/* ----------------------------------------------------------------- */
function SectionsTab({ menu, reload }) {
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const pager = usePagedArray(menu.sections);
  const set = (key) => (value) => setEditing((f) => ({ ...f, [key]: value }));

  async function save() {
    if (!editing.name_en.trim()) {
      notify({ title: 'Name needed', message: 'Enter the section name in English.' });
      return;
    }
    setSaving(true);
    try {
      const body = {
        name_en: editing.name_en.trim(), name_zh: editing.name_zh.trim() || null,
        sort_order: parseInt(editing.sort_order, 10) || 0, active: editing.active,
      };
      await api(editing.id ? `/qr-menu/sections/${editing.id}` : '/qr-menu/sections',
        { method: editing.id ? 'PUT' : 'POST', body });
      setEditing(null);
      reload();
    } catch (e) {
      fail(e);
    } finally {
      setSaving(false);
    }
  }

  async function remove(s) {
    if (s.items.length) {
      notify({ title: 'Section not empty', message: `${s.name_en} still has ${s.items.length} item(s). Move or delete them first.` });
      return;
    }
    const ok = await confirm({ title: 'Delete this section?', message: `${s.name_en} will be removed.`, confirmText: 'Delete', tone: 'danger' });
    if (!ok) return;
    try {
      await api(`/qr-menu/sections/${s.id}`, { method: 'DELETE' });
      reload();
    } catch (e) {
      fail(e);
    }
  }

  const edit = (s) => setEditing({
    id: s.id, name_en: s.name_en, name_zh: s.name_zh || '', sort_order: String(s.sort_order ?? 0), active: !!s.active,
  });

  return (
    <Card>
      <SectionTitle
        title="Sections"
        description="The tabs across the top of the menu, such as Snacks, Drinks or Duty free."
        right={<Button small title="New section" onPress={() => setEditing({ name_en: '', name_zh: '', sort_order: String(menu.sections.length + 1), active: true })} />}
      />
      <View style={{ height: SPACE.md }} />
      <DataTable
        columns={[
          { key: 'name_en', label: 'Section', flex: 2 },
          { key: 'name_zh', label: '中文', flex: 1.5, render: (s) => s.name_zh || '—' },
          { key: 'count', label: 'Items', flex: 0.7, align: 'right', render: (s) => String(s.items.length) },
          { key: 'sort_order', label: 'Order', flex: 0.7, align: 'right', render: (s) => String(s.sort_order) },
          { key: 'active', label: 'Status', flex: 0.9, render: (s) => <Pill tone={s.active ? 'success' : 'muted'} label={s.active ? 'Shown' : 'Hidden'} /> },
          { key: 'actions', label: '', flex: 1.3, align: 'right', render: (s) => <RowActions onEdit={() => edit(s)} onDelete={() => remove(s)} /> },
        ]}
        rows={pager.items}
        rowKey={(s) => String(s.id)}
        emptyText="No sections yet. Tap New section to add one."
        renderStacked={(s) => (
          <View style={[styles.rowBetween, { gap: SPACE.md }]}>
            <View style={styles.flex1}>
              <Text style={TYPE.bodyStrong}>{s.name_en}</Text>
              <Text style={[TYPE.caption, { color: COLORS.muted }]}>
                {[s.name_zh, `${s.items.length} item(s)`, s.active ? null : 'Hidden'].filter(Boolean).join(' | ')}
              </Text>
            </View>
            <RowActions onEdit={() => edit(s)} onDelete={() => remove(s)} />
          </View>
        )}
      />
      <Pagination pager={pager} label="sections" />
      {!!editing && (
        <EditorModal visible title={editing.id ? 'Edit section' : 'New section'} onClose={() => setEditing(null)} onSave={save} saving={saving}>
          <TwoCols>
            <Col><Field label="Name (English)" required value={editing.name_en} onChangeText={set('name_en')} maxLength={80} /></Col>
            <Col><Field label="Name (中文)" value={editing.name_zh} onChangeText={set('name_zh')} maxLength={80} /></Col>
          </TwoCols>
          <TwoCols>
            <Col><Field label="Order" value={editing.sort_order} onChangeText={set('sort_order')} keyboardType="number-pad" hint="Lower numbers come first." /></Col>
            <Col><ActiveToggle value={editing.active} onChange={set('active')} /></Col>
          </TwoCols>
        </EditorModal>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------- */
/* Posters                                                            */
/* ----------------------------------------------------------------- */
function PostersTab({ menu, reload }) {
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const set = (key) => (value) => setEditing((f) => ({ ...f, [key]: value }));

  async function save() {
    if (!editing.image) {
      notify({ title: 'Picture needed', message: 'Upload the poster picture first.' });
      return;
    }
    setSaving(true);
    try {
      const body = {
        title: editing.title.trim() || null, image: editing.image,
        sort_order: parseInt(editing.sort_order, 10) || 0, active: editing.active,
      };
      await api(editing.id ? `/qr-menu/posters/${editing.id}` : '/qr-menu/posters', { method: editing.id ? 'PUT' : 'POST', body });
      setEditing(null);
      reload();
    } catch (e) {
      fail(e);
    } finally {
      setSaving(false);
    }
  }

  async function remove(p) {
    const ok = await confirm({ title: 'Delete this poster?', message: `${p.title || 'This poster'} will be removed from the menu.`, confirmText: 'Delete', tone: 'danger' });
    if (!ok) return;
    try {
      await api(`/qr-menu/posters/${p.id}`, { method: 'DELETE' });
      reload();
    } catch (e) {
      fail(e);
    }
  }

  return (
    <Card>
      <SectionTitle
        title="Menu posters"
        description="Full-page pictures (promotions, the printed menu). Passengers tap one to see it full screen."
        right={<Button small title="New poster" onPress={() => setEditing({ title: '', image: '', sort_order: String(menu.posters.length + 1), active: true })} />}
      />
      <View style={[styles.wrap, { gap: SPACE.md, marginTop: SPACE.md }]}>
        {!menu.posters.length && <Text style={styles.empty}>No posters yet.</Text>}
        {menu.posters.map((p) => (
          <View key={p.id} style={{ width: 170, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.lg, overflow: 'hidden', backgroundColor: COLORS.surface }}>
            <Pressable onPress={() => setEditing({ ...p, title: p.title || '', sort_order: String(p.sort_order ?? 0), active: !!p.active })}>
              <Image source={{ uri: mediaUrl(p.image) }} style={{ width: 170, height: 226, backgroundColor: COLORS.surfaceAlt }} resizeMode="cover" />
            </Pressable>
            <View style={{ padding: SPACE.sm, gap: SPACE.xs }}>
              <Text style={TYPE.bodyStrong} numberOfLines={1}>{p.title || 'Untitled'}</Text>
              {!p.active && <Pill tone="muted" label="Hidden" />}
              <RowActions
                onEdit={() => setEditing({ ...p, title: p.title || '', sort_order: String(p.sort_order ?? 0), active: !!p.active })}
                onDelete={() => remove(p)}
              />
            </View>
          </View>
        ))}
      </View>
      {!!editing && (
        <EditorModal visible title={editing.id ? 'Edit poster' : 'New poster'} onClose={() => setEditing(null)} onSave={save} saving={saving}>
          <PhotoPicker label="Poster picture" wide value={editing.image} onChange={set('image')} />
          <Field label="Title" value={editing.title} onChangeText={set('title')} maxLength={120} placeholder="e.g. Duty free offers" />
          <TwoCols>
            <Col><Field label="Order" value={editing.sort_order} onChangeText={set('sort_order')} keyboardType="number-pad" /></Col>
            <Col><ActiveToggle value={editing.active} onChange={set('active')} /></Col>
          </TwoCols>
        </EditorModal>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------- */
/* Settings & QR                                                      */
/* ----------------------------------------------------------------- */
const TEXT_FIELDS = [
  ['title', 'Menu title', 120],
  ['subtitle', 'Subtitle', 160],
  ['note', 'Note at the top', 400],
  ['footer', 'Footer (one line per note)', 1500],
];

function SettingsTab({ menu, reload }) {
  const { width } = useWindowDimensions();
  const [form, setForm] = useState(() => ({ ...menu.settings }));
  const [saving, setSaving] = useState(false);
  const [menuUrl, setMenuUrl] = useState('');
  const [svg, setSvg] = useState(null);
  const [qrError, setQrError] = useState(null);
  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const dirty = JSON.stringify(form) !== JSON.stringify(menu.settings);

  useEffect(() => {
    let alive = true;
    api('/security/settings')
      .then((s) => { if (alive) setMenuUrl(defaultMenuUrl(s.app_url)); })
      .catch(() => { if (alive) setMenuUrl(defaultMenuUrl('')); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!/^https?:\/\/.{3,}/.test(menuUrl)) { setSvg(null); return undefined; }
    const timer = setTimeout(() => {
      apiText(`/qr-menu/qr.svg?url=${encodeURIComponent(menuUrl)}`)
        .then((text) => { setSvg(text); setQrError(null); })
        .catch((e) => setQrError(e.message));
    }, 400);
    return () => clearTimeout(timer);
  }, [menuUrl]);

  async function save() {
    if (!(form.title_en || '').trim()) {
      notify({ title: 'Title needed', message: 'Enter the menu title in English.' });
      return;
    }
    setSaving(true);
    try {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]));
      await api('/qr-menu/settings', { method: 'PUT', body });
      await reload();
      notify({ title: 'Saved', message: 'The QR menu page has been updated.' });
    } catch (e) {
      fail(e);
    } finally {
      setSaving(false);
    }
  }

  async function downloadSvg() {
    try {
      await downloadReport(`url=${encodeURIComponent(menuUrl)}`, FileSystem, Sharing, '/qr-menu/qr.svg');
    } catch (e) {
      fail(e);
    }
  }

  function printCard() {
    if (Platform.OS !== 'web' || !svg) return;
    const w = window.open('', '_blank');
    if (!w) return;
    const title = (form.title_en || '').replace(/[<>&]/g, '');
    w.document.write(`<!doctype html><meta charset="utf-8"><title>${title}</title>
<style>@page{size:A5;margin:12mm}body{font-family:Arial,sans-serif;text-align:center;color:#0B1F3A}
h1{font-size:24px;margin:10mm 0 2mm}p{color:#5B6778;margin:0 0 8mm}svg{width:110mm;height:110mm}</style>
<h1>${title}</h1><p>Scan to see the menu · 扫码查看菜单</p>${svg}
<script>setTimeout(function(){window.print()},300)</script>`);
    w.document.close();
  }

  const narrow = width < 900;
  return (
    <View style={{ flexDirection: narrow ? 'column' : 'row', gap: SPACE.lg, alignItems: 'flex-start' }}>
      <Card style={narrow ? { alignSelf: 'stretch' } : { flex: 3 }}>
        <SectionTitle title="Menu page" description="Texts shown on the passenger page, in English and Chinese." />
        <View style={{ height: SPACE.md }} />
        {TEXT_FIELDS.map(([key, label, max]) => (
          <TwoCols key={key}>
            <Col><Field label={`${label} (English)`} required={key === 'title'} multiline={max > 200} value={form[`${key}_en`] || ''} onChangeText={set(`${key}_en`)} maxLength={max} /></Col>
            <Col><Field label={`${label} (中文)`} multiline={max > 200} value={form[`${key}_zh`] || ''} onChangeText={set(`${key}_zh`)} maxLength={max} /></Col>
          </TwoCols>
        ))}
        <TwoCols>
          <Col><Field label="Currency shown with prices" value={form.currency || ''} onChangeText={set('currency')} maxLength={8} autoCapitalize="characters" /></Col>
          <Col><PhotoPicker label="Logo (top of the page)" value={form.logo} onChange={set('logo')} /></Col>
        </TwoCols>
        <PhotoPicker label="Banner picture (under the logo; leave empty to show the title text instead)" wide value={form.banner} onChange={set('banner')} />
        <View style={[styles.row, { justifyContent: 'flex-end', gap: SPACE.sm }]}>
          {dirty && <Button tone="ghost" title="Discard changes" onPress={() => setForm({ ...menu.settings })} />}
          <Button title="Save" onPress={save} loading={saving} disabled={!dirty} />
        </View>
      </Card>
      <Card style={narrow ? { alignSelf: 'stretch' } : { flex: 2 }}>
        <SectionTitle title="QR code" description="Passengers scan this to open the menu. It only opens the menu, nothing else in OBS." />
        <View style={{ height: SPACE.md }} />
        <Field
          label="Menu address"
          value={menuUrl}
          onChangeText={setMenuUrl}
          autoCapitalize="none"
          hint="Use the address phones can reach, e.g. http://<server address>/menu."
        />
        <View style={{ alignItems: 'center', paddingVertical: SPACE.md }}>
          {svg
            ? <View style={{ backgroundColor: '#fff', padding: SPACE.sm }}><SvgXml xml={svg} width={220} height={220} /></View>
            : <Text style={styles.empty}>{qrError || 'Enter an address starting with http:// or https://.'}</Text>}
        </View>
        <View style={[styles.wrap, { justifyContent: 'center' }]}>
          {Platform.OS === 'web' && <Button small title="Print table card" onPress={printCard} disabled={!svg} />}
          <Button small tone="secondary" title="Download QR (SVG)" onPress={downloadSvg} disabled={!svg} />
        </View>
        <View style={styles.divider} />
        <Text style={[TYPE.small, { color: COLORS.muted }]}>
          No internet on board? Use "Download offline package" at the top, copy the folder to the onboard laptop and
          double-click start-server.bat. Phones on the laptop's Wi-Fi scan the QR it shows.
        </Text>
      </Card>
    </View>
  );
}
