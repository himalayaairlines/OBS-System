import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator, Modal, Pressable, ScrollView, Text, TextInput, View,
} from 'react-native';

import { registerDialogHandler } from './dialog';
import { COLORS, MONTHS, styles } from './theme';

/* ----------------------------- buttons ----------------------------- */
export function Button({ title, onPress, disabled, tone = 'primary', small, style }) {
  const palette = {
    primary: { bg: COLORS.primary, fg: '#FFFFFF', border: COLORS.primary },
    secondary: { bg: COLORS.surface, fg: COLORS.primary, border: COLORS.primary },
    ghost: { bg: 'transparent', fg: COLORS.muted, border: COLORS.border },
    success: { bg: COLORS.success, fg: '#FFFFFF', border: COLORS.success },
    danger: { bg: COLORS.danger, fg: '#FFFFFF', border: COLORS.danger },
    warning: { bg: COLORS.warning, fg: '#FFFFFF', border: COLORS.warning },
  }[tone];

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        {
          backgroundColor: palette.bg,
          borderColor: palette.border,
          borderWidth: 1,
          borderRadius: 10,
          paddingVertical: small ? 8 : 12,
          paddingHorizontal: small ? 12 : 16,
          alignItems: 'center',
          opacity: disabled ? 0.45 : pressed ? 0.8 : 1,
        },
        style,
      ]}
    >
      <Text style={{ color: palette.fg, fontWeight: '700', fontSize: small ? 12 : 14 }}>
        {title}
      </Text>
    </Pressable>
  );
}

export function Chip({ label, active, onPress, tone = 'primary', disabled }) {
  const color = { primary: COLORS.primary, success: COLORS.success, danger: COLORS.danger }[tone];
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={{
        paddingVertical: 7,
        paddingHorizontal: 13,
        borderRadius: 20,
        borderWidth: 1,
        borderColor: active ? color : COLORS.border,
        backgroundColor: active ? color : COLORS.surface,
        opacity: disabled ? 0.45 : 1,
      }}
    >
      <Text style={{ color: active ? '#FFFFFF' : COLORS.text, fontSize: 12.5, fontWeight: '600' }}>
        {label}
      </Text>
    </Pressable>
  );
}

export function Pill({ label, tone = 'info' }) {
  const palette = {
    success: { bg: '#E3F5EE', fg: COLORS.success },
    warning: { bg: '#FDF1DF', fg: COLORS.warning },
    danger: { bg: '#FCE8E6', fg: COLORS.danger },
    info: { bg: '#E8F0FE', fg: COLORS.info },
    muted: { bg: COLORS.surfaceAlt, fg: COLORS.muted },
  }[tone];
  return (
    <View style={{ backgroundColor: palette.bg, borderRadius: 14, paddingVertical: 3, paddingHorizontal: 9 }}>
      <Text style={{ color: palette.fg, fontSize: 11, fontWeight: '700' }}>{label}</Text>
    </View>
  );
}

/* ----------------------------- layout ------------------------------ */
export function Card({ children, style, tight }) {
  return <View style={[styles.card, tight && styles.cardTight, style]}>{children}</View>;
}

export function SectionTitle({ title, description, right }) {
  return (
    <View style={[styles.rowBetween, { marginBottom: description ? 10 : 8 }]}>
      <View style={styles.flex1}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {!!description && <Text style={styles.sectionDescription}>{description}</Text>}
      </View>
      {right}
    </View>
  );
}

export function PageHeader({ title, description, right }) {
  return (
    <View style={[styles.rowBetween, { marginBottom: 14 }]}>
      <View style={styles.flex1}>
        <Text style={styles.pageTitle}>{title}</Text>
        {!!description && <Text style={styles.pageDescription}>{description}</Text>}
      </View>
      {right}
    </View>
  );
}

