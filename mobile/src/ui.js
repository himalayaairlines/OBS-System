import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions,
} from 'react-native';

import { registerDialogHandler } from './dialog';
import {
  COLORS, CONTROL, MONTHS, NUMERIC, RADIUS, SPACE, TYPE, styles,
} from './theme';

/*
 * Shared components. Every screen builds from these, so a change here is a
 * change everywhere: keep the props stable.
 */

/* ----------------------------- buttons ----------------------------- */
const BUTTON_TONES = {
  primary: { bg: COLORS.primary, fg: '#FFFFFF', border: COLORS.primary },
  secondary: { bg: COLORS.surface, fg: COLORS.primary, border: COLORS.primary },
  ghost: { bg: COLORS.surface, fg: COLORS.text, border: COLORS.borderStrong },
  success: { bg: COLORS.success, fg: '#FFFFFF', border: COLORS.success },
  danger: { bg: COLORS.danger, fg: '#FFFFFF', border: COLORS.danger },
  warning: { bg: COLORS.warning, fg: '#FFFFFF', border: COLORS.warning },
};

export function Button({ title, onPress, disabled, tone = 'primary', small, style, loading, accessibilityLabel }) {
  const palette = BUTTON_TONES[tone] || BUTTON_TONES.primary;
  const inactive = disabled || loading;
  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || (typeof title === 'string' ? title : undefined)}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      style={({ pressed, hovered }) => [
        {
          backgroundColor: palette.bg,
          borderColor: palette.border,
          borderWidth: 1,
          borderRadius: RADIUS.md,
          minHeight: small ? CONTROL.heightSmall : CONTROL.height,
          paddingHorizontal: small ? SPACE.md : SPACE.lg,
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'row',
          gap: SPACE.sm,
          opacity: inactive ? 0.5 : pressed ? 0.85 : hovered ? 0.93 : 1,
        },
        style,
      ]}
    >
      {loading && <ActivityIndicator size="small" color={palette.fg} />}
      <Text
        numberOfLines={2}
        style={{ color: palette.fg, fontWeight: '600', fontSize: small ? 13 : 14, textAlign: 'center' }}
      >
        {title}
      </Text>
    </Pressable>
  );
}

/** Filter and tab chip. Squared off to match inputs and buttons. */
export function Chip({ label, active, onPress, tone = 'primary', disabled }) {
  const color = { primary: COLORS.primary, success: COLORS.success, danger: COLORS.danger }[tone] || COLORS.primary;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ selected: !!active, disabled: !!disabled }}
      style={({ hovered }) => ({
        minHeight: 32,
        justifyContent: 'center',
        paddingHorizontal: SPACE.md,
        borderRadius: RADIUS.md,
        borderWidth: 1,
        borderColor: active ? color : COLORS.borderStrong,
        backgroundColor: active ? color : hovered ? COLORS.surfaceAlt : COLORS.surface,
        opacity: disabled ? 0.45 : 1,
      })}
    >
      <Text style={{ color: active ? '#FFFFFF' : COLORS.text, fontSize: 13, fontWeight: active ? '600' : '500' }}>
        {label}
      </Text>
    </Pressable>
  );
}

const PILL_TONES = {
  success: { bg: COLORS.successSoft, fg: COLORS.success },
  warning: { bg: COLORS.warningSoft, fg: COLORS.warning },
  danger: { bg: COLORS.dangerSoft, fg: COLORS.danger },
  info: { bg: COLORS.infoSoft, fg: COLORS.infoText },
  muted: { bg: COLORS.surfaceAlt, fg: COLORS.muted },
};

/** Status badge. The label always carries the meaning; colour only reinforces it. */
export function Pill({ label, tone = 'info' }) {
  const palette = PILL_TONES[tone] || PILL_TONES.info;
  return (
    <View style={{
      backgroundColor: palette.bg, borderRadius: RADIUS.sm, paddingVertical: 3, paddingHorizontal: SPACE.sm,
    }}>
      <Text style={[TYPE.badge, { color: palette.fg, letterSpacing: 0.3 }]}>{label}</Text>
    </View>
  );
}

/* ----------------------------- layout ------------------------------ */
export function Card({ children, style, tight }) {
  return <View style={[styles.card, tight && styles.cardTight, style]}>{children}</View>;
}

export function SectionTitle({ title, description, right }) {
  return (
    <View style={[styles.rowBetween, { marginBottom: SPACE.md, gap: SPACE.md, alignItems: 'flex-start' }]}>
      <View style={styles.flex1}>
        <Text style={styles.sectionTitle} accessibilityRole="header">{title}</Text>
        {!!description && <Text style={styles.sectionDescription}>{description}</Text>}
      </View>
      {right}
    </View>
  );
}

