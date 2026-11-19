/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F3F5F7",
        surface: "#FFFFFF",
        ink: { DEFAULT: "#14213D", 2: "#33405C", 3: "#5E6982", 4: "#8A93A6" },
        rule: { DEFAULT: "#DDE2E9", strong: "#C5CCD7" },
        wash: "#E9EDF2",
        focus: "#2F5BEA",
        supported: { DEFAULT: "#0E8A6A", soft: "#E3F3EE" },
        memory: { DEFAULT: "#B7791F", soft: "#FBF0DD" },
        false: { DEFAULT: "#C2413B", soft: "#FBE7E5" },
        unknown: { DEFAULT: "#6B4FBB", soft: "#EEE9FA" },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      fontSize: {
        xs: ["12px", "16px"], sm: ["13px", "19px"], base: ["15px", "23px"], md: ["17px", "25px"],
        lg: ["20px", "28px"], xl: ["26px", "32px"], "2xl": ["34px", "40px"], "3xl": ["46px", "52px"],
      },
      boxShadow: {
        pop: "0 1px 2px rgba(20,33,61,.06), 0 8px 24px -6px rgba(20,33,61,.18)",
        node: "0 1px 0 rgba(20,33,61,.05)",
      },
      keyframes: {
        rise: { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "none" } },
        pulseline: { "0%,100%": { opacity: ".35" }, "50%": { opacity: "1" } },
      },
      animation: { rise: "rise .35s ease-out both", pulseline: "pulseline 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};
