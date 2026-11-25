import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Stack } from 'expo-router';
import { useEffect, useMemo } from 'react';

import { type AppServices, createAppServices } from './auth/appServices';
import { AppServicesProvider } from './auth/AppServicesContext';
import { SessionProvider } from './auth/SessionProvider';
import { StepUpProvider } from './auth/StepUpProvider';
import { Body, Screen, Title } from './components/ui';
import { useChamaUi } from './features/chamas/store';
import { t } from './i18n';

export function ConfigError({ message }: { message: string }) {
  return (
    <Screen testID="config-error">
      <Title>{t('config.error.title')}</Title>
      <Body tone="danger">{message}</Body>
    </Screen>
  );
}

/** Cached reads only; writes are never retried silently and need connectivity. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000, gcTime: 24 * 3600_000 },
      mutations: { retry: false },
    },
  });
}

export function AppRoot({
  apiBaseUrl,
  create = createAppServices,
}: {
  apiBaseUrl: string;
  /** Injectable for tests. */
  create?: (apiBaseUrl: string) => AppServices;
}) {
  const services = useMemo(() => create(apiBaseUrl), [apiBaseUrl, create]);
  const queryClient = useMemo(() => createQueryClient(), []);

  // Signing out (any reason) forgets everything read for the previous user.
  useEffect(
    () =>
      services.signedOut.subscribe(() => {
        queryClient.clear();
        useChamaUi.getState().reset();
      }),
    [services, queryClient],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AppServicesProvider services={services}>
        <SessionProvider
          session={services.session}
          sessionApi={services.sessionApi}
          signedOut={services.signedOut}
          clock={services.clock}
        >
          <StepUpProvider
            tokens={services.session.tokens}
            api={services.stepUpApi}
            {...(services.devReadStepUpCode === undefined ? {} : { devReadCode: services.devReadStepUpCode })}
          >
            <Stack screenOptions={{ headerShown: false }} />
          </StepUpProvider>
        </SessionProvider>
      </AppServicesProvider>
    </QueryClientProvider>
  );
}
