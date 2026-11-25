import { StatusBar } from 'expo-status-bar';
import { type ComponentType, type ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AppRoot, ConfigError } from '../src/AppRoot';
import { loadConfig } from '../src/config';
import { t } from '../src/i18n';

type GateProps = { apiBaseUrl: string; children: ReactNode };

// The health gate is dev-only. `__DEV__` is false in release bundles, so this branch and the
// module it requires are removed by the minifier.
const DevHealthGate: ComponentType<GateProps> | null = __DEV__
  ? // eslint-disable-next-line @typescript-eslint/no-require-imports
    (require('../src/dev/DevHealthGate') as typeof import('../src/dev/DevHealthGate')).DevHealthGate
  : null;

export default function RootLayout() {
  const result = loadConfig();

  if (!result.ok) {
    return (
      <SafeAreaProvider>
        <ConfigError message={t(`config.error.${result.error}`)} />
      </SafeAreaProvider>
    );
  }

  const app = <AppRoot apiBaseUrl={result.config.apiBaseUrl} />;
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      {DevHealthGate === null ? (
        app
      ) : (
        <DevHealthGate apiBaseUrl={result.config.apiBaseUrl}>{app}</DevHealthGate>
      )}
    </SafeAreaProvider>
  );
}
