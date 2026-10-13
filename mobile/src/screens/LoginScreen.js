import React, { useEffect, useRef, useState } from 'react';
import {
  Image, Platform, Pressable, ScrollView, StatusBar, View, useWindowDimensions,
} from 'react-native';
import { Text, TextInput } from '../text';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Defs, LinearGradient, Path, RadialGradient, Rect, Stop } from 'react-native-svg';

import { API_BASE, KEYS, api } from '../api';
import { ws } from '../fonts';
import { LANGUAGES, LOGIN_TEXT } from '../i18n/login';
import { Banner } from '../ui';
import { PasswordRules, passwordOk } from '../password';

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
  // Sized from its own layout: on Android a percentage-sized Svg inside a
  // ScrollView did not stretch to the full width of the header.
  const [size, setSize] = useState({ width: 0, height: 0 });
  return (
    <View
      pointerEvents="none"
      style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: C.navy }}
      onLayout={(e) => setSize({ width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height })}
    >
      {size.width > 0 && (
        <Svg width={size.width} height={size.height} preserveAspectRatio="none">
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
      )}
    </View>
  );
}

const APP_ICON = require('../../assets/app-icon.png');

function BrandLogo({ size = 52 }) {
  return (
    <Image
      source={APP_ICON}
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.24), borderWidth: 1, borderColor: 'rgba(255,255,255,0.35)' }}
      resizeMode="contain"
      accessibilityLabel="Himalaya Airlines OBS"
    />
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
export default function LoginScreen({ onLogin, notify, resetToken, onResetDone }) {
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
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef(null);
  // signin | otp | forgot | recovery | reset
  const [mode, setMode] = useState(resetToken ? 'reset' : 'signin');

  // Two-factor sign-in: the code challenge from /login.
  const [otp, setOtp] = useState(null);
  const [code, setCode] = useState('');
  const [clock, setClock] = useState(Date.now());

  // Reset / invitation link opened in the browser.
  const [link, setLink] = useState(null);
  const [newPassword, setNewPassword] = useState('');
  const [repeatPassword, setRepeatPassword] = useState('');
  const [resetDone, setResetDone] = useState(null);

  // Break-glass administrator reset, shown only when the server has a recovery code configured.
  const [recoveryAvailable, setRecoveryAvailable] = useState(false);
  const [recovery, setRecovery] = useState({ employee_id: '', recovery_code: '', new_password: '', confirm_password: '' });
  const [recoveryMessage, setRecoveryMessage] = useState(null);
  const setRecoveryField = (key, value) => setRecovery((r) => ({ ...r, [key]: value }));

  useEffect(() => {
    AsyncStorage.getItem(LANGUAGE_KEY).then((saved) => { if (LOGIN_TEXT[saved]) setLang(saved); }).catch(() => {});
    api('/admin-recovery', { auth: false }).then((r) => setRecoveryAvailable(!!r.available)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!resetToken) return;
    api(`/password/reset-info?token=${encodeURIComponent(resetToken)}`, { auth: false })
      .then(setLink)
      .catch((err) => setError(err.message));
  }, [resetToken]);

  // One-second tick for the code countdowns.
  useEffect(() => {
    if (mode !== 'otp') return undefined;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [mode]);

  function changeLanguage(code_) {
    setLang(code_);
    AsyncStorage.setItem(LANGUAGE_KEY, code_).catch(() => {});
  }

  function go(next) {
    setError('');
    setInfo('');
    setMode(next);
  }

  async function finish(session) {
    await AsyncStorage.setItem(KEYS.session, JSON.stringify(session));
    onLogin(session);
  }

  async function submit() {
    setError('');
    if (!employeeId.trim() || !password) return setError(t.missingId);
    setBusy(true);
    try {
      const result = await api('/login', {
        method: 'POST', auth: false, body: { identifier: employeeId.trim(), password },
      });
      if (result.otp_required) {
        setOtp({ ...result, startedAt: Date.now() });
        setCode('');
        setPassword('');
        go('otp');
      } else {
        await finish(result);
      }
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  async function verify() {
    setError('');
    setBusy(true);
    try {
      await finish(await api('/login/verify', { method: 'POST', auth: false, body: { challenge: otp.challenge, code } }));
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  async function resend() {
    setError('');
    try {
      const result = await api('/login/resend', { method: 'POST', auth: false, body: { challenge: otp.challenge } });
      setOtp({ ...result, startedAt: Date.now() });
      setCode('');
    } catch (err) {
      setError(err.message);
    }
  }

  async function sendReset() {
    setError('');
    if (!employeeId.trim()) return setError(t.forgotNeedId);
    setBusy(true);
    try {
      const result = await api('/password/forgot', { method: 'POST', auth: false, body: { identifier: employeeId.trim() } });
      setInfo(result.detail);
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  }

  async function saveReset() {
    setError('');
    if (newPassword !== repeatPassword) return setError(t.mismatch);
    setBusy(true);
    try {
      const result = await api('/password/reset', {
        method: 'POST', auth: false,
        body: { token: resetToken, new_password: newPassword, confirm_password: repeatPassword },
      });
      setResetDone(result.detail);
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
      setRecovery({ employee_id: '', recovery_code: '', new_password: '', confirm_password: '' });
      go('signin');
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
      paddingBottom: wide ? 36 : 20,
      justifyContent: 'space-between',
      overflow: 'hidden',
    }}>
      <HeroBackground />
      <View style={{
        flexDirection: phone ? 'column' : 'row', alignItems: phone ? 'flex-start' : 'center',
        justifyContent: 'space-between', gap: 14,
      }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, flexShrink: 1 }}>
          <BrandLogo size={wide ? 52 : 44} />
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

      {wide && (
        <View style={{ maxWidth: 560 }}>
          <Text accessibilityRole="header" style={[ws(700), {
            color: '#FFFFFF', fontSize: 36, lineHeight: 46,
          }]}>
            {t.headline}
          </Text>
          <Text style={[ws(400), {
            color: 'rgba(255,255,255,0.80)', fontSize: 16.5, lineHeight: 26, marginTop: 14,
          }]}>
            {t.intro}
          </Text>
          <View style={{ marginTop: 26, gap: 11 }}>
            {t.features.map((feature) => (
              <View key={feature} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 11 }}>
                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.bullet, marginTop: 9 }} />
                <Text style={[ws(400), { color: 'rgba(255,255,255,0.92)', fontSize: 16, lineHeight: 24, flex: 1 }]}>
                  {feature}
                </Text>
              </View>
            ))}
          </View>
        </View>
      )}

      {wide && (
        <Text style={[ws(400), { color: 'rgba(255,255,255,0.55)', fontSize: 13 }]}>{t.footer}</Text>
      )}
    </View>
  );

  /* ------------------------------ form ------------------------------ */
  const title = (text) => <Text accessibilityRole="header" style={[ws(700), { fontSize: 26, color: C.text }]}>{text}</Text>;
  const sub = (text) => (
    <Text style={[ws(400), { fontSize: 15.5, color: C.muted, lineHeight: 24, marginTop: 8, marginBottom: 24 }]}>{text}</Text>
  );
  const eye = (
    <Pressable onPress={() => setShowPassword((v) => !v)} hitSlop={8}
      style={{ paddingHorizontal: 14, paddingVertical: 12 }}
      accessibilityRole="button" accessibilityLabel={showPassword ? t.hide : t.show}>
      <EyeIcon open={showPassword} />
    </Pressable>
  );
  const otpLeft = otp ? Math.max(0, otp.expires_in - Math.floor((clock - otp.startedAt) / 1000)) : 0;
  const resendLeft = otp ? Math.max(0, otp.resend_in - Math.floor((clock - otp.startedAt) / 1000)) : 0;
  const mmss = (secs) => `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  const rulesLabels = { length: t.ruleLength, mix: t.ruleMix, personal: t.ruleName };

  let content;
  if (mode === 'otp' && otp) {
    content = (
      <>
        {title(t.otpTitle)}
        {sub(t.otpIntro(otp.email || ''))}
        {!!error && <Banner tone="danger" message={error} />}
        <Input
          label={t.otpLabel}
          value={code}
          onChangeText={(v) => setCode(v.replace(/[^0-9]/g, '').slice(0, 6))}
          keyboardType="number-pad"
          autoComplete="one-time-code"
          textContentType="oneTimeCode"
          placeholder="••••••"
          maxLength={6}
          autoFocus
          onSubmitEditing={() => code.length === 6 && verify()}
        />
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: -8, marginBottom: 18 }}>
          <Text style={[ws(400), { fontSize: 13.5, color: otpLeft ? C.muted : '#B3261E' }]}>
            {otpLeft ? t.otpValid(mmss(otpLeft)) : t.otpExpired}
          </Text>
          {resendLeft > 0
            ? <Text style={[ws(400), { fontSize: 13.5, color: C.faint }]}>{t.resendIn(resendLeft)}</Text>
            : (
              <Pressable onPress={resend} accessibilityRole="link" hitSlop={6}>
                <Text style={[ws(600), { fontSize: 13.5, color: C.link }]}>{t.resend}</Text>
              </Pressable>
            )}
        </View>
        <PrimaryButton title={busy ? t.verifying : t.verify} onPress={verify} disabled={busy || code.length !== 6 || !otpLeft} />
        <Text style={[ws(400), { fontSize: 13, color: C.faint, lineHeight: 20, marginTop: 16 }]}>{t.otpHelp}</Text>
      </>
    );
  } else if (mode === 'forgot') {
    content = (
      <>
        {title(t.forgotTitle)}
        {sub(t.forgotIntro)}
        {!!error && <Banner tone="danger" message={error} />}
        {!!info && <Banner tone="success" message={info} />}
        <Input label={t.identifier} value={employeeId} onChangeText={setEmployeeId} autoCapitalize="none"
          autoCorrect={false} autoComplete="username" keyboardType="email-address"
          placeholder="name@himalaya-airlines.com" onSubmitEditing={sendReset} />
        <PrimaryButton title={busy ? t.sending : t.sendLink} onPress={sendReset} disabled={busy || !employeeId.trim()} />
        {recoveryAvailable && (
          <>
            <View style={{ height: 14 }} />
            <LinkButton align="center" title={t.useRecovery} onPress={() => { go('recovery'); setRecoveryMessage(null); }} />
          </>
        )}
      </>
    );
  } else if (mode === 'reset') {
    const person = link ? { name: link.name, employee_id: link.employee_id, email: link.email } : {};
    content = (
      <>
        {title(link?.kind === 'INVITE' ? t.inviteTitle : t.resetTitle)}
        {link ? (
          <Text style={[ws(400), { fontSize: 15.5, color: C.muted, lineHeight: 24, marginTop: 8, marginBottom: 24 }]}>
            {t.resetFor} <Text style={[ws(700), { color: C.text }]}>{link.name}</Text>
            {link.email ? ` (${link.email})` : ''}
          </Text>
        ) : !error && sub(t.checkingLink)}
        {!!error && <Banner tone="danger" message={error} />}
        {resetDone ? (
          <>
            <Banner tone="success" message={resetDone} />
            <PrimaryButton title={t.signIn} onPress={() => { onResetDone?.(); setResetDone(null); go('signin'); }} />
          </>
        ) : link && (
          <>
            <Input label={t.newPassword} value={newPassword} onChangeText={setNewPassword}
              secureTextEntry={!showPassword} autoComplete="new-password" textContentType="newPassword" right={eye} />
            <PasswordRules password={newPassword} person={person} labels={rulesLabels} />
            <Input label={t.repeatPassword} value={repeatPassword} onChangeText={setRepeatPassword}
              secureTextEntry={!showPassword} autoComplete="new-password" onSubmitEditing={saveReset} />
            <PrimaryButton title={busy ? t.saving : t.savePassword} onPress={saveReset}
              disabled={busy || !passwordOk(newPassword, person) || !repeatPassword} />
          </>
        )}
      </>
    );
  } else if (mode === 'recovery') {
    content = (
      <>
        {title(t.recTitle)}
        {sub(t.recIntro)}
        {!!recoveryMessage && <Banner tone={recoveryMessage.tone} message={recoveryMessage.text} />}
        <Input label={t.employeeId} value={recovery.employee_id} autoCapitalize="characters" autoCorrect={false}
          onChangeText={(v) => setRecoveryField('employee_id', v)} placeholder="ADMIN001" />
        <Input label={t.recoveryCode} value={recovery.recovery_code} secureTextEntry autoCapitalize="characters"
          onChangeText={(v) => setRecoveryField('recovery_code', v)} />
        <Input label={t.newPassword} value={recovery.new_password} secureTextEntry autoComplete="new-password"
          onChangeText={(v) => setRecoveryField('new_password', v)} />
        <PasswordRules password={recovery.new_password} person={{ employee_id: recovery.employee_id }} labels={rulesLabels} />
        <Input label={t.confirmPassword} value={recovery.confirm_password} secureTextEntry autoComplete="new-password"
          onChangeText={(v) => setRecoveryField('confirm_password', v)} onSubmitEditing={submitRecovery} />
        <PrimaryButton title={busy ? t.saving : t.setPassword} onPress={submitRecovery} disabled={busy} />
      </>
    );
  } else {
    content = (
      <>
        {title(t.signIn)}
        {sub(t.signInSub2)}
        {!!error && <Banner tone="danger" message={error} />}
        <Input
          label={t.identifier}
          value={employeeId}
          onChangeText={setEmployeeId}
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="username"
          textContentType="username"
          placeholder="CREW001 / name@himalaya-airlines.com"
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
          right={eye}
        />
        <LinkButton title={t.forgot} onPress={() => go('forgot')} />
        <PrimaryButton title={busy ? t.signingIn : t.continue} onPress={submit} disabled={busy} />
        <Text style={[ws(400), { fontSize: 12.5, color: C.faint, lineHeight: 19, marginTop: 14 }]}>{t.lockHint}</Text>
      </>
    );
  }

  const card = (
    <View style={{ width: '100%', maxWidth: 480 }}>
      <View style={{
        width: '100%', backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border,
        padding: tablet ? 38 : 24,
        shadowColor: '#0A1A3F', shadowOpacity: 0.06, shadowRadius: 24, shadowOffset: { width: 0, height: 8 }, elevation: 2,
      }}>
        {content}
      </View>
      {mode !== 'signin' && !(mode === 'reset' && resetDone) && (
        <View style={{ marginTop: 18 }}>
          <LinkButton align="center" title={mode === 'otp' ? t.otherAccount : t.backToSignIn}
            onPress={() => { if (mode === 'reset') onResetDone?.(); setOtp(null); go('signin'); }} />
        </View>
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
