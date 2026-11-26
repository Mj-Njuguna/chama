import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Modal, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Body, Button, LinkButton, TextField, Title } from '../../components/ui';
import { type MessageKey, t } from '../../i18n';
import { minorToInput, parseKesInput } from '../../lib/money';
import { c } from '../app/theme';
import { writeFailure } from '../m2/deps';
import type { M2Api, StkRequest, StkStatus } from '../m2/m2Api';
import type { CallAuthed } from '../shell/useAuthedQuery';

/** How often the request status is read while M-Pesa is waiting for the member's PIN. */
export const POLL_MS = 4_000;
/** Stop polling after this; the server sweeps unanswered requests to `timeout` itself. */
export const MAX_WAIT_MS = 120_000;

type Phase =
  | { readonly kind: 'enter' }
  | { readonly kind: 'sending' }
  | { readonly kind: 'waiting'; readonly request: StkRequest }
  | { readonly kind: 'done'; readonly status: Exclude<StkStatus, 'pending'>; readonly receipt: string | null }
  | { readonly kind: 'gave_up' };

const RESULT_TEXT: Record<Exclude<StkStatus, 'pending'>, MessageKey> = {
  succeeded: 'pay.succeeded',
  failed: 'pay.failed',
  cancelled: 'pay.cancelled',
  timeout: 'pay.timeout',
};

/**
 * One-tap M-Pesa payment: the member confirms an amount (prefilled with what the server says is
 * still due), the server sends an STK request to their phone, and the app polls its status. The
 * app never decides a payment succeeded: only the server's status does.
 */
export function PayModal({
  visible,
  m2,
  callAuthed,
  prefillMinor,
  onClose,
  pollMs = POLL_MS,
  maxWaitMs = MAX_WAIT_MS,
}: {
  visible: boolean;
  m2: M2Api;
  callAuthed: CallAuthed;
  /** The server's figure for what is still due this cycle; null leaves the field empty. Mount the
   * modal only while it is open so every opening starts from this figure. */
  prefillMinor: number | null;
  onClose: () => void;
  pollMs?: number;
  maxWaitMs?: number;
}) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState(prefillMinor === null ? '' : minorToInput(prefillMinor));
  const [amountError, setAmountError] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: 'enter' });
  const startedAt = useRef(0);

  const waitingId = phase.kind === 'waiting' ? phase.request.stk_request_id : null;
  useEffect(() => {
    if (waitingId === null) return undefined;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (stopped) return;
      if (Date.now() - startedAt.current >= maxWaitMs) {
        setPhase({ kind: 'gave_up' });
        return;
      }
      const result = await callAuthed(m2.stkStatus(waitingId));
      if (stopped) return;
      if (result.ok && result.data.status !== 'pending') {
        setPhase({ kind: 'done', status: result.data.status, receipt: result.data.mpesa_receipt });
        void queryClient.invalidateQueries();
        return;
      }
      // Pending, offline or a server hiccup: keep waiting until the deadline.
      timer = setTimeout(() => void tick(), pollMs);
    };
    timer = setTimeout(() => void tick(), pollMs);
    return () => {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [waitingId, callAuthed, m2, pollMs, maxWaitMs, queryClient]);

  async function submit(): Promise<void> {
    const minor = parseKesInput(amount);
    if (minor === null) {
      setAmountError(t('pay.amountInvalid'));
      return;
    }
    setAmountError(undefined);
    setError(null);
    setPhase({ kind: 'sending' });
    const result = await callAuthed(m2.createStk(minor));
    if (result.ok) {
      startedAt.current = Date.now();
      const request = result.data;
      setPhase(
        request.status === 'pending'
          ? { kind: 'waiting', request }
          : { kind: 'done', status: request.status, receipt: request.mpesa_receipt },
      );
      return;
    }
    setPhase({ kind: 'enter' });
    setError(
      writeFailure(result, (e) => {
        if (e.code === 'state_conflict' && e.reason === 'payments_not_linked') return 'pay.notLinked';
        if (e.code === 'validation_error') return 'pay.amountInvalid';
        return 'pay.error';
      }),
    );
  }

  let content;
  if (phase.kind === 'waiting' || phase.kind === 'sending') {
    content = (
      <View style={styles.stack} testID="pay-waiting">
        <Body>{t(phase.kind === 'sending' ? 'pay.waiting' : 'pay.pending')}</Body>
        <Button label={t('pay.waiting')} busy onPress={() => undefined} />
        <LinkButton label={t('common.close')} onPress={onClose} />
      </View>
    );
  } else if (phase.kind === 'done' || phase.kind === 'gave_up') {
    const status = phase.kind === 'done' ? phase.status : 'timeout';
    const ok = status === 'succeeded';
    content = (
      <View style={styles.stack} testID={`pay-result-${status}`}>
        <Text style={[styles.result, ok ? styles.ok : styles.bad]} accessibilityLiveRegion="polite">
          {t(RESULT_TEXT[status])}
        </Text>
        {phase.kind === 'done' && phase.receipt !== null ? (
          <Body tone="muted">{t('pay.receipt', { receipt: phase.receipt })}</Body>
        ) : null}
        {ok || status === 'timeout' ? (
          <Button label={t('pay.done')} onPress={onClose} />
        ) : (
          <>
            <Button label={t('pay.again')} onPress={() => setPhase({ kind: 'enter' })} />
            <LinkButton label={t('common.close')} onPress={onClose} />
          </>
        )}
      </View>
    );
  } else {
    content = (
      <View style={styles.stack}>
        <TextField
          label={t('pay.amount')}
          value={amount}
          onChangeText={setAmount}
          keyboardType="number-pad"
          hint={t('pay.amountHint')}
          testID="pay-amount"
          {...(amountError === undefined ? {} : { error: amountError })}
        />
        {error !== null ? (
          <Text style={styles.error} accessibilityRole="alert" testID="pay-error">
            {error}
          </Text>
        ) : null}
        <Button label={t('pay.submit')} onPress={() => void submit()} />
        <LinkButton label={t('common.cancel')} onPress={onClose} />
      </View>
    );
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <ScrollView contentContainerStyle={styles.screen} testID="pay-modal">
        <Title>{t('pay.title')}</Title>
        {content}
      </ScrollView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  screen: { padding: 24, gap: 16, backgroundColor: c.white, flexGrow: 1 },
  stack: { gap: 16 },
  result: { fontSize: 18, fontWeight: '600' },
  ok: { color: c.teal },
  bad: { color: c.red },
  error: { color: c.red, fontSize: 15 },
});
