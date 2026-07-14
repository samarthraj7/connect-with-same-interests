/** Design tokens — deep forest + warm paper; dark mode counterpart. */
export type ThemeColors = {
  ink: string;
  forest: string;
  moss: string;
  leaf: string;
  mist: string;
  paper: string;
  chalk: string;
  ember: string;
  emberSoft: string;
  line: string;
  muted: string;
  danger: string;
  gradient: [string, string, string];
  orbA: string;
  orbB: string;
  statusBar: "dark" | "light";
  /** Input / surface fill */
  modeBg?: string;
};

export const lightColors: ThemeColors = {
  ink: "#12201C",
  forest: "#1A2F28",
  moss: "#2F4F44",
  leaf: "#3D6B5A",
  mist: "#E8EFEA",
  paper: "#F4F7F5",
  chalk: "#FBFCFA",
  ember: "#C45C26",
  emberSoft: "#E8A07A",
  line: "rgba(18, 32, 28, 0.12)",
  muted: "rgba(18, 32, 28, 0.58)",
  danger: "#A33B2B",
  gradient: ["#F7FAF8", "#E4EDE8", "#D5E4DC"],
  orbA: "rgba(61, 107, 90, 0.14)",
  orbB: "rgba(196, 92, 38, 0.08)",
  statusBar: "dark",
};

export const darkColors: ThemeColors = {
  ink: "#E8EFEA",
  forest: "#A8C4B8",
  moss: "#8FA99C",
  leaf: "#7BA892",
  mist: "#1E2E28",
  paper: "#0F1815",
  chalk: "#15201C",
  ember: "#E07A45",
  emberSoft: "#C45C26",
  line: "rgba(232, 239, 234, 0.14)",
  muted: "rgba(232, 239, 234, 0.58)",
  danger: "#E07060",
  gradient: ["#0F1815", "#15241F", "#1A2F28"],
  orbA: "rgba(61, 107, 90, 0.22)",
  orbB: "rgba(196, 92, 38, 0.14)",
  statusBar: "light",
};

/** @deprecated Prefer useTheme().colors — kept for gradual migration. */
export const colors = lightColors;

export const fonts = {
  display: "Fraunces_600SemiBold",
  body: "Outfit_400Regular",
  bodyMed: "Outfit_500Medium",
  bodySemi: "Outfit_600SemiBold",
};

export const space = {
  xs: 6,
  sm: 10,
  md: 16,
  lg: 24,
  xl: 36,
  xxl: 48,
};
