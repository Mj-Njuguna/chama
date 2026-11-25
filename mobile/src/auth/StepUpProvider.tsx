import { createContext, type ReactNode, useCallback, useContext, useRef, useState } from 'react';
import { Modal } from 'react-native';

import type { StepUpFactor } from '../api/errors';
import { Body, Button, Screen, Title } from '../components/ui';
import { type MessageKey, t } from '../i18n';
import { useCountdown } from '../lib/useCountdown';
import { type FactorPrompter, type StepUpResult, withStepUp } from './stepUp';
import type { StepUpApi, StepUpOtpSent } from './stepUpApi';
import { otpErrorView, pinErrorView, type StepUpErrorView } from './stepUpMessages';
import { StepUpOtpPrompt, type PromptOutcome } from './StepUpOtpPrompt';
import { StepUpPrompt } from './StepUpPrompt';
import type { AuthedCall, TokenManager } from './tokenManager';

/** Runs an authenticated action, handling `step_up_required` (prompt each factor, retry once). */
export type RunWithStepUp = <T>(
  call: AuthedCall<T>,
  /** Why the PIN is needed, shown under the prompt title ("Needed to record a payment"). */
  options?: { readonly reason?: MessageKey },
) => Promise<StepUpResult<T>>;

const StepUpContext = createContext<RunWithStepUp | null>(null);

type Pending = {
  readonly id: number;
  readonly factor: StepUpFactor;
  readonly reason: MessageKey | undefined;
  readonly resolve: (result: 'verified' | 'cancelled') => void;
};

type Calls = Pick<TokenManager, 'callAuthed'>;

/**
 * Hosts the step-up prompts for the whole signed-in app.
 * Screens call `useStepUp()(action)`; this provider shows the PIN or code prompt the server asks
 * for, and a plain error if the retried action still needs step-up. Nothing is cached: freshness
 * is kept by the server on this session .
 */
export function StepUpProvider({
  tokens,
  api,
  devReadCode,
  children,
}: {
  tokens: Calls;
  api: StepUpApi;
  /** Development builds only: reads the step-up code from the fake outbox. */
  devReadCode?: () => Promise<string | null>;
  children: ReactNode;
}) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [failed, setFailed] = useState(false);
  const nextId = useRef(0);
  const reasonRef = useRef<MessageKey | undefined>(undefined);

  const prompt = useCallback<FactorPrompter>(
    (factor) =>
      new Promise((resolve) => {
        nextId.current += 1;
        setPending({ id: nextId.current, factor, reason: reasonRef.current, resolve });
      }),
    [],
  );

  const run = useCallback<RunWithStepUp>(
    async (call, options) => {
      reasonRef.current = options?.reason;
      const result = await withStepUp(() => tokens.callAuthed(call), prompt);
      if (!result.ok && 'stepUp' in result && result.stepUp === 'failed') setFailed(true);
      return result;
    },
    [tokens, prompt],
  );

  function finish(result: 'verified' | 'cancelled'): void {
    pending?.resolve(result);
    setPending(null);
  }

  const otpProps = {
    requestCode: async (): Promise<PromptOutcome<StepUpOtpSent>> => {
      const r = await tokens.callAuthed(api.requestOtp);
      return r.ok ? { ok: true, data: r.data } : { ok: false, view: otpErrorView(r) };
    },
    verifyCode: async (challengeId: string, code: string): Promise<PromptOutcome<unknown>> => {
      const r = await tokens.callAuthed(api.verifyOtp(challengeId, code));
      return r.ok ? { ok: true, data: r.data } : { ok: false, view: otpErrorView(r) };
    },
    ...(devReadCode === undefined ? {} : { devReadCode }),
  };

  let content: ReactNode = null;
  if (pending?.factor === 'otp') {
    content = (
      <StepUpOtpPrompt
        key={pending.id}
        {...otpProps}
        onVerified={() => finish('verified')}
        onCancel={() => finish('cancelled')}
        onClose={() => finish('cancelled')}
      />
    );
  } else if (pending?.factor === 'pin') {
    content = (
      <PinFactor
        key={pending.id}
        tokens={tokens}
        api={api}
        otpProps={otpProps}
        onDone={finish}
        {...(pending.reason === undefined ? {} : { reason: t(pending.reason) })}
      />
    );
  } else if (failed) {
    content = (
      <Screen testID="step-up-failed">
        <Title>{t('stepUp.otp.title')}</Title>
        <Body tone="danger">{t('stepUp.failed')}</Body>
        <Button label={t('stepUp.failed.ok')} onPress={() => setFailed(false)} />
      </Screen>
    );
  }

  return (
    <StepUpContext.Provider value={run}>
      {children}
      <Modal
        visible={content !== null}
        animationType="none"
        onRequestClose={() => (pending !== null ? finish('cancelled') : setFailed(false))}
      >
        {content}
      </Modal>
    </StepUpContext.Provider>
  );
}

/** The PIN factor. After a PIN lock ends the server wants a fresh code first: ask, then the PIN. */
function PinFactor({
  tokens,
  api,
  otpProps,
  onDone,
  reason,
}: {
  tokens: Calls;
  reason?: string;
  api: StepUpApi;
  otpProps: Omit<Parameters<typeof StepUpOtpPrompt>[0], 'onVerified' | 'onCancel' | 'onClose'>;
  onDone: (result: 'verified' | 'cancelled') => void;
}) {
  const [view, setView] = useState<StepUpErrorView | null>(null);
  const [needsOtp, setNeedsOtp] = useState(false);
  const wait = useCountdown();

  async function submit(pin: string): Promise<void> {
    const result = await tokens.callAuthed(api.verifyPin(pin));
    if (result.ok) {
      onDone('verified');
      return;
    }
    const next = pinErrorView(result);
    if (next.close === true) {
      onDone('cancelled');
      return;
    }
    if (next.needsOtp === true) {
      setView(null);
      setNeedsOtp(true);
      return;
    }
    if (next.waitSeconds !== undefined) wait.start(next.waitSeconds);
    setView(next);
  }

  if (needsOtp) {
    return (
      <StepUpOtpPrompt
        {...otpProps}
        onVerified={() => setNeedsOtp(false)}
        onCancel={() => onDone('cancelled')}
        onClose={() => onDone('cancelled')}
      />
    );
  }

  return (
    <StepUpPrompt
      onSubmit={submit}
      onCancel={() => onDone('cancelled')}
      waiting={wait.secondsLeft > 0}
      final={view?.final === true}
      {...(reason === undefined ? {} : { reason })}
      {...(view === null ? {} : { error: view.message })}
    />
  );
}

export function useStepUp(): RunWithStepUp {
  const run = useContext(StepUpContext);
  if (run === null) throw new Error('useStepUp outside StepUpProvider');
  return run;
}
