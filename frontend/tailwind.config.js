/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          100: "#e0e9ff",
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

