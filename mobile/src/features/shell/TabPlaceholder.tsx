import { StyleSheet, Text, View } from 'react-native';

import { t } from '../../i18n';
import { c } from '../app/theme';
import { isOfficial, type Role } from '../chamas/chamaApi';
import type { Tab } from './SignedInApp';

/** Tabs whose screens arrive in later phases. Describes what is coming for this role. */
export function TabPlaceholder({ tab, role }: { tab: Exclude<Tab, 'home'>; role: Role }) {
  const view = isOfficial(role) ? 'official' : 'member';
  return (
    <View style={styles.box} testID={`placeholder-${tab}`}>
      <Text accessibilityRole="header" style={styles.title}>
        {t(`tabs.${tab}`)}
      </Text>
      <Text style={styles.body}>{t(`placeholder.${tab}.${view}`)}</Text>
      <Text style={styles.soon}>{t('placeholder.soon')}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { flex: 1, padding: 24, gap: 12, justifyContent: 'center' },
  title: { fontSize: 22, fontWeight: '700', color: c.ink },
  body: { fontSize: 16, lineHeight: 22, color: c.muted },
  soon: { fontSize: 14, color: c.teal, fontWeight: '600' },
});
