import { Linking } from 'react-native';

import { Body, Button, LinkButton, Screen, Title } from '../../components/ui';
import { t } from '../../i18n';

/** S2, no screen lock: the app is blocked until the device has one. */
export function NoScreenLockScreen({ onRetry }: { onRetry: () => Promise<void> }) {
  return (
    <Screen testID="no-screen-lock">
      <Title>{t('lock.noScreenLock.title')}</Title>
      <Body>{t('lock.noScreenLock')}</Body>
      <Button label={t('lock.openSettings')} onPress={() => void Linking.openSettings()} />
      <LinkButton label={t('lock.retry')} onPress={() => void onRetry()} />
    </Screen>
  );
}
