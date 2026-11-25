import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { t } from '../../i18n';
import { c } from './theme';

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'] as const;

/** 3 x 4 keypad (PIN). `extraKey` replaces the empty bottom-left key. */
export function Keypad({
  onKey,
  extraKey,
  disabled = false,
  style,
}: {
  onKey: (key: string) => void;
  extraKey?: string;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  return (
    <View style={[s.keypad, style]}>
      {KEYS.map((key, i) => {
        const label = key === '' ? (extraKey ?? '') : key;
        if (label === '') return <View key={`blank-${i}`} style={s.keyBlank} />;
        const isDelete = label === '⌫';
        return (
          <Pressable
            key={label}
            accessibilityRole="button"
            accessibilityLabel={isDelete ? t('common.delete') : label}
            accessibilityState={{ disabled }}
            disabled={disabled}
            onPress={() => onKey(isDelete ? 'del' : label)}
            style={({ pressed }) => [s.key, pressed && { opacity: 0.7 }]}
          >
            <Text style={s.keyText}>{label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** A card with a small label and one big value (savings, liquidity). */
export function StatCard({
  label,
  value,
  note,
  testID,
}: {
  label: string;
  value: string;
  note?: string;
  testID?: string;
}) {
  return (
    <View style={s.card} testID={testID}>
      <Text style={s.cardLabel}>{label}</Text>
      <Text style={s.cardValue} accessibilityLabel={`${label}: ${value}`}>
        {value}
      </Text>
      {note !== undefined ? <Text style={s.cardNote}>{note}</Text> : null}
    </View>
  );
}

/** A labelled row inside a card. */
export function Row({
  label,
  value,
  tone = 'normal',
}: {
  label: string;
  value: string;
  tone?: 'normal' | 'alert';
}) {
  return (
    <View style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={[s.rowValue, tone === 'alert' && { color: c.red }]}>{value}</Text>
    </View>
  );
}

/** Progress bar for a server-computed percentage (0-100); the app never computes it. */
export function ProgressBar({ percent, label }: { percent: number; label: string }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={label}
      accessibilityValue={{ min: 0, max: 100, now: clamped }}
      style={s.track}
    >
      <View style={[s.fill, { width: `${clamped}%` }]} />
    </View>
  );
}

/** Small count badge (pending approvals). */
export function Badge({ count, label }: { count: number; label: string }) {
  return (
    <View style={s.badge} accessibilityLabel={label}>
      <Text style={s.badgeText}>{String(count)}</Text>
    </View>
  );
}

export function Card({ children, testID }: { children: ReactNode; testID?: string }) {
  return (
    <View style={s.card} testID={testID}>
      {children}
    </View>
  );
}

export function SectionLabel({ children }: { children: string }) {
  return <Text style={s.section}>{children}</Text>;
}

const s = StyleSheet.create({
  keypad: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, paddingHorizontal: 16 },
  key: {
    flexBasis: '31%',
    flexGrow: 1,
    height: 54,
    borderRadius: 16,
    backgroundColor: c.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  keyBlank: { flexBasis: '31%', flexGrow: 1, height: 54 },
  keyText: { fontSize: 22, fontWeight: '500', color: c.ink },
  card: {
    backgroundColor: c.white,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: c.border,
    padding: 16,
    gap: 8,
  },
  cardLabel: { fontSize: 14, color: c.muted },
  cardValue: { fontSize: 26, fontWeight: '700', color: c.ink },
  cardNote: { fontSize: 13, color: c.muted },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', minHeight: 32 },
  rowLabel: { fontSize: 15, color: c.muted, flexShrink: 1 },
  rowValue: { fontSize: 15, fontWeight: '600', color: c.ink },
  track: { height: 10, borderRadius: 5, backgroundColor: c.line, overflow: 'hidden' },
  fill: { height: 10, backgroundColor: c.teal },
  badge: {
    minWidth: 24,
    height: 24,
    borderRadius: 12,
    paddingHorizontal: 6,
    backgroundColor: c.alertDot,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badgeText: { color: c.white, fontSize: 13, fontWeight: '700' },
  section: { fontSize: 13, fontWeight: '600', color: c.muted, textTransform: 'uppercase', marginTop: 8 },
});
