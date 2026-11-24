import globals from 'globals';

export default [{
  files: ['frontend/boot.js', 'frontend/js/**/*.js', 'frontend/i18n.js', 'backend/analysis/static/*.js'],
  languageOptions: {
    sourceType: 'module',
    globals: { ...globals.browser, PortLightI18n: 'readonly' },
  },
  rules: { 'no-undef': 'error' },
}];
