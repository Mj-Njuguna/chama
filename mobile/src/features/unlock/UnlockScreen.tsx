import { useEffect, useRef, useState } from 'react';

import { Body, Button, Screen, Title } from '../../components/ui';
import { t } from '../../i18n';

/**
 * S2 Unlock. Shows the system prompt (biometrics or screen lock) once on arrival; after a cancel
 * the app stays here, locked, with a button to try again. Never renders signed-in content.
 */
export function UnlockScreen({ cancelled, onUnlock }: { cancelled: boolean; onUnlock: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const prompted = useRef(false);

  async function unlock(): Promise<void> {
    setBusy(true);
    try {
      await onUnlock();
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (prompted.current || cancelled) return;
    prompted.current = true;
    void unlock();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- prompt once on arrival only
  }, []);

  return (
    <Screen testID="unlock">
      <Title>{t('unlock.title')}</Title>
      <Body tone="muted">{t(cancelled ? 'unlock.cancelled' : 'unlock.explain')}</Body>
      <Button label={t('unlock.button')} busy={busy} onPress={() => void unlock()} />
    </Screen>
  );
}
