/**
 * Line icons for navigation and page titles (24 x 24 grid, 1.8 stroke),
 * drawn in the style of the Lucide set so they sit well together.
 */
import React from 'react';
import Svg, { Circle, Path, Rect } from 'react-native-svg';

const P = {
  dashboard: [['rect', 3, 3, 7, 9], ['rect', 14, 3, 7, 5], ['rect', 14, 12, 7, 9], ['rect', 3, 16, 7, 5]],
  analytics: ['M3 3v18h18', 'M7 15l4-5 3 3 5-7'],
  sales: ['M6 6h15l-1.5 8.5a2 2 0 0 1-2 1.5H9a2 2 0 0 1-2-1.6L5 3H2', ['circle', 9, 20, 1.3], ['circle', 18, 20, 1.3]],
  inventory: ['M21 8l-9-5-9 5v8l9 5 9-5V8z', 'M3.3 7.6L12 12.5l8.7-4.9', 'M12 22V12.5'],
  warehouse: ['M3 21V8l9-5 9 5v13', 'M7 21v-8h10v8', 'M7 17h10'],
  sync: ['M21 12a9 9 0 0 1-15.4 6.4L3 16', 'M3 21v-5h5', 'M3 12a9 9 0 0 1 15.4-6.4L21 8', 'M21 3v5h-5'],
  ledger: ['M4 4h16v16H4z', 'M8 9h8', 'M8 13h8', 'M8 17h5'],
  rates: [['circle', 12, 12, 9], 'M15 9.5c-.6-1-1.7-1.5-3-1.5-1.7 0-3 .9-3 2.2 0 3 6 1.6 6 4.6 0 1.3-1.3 2.2-3 2.2-1.4 0-2.5-.6-3.1-1.6', 'M12 6v2', 'M12 16v2'],
  reports: ['M9 3h6v3H9z', 'M8 4.5H6a1 1 0 0 0-1 1V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5.5a1 1 0 0 0-1-1h-2', 'M9 12h6', 'M9 16h4'],
  comms: ['M3 11v3a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1z', 'M16 9a4 4 0 0 1 0 6', 'M19 6a8 8 0 0 1 0 12'],
  smtp: [['rect', 3, 5, 18, 14, 2], 'M3 7l9 6 9-6'],
  users: [['circle', 9, 8, 3.5], 'M2.5 20a6.5 6.5 0 0 1 13 0', 'M16 4.6a3.5 3.5 0 0 1 0 6.8', 'M18 14.2a6.5 6.5 0 0 1 3.5 5.8'],
  cash: [['rect', 2, 6, 20, 12, 2], ['circle', 12, 12, 2.8], 'M6 10v4', 'M18 10v4'],
  qr: [['rect', 3, 3, 7, 7, 1], ['rect', 14, 3, 7, 7, 1], ['rect', 3, 14, 7, 7, 1], 'M14 14h3v3h-3z', 'M20 14v1', 'M14 20h1', 'M18 18h3v3h-3z'],
  audit: ['M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6l8-3z', 'M8.5 12l2.5 2.5 4.5-5'],
  account: [['circle', 12, 8, 4], 'M4 21a8 8 0 0 1 16 0'],
  bell: ['M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
  logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'M16 17l5-5-5-5', 'M21 12H9'],
  chevronDown: ['M6 9l6 6 6-6'],
  chevronRight: ['M9 6l6 6-6 6'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  plane: ['M17.8 19.2L16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z'],
  key: [['circle', 7.5, 15.5, 4.5], 'M10.7 12.3L21 2', 'M16 7l3 3', 'M19 4l2 2'],
  settings: [['circle', 12, 12, 3], 'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'],
};

export default function Icon({ name, size = 20, color = '#16202B', strokeWidth = 1.8 }) {
  const parts = P[name] || P.dashboard;
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      {parts.map((part, i) => {
        if (typeof part === 'string') {
          return <Path key={i} d={part} stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />;
        }
        if (part[0] === 'circle') {
          return <Circle key={i} cx={part[1]} cy={part[2]} r={part[3]} stroke={color} strokeWidth={strokeWidth} />;
        }
        return (
          <Rect key={i} x={part[1]} y={part[2]} width={part[3]} height={part[4]} rx={part[5] ?? 1.5}
            stroke={color} strokeWidth={strokeWidth} />
        );
      })}
    </Svg>
  );
}
