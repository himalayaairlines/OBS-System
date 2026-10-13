/**
 * The password rules, mirrored from the server (security.password_problems)
 * so people see what is missing while typing. The server still decides.
 */
import React from 'react';
import { View } from 'react-native';

import { Text } from './text';

export const MIN_LENGTH = 10;

export function passwordChecks(password = '', person = {}) {
  const value = password || '';
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(value)).length;
  const lowered = value.toLowerCase();
  const words = [person.name, person.employee_id, (person.email || '').split('@')[0]]
    .flatMap((part) => String(part || '').toLowerCase().split(/[\s._\-@]+/))
    .filter((part) => part.length >= 3);
  return {
    length: value.length >= MIN_LENGTH,
    mix: classes >= 3,
    personal: !words.some((word) => lowered.includes(word)),
  };
}

export function passwordOk(password, person) {
  const c = passwordChecks(password, person);
  return c.length && c.mix && c.personal;
}

/** ✓ / ○ checklist under a new-password field. */
export function PasswordRules({ password, person, labels = {}, colors = {} }) {
  const c = passwordChecks(password, person);
  const ok = colors.ok || '#0F7552';
  const idle = colors.idle || '#8A96A8';
  const bad = colors.bad || '#B3261E';
  const row = (pass, text, neutral) => (
    <Text style={{ fontSize: 13, lineHeight: 20, color: password && !neutral ? (pass ? ok : idle) : idle }}>
      {neutral ? '' : (password && pass ? '✓  ' : '○  ')}{text}
    </Text>
  );
  return (
    <View style={{ marginTop: -8, marginBottom: 14 }}>
      {row(c.length, labels.length || `At least ${MIN_LENGTH} characters`)}
      {row(c.mix, labels.mix || '3 of: lower-case, upper-case, digit, symbol')}
      <Text style={{ fontSize: 13, lineHeight: 20, color: password && !c.personal ? bad : idle }}>
        {labels.personal || 'Must not contain your name or e-mail name.'}
      </Text>
    </View>
  );
}
