import React, { useEffect, useRef, useState } from 'react';
import {
  Platform, Pressable, ScrollView, StatusBar, Text, TextInput, View, useWindowDimensions,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Defs, LinearGradient, Path, RadialGradient, Rect, Stop } from 'react-native-svg';

import { API_BASE, KEYS, api } from '../api';
import { ws } from '../fonts';
import { LANGUAGES, LOGIN_TEXT } from '../i18n/login';
import { Banner } from '../ui';

const LANGUAGE_KEY = 'obs.loginLanguage';
const C = {
  navy: '#0A1A3F',
  blue: '#1D4E9E',
  glow: '#2F6FED',
  page: '#F5F7FB',
  card: '#FFFFFF',
  border: '#E4E9F1',
  text: '#16202B',
  muted: '#5B6778',
  faint: '#8A96A8',
  input: '#EAF1FB',
  focus: '#2F6FED',
  button: '#2468E0',
  link: '#1F5FCC',
  bullet: '#4DA3FF',
};

/* ------------------------------ pieces ------------------------------ */
function HeroBackground() {
  return (
    <Svg style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} width="100%" height="100%"
      preserveAspectRatio="none">
      <Defs>
        <LinearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0" stopColor={C.navy} />
          <Stop offset="0.55" stopColor="#10306B" />
          <Stop offset="1" stopColor={C.blue} />
        </LinearGradient>
        <RadialGradient id="glow" cx="80%" cy="95%" r="60%">
          <Stop offset="0" stopColor={C.glow} stopOpacity="0.45" />
          <Stop offset="1" stopColor={C.glow} stopOpacity="0" />
        </RadialGradient>
      </Defs>
      <Rect x="0" y="0" width="100%" height="100%" fill="url(#sky)" />
      <Rect x="0" y="0" width="100%" height="100%" fill="url(#glow)" />
    </Svg>
  );
}

function PlaneMark({ size = 52 }) {
  return (
    <View style={{
      width: size, height: size, borderRadius: 14, alignItems: 'center', justifyContent: 'center',
      backgroundColor: 'rgba(255,255,255,0.10)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.18)',
    }}>
      <Svg width={size * 0.46} height={size * 0.46} viewBox="0 0 24 24">
        <Path
          fill="#FFFFFF"
          d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5Z"
        />
      </Svg>
    </View>
  );
}

