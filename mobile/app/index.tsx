import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { loadConfig } from '../src/config';
import { t } from '../src/i18n';

export default function Index() {
  const result = loadConfig();
  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.body}>
        <Text style={styles.title} accessibilityRole="header">
          {t('app.name')}
        </Text>
        <Text style={styles.subtitle}>
          {result.ok ? t('app.tagline') : t(`config.error.${result.error}`)}
        </Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#FFFFFF' },
  body: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { fontSize: 28, fontWeight: '700', color: '#0F172A' },
  subtitle: { marginTop: 8, fontSize: 16, color: '#475569', textAlign: 'center' },
});
