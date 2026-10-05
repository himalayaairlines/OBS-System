import React from 'react';
import { Text as RNText, View } from 'react-native';
import Svg, { Circle, G, Line, Path, Polyline, Rect, Text as SvgText } from 'react-native-svg';

import { CHART_COLORS, COLORS, compact, styles } from './theme';

function niceMax(value) {
  if (!value || value <= 0) return 10;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * magnitude;
}

export function EmptyChart({ label = 'No data for this period.' }) {
  return <RNText style={styles.empty}>{label}</RNText>;
}

/** Vertical bars with a value axis. `data` = [{ label, value }]. */
export function BarChart({ data = [], height = 210, width = 340, color = COLORS.primary, format = compact }) {
  if (!data.length || data.every((d) => !Number(d.value))) return <EmptyChart />;

  const padLeft = 42;
  const padBottom = 34;
  const padTop = 12;
  const plotWidth = width - padLeft - 8;
  const plotHeight = height - padBottom - padTop;
  const max = niceMax(Math.max(...data.map((d) => Number(d.value) || 0)));
  const slot = plotWidth / data.length;
  const barWidth = Math.max(8, Math.min(38, slot * 0.6));

  return (
    <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
      {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
        const y = padTop + plotHeight * (1 - tick);
        return (
          <G key={tick}>
            <Line x1={padLeft} y1={y} x2={width - 8} y2={y} stroke={COLORS.border} strokeWidth={1} />
            <SvgText x={padLeft - 6} y={y + 4} fontSize="9" fill={COLORS.faint} textAnchor="end">
              {format(max * tick)}
            </SvgText>
          </G>
        );
      })}
      {data.map((item, index) => {
        const value = Number(item.value) || 0;
        const barHeight = Math.max(1, (value / max) * plotHeight);
        const x = padLeft + slot * index + (slot - barWidth) / 2;
        const y = padTop + plotHeight - barHeight;
        return (
          <G key={index}>
            <Rect x={x} y={y} width={barWidth} height={barHeight} rx={3} fill={item.color || color} />
            <SvgText
              x={x + barWidth / 2}
              y={height - padBottom + 14}
              fontSize="9"
              fill={COLORS.muted}
              textAnchor="middle"
            >
              {String(item.label).slice(0, 8)}
            </SvgText>
            {value > 0 && (
              <SvgText x={x + barWidth / 2} y={y - 4} fontSize="9" fill={COLORS.text} textAnchor="middle">
                {format(value)}
              </SvgText>
            )}
          </G>
        );
      })}
    </Svg>
  );
}

/** Horizontal bars, better for long labels such as crew names. */
export function RankBarChart({ data = [], width = 340, rowHeight = 30, format = compact }) {
  if (!data.length) return <EmptyChart />;
  const height = data.length * rowHeight + 16;
  const labelWidth = 104;
  const plotWidth = width - labelWidth - 54;
  const max = Math.max(...data.map((d) => Number(d.value) || 0)) || 1;

  return (
    <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
      {data.map((item, index) => {
        const value = Number(item.value) || 0;
        const barWidth = Math.max(2, (value / max) * plotWidth);
        const y = index * rowHeight + 8;
        return (
          <G key={index}>
            <SvgText x={0} y={y + 14} fontSize="10.5" fill={COLORS.text}>
              {String(item.label).slice(0, 16)}
            </SvgText>
            <Rect
              x={labelWidth} y={y + 3} width={plotWidth} height={rowHeight - 12}
              rx={4} fill={COLORS.surfaceAlt}
            />
            <Rect
              x={labelWidth} y={y + 3} width={barWidth} height={rowHeight - 12}
              rx={4} fill={item.color || CHART_COLORS[index % CHART_COLORS.length]}
            />
            <SvgText x={labelWidth + plotWidth + 6} y={y + 14} fontSize="10" fill={COLORS.muted}>
              {format(value)}
            </SvgText>
          </G>
        );
      })}
    </Svg>
  );
}

