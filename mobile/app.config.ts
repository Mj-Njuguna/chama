import type { ConfigContext, ExpoConfig } from 'expo/config';

// Public build-time config only (EXPO_PUBLIC_*). Never put secrets here: the bundle is public.
// Runtime values are read and validated in src/config, never in feature code.
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'Chama',
  slug: 'chama',
  version: '0.1.0',
  scheme: 'chama',
  orientation: 'portrait',
  icon: './assets/icon.png',
  ios: { bundleIdentifier: 'app.chama.mobile' },
  android: {
    package: 'app.chama.mobile',
    // No Google Drive / device-transfer backup of app data: secure-store ciphertext and
    // preferences must never be restored onto another install.
    allowBackup: false,
    adaptiveIcon: {
      backgroundColor: '#E6F4FE',
      foregroundImage: './assets/android-icon-foreground.png',
      backgroundImage: './assets/android-icon-background.png',
      monochromeImage: './assets/android-icon-monochrome.png',
    },
    predictiveBackGestureEnabled: false,
  },
  plugins: [
    'expo-router',
    './plugins/withCleartextPolicy',
    // Android 12+ device-to-device transfer ignores allowBackup; exclude all app data.
    './plugins/withDataExtractionRules',
    ['expo-local-authentication', { faceIDPermission: 'Allow Chama to use Face ID to unlock the app.' }],
  ],
  experiments: { typedRoutes: true },
});
