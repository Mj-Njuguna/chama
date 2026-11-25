// https://docs.expo.dev/guides/using-eslint/
const { defineConfig, globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');
const prettierConfig = require('eslint-config-prettier');

const LETTER = /\p{L}/u;
const TEXT_PROPS = new Set(['accessibilityLabel', 'accessibilityHint', 'placeholder', 'title', 'label']);

/**
 * Local rules (no extra dependency).
 * no-raw-ui-text: user-facing text must come from t(); flags JSX text with letters and
 * string literals in text-like props.
 */
const local = {
  rules: {
    'no-raw-ui-text': {
      meta: { type: 'problem', messages: { raw: 'User-facing text must go through t() (src/i18n).' } },
      create(context) {
        return {
          JSXText(node) {
            if (LETTER.test(node.value)) context.report({ node, messageId: 'raw' });
          },
          JSXAttribute(node) {
            const name = node.name && node.name.name;
            const value = node.value;
            if (
              TEXT_PROPS.has(name) &&
              value &&
              value.type === 'Literal' &&
              LETTER.test(String(value.value))
            ) {
              context.report({ node, messageId: 'raw' });
            }
          },
        };
      },
    },
  },
};

module.exports = defineConfig([
  globalIgnores(['dist/*', 'android/*', '.expo/*', 'src/api/generated/*', 'coverage/*']),
  expoConfig,
  prettierConfig,
  {
    plugins: { local },
    rules: {
      // logging goes through src/lib/log (redacting), never console directly.
      'no-console': 'error',
      // tokens only in expo-secure-store.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@react-native-async-storage/async-storage',
              message: 'Never store credentials in AsyncStorage; use expo-secure-store via src/auth.',
            },
            {
              name: 'openapi-fetch',
              message: 'Use the client from src/api; only src/api/client.ts creates it.',
            },
          ],
          patterns: [
            {
              group: ['**/api/generated/*', '**/generated/schema'],
              message: 'Import API types via src/api (Schemas), not the generated file directly.',
            },
          ],
        },
      ],
    },
  },
  {
    // src/api is the one place allowed to touch openapi-fetch and the generated schema.
    files: ['src/api/**'],
    rules: { 'no-restricted-imports': 'off' },
  },
  {
    files: ['app/**/*.tsx', 'src/**/*.tsx'],
    ignores: ['**/__tests__/**', '**/*.test.tsx'],
    rules: { 'local/no-raw-ui-text': 'error' },
  },
  {
    files: ['**/__tests__/**', '**/*.test.ts', '**/*.test.tsx', '**/*.test.js'],
    languageOptions: {
      globals: Object.fromEntries(
        ['describe', 'it', 'test', 'expect', 'jest', 'beforeEach', 'afterEach', 'beforeAll', 'afterAll'].map(
          (g) => [g, 'readonly'],
        ),
      ),
    },
  },
  {
    files: ['scripts/**/*.js', 'plugins/**/*.js', '*.config.js'],
    languageOptions: {
      globals: {
        require: 'readonly',
        module: 'writable',
        __dirname: 'readonly',
        process: 'readonly',
        console: 'readonly',
      },
    },
    rules: { 'no-console': 'off' },
  },
]);
