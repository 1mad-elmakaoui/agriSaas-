import js from '@eslint/js'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsparser from '@typescript-eslint/parser'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'

/**
 * `react/no-danger` is an **error**, not a warning.
 *
 * A supplier or crop name containing `<img src=x onerror=…>` is a realistic row
 * in any system that accepts typed input, and this product accepts typed input
 * from farmers. React escapes by default; the only way to lose that is
 * `dangerouslySetInnerHTML`, so the rule is the guard and a test asserts the
 * string appears nowhere in the tree.
 */
export default [
  js.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsparser,
      parserOptions: { ecmaFeatures: { jsx: true }, sourceType: 'module' },
      globals: { window: 'readonly', document: 'readonly', localStorage: 'readonly',
                 fetch: 'readonly', console: 'readonly', setTimeout: 'readonly',
                 HTMLElement: 'readonly', HTMLDivElement: 'readonly', URL: 'readonly' },
    },
    plugins: { '@typescript-eslint': tseslint, react, 'react-hooks': reactHooks },
    settings: { react: { version: '18.3' } },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react/no-danger': 'error',
      'no-undef': 'off',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
]