export function Metric({ label, value, sub, tone = 'default', width }) {
  const color = {
    default: COLORS.text, success: COLORS.success,
    danger: COLORS.danger, warning: COLORS.warning, primary: COLORS.primary,
  }[tone];
  return (
    <View
      style={{
        backgroundColor: COLORS.surfaceAlt,
        borderRadius: 12,
        padding: 12,
        minWidth: width || 148,
        flexGrow: 1,
      }}
    >
      <Text style={{ fontSize: 11, color: COLORS.muted, fontWeight: '600' }}>{label}</Text>
      <Text style={{ fontSize: 19, fontWeight: '700', color, marginTop: 4 }} numberOfLines={1}>
        {value}
      </Text>
      {!!sub && <Text style={{ fontSize: 11, color: COLORS.faint, marginTop: 2 }}>{sub}</Text>}
    </View>
  );
}

export function Banner({ message, tone = 'info' }) {
  if (!message) return null;
  const palette = {
    info: { bg: '#E8F0FE', border: '#BBD2FB', fg: '#1A4FBF' },
    success: { bg: '#E3F5EE', border: '#B6E2D2', fg: COLORS.success },
    warning: { bg: '#FDF1DF', border: '#F2D9AE', fg: COLORS.warning },
    danger: { bg: '#FCE8E6', border: '#F3C2BD', fg: COLORS.danger },
  }[tone];
  return (
    <View style={[styles.banner, { backgroundColor: palette.bg, borderColor: palette.border }]}>
      <Text style={[styles.bannerText, { color: palette.fg }]}>{message}</Text>
    </View>
  );
}

export function Field({ label, hint, ...props }) {
  return (
    <View style={{ marginBottom: 12 }}>
      {!!label && <Text style={styles.label}>{label}</Text>}
      <TextInput
        placeholderTextColor={COLORS.faint}
        {...props}
        style={[styles.input, props.multiline && styles.inputMultiline, props.style]}
      />
      {!!hint && <Text style={[styles.mono, { marginTop: 4 }]}>{hint}</Text>}
    </View>
  );
}