/** Line chart with a target reference line. `data` = [{ label, value }]. */
export function LineChartView({ data = [], height = 200, width = 340, target = null, format = compact }) {
  if (data.length < 2) return <EmptyChart label="At least two data points are needed for a trend." />;

  const padLeft = 42;
  const padBottom = 30;
  const padTop = 12;
  const plotWidth = width - padLeft - 10;
  const plotHeight = height - padBottom - padTop;
  const values = data.map((d) => Number(d.value) || 0);
  const max = niceMax(Math.max(...values, target || 0));
  const stepX = plotWidth / (data.length - 1);
  const pointFor = (value, index) => [
    padLeft + stepX * index,
    padTop + plotHeight - (value / max) * plotHeight,
  ];
  const points = values.map(pointFor).map((p) => p.join(',')).join(' ');

  return (
    <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
      {[0, 0.5, 1].map((tick) => {
        const y = padTop + plotHeight * (1 - tick);
        return (
          <G key={tick}>
            <Line x1={padLeft} y1={y} x2={width - 10} y2={y} stroke={COLORS.border} strokeWidth={1} />
            <SvgText x={padLeft - 6} y={y + 4} fontSize="9" fill={COLORS.faint} textAnchor="end">
              {format(max * tick)}
            </SvgText>
          </G>
        );
      })}
      {target ? (
        <G>
          <Line
            x1={padLeft}
            y1={padTop + plotHeight - (target / max) * plotHeight}
            x2={width - 10}
            y2={padTop + plotHeight - (target / max) * plotHeight}
            stroke={COLORS.accent}
            strokeWidth={1.5}
            strokeDasharray="5 4"
          />
          <SvgText
            x={width - 12}
            y={padTop + plotHeight - (target / max) * plotHeight - 5}
            fontSize="9"
            fill={COLORS.accent}
            textAnchor="end"
          >
            target
          </SvgText>
        </G>
      ) : null}
      <Polyline points={points} fill="none" stroke={COLORS.primary} strokeWidth={2.5} />
      {values.map((value, index) => {
        const [x, y] = pointFor(value, index);
        return <Circle key={index} cx={x} cy={y} r={3.5} fill={COLORS.primary} />;
      })}
      {data.map((item, index) => {
        if (data.length > 8 && index % Math.ceil(data.length / 6) !== 0) return null;
        return (
          <SvgText
            key={index}
            x={padLeft + stepX * index}
            y={height - padBottom + 15}
            fontSize="9"
            fill={COLORS.muted}
            textAnchor="middle"
          >
            {String(item.label).slice(5)}
          </SvgText>
        );
      })}
    </Svg>
  );
}

function arcPath(cx, cy, radius, inner, start, end) {
  const toXY = (angle, r) => [
    cx + r * Math.cos((angle - 90) * (Math.PI / 180)),
    cy + r * Math.sin((angle - 90) * (Math.PI / 180)),
  ];
  const large = end - start > 180 ? 1 : 0;
  const [x1, y1] = toXY(start, radius);
  const [x2, y2] = toXY(end, radius);
  const [x3, y3] = toXY(end, inner);
  const [x4, y4] = toXY(start, inner);
  return `M ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${inner} ${inner} 0 ${large} 0 ${x4} ${y4} Z`;
}

/** Donut with a legend. `data` = [{ label, value }]. */
export function DonutChart({ data = [], size = 168, format = compact }) {
  const usable = data.filter((d) => Number(d.value) > 0);
  if (!usable.length) return <EmptyChart />;
  const total = usable.reduce((sum, d) => sum + Number(d.value), 0);
  const radius = size / 2 - 4;
  const inner = radius * 0.58;
  let angle = 0;

  return (
    <View style={[styles.row, { alignItems: 'center', gap: 14, flexWrap: 'wrap' }]}>
      <Svg width={size} height={size}>
        {usable.map((item, index) => {
          const sweep = (Number(item.value) / total) * 360;
          const path = arcPath(size / 2, size / 2, radius, inner, angle, angle + sweep - 0.4);
          angle += sweep;
          return <Path key={index} d={path} fill={CHART_COLORS[index % CHART_COLORS.length]} />;
        })}
        <SvgText
          x={size / 2} y={size / 2 + 4} fontSize="13" fontWeight="700"
          fill={COLORS.text} textAnchor="middle"
        >
          {format(total)}
        </SvgText>
      </Svg>
      <View style={{ flex: 1, minWidth: 130 }}>
        {usable.map((item, index) => (
          <View key={index} style={[styles.rowBetween, { paddingVertical: 3 }]}>
            <View style={[styles.row, { gap: 6, flex: 1 }]}>
              <View
                style={{
                  width: 10, height: 10, borderRadius: 3,
                  backgroundColor: CHART_COLORS[index % CHART_COLORS.length],
                }}
              />
              <RNText style={{ fontSize: 12, color: COLORS.text, flex: 1 }} numberOfLines={1}>
                {item.label}
              </RNText>
            </View>
            <RNText style={{ fontSize: 11.5, color: COLORS.muted }}>
              {((Number(item.value) / total) * 100).toFixed(0)}%
            </RNText>
          </View>
        ))}
      </View>
    </View>
  );
}

/** Horizontal progress bar used for target achievement. */
export function ProgressBar({ value, max, tone = COLORS.primary, height = 10 }) {
  const ratio = Math.max(0, Math.min(1.5, (Number(value) || 0) / (Number(max) || 1)));
  return (
    <View style={{ backgroundColor: COLORS.surfaceAlt, borderRadius: height, height, overflow: 'hidden' }}>
      <View
        style={{
          width: `${Math.min(100, ratio * 100)}%`,
          height,
          backgroundColor: ratio >= 1 ? COLORS.success : ratio >= 0.7 ? tone : COLORS.warning,
        }}
      />
    </View>
  );
}
