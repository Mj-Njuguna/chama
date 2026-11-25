import { useRef, useState } from 'react';
import { Linking } from 'react-native';

import type { LoginResult } from '../../auth/session';
import { Body, Button, LinkButton, Screen, TextField, Title } from '../../components/ui';
import { t } from '../../i18n';
import { normalizePhone } from '../../lib/phone';
import { useCountdown } from '../../lib/useCountdown';
import type { LoginApi, LoginResponse, OtpRequested } from './loginApi';
import { formatWait, loginErrorView, phoneErrorMessage } from './messages';

type Props = {
  api: LoginApi;
  installationId: () => Promise<string>;
  /** Stores the session (may show the one "confirm it's you" prompt). */
  completeLogin: (login: LoginResponse) => Promise<LoginResult>;
  /** Shown above the form, e.g. why the user was signed out. */
  notice?: string;
  /** Development builds only: reads the code from the backend's fake outbox. */
  devReadCode?: (phoneE164: string) => Promise<string | null>;
};

type Challenge = { phone: string; id: string; channel: OtpRequested['channel'] };

/**
 * S1 Sign in. Same screens and messages whether or not the number is
 * registered. Waits come from the server (`resend_after`, `retry_after`) and are shown, never
 * auto-retried.
 */
export function LoginFlow({ api, installationId, completeLogin, notice, devReadCode }: Props) {
  const [phoneText, setPhoneText] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [phoneError, setPhoneError] = useState<string | undefined>();
  const [confirm, setConfirm] = useState<'cancelled' | 'no_screen_lock' | null>(null);
  const pendingLogin = useRef<LoginResponse | null>(null);
  const resend = useCountdown();
  const blocked = useCountdown();

  function showError(error: Parameters<typeof loginErrorView>[0]): ReturnType<typeof loginErrorView> {
    const view = loginErrorView(error);
    setMessage(view.message);
    if (view.waitSeconds !== undefined) blocked.start(view.waitSeconds);
    return view;
  }

  async function sendCode(phone: string): Promise<void> {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.requestOtp(phone, await installationId());
      if (result.ok) {
        setChallenge({ phone, id: result.data.challenge_id, channel: result.data.channel });
        setCode('');
        resend.start(result.data.resend_after);
        return;
      }
      const view = showError(result.error);
      if (view.phoneRejected) {
        setPhoneError(view.message);
        setMessage(null);
      }
    } finally {
      setBusy(false);
    }
  }

  async function onSubmitPhone(): Promise<void> {
    const normalized = normalizePhone(phoneText);
    if (!normalized.ok) {
      setPhoneError(phoneErrorMessage(normalized.error));
      return;
    }
    setPhoneError(undefined);
    await sendCode(normalized.e164);
  }

  async function finish(login: LoginResponse): Promise<void> {
    const stored = await completeLogin(login);
    if (stored.kind === 'ok') {
      pendingLogin.current = null;
      return;
    }
    pendingLogin.current = login;
    setConfirm(stored.kind);
  }

  async function onSubmitCode(): Promise<void> {
    if (challenge === null) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.verifyOtp(challenge.id, code, await installationId());
      if (result.ok) {
        await finish(result.data);
        return;
      }
      const view = showError(result.error);
      if (view.codeRejected) setCode('');
    } finally {
      setBusy(false);
    }
  }

  async function onRetryConfirm(): Promise<void> {
    const login = pendingLogin.current;
    if (login === null) return;
    setBusy(true);
    try {
      setConfirm(null);
      await finish(login);
    } finally {
      setBusy(false);
    }
  }

  async function onDevFill(): Promise<void> {
    if (challenge === null || devReadCode === undefined) return;
    const found = await devReadCode(challenge.phone);
    if (found === null) setMessage(t('dev.outbox.empty'));
    else setCode(found);
  }

  const waiting = blocked.secondsLeft > 0;

  if (confirm !== null) {
    return (
      <Screen testID="login-confirm">
        <Title>{t('login.title')}</Title>
        {confirm === 'no_screen_lock' ? (
          <>
            <Body>{t('lock.noScreenLock')}</Body>
            <LinkButton label={t('lock.openSettings')} onPress={() => void Linking.openSettings()} />
          </>
        ) : (
          <Body>{t('login.confirm.cancelled')}</Body>
        )}
        <Button label={t('login.confirm.retry')} busy={busy} onPress={() => void onRetryConfirm()} />
      </Screen>
    );
  }

  if (challenge === null) {
    return (
      <Screen testID="login-phone">
        <Title>{t('login.title')}</Title>
        {notice !== undefined ? <Body tone="muted">{notice}</Body> : null}
        <TextField
          label={t('login.phone.label')}
          hint={t('login.phone.hint')}
          value={phoneText}
          onChangeText={(text) => {
            setPhoneText(text);
            setPhoneError(undefined);
          }}
          error={phoneError}
          keyboardType="phone-pad"
          autoComplete="tel"
          maxLength={20}
          testID="phone-input"
        />
        {message !== null ? <Body tone="danger">{message}</Body> : null}
        <Button
          label={t('login.phone.submit')}
          busy={busy}
          disabled={waiting || phoneText.trim() === ''}
          onPress={() => void onSubmitPhone()}
        />
      </Screen>
    );
  }

  return (
    <Screen testID="login-code">
      <Title>{t('login.code.title')}</Title>
      <Body>{t(challenge.channel === 'sms' ? 'login.code.sent.sms' : 'login.code.sent.whatsapp')}</Body>
      <TextField
        label={t('login.code.label')}
        value={code}
        onChangeText={(text) => setCode(text.replace(/\D/g, ''))}
        keyboardType="number-pad"
        autoComplete="sms-otp"
        maxLength={6}
        testID="code-input"
      />
      {message !== null ? <Body tone="danger">{message}</Body> : null}
      <Button
        label={t('login.code.submit')}
        busy={busy}
        disabled={waiting || code.length !== 6}
        onPress={() => void onSubmitCode()}
      />
      {resend.secondsLeft > 0 ? (
        <Body tone="muted">{t('login.code.resendIn', { time: formatWait(resend.secondsLeft) })}</Body>
      ) : (
        <LinkButton
          label={t('login.code.resend')}
          disabled={busy || waiting}
          onPress={() => void sendCode(challenge.phone)}
        />
      )}
      <LinkButton
        label={t('login.code.changeNumber')}
        disabled={busy}
        onPress={() => {
          setChallenge(null);
          setMessage(null);
        }}
      />
      {devReadCode !== undefined ? (
        <LinkButton label={t('dev.outbox.fill')} onPress={() => void onDevFill()} />
      ) : null}
    </Screen>
  );
}