export function PageHeader({ title, description, right }) {
  return (
    <View style={[styles.rowBetween, { marginBottom: SPACE.lg, gap: SPACE.md, alignItems: 'flex-start' }]}>
      <View style={styles.flex1}>
        <Text style={styles.pageTitle} accessibilityRole="header">{title}</Text>
        {!!description && <Text style={styles.pageDescription}>{description}</Text>}
      </View>
      {right}
    </View>
  );
}

/** KPI tile. Same proportions everywhere; tone only for meaningful states. */
export function Metric({ label, value, sub, tone = 'default', width }) {
  const color = {
    default: COLORS.text, success: COLORS.success,
    danger: COLORS.danger, warning: COLORS.warning, primary: COLORS.primary,
  }[tone] || COLORS.text;
  return (
    <View
      style={{
        backgroundColor: COLORS.surfaceAlt,
        borderRadius: RADIUS.md,
        paddingVertical: SPACE.md,
        paddingHorizontal: SPACE.md,
        minWidth: width || 132,
        flexGrow: 1,
      }}
    >
      <Text style={[TYPE.caption, { color: COLORS.muted, fontWeight: '500' }]} numberOfLines={1}>{label}</Text>
      <Text style={[TYPE.metric, NUMERIC, { color, marginTop: SPACE.xs }]}>
        {value}
      </Text>
      {!!sub && <Text style={[TYPE.caption, { color: COLORS.muted, marginTop: SPACE.xxs }]}>{sub}</Text>}
    </View>
  );
}

const BANNER_TONES = {
  info: { bg: COLORS.infoSoft, border: '#C3D6FA', accent: COLORS.info, fg: COLORS.infoText },
  success: { bg: COLORS.successSoft, border: '#BFE2D1', accent: COLORS.success, fg: COLORS.success },
  warning: { bg: COLORS.warningSoft, border: '#EED7AE', accent: COLORS.warning, fg: COLORS.warning },
  danger: { bg: COLORS.dangerSoft, border: '#F1C4BF', accent: COLORS.danger, fg: COLORS.danger },
};

export function Banner({ message, tone = 'info' }) {
  if (!message) return null;
  const palette = BANNER_TONES[tone] || BANNER_TONES.info;
  return (
    <View
      accessibilityRole={tone === 'danger' ? 'alert' : undefined}
      accessibilityLiveRegion={tone === 'danger' ? 'assertive' : 'polite'}
      style={[styles.banner, { backgroundColor: palette.bg, borderColor: palette.border, borderLeftColor: palette.accent }]}
    >
      <Text style={[styles.bannerText, { color: palette.fg }]}>{message}</Text>
    </View>
  );
}

/* ------------------------------ inputs ------------------------------ */
// Layout props belong on the wrapper, not the text box, so `style={{ flex: 1 }}`
// on a Field in a row sizes the whole field (label and all) and never overflows.
const LAYOUT_KEYS = [
  'flex', 'flexGrow', 'flexShrink', 'flexBasis', 'width', 'minWidth', 'maxWidth', 'alignSelf',
  'margin', 'marginTop', 'marginBottom', 'marginLeft', 'marginRight', 'marginHorizontal', 'marginVertical',
];

function splitStyle(style) {
  const flat = StyleSheet.flatten(style) || {};
  const outer = {};
  const inner = {};
  Object.entries(flat).forEach(([key, value]) => {
    if (LAYOUT_KEYS.includes(key)) outer[key] = value; else inner[key] = value;
  });
  return [outer, inner];
}

export function Field({ label, hint, error, required, style, onFocus, onBlur, ...props }) {
  const [focused, setFocused] = useState(false);
  const [outer, inner] = splitStyle(style);
  return (
    <View style={[{ marginBottom: SPACE.md }, outer]}>
      {!!label && (
        <Text style={styles.label}>
          {label}
          {required && <Text style={{ color: COLORS.danger }}> *</Text>}
        </Text>
      )}
      <TextInput
        placeholderTextColor={COLORS.faint}
        accessibilityLabel={label}
        {...props}
        onFocus={(e) => { setFocused(true); onFocus?.(e); }}
        onBlur={(e) => { setFocused(false); onBlur?.(e); }}
        style={[
          styles.input,
          props.multiline && styles.inputMultiline,
          props.editable === false && { backgroundColor: COLORS.surfaceAlt, color: COLORS.muted },
          focused && styles.inputFocused,
          !!error && { borderColor: COLORS.danger },
          inner,
        ]}
      />
      {!!error && <Text style={[TYPE.caption, { color: COLORS.danger, marginTop: SPACE.xs }]}>{error}</Text>}
      {!!hint && !error && <Text style={[styles.mono, { marginTop: SPACE.xs }]}>{hint}</Text>}
    </View>
  );
}

