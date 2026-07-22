/** @type {import('tailwindcss').Config} */
import typography from "@tailwindcss/typography";
import colors from "tailwindcss/colors";

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Single cool neutral system: every `gray-*` renders as slate.
        // Rule: always write `gray-*`, never `slate-*`.
        gray: colors.slate,
        // Brand — vivid logo blue ("NeuraFix blue"). Primary action = 600.
        brand: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6", // icons/accents only — below AA for small text on white
          600: "#2b6fe4", // PRIMARY action (white text = 4.67:1 AA — don't lighten)
          700: "#2160cd", // hover on primary
          800: "#1d4fa8", // active/pressed
          900: "#1c4184",
          950: "#132a52",
        },
        // Muted semantic ramps — desaturated so badges/alerts read professional.
        success: { 50: "#f2f9f4", 100: "#ddf0e2", 500: "#3d9a63", 600: "#2e7d4f", 700: "#276841" },
        warning: { 50: "#fdf8ec", 100: "#faedcc", 500: "#d99b28", 600: "#b57d1e", 700: "#93651d" },
        danger: { 50: "#fcf3f2", 100: "#f8dedb", 500: "#d2544a", 600: "#b83e35", 700: "#98352e" },
        info: { 50: "#f2f7fb", 100: "#dfebf6", 500: "#4585b5", 600: "#356e99", 700: "#2d5a7d" },
      },
      boxShadow: {
        // Border-first: hairline card + one elevated pop (modals/toasts/menus only).
        card: "0 1px 2px 0 rgb(15 23 42 / 0.04)",
        pop: "0 8px 24px -8px rgb(15 23 42 / 0.14), 0 2px 8px -2px rgb(15 23 42 / 0.08)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "Noto Sans",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        deva: ["Noto Sans Devanagari", "Noto Sans", "system-ui", "sans-serif"],
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out",
      },
      typography: ({ theme }) => ({
        brand: {
          css: {
            "--tw-prose-body": theme("colors.gray[700]"),
            "--tw-prose-headings": theme("colors.gray[900]"),
            "--tw-prose-bold": theme("colors.gray[900]"),
            "--tw-prose-bullets": theme("colors.gray[400]"),
            "--tw-prose-counters": theme("colors.gray[400]"),
            "--tw-prose-links": theme("colors.brand[600]"),
            "--tw-prose-quotes": theme("colors.gray[600]"),
            "--tw-prose-quote-borders": theme("colors.gray[300]"),
            "--tw-prose-hr": theme("colors.gray[200]"),
            "--tw-prose-th-borders": theme("colors.gray[300]"),
            "--tw-prose-td-borders": theme("colors.gray[200]"),
            maxWidth: "none",
            lineHeight: "1.7",
            h1: { fontWeight: "600", fontSize: "1.4em", marginTop: "1.2em", marginBottom: "0.5em" },
            h2: { fontWeight: "600", fontSize: "1.2em", marginTop: "1.1em", marginBottom: "0.4em" },
            h3: { fontWeight: "600", fontSize: "1.05em", marginTop: "1em", marginBottom: "0.3em" },
            "h1, h2, h3, h4": { lineHeight: "1.3", scrollMarginTop: "5rem" },
            p: { marginTop: "0.6em", marginBottom: "0.6em" },
            "ul, ol": { marginTop: "0.5em", marginBottom: "0.5em", paddingLeft: "1.4em" },
            "li": { marginTop: "0.2em", marginBottom: "0.2em" },
            "li::marker": { fontWeight: "600" },
            strong: { fontWeight: "600" },
            code: {
              backgroundColor: theme("colors.gray[100]"),
              padding: "0.15em 0.4em",
              borderRadius: "0.35rem",
              fontWeight: "500",
              fontSize: "0.9em",
            },
            "code::before": { content: '""' },
            "code::after": { content: '""' },
            blockquote: {
              fontStyle: "normal",
              borderLeftWidth: "3px",
              paddingLeft: "1em",
              backgroundColor: theme("colors.gray[50]"),
              borderRadius: "0 0.5rem 0.5rem 0",
              paddingTop: "0.4em",
              paddingBottom: "0.4em",
            },
            "blockquote p:first-of-type::before": { content: '""' },
            "blockquote p:last-of-type::after": { content: '""' },
          },
        },
      }),
    },
  },
  plugins: [typography],
};
