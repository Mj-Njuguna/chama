import { StyleSheet, Text, View } from 'react-native';

import { Body, Button, Loading, Screen } from '../../components/ui';
import { t } from '../../i18n';
import { c } from '../app/theme';
import type { ReadError } from './useAuthedQuery';

/** Notice above cached data after a failed refresh (offline or server error). */
export function StaleNotice({ error }: { error: ReadError | null }) {
  if (error === null) return null;
  return (
    <View style={styles.notice} accessibilityRole="alert" testID="stale-notice">
      <Text style={styles.noticeText}>
        {t(error.failure === 'offline' ? 'common.offline' : 'common.staleFailed')}
      </Text>
    </View>
  );
}

/** Full-screen state when there is nothing cached to show. */
export function NoData({
  loading,
  error,
  onRetry,
}: {
  loading: boolean;
  error: ReadError | null;
  onRetry: () => void;
}) {
  if (loading || error === null) return <Loading label={t('common.loading')} />;
  return (
    <Screen testID="no-data">
      <Body tone="danger">{t(error.failure === 'offline' ? 'common.offlineEmpty' : 'common.failed')}</Body>
      <Button label={t('common.retry')} onPress={onRetry} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  notice: {
    backgroundColor: c.warnBg,
    borderColor: c.warnBorder,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  noticeText: { color: c.warnText, fontSize: 14 },
});
