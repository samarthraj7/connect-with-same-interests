import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from "react-native";
import { colors, fonts, space } from "../lib/theme";

export function BrandMark({ size = "lg" }: { size?: "lg" | "sm" }) {
  const big = size === "lg";
  return (
    <View>
      <Text style={[styles.brand, big ? styles.brandLg : styles.brandSm]}>Connect Deeply</Text>
      {big ? (
        <Text style={styles.brandSub}>Meet people with something real in common.</Text>
      ) : null}
    </View>
  );
}

export function Field(props: TextInputProps & { label: string }) {
  const { label, style, ...rest } = props;
  return (
    <View style={styles.fieldWrap}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.muted}
        style={[styles.input, style]}
        {...rest}
      />
    </View>
  );
}

export function ChipInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = React.useState("");
  const add = () => {
    const t = draft.trim();
    if (!t) return;
    if (!value.includes(t)) onChange([...value, t]);
    setDraft("");
  };
  return (
    <View style={styles.fieldWrap}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.chipRow}>
        {value.map((v) => (
          <Pressable key={v} onPress={() => onChange(value.filter((x) => x !== v))} style={styles.chip}>
            <Text style={styles.chipText}>{v} ×</Text>
          </Pressable>
        ))}
      </View>
      <TextInput
        value={draft}
        onChangeText={setDraft}
        onSubmitEditing={add}
        placeholder={placeholder || "Type and hit return"}
        placeholderTextColor={colors.muted}
        style={styles.input}
        returnKeyType="done"
      />
    </View>
  );
}

export function Button({
  title,
  onPress,
  variant = "primary",
  loading,
  disabled,
  style,
}: {
  title: string;
  onPress: () => void;
  variant?: "primary" | "ghost" | "ember";
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  const bg =
    variant === "primary" ? colors.forest : variant === "ember" ? colors.ember : "transparent";
  const color = variant === "ghost" ? colors.forest : colors.chalk;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.btn,
        { backgroundColor: bg, opacity: pressed || disabled ? 0.7 : 1 },
        variant === "ghost" && styles.btnGhost,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={color} />
      ) : (
        <Text style={[styles.btnText, { color }]}>{title}</Text>
      )}
    </Pressable>
  );
}

export function SectionTitle({ children }: { children: string }) {
  return <Text style={styles.section}>{children}</Text>;
}

export function Body({ children }: { children: React.ReactNode }) {
  return <Text style={styles.body}>{children}</Text>;
}

export function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.bulletRow}>
      <Text style={styles.bulletDot}>·</Text>
      <Text style={styles.bulletText}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  brand: {
    fontFamily: fonts.display,
    color: colors.ink,
  },
  brandLg: { fontSize: 40, lineHeight: 46, letterSpacing: -0.8 },
  brandSm: { fontSize: 22, letterSpacing: -0.4 },
  brandSub: {
    marginTop: 8,
    fontFamily: fonts.body,
    fontSize: 16,
    lineHeight: 22,
    color: colors.muted,
    maxWidth: 280,
  },
  fieldWrap: { marginBottom: space.md },
  label: {
    fontFamily: fonts.bodyMed,
    fontSize: 13,
    color: colors.moss,
    marginBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.8,
  },
  input: {
    fontFamily: fonts.body,
    fontSize: 16,
    color: colors.ink,
    backgroundColor: "rgba(251, 252, 250, 0.85)",
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 8 },
  chip: {
    backgroundColor: colors.mist,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
  },
  chipText: { fontFamily: fonts.bodyMed, color: colors.forest, fontSize: 13 },
  btn: {
    borderRadius: 16,
    paddingVertical: 14,
    paddingHorizontal: 18,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 52,
  },
  btnGhost: { borderWidth: 1, borderColor: colors.line },
  btnText: { fontFamily: fonts.bodySemi, fontSize: 16 },
  section: {
    fontFamily: fonts.display,
    fontSize: 22,
    color: colors.ink,
    marginBottom: 10,
    marginTop: 8,
  },
  body: {
    fontFamily: fonts.body,
    fontSize: 15,
    lineHeight: 22,
    color: colors.ink,
  },
  bulletRow: { flexDirection: "row", gap: 8, marginBottom: 8 },
  bulletDot: { fontFamily: fonts.bodySemi, color: colors.ember, fontSize: 18, lineHeight: 22 },
  bulletText: { flex: 1, fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink },
});
