/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#2B1D15',
        cream: '#FFF7EB',
        paper: '#FFF0DA',
        orange: '#F56A22',
        peach: '#FFB36B',
      },
      boxShadow: {
        soft: '0 16px 50px rgba(111, 59, 26, .12)',
        lift: '0 22px 60px rgba(111, 59, 26, .18)',
      },
    },
  },
  plugins: [],
};