export function Select({ label, valueLabel, onPress, style, placeholder = 'Select', disabled }) {
  return (
    <View style={[{ marginBottom: SPACE.md }, style]}>
      {!!label && <Text style={styles.label}>{label}</Text>}
      <Pressable
        onPress={onPress}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel={label ? `${label}: ${valueLabel || placeholder}` : valueLabel || placeholder}
        style={({ hovered }) => [styles.input, styles.rowBetween, {
          justifyContent: 'space-between',
          borderColor: hovered ? COLORS.primary : COLORS.borderStrong,
          opacity: disabled ? 0.5 : 1,
        }]}
      >
        <Text style={{ color: valueLabel ? COLORS.text : COLORS.faint, fontSize: 14, flex: 1 }} numberOfLines={1}>
          {valueLabel || placeholder}
        </Text>
        <Text style={{ color: COLORS.muted, fontSize: 12, marginLeft: SPACE.sm }}>▾</Text>
      </Pressable>
    </View>
  );
}

/* ----------------------------- overlays ----------------------------- */
const sheet = {
  backgroundColor: COLORS.surface,
  borderRadius: RADIUS.xl,
  overflow: 'hidden',
  width: '100%',
  alignSelf: 'center',
};

export function OptionModal({
  visible, title, options, selectedValue, onSelect, onClose, multi,
  searchable, searchPlaceholder = 'Search…',
}) {
  const [term, setTerm] = useState('');
  React.useEffect(() => { if (!visible) setTerm(''); }, [visible]);

  // Long lists (the 86-strong crew roster, for one) need filtering to be usable.
  const needle = term.trim().toLowerCase();
  const shown = (searchable ?? options.length > 12) && needle
    ? options.filter((o) => `${o.label} ${o.sub || ''}`.toLowerCase().includes(needle))
    : options;
  const showSearch = searchable ?? options.length > 12;

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        onPress={onClose}
        style={{ flex: 1, backgroundColor: COLORS.overlay, justifyContent: 'center', padding: SPACE.xl }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          accessibilityViewIsModal
          style={[sheet, { maxHeight: '80%', maxWidth: 520 }]}
        >
          <View style={{ padding: SPACE.lg, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
            <View style={styles.rowBetween}>
              <Text style={styles.sectionTitle} accessibilityRole="header">{title}</Text>
              {multi && <Pill label={`${(selectedValue || []).length} selected`} tone="info" />}
            </View>
            {showSearch && (
              <TextInput
                value={term}
                onChangeText={setTerm}
                placeholder={searchPlaceholder}
                placeholderTextColor={COLORS.faint}
                autoCorrect={false}
                accessibilityLabel={searchPlaceholder}
                style={[styles.input, { marginTop: SPACE.md }]}
              />
            )}
          </View>
          <ScrollView keyboardShouldPersistTaps="handled">
            {shown.length === 0 && (
              <Text style={styles.empty}>
                {options.length ? `Nothing matches “${term}”.` : 'Nothing available.'}
              </Text>
            )}
            {shown.map((option) => {
              const selected = multi
                ? (selectedValue || []).includes(option.value)
                : option.value === selectedValue;
              return (
                <Pressable
                  key={String(option.value)}
                  onPress={() => onSelect(option.value)}
                  accessibilityRole={multi ? 'checkbox' : 'radio'}
                  accessibilityState={{ checked: selected, selected }}
                  style={({ hovered }) => ({
                    minHeight: 44,
                    justifyContent: 'center',
                    paddingVertical: SPACE.sm + 2,
                    paddingHorizontal: SPACE.lg,
                    borderBottomWidth: 1,
                    borderBottomColor: COLORS.surfaceAlt,
                    backgroundColor: selected ? COLORS.primarySoft : hovered ? COLORS.surfaceAlt : 'transparent',
                  })}
                >
                  <Text style={{ fontSize: 14, color: COLORS.text, fontWeight: selected ? '600' : '400' }}>
                    {selected ? '✓  ' : ''}{option.label}
                  </Text>
                  {!!option.sub && <Text style={[styles.mono, { marginTop: 2 }]}>{option.sub}</Text>}
                </Pressable>
              );
            })}
          </ScrollView>
          <View style={{ padding: SPACE.md, borderTopWidth: 1, borderTopColor: COLORS.border }}>
            <Button title={multi ? 'Done' : 'Close'} tone={multi ? 'primary' : 'ghost'} onPress={onClose} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

/* ---------------------------- calendar ----------------------------- */
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const lead = first.getDay();
  const cells = Array.from({ length: lead }, () => null);
  for (let day = 1; day <= days; day += 1) cells.push(day);
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

function iso(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** Month-grid date picker. No extra dependency, works identically on web. */
export function CalendarModal({ visible, title = 'Select date', value, onSelect, onClose, minDate, allowClear }) {
  const initial = value ? new Date(`${value}T00:00:00`) : new Date();
  const [cursor, setCursor] = useState({ year: initial.getFullYear(), month: initial.getMonth() });

  React.useEffect(() => {
    if (visible) {
      const base = value ? new Date(`${value}T00:00:00`) : new Date();
      setCursor({ year: base.getFullYear(), month: base.getMonth() });
    }
  }, [visible, value]);

  const cells = useMemo(() => monthGrid(cursor.year, cursor.month), [cursor]);
  const step = (delta) => {
    const next = new Date(cursor.year, cursor.month + delta, 1);
    setCursor({ year: next.getFullYear(), month: next.getMonth() });
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        onPress={onClose}
        style={{ flex: 1, backgroundColor: COLORS.overlay, justifyContent: 'center', padding: 22 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          accessibilityViewIsModal
          style={[sheet, { padding: SPACE.lg, maxWidth: 420 }]}
        >
          <Text style={[styles.sectionTitle, { marginBottom: 12 }]}>{title}</Text>

          <View style={[styles.rowBetween, { marginBottom: 10 }]}>
            <Button title="‹" tone="ghost" small onPress={() => step(-1)} style={{ width: 46 }} />
            <Text style={[TYPE.bodyStrong, { fontSize: 15, color: COLORS.text }]}>
              {MONTHS[cursor.month]} {cursor.year}
            </Text>
            <Button title="›" tone="ghost" small onPress={() => step(1)} style={{ width: 46 }} />
          </View>

          <View style={styles.row}>
            {WEEKDAYS.map((day) => (
              <Text
                key={day}
                style={{ flex: 1, textAlign: 'center', fontSize: 12, color: COLORS.muted, fontWeight: '600' }}
              >
                {day}
              </Text>
            ))}
          </View>

          {Array.from({ length: cells.length / 7 }, (_, week) => (
            <View key={week} style={styles.row}>
              {cells.slice(week * 7, week * 7 + 7).map((day, index) => {
                if (day === null) return <View key={index} style={{ flex: 1, height: 42 }} />;
                const stamp = iso(cursor.year, cursor.month, day);
                const selected = stamp === value;
                const blocked = minDate && stamp < minDate;
                return (
                  <Pressable
                    key={index}
                    disabled={blocked}
                    onPress={() => { onSelect(stamp); onClose(); }}
                    style={{
                      flex: 1, height: 42, alignItems: 'center', justifyContent: 'center',
                      borderRadius: 8, margin: 1,
                      backgroundColor: selected ? COLORS.primary : 'transparent',
                      opacity: blocked ? 0.28 : 1,
                    }}
                  >
                    <Text style={[NUMERIC, { fontSize: 14, color: selected ? '#FFFFFF' : COLORS.text, fontWeight: selected ? '600' : '400' }]}>
                      {day}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ))}

          <View style={[styles.row, styles.gap8, { marginTop: 12 }]}>
            <Button
              title="Today"
              tone="secondary"
              small
              style={styles.flex1}
              onPress={() => { onSelect(new Date().toISOString().slice(0, 10)); onClose(); }}
            />
            {allowClear && (
              <Button
                title="Clear"
                tone="ghost"
                small
                style={styles.flex1}
                onPress={() => { onSelect(null); onClose(); }}
              />
            )}
            <Button title="Cancel" tone="ghost" small style={styles.flex1} onPress={onClose} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

export function Loading({ label = 'Loading…' }) {
  return (
    <View style={{ padding: 28, alignItems: 'center' }}>
      <ActivityIndicator color={COLORS.primary} />
      <Text style={[styles.mono, { marginTop: SPACE.sm }]} accessibilityLiveRegion="polite">{label}</Text>
    </View>
  );
}

export function KeyValue({ label, value, tone }) {
  return (
    <View style={[styles.rowBetween, { paddingVertical: 6, gap: SPACE.lg, alignItems: 'flex-start' }]}>
      <Text style={[TYPE.small, { color: COLORS.muted }]}>{label}</Text>
      <Text style={[TYPE.small, NUMERIC, { color: tone || COLORS.text, fontWeight: '600', flexShrink: 1, textAlign: 'right' }]}>
        {value}
      </Text>
    </View>
  );
}

/* --------------------------- seat map ------------------------------ */
/**
 * Cabin seat map. `spec` comes from /aircraft-types and gives the row count and
 * the seat blocks either side of the aisle, so an A319 shows 24 rows and an
 * A320 shows 30 without anything being hardcoded here.
 */
export function SeatMapModal({ visible, spec, selected, onSelect, onClose, taken = [] }) {
  if (!spec) spec = { rows: 30, layout: [['A', 'B', 'C'], ['D', 'E', 'F']] };
  const rows = Array.from({ length: spec.rows }, (_, i) => i + 1);
  const takenSet = new Set(taken);

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable
        onPress={onClose}
        style={{ flex: 1, backgroundColor: COLORS.overlay, justifyContent: 'center', padding: 16 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          accessibilityViewIsModal
          style={[sheet, { maxHeight: '88%', maxWidth: 420 }]}
        >
          <View style={{ padding: 14, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
            <Text style={styles.sectionTitle}>Select seat</Text>
            <Text style={styles.sectionDescription}>
              {spec.code || 'Aircraft'} · {spec.rows} rows · {spec.rows * spec.layout.flat().length} seats
            </Text>
          </View>

          <View style={[styles.row, { paddingTop: 10, paddingHorizontal: 14 }]}>
            <View style={{ width: 26 }} />
            {spec.layout.map((block, blockIndex) => (
              <View key={blockIndex} style={styles.row}>
                {block.map((letter) => (
                  <Text
                    key={letter}
                    style={{
                      width: 40, textAlign: 'center', fontSize: 12,
                      fontWeight: '600', color: COLORS.muted,
                    }}
                  >
                    {letter}
                  </Text>
                ))}
                {blockIndex < spec.layout.length - 1 && <View style={{ width: 22 }} />}
              </View>
            ))}
          </View>

          <ScrollView contentContainerStyle={{ paddingHorizontal: 14, paddingBottom: 10 }}>
            {rows.map((row) => (
              <View key={row} style={[styles.row, { marginBottom: 4 }]}>
                <Text style={[NUMERIC, { width: 26, fontSize: 12, color: COLORS.muted, fontWeight: '500' }]}>
                  {row}
                </Text>
                {spec.layout.map((block, blockIndex) => (
                  <View key={blockIndex} style={styles.row}>
                    {block.map((letter) => {
                      const seat = `${row}${letter}`;
                      const isSelected = seat === selected;
                      const isTaken = takenSet.has(seat);
                      return (
                        <Pressable
                          key={seat}
                          onPress={() => { onSelect(seat); onClose(); }}
                          style={{
                            width: 36, height: 32, marginHorizontal: 2, borderRadius: 6,
                            alignItems: 'center', justifyContent: 'center',
                            borderWidth: 1,
                            borderColor: isSelected ? COLORS.primary : COLORS.border,
                            backgroundColor: isSelected
                              ? COLORS.primary
                              : isTaken ? COLORS.surfaceAlt : COLORS.surface,
                          }}
                        >
                          <Text
                            style={{
                              fontSize: 11,
                              fontWeight: isSelected ? '600' : '500',
                              color: isSelected ? '#FFFFFF' : isTaken ? COLORS.faint : COLORS.text,
                            }}
                          >
                            {seat}
                          </Text>
                        </Pressable>
                      );
                    })}
                    {blockIndex < spec.layout.length - 1 && <View style={{ width: 22 }} />}
                  </View>
                ))}
              </View>
            ))}
          </ScrollView>

          <View style={{ padding: 12, borderTopWidth: 1, borderTopColor: COLORS.border }}>
            <Button title="Cancel" tone="ghost" onPress={onClose} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

/** Text filter used above long product and group lists. */
export function SearchBar({ value, onChangeText, placeholder = 'Search…' }) {
  return (
    <View style={{ marginBottom: 10 }}>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={COLORS.faint}
        autoCorrect={false}
        accessibilityLabel={placeholder}
        style={[styles.input, { paddingRight: 40 }]}
      />
      {!!value && (
        <Pressable
          onPress={() => onChangeText('')}
          accessibilityRole="button"
          accessibilityLabel="Clear search"
          hitSlop={8}
          style={{ position: 'absolute', right: 6, top: 0, bottom: 0, width: 32, alignItems: 'center', justifyContent: 'center' }}
        >
          <Text style={{ color: COLORS.muted, fontSize: 18 }}>×</Text>
        </Pressable>
      )}
    </View>
  );
}

/* ----------------------------- pagination ---------------------------- */
function pageWindow(page, pages, span) {
  const start = Math.max(1, Math.min(page - Math.floor(span / 2), pages - span + 1));
  return Array.from({ length: Math.min(span, pages) }, (_, i) => start + i);
}

function PageButton({ label, onPress, disabled, active, accessibilityLabel }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || active}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || String(label)}
      accessibilityState={{ disabled: !!disabled, selected: !!active }}
      style={({ hovered }) => ({
        minWidth: 34, height: 34, paddingHorizontal: SPACE.sm, borderRadius: RADIUS.md,
        alignItems: 'center', justifyContent: 'center', borderWidth: 1,
        borderColor: active ? COLORS.primary : COLORS.borderStrong,
        backgroundColor: active ? COLORS.primary : hovered && !disabled ? COLORS.surfaceAlt : COLORS.surface,
        opacity: disabled ? 0.4 : 1,
      })}
    >
      <Text style={[NUMERIC, { fontSize: 13, fontWeight: active ? '600' : '500', color: active ? '#FFFFFF' : COLORS.text }]}>
        {label}
      </Text>
    </Pressable>
  );
}

/**
 * Page controls for a paged list: "26–50 of 1,234", previous / numbered /
 * next, and a rows-per-page choice. Pass the object returned by usePaged or
 * usePagedArray straight in as `pager`. Renders nothing for a single page
 * unless the page size can still be changed.
 */
export function Pagination({ pager, label = 'rows', sizes = [10, 25, 50, 100] }) {
  const { width } = useWindowDimensions();
  const { page, pages, total, pageSize, setPage, setPageSize, loading } = pager;
  if (!total) return null;
  const compactMode = width < 520;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const go = (p) => setPage(Math.min(Math.max(1, p), pages));
  return (
    <View
      accessibilityRole="toolbar"
      accessibilityLabel="Pagination"
      style={{
        flexDirection: compactMode ? 'column' : 'row', alignItems: compactMode ? 'stretch' : 'center',
        justifyContent: 'space-between', gap: SPACE.sm, paddingTop: SPACE.md, marginTop: SPACE.sm,
        borderTopWidth: 1, borderTopColor: COLORS.surfaceAlt,
      }}
    >
      <Text style={[TYPE.small, NUMERIC, { color: COLORS.muted }]} accessibilityLiveRegion="polite">
        {loading ? 'Loading… ' : ''}{from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()} {label}
      </Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: SPACE.xs, flexWrap: 'wrap', justifyContent: compactMode ? 'space-between' : 'flex-end' }}>
        {pages > 1 && (
          <View style={{ flexDirection: 'row', gap: SPACE.xs }}>
            {!compactMode && <PageButton label="«" accessibilityLabel="First page" onPress={() => go(1)} disabled={page <= 1} />}
            <PageButton label="‹" accessibilityLabel="Previous page" onPress={() => go(page - 1)} disabled={page <= 1} />
            {compactMode ? (
              <View style={{ height: 34, justifyContent: 'center', paddingHorizontal: SPACE.sm }}>
                <Text style={[TYPE.small, NUMERIC, { color: COLORS.text }]}>Page {page} of {pages}</Text>
              </View>
            ) : pageWindow(page, pages, 5).map((p) => (
              <PageButton key={p} label={p} accessibilityLabel={`Page ${p}`} active={p === page} onPress={() => go(p)} />
            ))}
            <PageButton label="›" accessibilityLabel="Next page" onPress={() => go(page + 1)} disabled={page >= pages} />
            {!compactMode && <PageButton label="»" accessibilityLabel="Last page" onPress={() => go(pages)} disabled={page >= pages} />}
          </View>
        )}
        {!!setPageSize && total > sizes[0] && (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: SPACE.xs, marginLeft: compactMode ? 0 : SPACE.md }}>
            <Text style={[TYPE.caption, { color: COLORS.muted }]}>Per page</Text>
            {sizes.map((size) => (
              <PageButton key={size} label={size} accessibilityLabel={`${size} per page`} active={size === pageSize}
                onPress={() => setPageSize(size)} />
            ))}
          </View>
        )}
      </View>
    </View>
  );
}

/* ---------------------------- stock status --------------------------- */
/** One vocabulary for stock state across every screen. */
export function StockPill({ quantity, low }) {
  if (Number(quantity) <= 0) return <Pill label="OUT OF STOCK" tone="danger" />;
  if (low) return <Pill label="LOW STOCK" tone="warning" />;
  return <Pill label="IN STOCK" tone="success" />;
}

/* --------------------------- simple table --------------------------- */
/**
 * Column table on tablets and desktops, stacked rows on phones.
 *
 *   columns  [{ key, label, flex = 1, align: 'left' | 'right' | 'center', render(row) }]
 *   rows     array of records
 *   rowKey   (row) => key
 *   renderStacked (row) => node   phone layout for one row
 */
export function DataTable({ columns, rows, rowKey, renderStacked, emptyText = 'Nothing to show.', breakpoint = 700 }) {
  const { width } = useWindowDimensions();
  if (!rows.length) return <Text style={styles.empty}>{emptyText}</Text>;
  if (width < breakpoint && renderStacked) {
    return rows.map((row) => <View key={rowKey(row)} style={styles.listRow}>{renderStacked(row)}</View>);
  }
  const justify = (align) => (align === 'right' ? 'flex-end' : align === 'center' ? 'center' : 'flex-start');
  return (
    <View accessibilityRole="table">
      <View style={{
        flexDirection: 'row', paddingVertical: SPACE.sm, borderBottomWidth: 1, borderBottomColor: COLORS.border,
        gap: SPACE.md,
      }}>
        {columns.map((c) => (
          <View key={c.key} style={{ flex: c.flex || 1, alignItems: justify(c.align) }}>
            <Text style={[TYPE.caption, { color: COLORS.muted, fontWeight: '600', textAlign: c.align || 'left' }]}>
              {c.label}
            </Text>
          </View>
        ))}
      </View>
      {rows.map((row) => (
        <View
          key={rowKey(row)}
          accessibilityRole="row"
          style={{
            flexDirection: 'row', alignItems: 'center', minHeight: 44, paddingVertical: SPACE.sm,
            borderBottomWidth: 1, borderBottomColor: COLORS.surfaceAlt, gap: SPACE.md,
          }}
        >
          {columns.map((c) => {
            const value = c.render ? c.render(row) : row[c.key];
            return (
              <View key={c.key} style={{ flex: c.flex || 1, alignItems: justify(c.align) }}>
                {typeof value === 'string' || typeof value === 'number' ? (
                  <Text
                    numberOfLines={2}
                    style={[TYPE.body, c.align === 'right' && NUMERIC, { color: COLORS.text, textAlign: c.align || 'left' }]}
                  >
                    {value}
                  </Text>
                ) : value}
              </View>
            );
          })}
        </View>
      ))}
    </View>
  );
}

/* ---------------------------- data table ---------------------------- */
/**
 * Spreadsheet-style table with a frozen leading column and a horizontally
 * scrolling body, so a month of day-columns can be read on a tablet the way
 * the source workbook reads on a desktop.
 *
 *   frozen      { width, header: [line, line?], cells: [node|string] }
 *   groupHeader optional [{ label, width }] drawn above the column header
 *   columns     [{ label, width, align }]
 *   rows        [{ key, cells: [{ text, tone, bold, onPress, faint }] }]
 */
export function FrozenTable({
  frozen, groupHeader, columns, rows, rowHeight = 36, headerHeight = 32,
}) {
  const headerRows = frozen.header.length;
  const totalHeaderHeight = headerHeight * (groupHeader ? 2 : headerRows);

  const cellBorder = {
    borderRightWidth: 1,
    borderRightColor: COLORS.border,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.surfaceAlt,
    justifyContent: 'center',
    paddingHorizontal: SPACE.sm,
  };

  return (
    <View style={{ borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, overflow: 'hidden' }}>
      <View style={{ flexDirection: 'row' }}>
        {/* frozen column */}
        <View style={{ width: frozen.width, backgroundColor: COLORS.surface }}>
          <View
            style={[cellBorder, {
              height: totalHeaderHeight,
              backgroundColor: COLORS.primary,
              borderBottomColor: COLORS.primary,
            }]}
          >
            {frozen.header.map((line, index) => (
              <Text
                key={index}
                numberOfLines={1}
                style={{ color: '#FFFFFF', fontSize: 12, fontWeight: '600' }}
              >
                {line}
              </Text>
            ))}
          </View>
          {rows.map((row, index) => (
            <View
              key={row.key}
              style={[cellBorder, {
                height: rowHeight,
                backgroundColor: index % 2 ? COLORS.surfaceAlt : COLORS.surface,
              }]}
            >
              {typeof frozen.cells[index] === 'string'
                ? (
                  <Text numberOfLines={1} style={{ fontSize: 13, fontWeight: '500', color: COLORS.text }}>
                    {frozen.cells[index]}
                  </Text>
                )
                : frozen.cells[index]}
            </View>
          ))}
        </View>

        {/* scrolling body */}
        <ScrollView horizontal showsHorizontalScrollIndicator>
          <View>
            {!!groupHeader && (
              <View style={{ flexDirection: 'row' }}>
                {groupHeader.map((group, index) => (
                  <View
                    key={index}
                    style={[cellBorder, {
                      width: group.width,
                      height: headerHeight,
                      backgroundColor: COLORS.primary,
                      borderBottomColor: COLORS.primaryDark,
                      alignItems: 'center',
                    }]}
                  >
                    <Text numberOfLines={1} style={{ color: '#FFFFFF', fontSize: 12, fontWeight: '600' }}>
                      {group.label}
                    </Text>
                  </View>
                ))}
              </View>
            )}
            <View style={{ flexDirection: 'row' }}>
              {columns.map((column, index) => (
                <View
                  key={index}
                  style={[cellBorder, {
                    width: column.width,
                    height: groupHeader ? headerHeight : totalHeaderHeight,
                    backgroundColor: COLORS.primary,
                    borderBottomColor: COLORS.primary,
                    alignItems: column.align === 'right' ? 'flex-end' : 'center',
                  }]}
                >
                  <Text numberOfLines={2} style={{ color: '#FFFFFF', fontSize: 11, fontWeight: '600', textAlign: 'center' }}>
                    {column.label}
                  </Text>
                </View>
              ))}
            </View>

            {rows.map((row, rowIndex) => (
              <View key={row.key} style={{ flexDirection: 'row' }}>
                {row.cells.map((cell, index) => {
                  const body = (
                    <Text
                      numberOfLines={1}
                      style={{
                        ...NUMERIC,
                        fontSize: 13,
                        textAlign: columns[index]?.align === 'right' ? 'right' : 'center',
                        fontWeight: cell.bold ? '600' : '400',
                        color: cell.faint ? COLORS.faint : cell.tone || COLORS.text,
                      }}
                    >
                      {cell.text}
                    </Text>
                  );
                  const style = [cellBorder, {
                    width: columns[index]?.width || 60,
                    height: rowHeight,
                    backgroundColor: cell.highlight
                      ? COLORS.warningSoft
                      : rowIndex % 2 ? COLORS.surfaceAlt : COLORS.surface,
                  }];
                  return cell.onPress
                    ? <Pressable key={index} onPress={cell.onPress} style={style}>{body}</Pressable>
                    : <View key={index} style={style}>{body}</View>;
                })}
              </View>
            ))}
          </View>
        </ScrollView>
      </View>
    </View>
  );
}

/* ---------------------------- dialog host --------------------------- */
/**
 * Renders confirmations raised through src/dialog.js. Mounted once, at the top
 * of the app, so any screen can await a confirmation without holding its own
 * modal state.
 */
export function DialogHost() {
  const [request, setRequest] = useState(null);

  React.useEffect(() => {
    registerDialogHandler((options) => setRequest(options));
    return () => registerDialogHandler(null);
  }, []);

  function close(answer) {
    request?.resolve(answer);
    setRequest(null);
  }

  if (!request) return null;

  return (
    <Modal visible transparent animationType="fade" onRequestClose={() => close(false)}>
      <View
        style={{
          flex: 1, backgroundColor: COLORS.overlay,
          alignItems: 'center', justifyContent: 'center', padding: 22,
        }}
      >
        <View
          style={{
            ...sheet, padding: SPACE.xl, maxWidth: 420,
          }}
          accessibilityViewIsModal
          accessibilityRole="alert"
        >
          {!!request.title && (
            <Text style={[TYPE.sectionTitle, { fontSize: 17, color: COLORS.text, marginBottom: SPACE.sm }]}>
              {request.title}
            </Text>
          )}
          {!!request.message && (
            <Text style={[TYPE.body, { color: COLORS.muted, marginBottom: SPACE.xl }]}>
              {request.message}
            </Text>
          )}
          <View style={[styles.row, styles.gap8]}>
            {!request.single && (
              <Button
                title={request.cancelText}
                tone="ghost"
                style={styles.flex1}
                onPress={() => close(false)}
              />
            )}
            <Button
              title={request.confirmText}
              tone={request.tone === 'danger' ? 'danger' : request.single ? 'primary' : 'primary'}
              style={styles.flex1}
              onPress={() => close(true)}
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}
