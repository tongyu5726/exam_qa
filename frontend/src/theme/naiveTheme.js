export function themeOverrides(dark) {
  const primary = dark ? '#4ecdc4' : '#2a9d8f'
  return {
    common: {
      primaryColor: primary,
      primaryColorHover: dark ? '#6fd9d1' : '#238f82',
      primaryColorPressed: dark ? '#3bb8b0' : '#1f7f73',
      primaryColorSuppl: primary,
      fontFamily: 'var(--sz-font-ui)',
      fontWeight: '600',
      fontWeightStrong: '700',
      borderRadius: '10px',
      bodyColor: dark ? '#0e1412' : '#f7f5f0',
      cardColor: dark ? '#151b18' : '#ffffff',
      modalColor: dark ? '#151b18' : '#ffffff',
      popoverColor: dark ? '#151b18' : '#ffffff',
    },
  }
}
