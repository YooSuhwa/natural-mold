import baseConfig from './eslint.config.mjs'

const jsxA11yRules = {
  'jsx-a11y/alt-text': 'warn',
  'jsx-a11y/anchor-has-content': 'warn',
  'jsx-a11y/anchor-is-valid': 'warn',
  'jsx-a11y/aria-props': 'warn',
  'jsx-a11y/aria-proptypes': 'warn',
  'jsx-a11y/aria-role': 'warn',
  'jsx-a11y/aria-unsupported-elements': 'warn',
  'jsx-a11y/click-events-have-key-events': 'warn',
  'jsx-a11y/control-has-associated-label': 'warn',
  'jsx-a11y/interactive-supports-focus': 'warn',
  'jsx-a11y/label-has-associated-control': 'warn',
  'jsx-a11y/no-noninteractive-element-interactions': 'warn',
  'jsx-a11y/no-static-element-interactions': 'warn',
  'jsx-a11y/role-has-required-aria-props': 'warn',
  'jsx-a11y/role-supports-aria-props': 'warn',
}

const eslintA11yConfig = [
  ...baseConfig,
  {
    files: ['src/**/*.{jsx,tsx}'],
    languageOptions: {
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    rules: jsxA11yRules,
  },
]

export default eslintA11yConfig
