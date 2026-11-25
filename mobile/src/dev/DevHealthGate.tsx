import { type ReactNode, useCallback, useEffect, useState } from 'react';

import { Body, Button, Loading, Screen, Title } from '../components/ui';
import { t } from '../i18n';
import { checkBackendHealth, HEALTH_TIMEOUT_MS, type HealthResult } from './healthCheck';

export const DEV_HEALTH_GATE_MARKER = 'DEV_ONLY_HEALTH_GATE__NEVER_IN_RELEASE';

/**
 * DEV ONLY. Blocks the app until the backend answers `/healthz`, so a wrong host, port or
 * ALLOWED_HOSTS shows up as a clear message on first launch instead of a failed login later.
 */
export function DevHealthGate({
  apiBaseUrl,
  children,
  check = checkBackendHealth,
}: {
  apiBaseUrl: string;
  children: ReactNode;
  check?: (apiBaseUrl: string) => Promise<HealthResult>;
}) {
  const [result, setResult] = useState<HealthResult | 'pending'>('pending');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void check(apiBaseUrl).then((next) => {
      if (active) setResult(next);
    });
    return () => {
      active = false;
    };
  }, [apiBaseUrl, check, attempt]);

  const retry = useCallback(() => {
    setResult('pending');
    setAttempt((n) => n + 1);
  }, []);

  if (result === 'pending') {
    return <Loading label={t('common.checking')} />;
  }
  if (result.ok) {
    return children;
  }
  return (
    <Screen testID="dev-health-error">
      <Title>{t('dev.health.title')}</Title>
      <Body>{t('dev.health.url', { url: `${apiBaseUrl}/healthz` })}</Body>
      <Body tone="danger">{describe(result)}</Body>
      <Button label={t('common.retry')} onPress={retry} />
    </Screen>
  );
}

function describe(result: Extract<HealthResult, { ok: false }>): string {
  switch (result.kind) {
    case 'timeout':
      return t('dev.health.timeout', { seconds: HEALTH_TIMEOUT_MS / 1000 });
    case 'unreachable':
      return t('dev.health.unreachable');
    case 'host_not_allowed':
      return t('dev.health.host_not_allowed');
    case 'http_error':
      return t('dev.health.http_error', { status: result.status ?? 0 });
  }
}
