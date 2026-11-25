import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { StepUpResult } from '../../auth/stepUp';
import type { AuthedCall } from '../../auth/tokenManager';
import { PIN_LENGTH } from '../../auth/StepUpPrompt';
import { type MessageKey, t } from '../../i18n';
import { Keypad } from '../app/kit';
import { c } from '../app/theme';

type Step = 'create' | 'confirm';

const REJECTED: Readonly<Record<string, MessageKey>> = {
  repeated: 'setPin.rejected.repeated',
  sequence: 'setPin.rejected.sequence',
  phone_digits: 'setPin.rejected.phone_digits',
};

/** What the screen shows after the server's answer to PUT /auth/pin (exported for tests). */
export function setPinOutcome(result: StepUpResult<null>): 'done' | MessageKey {
  if (result.ok) return 'done';
  if ('stepUp' in result) return result.stepUp === 'cancelled' ? 'setPin.needsCode' : 'setPin.failed';
  if ('offline' in result) return 'common.needsConnection';
  if (!('error' in result)) return 'setPin.failed';
  if (result.error.code === 'pin_already_set') return 'done';
  if (result.error.code === 'pin_rejected') {
    return REJECTED[result.error.reason ?? ''] ?? 'setPin.rejected.other';
  }
  return 'setPin.failed';
}

/**
 * First-time setup: the user must create the SERVER step-up PIN before using the app,
 * because recording a payment needs it. Shown while GET /me says `has_pin: false`; there is no
 * skip. The app only collects the six digits twice and sends them; the server checks the rules
 * (no repeats, no runs, not the phone number's end) and stores only a hash. This is not the
 * phone's screen lock. The PIN is never shown, kept or logged.
 */
export function SetPinScreen({
  setPin,
  runWithStepUp,
  onDone,
}: {
  setPin: (pin: string) => AuthedCall<null>;
  /** Runs the call; the server may ask for a fresh code first (the sign-in counts for 5 min). */
  runWithStepUp: (call: AuthedCall<null>) => Promise<StepUpResult<null>>;
  onDone: () => void;
}) {
  const [step, setStep] = useState<Step>('create');
  const [first, setFirst] = useState('');
  const [entry, setEntry] = useState('');
  const [error, setError] = useState<MessageKey | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(pin: string): Promise<void> {
    setBusy(true);
    try {
      const outcome = setPinOutcome(await runWithStepUp(setPin(pin)));
      if (outcome === 'done') {
        onDone();
        return;
      }
      setError(outcome);
      setStep('create');
    } finally {
      setFirst('');
      setEntry('');
      setBusy(false);
    }
  }

  function press(key: string): void {
    if (busy) return;
    if (key === 'del') {
      setEntry((value) => value.slice(0, -1));
      return;
    }
    const next = (entry + key).slice(0, PIN_LENGTH);
    setEntry(next);
    if (next.length < PIN_LENGTH) return;
    if (step === 'create') {
      setFirst(next);
      setEntry('');
      setError(null);
      setStep('confirm');
    } else if (next !== first) {
      setFirst('');
      setEntry('');
      setError('setPin.mismatch');
      setStep('create');
    } else {
      void submit(next);
    }
  }

  return (
    <SafeAreaView style={styles.screen} testID="set-pin">
      <View style={styles.middle}>
        <View style={styles.lockBox} importantForAccessibility="no-hide-descendants">
          <View style={styles.shackle} />
          <View style={styles.lockBody} />
        </View>
        <Text accessibilityRole="header" style={styles.title}>
          {t(step === 'create' ? 'setPin.title.create' : 'setPin.title.confirm')}
        </Text>
        <Text style={styles.why}>{t('setPin.why')}</Text>
        <View
          style={styles.dots}
          accessible
          accessibilityLabel={t('stepUp.pinProgress', { count: entry.length, total: PIN_LENGTH })}
          testID="set-pin-dots"
        >
          {Array.from({ length: PIN_LENGTH }, (_, i) => (
            <View key={i} style={[styles.dot, i < entry.length && styles.dotOn]} />
          ))}
        </View>
        {error !== null ? (
          <Text style={styles.error} accessibilityLiveRegion="polite">
            {t(error)}
          </Text>
        ) : null}
      </View>
      <Keypad onKey={press} disabled={busy} />
      <View style={styles.bottom} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.white },
  middle: { flex: 1, alignItems: 'center', paddingTop: 72, paddingHorizontal: 28 },
  lockBox: {
    width: 60,
    height: 60,
    borderRadius: 20,
    backgroundColor: c.tealSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shackle: {
    width: 14,
    height: 12,
    borderWidth: 2.5,
    borderBottomWidth: 0,
    borderColor: c.teal,
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
  },
  lockBody: { width: 20, height: 15, borderRadius: 3, backgroundColor: c.teal, marginTop: -1 },
  title: { fontSize: 24, fontWeight: '700', marginTop: 18, color: c.ink, textAlign: 'center' },
  why: { fontSize: 15, color: c.muted, marginTop: 6, lineHeight: 21, textAlign: 'center' },
  dots: { flexDirection: 'row', gap: 18, marginTop: 32 },
  dot: { width: 16, height: 16, borderRadius: 8, borderWidth: 2, borderColor: c.ink },
  dotOn: { backgroundColor: c.ink },
  error: { fontSize: 14, color: c.red, marginTop: 16, textAlign: 'center' },
  bottom: { height: 24 },
});
