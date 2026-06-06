/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          100: "#e0e9ff",
          200: "#c7d4fb",
          300: "#a5b8f5",
          400: "#7a93ef",
          500: "#4f70e8",
          600: "#3d5cd4",
          700: "#2d47c0",
          900: "#1a2d7a",
        },
      },
    },
  },
  plugins: [],
};

