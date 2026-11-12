import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import i18next from 'eslint-plugin-i18next'
import tseslint from 'typescript-eslint'
import { globalIgnores } from 'eslint/config'

export default tseslint.config([
  globalIgnores(['dist', 'src/vendor']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', {
        varsIgnorePattern: '^_',
        argsIgnorePattern: '^_',
        destructuredArrayIgnorePattern: '^_',
        ignoreRestSiblings: true,
      }],
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  // Hook + component modules (live context, row cells, widget frame) export helpers on purpose.
  {
    files: ['src/app/live.tsx', 'src/app/providers.tsx', 'src/ui/**/*.tsx', 'src/widgets/**/*.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  // i18n guard: user-visible literals in UI must come from the dictionaries (ru / en).
  {
    files: ['src/pages/**/*.tsx', 'src/widgets/**/*.tsx', 'src/ui/**/*.tsx', 'src/app/**/*.tsx'],
    ignores: ['src/pages/dev/**'],
    plugins: { i18next },
    rules: {
      'i18next/no-literal-string': ['warn', {
        markupOnly: true,
        onlyAttribute: ['title', 'placeholder', 'aria-label', 'alt', 'label'],
        words: {
          exclude: [
            String.raw`^[^\p{L}]*$`,
            String.raw`^\s*[×v]\d`,
            String.raw`^\s*(NEAR|Sendler|Dash|LIVE|TradingView|S|M|L)\b`,
          ],
        },
      }],
    },
  },
])