function LanguageSwitch({ value, onChange, dark, label }) {
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
      style={{
        flexDirection: 'row', padding: 4, borderRadius: 12, gap: 2,
        backgroundColor: dark ? 'rgba(255,255,255,0.10)' : '#FFFFFF',
        borderWidth: 1, borderColor: dark ? 'rgba(255,255,255,0.18)' : C.border,
      }}
    >
      {LANGUAGES.map((lang) => {
        const active = lang.code === value;
        return (
          <Pressable
            key={lang.code}
            onPress={() => onChange(lang.code)}
            accessibilityRole="radio"
            accessibilityState={{ selected: active }}
            style={{
              paddingVertical: 6, paddingHorizontal: 11, borderRadius: 9,
              backgroundColor: active ? (dark ? 'rgba(255,255,255,0.22)' : '#F1F4F9') : 'transparent',
            }}
          >
            <Text style={[ws(active ? 700 : 500), {
              fontSize: 13, color: dark ? '#FFFFFF' : (active ? C.text : C.muted),
            }]}>
              {lang.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function Input({ label, right, inputRef, ...props }) {
  const [focused, setFocused] = useState(false);
  return (
    <View style={{ marginBottom: 18 }}>
      <Text style={[ws(600), { fontSize: 14, color: C.text, marginBottom: 8 }]}>{label}</Text>
      <View style={{
        flexDirection: 'row', alignItems: 'center', backgroundColor: C.input, borderRadius: 10,
        borderWidth: 1.5, borderColor: focused ? C.focus : 'transparent',
      }}>
        <TextInput
          ref={inputRef}
          placeholderTextColor={C.faint}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          accessibilityLabel={label}
          {...props}
          style={[ws(500), {
            flex: 1, minWidth: 0, width: '100%', fontSize: 16, color: C.text, paddingHorizontal: 16, paddingVertical: 14,
            ...(Platform.OS === 'web' ? { outlineStyle: 'none' } : null),
          }]}
        />
        {right}
      </View>
    </View>
  );
}

function EyeIcon({ open }) {
  return (
    <Svg width={20} height={20} viewBox="0 0 24 24" fill="none">
      <Path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" stroke={C.muted} strokeWidth={1.7} strokeLinejoin="round" />
      <Path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke={C.muted} strokeWidth={1.7} />
      {!open && <Path d="M4 4l16 16" stroke={C.muted} strokeWidth={1.7} strokeLinecap="round" />}
    </Svg>
  );
}

function PrimaryButton({ title, onPress, disabled }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      style={({ pressed }) => ({
        backgroundColor: C.button, borderRadius: 12, paddingVertical: 15, alignItems: 'center', marginTop: 4,
        opacity: disabled ? 0.6 : pressed ? 0.88 : 1,
        shadowColor: C.button, shadowOpacity: 0.28, shadowRadius: 12, shadowOffset: { width: 0, height: 6 },
        elevation: 3,
      })}
    >
      <Text style={[ws(600), { color: '#FFFFFF', fontSize: 16 }]}>{title}</Text>
    </Pressable>
  );
}

function LinkButton({ title, onPress, align = 'flex-end' }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="link" hitSlop={8} style={{ alignSelf: align, marginBottom: 20 }}>
      <Text style={[ws(600), { color: C.link, fontSize: 14.5 }]}>{title}</Text>
    </Pressable>
  );
}

/* ------------------------------ screen ------------------------------ */
export default function LoginScreen({ onLogin, notify }) {
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const wide = width >= 900;
  const tablet = width >= 600;
  const phone = width < 480;

  const [lang, setLang] = useState('en');
  const t = LOGIN_TEXT[lang];
  const [employeeId, setEmployeeId] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef(null);

  // Break-glass reset for an administrator who is locked out or has forgotten
  // the password. Only an administrator can unlock accounts, so without this
  // one forgotten password would strand the whole system.
  const [recovering, setRecovering] = useState(false);
  const [recovery, setRecovery] = useState({ employee_id: '', recovery_code: '', new_password: '', confirm_password: '' });
  const [recoveryMessage, setRecoveryMessage] = useState(null);
  const setRecoveryField = (key, value) => setRecovery((r) => ({ ...r, [key]: value }));

  useEffect(() => {
    AsyncStorage.getItem(LANGUAGE_KEY).then((saved) => { if (LOGIN_TEXT[saved]) setLang(saved); }).catch(() => {});
  }, []);

  function changeLanguage(code) {
    setLang(code);
    AsyncStorage.setItem(LANGUAGE_KEY, code).catch(() => {});
  }

  async function submit() {
    setError('');
    if (!employeeId.trim() || !password) return setError(t.missing);
    setBusy(true);
    try {
      const session = await api('/login', {
        method: 'POST', auth: false, body: { employee_id: employeeId.trim(), password },
      });
      await AsyncStorage.setItem(KEYS.session, JSON.stringify(session));
      onLogin(session);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  async function submitRecovery() {
    setRecoveryMessage(null);
    if (!recovery.employee_id.trim() || !recovery.recovery_code.trim()) {
      return setRecoveryMessage({ tone: 'danger', text: t.recMissing });
    }
    if (recovery.new_password.length < 4) return setRecoveryMessage({ tone: 'danger', text: t.recShort });
    if (recovery.new_password !== recovery.confirm_password) {
      return setRecoveryMessage({ tone: 'danger', text: t.recMismatch });
    }
    setBusy(true);
    try {
      const result = await api('/admin-recovery', {
        method: 'POST',
        auth: false,
        body: {
          employee_id: recovery.employee_id.trim(),
          recovery_code: recovery.recovery_code.trim(),
          new_password: recovery.new_password,
          confirm_password: recovery.confirm_password,
        },
      });
      setEmployeeId(result.employee_id);
      setPassword('');
      setRecovering(false);
      setRecovery({ employee_id: '', recovery_code: '', new_password: '', confirm_password: '' });
      setError('');
      notify?.({ title: t.signIn, message: t.recDone(result.employee_id) });
    } catch (err) {
      setRecoveryMessage({ tone: 'danger', text: err.message });
    }
    setBusy(false);
  }

  /* ------------------------------ hero ------------------------------ */
  const hero = (
    <View style={{
      ...(wide ? { width: '51%' } : null),
      paddingHorizontal: wide ? 52 : 24,
      paddingTop: (wide ? 48 : 22) + (wide ? 0 : insets.top),
      paddingBottom: wide ? 36 : 30,
      justifyContent: 'space-between',
      overflow: 'hidden',
    }}>
      <HeroBackground />
      <View style={{
        flexDirection: phone ? 'column' : 'row', alignItems: phone ? 'flex-start' : 'center',
        justifyContent: 'space-between', gap: 14,
      }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, flexShrink: 1 }}>
          <PlaneMark size={wide ? 52 : 44} />
          <View style={{ flexShrink: 1 }}>
            <Text style={[ws(700), { color: '#FFFFFF', fontSize: wide ? 19 : 16, letterSpacing: 0.4 }]} numberOfLines={1}>
              HIMALAYA AIRLINES
            </Text>
            <Text style={[ws(400), { color: 'rgba(255,255,255,0.78)', fontSize: wide ? 14 : 12.5, marginTop: 2 }]}>
              {t.brandSub}
            </Text>
          </View>
        </View>
        {!wide && <LanguageSwitch value={lang} onChange={changeLanguage} dark label={t.language} />}
      </View>

      <View style={{ marginTop: wide ? 0 : 26, maxWidth: 560 }}>
        <Text accessibilityRole="header" style={[ws(700), {
          color: '#FFFFFF', fontSize: wide ? 36 : tablet ? 30 : 25, lineHeight: wide ? 46 : tablet ? 38 : 32,
        }]}>
          {t.headline}
        </Text>
        <Text style={[ws(400), {
          color: 'rgba(255,255,255,0.80)', fontSize: wide ? 16.5 : 14.5, lineHeight: wide ? 26 : 22, marginTop: 14,
        }]}>
          {t.intro}
        </Text>
        {(wide || tablet) && (
          <View style={{ marginTop: 26, gap: 11 }}>
            {t.features.map((feature) => (
              <View key={feature} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 11 }}>
                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.bullet, marginTop: 9 }} />
                <Text style={[ws(400), { color: 'rgba(255,255,255,0.92)', fontSize: wide ? 16 : 15, lineHeight: 24, flex: 1 }]}>
                  {feature}
                </Text>
              </View>
            ))}
          </View>
        )}
      </View>

      {wide && (
        <Text style={[ws(400), { color: 'rgba(255,255,255,0.55)', fontSize: 13 }]}>{t.footer}</Text>
      )}
    </View>
  );

  /* ------------------------------ form ------------------------------ */
  const card = (
    <View style={{
      width: '100%', maxWidth: 480, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border,
      padding: tablet ? 38 : 24,
      shadowColor: '#0A1A3F', shadowOpacity: 0.06, shadowRadius: 24, shadowOffset: { width: 0, height: 8 }, elevation: 2,
    }}>
      {!recovering ? (
        <>
          <Text accessibilityRole="header" style={[ws(700), { fontSize: 28, color: C.text }]}>{t.signIn}</Text>
          <Text style={[ws(400), { fontSize: 16, color: C.muted, lineHeight: 25, marginTop: 8, marginBottom: 26 }]}>
            {t.signInSub}
          </Text>
          {!!error && <Banner tone="danger" message={error} />}
          <Input
            label={t.employeeId}
            value={employeeId}
            onChangeText={setEmployeeId}
            autoCapitalize="characters"
            autoCorrect={false}
            autoComplete="username"
            textContentType="username"
            placeholder="CREW001"
            returnKeyType="next"
            onSubmitEditing={() => passwordRef.current?.focus()}
          />
          <Input
            label={t.password}
            inputRef={passwordRef}
            value={password}
            onChangeText={setPassword}
            secureTextEntry={!showPassword}
            autoCapitalize="none"
            autoComplete="current-password"
            textContentType="password"
            placeholder="••••••••"
            returnKeyType="go"
            onSubmitEditing={submit}
            right={(
              <Pressable onPress={() => setShowPassword((s) => !s)} hitSlop={8}
                style={{ paddingHorizontal: 14, paddingVertical: 12 }}
                accessibilityRole="button" accessibilityLabel={showPassword ? t.hide : t.show}>
                <EyeIcon open={showPassword} />
              </Pressable>
            )}
          />
          <LinkButton title={t.forgot} onPress={() => { setRecovering(true); setRecoveryMessage(null); }} />
          <PrimaryButton title={busy ? t.signingIn : t.continue} onPress={submit} disabled={busy} />
        </>
      ) : (
        <>
          <Text accessibilityRole="header" style={[ws(700), { fontSize: 22, color: C.text }]}>{t.recTitle}</Text>
          <Text style={[ws(400), { fontSize: 14.5, color: C.muted, lineHeight: 22, marginTop: 8, marginBottom: 22 }]}>
            {t.recIntro}
          </Text>
          {!!recoveryMessage && <Banner tone={recoveryMessage.tone} message={recoveryMessage.text} />}
          <Input label={t.employeeId} value={recovery.employee_id} autoCapitalize="characters" autoCorrect={false}
            onChangeText={(v) => setRecoveryField('employee_id', v)} placeholder="ADMIN001" />
          <Input label={t.recoveryCode} value={recovery.recovery_code} secureTextEntry autoCapitalize="characters"
            onChangeText={(v) => setRecoveryField('recovery_code', v)} />
          <Input label={t.newPassword} value={recovery.new_password} secureTextEntry autoComplete="new-password"
            onChangeText={(v) => setRecoveryField('new_password', v)} />
          <Input label={t.confirmPassword} value={recovery.confirm_password} secureTextEntry autoComplete="new-password"
            onChangeText={(v) => setRecoveryField('confirm_password', v)} onSubmitEditing={submitRecovery} />
          <PrimaryButton title={busy ? t.saving : t.setPassword} onPress={submitRecovery} disabled={busy} />
          <View style={{ height: 16 }} />
          <LinkButton align="center" title={t.back} onPress={() => { setRecovering(false); setRecoveryMessage(null); }} />
        </>
      )}
    </View>
  );

  const formSide = (
    <View style={{
      flexGrow: 1, alignItems: 'center', justifyContent: 'center',
      paddingHorizontal: tablet ? 32 : 16, paddingTop: wide ? 96 : 28, paddingBottom: 32 + insets.bottom,
    }}>
      {card}
      <Text style={[ws(400), {
        color: C.muted, fontSize: 14.5, lineHeight: 22, textAlign: 'center', marginTop: 26, maxWidth: 480,
      }]}>
        {t.accessNote}
      </Text>
      {!wide && (
        <Text style={[ws(400), { color: C.faint, fontSize: 12.5, textAlign: 'center', marginTop: 14 }]}>{t.footer}</Text>
      )}
      {__DEV__ && (
        <Text style={[ws(400), { color: C.faint, fontSize: 12, textAlign: 'center', marginTop: 18, lineHeight: 18 }]}>
          Demo: CREW001 · CSUP001 · INV001 · ISUP001 · MGT001 · FIN001 · ADMIN001 (password 1234){'\n'}API: {API_BASE}
        </Text>
      )}
    </View>
  );

  if (wide) {
    return (
      <View style={{ flex: 1, flexDirection: 'row', backgroundColor: C.page }}>
        <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
        {hero}
        <View style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled">
            {formSide}
          </ScrollView>
          <View style={{ position: 'absolute', top: 40 + insets.top, right: 44 }}>
            <LanguageSwitch value={lang} onChange={changeLanguage} label={t.language} />
          </View>
        </View>
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: C.page }}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled">
        {hero}
        {formSide}
      </ScrollView>
    </View>
  );
}
