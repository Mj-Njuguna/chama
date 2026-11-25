import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

/** Shared, deliberately plain UI primitives. Text passed in must already be translated. */

export const colors = {
  text: '#1A1A1A',
  muted: '#555555',
  primary: '#0F766E',
  onPrimary: '#FFFFFF',
  danger: '#A61B1B',
  background: '#FFFFFF',
} as const;

export function Screen({ children, testID }: { children: ReactNode; testID?: string }) {
  return (
    <SafeAreaView style={styles.screen} testID={testID}>
      <View style={styles.content}>{children}</View>
    </SafeAreaView>
  );
}

export function Title({ children }: { children: string }) {
  return (
    <Text accessibilityRole="header" style={styles.title}>
      {children}
    </Text>
  );
}

export function Body({
  children,
  tone = 'normal',
}: {
  children: string;
  tone?: 'normal' | 'muted' | 'danger';
}) {
  return <Text style={[styles.body, tone !== 'normal' && { color: colors[tone] }]}>{children}</Text>;
}

export function Button({
  label,
  onPress,
  disabled = false,
  busy = false,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  const inactive = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: inactive, busy }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [styles.button, (pressed || inactive) && styles.buttonDim]}
    >
      {busy ? <ActivityIndicator color={colors.onPrimary} /> : <Text style={styles.buttonText}>{label}</Text>}
    </Pressable>
  );
}

export function TextField({
  label,
  value,
  onChangeText,
  hint,
  error,
  keyboardType = 'default',
  maxLength,
  testID,
  autoComplete,
  secure = false,
}: {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
  hint?: string;
  error?: string;
  keyboardType?: 'default' | 'phone-pad' | 'number-pad';
  maxLength?: number;
  testID?: string;
  autoComplete?: 'tel' | 'sms-otp' | 'off';
  /** Hides what is typed (the server PIN). */
  secure?: boolean;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={hint}
        value={value}
        onChangeText={onChangeText}
        keyboardType={keyboardType}
        maxLength={maxLength}
        autoComplete={autoComplete}
        secureTextEntry={secure}
        testID={testID}
        style={[styles.input, error !== undefined && styles.inputError]}
      />
      {hint !== undefined && error === undefined ? <Text style={styles.hint}>{hint}</Text> : null}
      {error !== undefined ? (
        <Text accessibilityLiveRegion="polite" style={styles.error}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}

export function LinkButton({
  label,
  onPress,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={styles.link}
    >
      <Text style={[styles.linkText, disabled && { color: colors.muted }]}>{label}</Text>
    </Pressable>
  );
}

/** Two or more mutually exclusive options (e.g. official view / my view). */
export function Segmented<K extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { key: K; label: string }[];
  value: K;
  onChange: (key: K) => void;
}) {
  return (
    <View style={styles.segmented} accessibilityRole="tablist">
      {options.map((o) => {
        const on = o.key === value;
        return (
          <Pressable
            key={o.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            accessibilityLabel={o.label}
            onPress={() => onChange(o.key)}
            style={[styles.segment, on && styles.segmentOn]}
          >
            <Text style={[styles.segmentText, on && styles.segmentTextOn]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <View style={styles.centered} accessibilityLabel={label}>
      <ActivityIndicator size="large" color={colors.primary} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { flex: 1, padding: 24, gap: 16, justifyContent: 'center' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  title: { fontSize: 22, fontWeight: '600', color: colors.text },
  body: { fontSize: 16, lineHeight: 22, color: colors.text },
  button: {
    minHeight: 48,
    borderRadius: 8,
    paddingHorizontal: 16,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
  },
  buttonDim: { opacity: 0.6 },
  field: { gap: 6 },
  label: { fontSize: 16, fontWeight: '600', color: colors.text },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: colors.muted,
    borderRadius: 8,
    paddingHorizontal: 12,
    fontSize: 18,
    color: colors.text,
  },
  inputError: { borderColor: colors.danger },
  hint: { fontSize: 14, color: colors.muted },
  error: { fontSize: 14, color: colors.danger },
  link: { minHeight: 48, justifyContent: 'center', alignItems: 'center' },
  linkText: { fontSize: 16, fontWeight: '600', color: colors.primary },
  buttonText: { fontSize: 16, fontWeight: '600', color: colors.onPrimary },
  segmented: { flexDirection: 'row', backgroundColor: '#F1F5F9', borderRadius: 12, padding: 4, gap: 4 },
  segment: { flex: 1, minHeight: 40, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  segmentOn: { backgroundColor: colors.background },
  segmentText: { fontSize: 15, color: colors.muted, fontWeight: '500' },
  segmentTextOn: { color: colors.text, fontWeight: '600' },
});
