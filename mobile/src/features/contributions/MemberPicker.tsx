import { Pressable, StyleSheet, Text, View } from 'react-native';

import { c } from '../app/theme';

export type PickOption = { readonly key: string; readonly label: string; readonly note?: string };

/** A single-choice list (members), each row a large touch target. */
export function OptionList({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly PickOption[];
  value: string | null;
  onChange: (key: string) => void;
  label: string;
}) {
  return (
    <View style={styles.list} accessibilityRole="radiogroup" accessibilityLabel={label}>
      <Text style={styles.label}>{label}</Text>
      {options.map((o) => {
        const on = o.key === value;
        return (
          <Pressable
            key={o.key}
            accessibilityRole="radio"
            accessibilityState={{ selected: on }}
            accessibilityLabel={o.note === undefined ? o.label : `${o.label}, ${o.note}`}
            onPress={() => onChange(o.key)}
            style={[styles.option, on && styles.optionOn]}
            testID={`option-${o.key}`}
          >
            <Text style={[styles.text, on && styles.textOn]}>{o.label}</Text>
            {o.note !== undefined ? <Text style={styles.note}>{o.note}</Text> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: 8 },
  label: { fontSize: 15, fontWeight: '600', color: c.ink },
  option: {
    minHeight: 48,
    justifyContent: 'center',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: c.border,
    backgroundColor: c.white,
  },
  optionOn: { borderColor: c.teal, backgroundColor: c.selectedBg },
  text: { fontSize: 15, color: c.ink },
  textOn: { fontWeight: '700', color: c.teal },
  note: { fontSize: 13, color: c.muted },
});
