/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/dashboard/templates/**/*.html',
    './src/templates/**/*.html'
  ],
  safelist: [
    {
      pattern: /^(bg|ring|text|border)-(indigo|blue|green|yellow|red|purple|pink|gray|orange|teal|cyan|emerald)-500$/
    }
  ],
  theme: {
    extend: {}
  },
  plugins: []
};
