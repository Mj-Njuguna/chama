import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button, Loading } from '../../components/ui';
import { t } from '../../i18n';
import { c } from '../app/theme';
import { StaleNotice } from '../shell/QueryStates';
import type { ReadError } from '../shell/useAuthedQuery';

/**
 * Loading, error, offline and empty states for one server read inside a screen section. Cached
 * data stays visible read-only with a notice when a refresh fails.
 */
export function QueryBlock<T>({
  query,
  isEmpty,
  emptyText,
  children,
}: {
  query: UseQueryResult<T, ReadError>;
  isEmpty?: (data: T) => boolean;
  emptyText?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.data === undefined) {
    if (query.error === null) return <Loading label={t('common.loading')} />;
    return (
      <View style={styles.box} testID="block-error">
        <Text style={styles.text}>
          {t(query.error.failure === 'offline' ? 'common.offlineEmpty' : 'common.failed')}
        </Text>
        <Button label={t('common.retry')} onPress={() => void query.refetch()} />
      </View>
    );
  }
  const data = query.data;
  return (
    <View style={styles.stack}>
      <StaleNotice error={query.error} />
      {isEmpty?.(data) === true && emptyText !== undefined ? (
        <Text style={styles.text}>{emptyText}</Text>
      ) : (
        children(data)
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { gap: 12, paddingVertical: 16 },
  stack: { gap: 12 },
  text: { fontSize: 15, color: c.muted },
});
