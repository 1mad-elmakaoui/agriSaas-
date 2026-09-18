/**
 * Semantic colour only.
 *
 * The five risk/stress colours below are the *only* hues that carry meaning.
 * Everything else in the interface is neutral, so that a coloured pixel is
 * always a statement about severity rather than decoration. A dashboard where
 * every card has its own accent teaches the user to ignore colour, which is
 * exactly the signal this product needs to keep.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Neutral ground. Slightly warm, so the semantic colours stay separable.
        ink: {
          50: '#f8f9fa', 100: '#f1f3f5', 200: '#e5e8eb', 300: '#ced4da',
          400: '#adb5bd', 500: '#868e96', 600: '#5c636a', 700: '#41474d',
          800: '#2b3035', 900: '#191c1f',
        },
        // Semantic — fixed by the glossary, used in the map legend too.
        low: '#2f7d4f',
        moderate: '#b8860b',
        high: '#c2570f',
        critical: '#b3261e',
        recommended: '#1b5fa8',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
      fontSize: {
        // A dedicated size for figures, so a number never inherits body text.
        figure: ['1.75rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
      },
      boxShadow: {
        panel: '0 1px 2px rgb(25 28 31 / 0.05), 0 1px 3px rgb(25 28 31 / 0.06)',
      },
    },
  },
  plugins: [],
}