export function Select({ label, valueLabel, onPress, style, placeholder = 'Select' }) {
  return (
    <View style={[{ marginBottom: 12 }, style]}>
      {!!label && <Text style={styles.label}>{label}</Text>}
      <Pressable onPress={onPress} style={[styles.input, styles.rowBetween]}>
        <Text style={{ color: valueLabel ? COLORS.text : COLORS.faint, fontSize: 14 }} numberOfLines={1}>
          {valueLabel || placeholder}
        </Text>
        <Text style={{ color: COLORS.muted, fontSize: 12 }}>▾</Text>
      </Pressable>
    </View>
  );
}

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
        style={{ flex: 1, backgroundColor: '#0009', justifyContent: 'center', padding: 22 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: COLORS.surface, borderRadius: 14, maxHeight: '75%', overflow: 'hidden' }}
        >
          <View style={{ padding: 15, borderBottomWidth: 1, borderBottomColor: COLORS.border }}>
            <View style={styles.rowBetween}>
              <Text style={styles.sectionTitle}>{title}</Text>
              {multi && <Pill label={`${(selectedValue || []).length} selected`} tone="info" />}
            </View>
            {showSearch && (
              <TextInput
                value={term}
                onChangeText={setTerm}
                placeholder={searchPlaceholder}
                placeholderTextColor={COLORS.faint}
                autoCorrect={false}
                style={[styles.input, { marginTop: 10, paddingVertical: 8 }]}
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
                  style={{
                    padding: 14,
                    borderBottomWidth: 1,
                    borderBottomColor: COLORS.surfaceAlt,
                    backgroundColor: selected ? '#EAF1FA' : 'transparent',
                  }}
                >
                  <Text style={{ fontSize: 14, color: COLORS.text, fontWeight: selected ? '700' : '400' }}>
                    {selected ? '✓  ' : ''}{option.label}
                  </Text>
                  {!!option.sub && <Text style={[styles.mono, { marginTop: 2 }]}>{option.sub}</Text>}
                </Pressable>
              );
            })}
          </ScrollView>
          <View style={{ padding: 12, borderTopWidth: 1, borderTopColor: COLORS.border }}>
            <Button title={multi ? 'Done' : 'Close'} tone="secondary" onPress={onClose} />
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
        style={{ flex: 1, backgroundColor: '#0009', justifyContent: 'center', padding: 22 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: COLORS.surface, borderRadius: 14, padding: 16 }}
        >
          <Text style={[styles.sectionTitle, { marginBottom: 12 }]}>{title}</Text>

          <View style={[styles.rowBetween, { marginBottom: 10 }]}>
            <Button title="‹" tone="ghost" small onPress={() => step(-1)} style={{ width: 46 }} />
            <Text style={{ fontWeight: '700', fontSize: 15, color: COLORS.text }}>
              {MONTHS[cursor.month]} {cursor.year}
            </Text>
            <Button title="›" tone="ghost" small onPress={() => step(1)} style={{ width: 46 }} />
          </View>

          <View style={styles.row}>
            {WEEKDAYS.map((day) => (
              <Text
                key={day}
                style={{ flex: 1, textAlign: 'center', fontSize: 11, color: COLORS.faint, fontWeight: '700' }}
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
                    <Text style={{ color: selected ? '#FFFFFF' : COLORS.text, fontWeight: selected ? '700' : '500' }}>
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
      <Text style={[styles.mono, { marginTop: 8 }]}>{label}</Text>
    </View>
  );
}

export function KeyValue({ label, value, tone }) {
  return (
    <View style={[styles.rowBetween, { paddingVertical: 5 }]}>
      <Text style={{ fontSize: 12.5, color: COLORS.muted }}>{label}</Text>
      <Text style={{ fontSize: 13, color: tone || COLORS.text, fontWeight: '600' }}>{value}</Text>
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
        style={{ flex: 1, backgroundColor: '#0009', justifyContent: 'center', padding: 16 }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{ backgroundColor: COLORS.surface, borderRadius: 14, maxHeight: '88%', overflow: 'hidden' }}
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
                      width: 40, textAlign: 'center', fontSize: 11,
                      fontWeight: '700', color: COLORS.faint,
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
                <Text style={{ width: 26, fontSize: 11, color: COLORS.muted, fontWeight: '600' }}>
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
                              fontSize: 10.5,
                              fontWeight: isSelected ? '700' : '500',
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
        style={[styles.input, { paddingVertical: 9 }]}
      />
      {!!value && (
        <Pressable
          onPress={() => onChangeText('')}
          style={{ position: 'absolute', right: 10, top: 9, padding: 4 }}
        >
          <Text style={{ color: COLORS.muted, fontSize: 14 }}>×</Text>
        </Pressable>
      )}
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
  frozen, groupHeader, columns, rows, rowHeight = 34, headerHeight = 30,
}) {
  const headerRows = frozen.header.length;
  const totalHeaderHeight = headerHeight * (groupHeader ? 2 : headerRows);

  const cellBorder = {
    borderRightWidth: 1,
    borderRightColor: COLORS.border,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.surfaceAlt,
    justifyContent: 'center',
    paddingHorizontal: 6,
  };

  return (
    <View style={{ borderWidth: 1, borderColor: COLORS.border, borderRadius: 10, overflow: 'hidden' }}>
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
                style={{ color: '#FFFFFF', fontSize: 11, fontWeight: '700' }}
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
                  <Text numberOfLines={1} style={{ fontSize: 11.5, fontWeight: '600', color: COLORS.text }}>
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
                    <Text numberOfLines={1} style={{ color: '#FFFFFF', fontSize: 10.5, fontWeight: '700' }}>
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
                  <Text numberOfLines={2} style={{ color: '#FFFFFF', fontSize: 9.5, fontWeight: '700', textAlign: 'center' }}>
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
                        fontSize: 11.5,
                        textAlign: columns[index]?.align === 'right' ? 'right' : 'center',
                        fontWeight: cell.bold ? '700' : '400',
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
                      ? '#FDF1DF'
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
          flex: 1, backgroundColor: '#0009',
          alignItems: 'center', justifyContent: 'center', padding: 22,
        }}
      >
        <View
          style={{
            backgroundColor: COLORS.surface, borderRadius: 16, padding: 20,
            width: '100%', maxWidth: 420,
          }}
        >
          {!!request.title && (
            <Text style={{ fontSize: 17, fontWeight: '800', color: COLORS.text, marginBottom: 8 }}>
              {request.title}
            </Text>
          )}
          {!!request.message && (
            <Text style={{ fontSize: 13.5, color: COLORS.muted, lineHeight: 20, marginBottom: 18 }}>
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
