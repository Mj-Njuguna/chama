import { useEffect, useRef, useState } from 'react';

import { Body, Button, LinkButton, Screen, TextField, Title } from '../components/ui';
import { formatWait } from '../features/login/messages';
import { t } from '../i18n';
import { useCountdown } from '../lib/useCountdown';
import type { StepUpOtpSent } from './stepUpApi';
import type { StepUpErrorView } from './stepUpMessages';

export type PromptOutcome<T> =
  { readonly ok: true; readonly data: T } | { readonly ok: false; readonly view: StepUpErrorView };

/**
 * Fresh-code step-up: sends a code to the user's own number once on arrival (never auto-resent),
 * then checks what the user types with the server. Waits come from the server and are shown.
 */
export function StepUpOtpPrompt({
  requestCode,
  verifyCode,
  onVerified,
  onCancel,
  onClose,
  devReadCode,
}: {
  requestCode: () => Promise<PromptOutcome<StepUpOtpSent>>;
  verifyCode: (challengeId: string, code: string) => Promise<PromptOutcome<unknown>>;
  onVerified: () => void;
  onCancel: () => void;
  /** The session ended while prompting: close without a message. */
  onClose: () => void;
  devReadCode?: () => Promise<string | null>;
}) {
  const [challenge, setChallenge] = useState<StepUpOtpSent | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [final, setFinal] = useState(false);
  const resend = useCountdown();
  const blocked = useCountdown();
  const requested = useRef(false);

  function show(view: StepUpErrorView): void {
    if (view.close === true) {
      onClose();
      return;
    }
    setMessage(view.message);
    if (view.waitSeconds !== undefined) blocked.start(view.waitSeconds);
    if (view.final === true) setFinal(true);
  }

  async function send(): Promise<void> {
    setBusy(true);
    setMessage(null);
    try {
      const result = await requestCode();
      if (result.ok) {
        setChallenge(result.data);
        setCode('');
        resend.start(result.data.resend_after);
      } else {
        show(result.view);
      }
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    // Send once on arrival only; resending is the user's choice.
    if (requested.current) return;
    requested.current = true;
    void send();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(): Promise<void> {
    if (challenge === null) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await verifyCode(challenge.challenge_id, code);
      if (result.ok) {
        onVerified();
        return;
      }
      if (result.view.codeRejected === true) setCode('');
      show(result.view);
    } finally {
      setBusy(false);
    }
  }

  async function devFill(): Promise<void> {
    if (devReadCode === undefined) return;
    const found = await devReadCode();
    if (found === null) setMessage(t('dev.outbox.empty'));
    else setCode(found);
  }

  const waiting = blocked.secondsLeft > 0;

  return (
    <Screen testID="step-up-otp">
      <Title>{t('stepUp.otp.title')}</Title>
      <Body>{t('stepUp.otp.why')}</Body>
      {challenge === null && busy ? <Body tone="muted">{t('stepUp.otp.sending')}</Body> : null}
      {challenge !== null ? (
        <Body>{t(challenge.channel === 'sms' ? 'login.code.sent.sms' : 'login.code.sent.whatsapp')}</Body>
      ) : null}
      {challenge !== null && !final ? (
        <>
          <TextField
            label={t('login.code.label')}
            value={code}
            onChangeText={(text) => setCode(text.replace(/\D/g, ''))}
            keyboardType="number-pad"
            autoComplete="sms-otp"
            maxLength={6}
            testID="step-up-code"
          />
          <Button
            label={t('stepUp.submit')}
            busy={busy}
            disabled={waiting || code.length !== 6}
            onPress={() => void submit()}
          />
        </>
      ) : null}
      {message !== null && message !== '' ? <Body tone="danger">{message}</Body> : null}
      {final ? null : resend.secondsLeft > 0 ? (
        <Body tone="muted">{t('login.code.resendIn', { time: formatWait(resend.secondsLeft) })}</Body>
      ) : (
        <LinkButton label={t('login.code.resend')} disabled={busy || waiting} onPress={() => void send()} />
      )}
      <LinkButton label={t('stepUp.cancel')} disabled={busy} onPress={onCancel} />
      {devReadCode !== undefined && challenge !== null ? (
        <LinkButton label={t('dev.outbox.fill')} onPress={() => void devFill()} />
      ) : null}
    </Screen>
  );
}
