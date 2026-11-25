import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Keypad } from '../features/app/kit';
import { c } from '../features/app/theme';
import { t } from '../i18n';

/** Server PINs are exactly six digits. */
export const PIN_LENGTH = 6;

/**
 * The SERVER step-up PIN prompt: lock icon, dots and a keypad.
 * It only collects the PIN and hands it to `onSubmit` when the sixth digit is typed; the backend
 * checks it. The app NEVER checks a PIN itself. This is unrelated to
 * local unlock. The PIN is never shown, is cleared after every try and is never stored.
 */
export function StepUpPrompt({
  onSubmit,
  onCancel,
  error,
  reason,
  waiting = false,
  final = false,
}: {
  onSubmit: (pin: string) => Promise<void>;
  onCancel: () => void;
  error?: string;
  /** Why it is asked ("Needed to record a payment"). */
  reason?: string;
  /** A server-given wait is running: typing is disabled. */
  waiting?: boolean;
  /** Typing a PIN cannot help (no PIN set, account frozen): only Cancel is offered. */
  final?: boolean;
}) {
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(full: string): Promise<void> {
    setBusy(true);
    try {
      await onSubmit(full);
    } finally {
      setPin('');
      setBusy(false);
    }
  }

  function press(key: string): void {
    if (busy || waiting) return;
    if (key === 'del') {
      setPin((p) => p.slice(0, -1));
      return;
    }
    const next = (pin + key).slice(0, PIN_LENGTH);
    setPin(next);
    if (next.length === PIN_LENGTH) void submit(next);
  }

  return (
    <SafeAreaView style={styles.screen} testID="step-up">
      <View style={styles.top}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t('stepUp.cancel')}
          accessibilityState={{ disabled: busy }}
          disabled={busy}
          onPress={onCancel}
          style={styles.close}
        >
          <Text style={styles.closeText}>{'✕'}</Text>
        </Pressable>
      </View>
      <View style={styles.middle}>
        <View style={styles.lockBox} importantForAccessibility="no-hide-descendants">
          <View style={styles.shackle} />
          <View style={styles.lockBody} />
        </View>
        <Text accessibilityRole="header" style={styles.title}>
          {t('stepUp.title')}
        </Text>
        <Text style={styles.why}>{t('step_up_why')}</Text>
        {reason !== undefined ? <Text style={styles.reason}>{reason}</Text> : null}
        {final ? null : (
          <View
            style={styles.dots}
            accessible
            accessibilityLabel={t('stepUp.pinProgress', { count: pin.length, total: PIN_LENGTH })}
            testID="step-up-pin"
          >
            {Array.from({ length: PIN_LENGTH }, (_, i) => (
              <View key={i} style={[styles.dot, i < pin.length && styles.dotOn]} />
            ))}
          </View>
        )}
        {error !== undefined && error !== '' ? (
          <Text style={styles.error} accessibilityLiveRegion="polite">
            {error}
          </Text>
        ) : null}
      </View>
      {final ? null : <Keypad onKey={press} disabled={busy || waiting} />}
      <View style={styles.bottom} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.white },
  top: { paddingTop: 8, paddingHorizontal: 8 },
  close: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center' },
  closeText: { fontSize: 20, color: c.ink },
  middle: { flex: 1, alignItems: 'center', paddingTop: 16, paddingHorizontal: 28 },
  lockBox: {
    width: 60,
    height: 60,
    borderRadius: 20,
    backgroundColor: c.tealSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  lockBody: { width: 20, height: 15, borderRadius: 3, backgroundColor: c.teal, marginTop: -1 },
  shackle: {
    width: 14,
    height: 12,
    borderWidth: 2.5,
    borderBottomWidth: 0,
    borderColor: c.teal,
    borderTopLeftRadius: 8,
    borderTopRightRadius: 8,
  },
  title: { fontSize: 24, fontWeight: '700', marginTop: 18, color: c.ink, textAlign: 'center' },
  why: { fontSize: 15, color: c.muted, marginTop: 6, lineHeight: 21, textAlign: 'center' },
  reason: { fontSize: 13, color: c.muted, marginTop: 4, textAlign: 'center' },
  dots: { flexDirection: 'row', gap: 18, marginTop: 32 },
  dot: { width: 16, height: 16, borderRadius: 8, borderWidth: 2, borderColor: c.ink },
  dotOn: { backgroundColor: c.ink },
  error: { fontSize: 14, color: c.red, marginTop: 16, textAlign: 'center' },
  bottom: { height: 24 },
});
